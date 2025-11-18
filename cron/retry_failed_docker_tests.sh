#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# retry_failed_docker_tests.sh - Retry Tests That Failed Due to Docker Image Unavailability
#
# DESCRIPTION:
#   After the daily CI summary, this script checks if Docker images are now available
#   and re-runs any tests that failed due to image unavailability. It reads configuration
#   from crontab_rules.txt and queries the database for tests to retry.
#
# USAGE:
#   retry_failed_docker_tests.sh [OPTIONS]
#
# OPTIONS:
#   --crontab-file=PATH      Path to crontab rules file [default: cron/crontab_rules.txt]
#   --date=YYYYMMDD          Date to check logs for [default: today]
#   --help, -h               Show detailed help message
#
# EXAMPLES:
#   retry_failed_docker_tests.sh
#   retry_failed_docker_tests.sh --date=20251117
# ---------------------------------------------------------------------------
set -euo pipefail

# Set timezone to PST/PDT for consistent logging
export TZ='America/Los_Angeles'

###############################################################################
# Configuration Variables
###############################################################################
CRONTAB_FILE=""
CHECK_DATE=""

###############################################################################
# CLI Parameter Processing
###############################################################################
for arg in "$@"; do
  case $arg in
    --crontab-file=*)
      CRONTAB_FILE="${arg#*=}"
      ;;
    --date=*)
      CHECK_DATE="${arg#*=}"
      ;;
    --help|-h)
      echo "Usage: $0 [OPTIONS]"
      echo ""
      echo "Retry tests that failed due to Docker image unavailability"
      echo ""
      echo "Options:"
      echo "  --crontab-file=PATH      Path to crontab rules file"
      echo "  --date=YYYYMMDD          Date to check logs for [default: today]"
      echo "  --help, -h               Show this help message"
      exit 0
      ;;
    *)
      echo "Unknown argument: $arg"
      echo "Use --help for usage information"
      exit 1
      ;;
  esac
done

###############################################################################
# Source Variables from Crontab File
###############################################################################

# Determine script and crontab locations
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Default crontab file if not specified
if [[ -z "$CRONTAB_FILE" ]]; then
  CRONTAB_FILE="${SCRIPT_DIR}/crontab_rules.txt"
fi

if [[ ! -f "$CRONTAB_FILE" ]]; then
  echo "[retry] ERROR: Crontab file not found: $CRONTAB_FILE"
  exit 1
fi

echo "[retry] =========================================="
echo "[retry] Docker Image Retry Check Started"
echo "[retry] =========================================="
echo "[retry] Run Time: $(date '+%Y-%m-%d %H:%M:%S %Z')"
echo "[retry] Machine: $(hostname)"
echo "[retry] Crontab File: $CRONTAB_FILE"
echo ""

# Extract environment variables from crontab file
echo "[retry] Loading configuration from crontab file..."

# Parse environment variables from crontab file (lines like VAR="value")
while IFS='=' read -r key value; do
  # Skip comments and empty lines
  [[ "$key" =~ ^#.*$ ]] && continue
  [[ -z "$key" ]] && continue

  # Skip cron schedule lines
  [[ "$key" =~ ^[0-9] ]] && continue

  # Clean up key and value
  key=$(echo "$key" | tr -d '[:space:]')
  value=$(echo "$value" | sed 's/^["'"'"']//' | sed 's/["'"'"']$//')

  # Export the variable
  if [[ -n "$key" && -n "$value" ]]; then
    export "$key=$value"
    echo "[retry]   $key=$value"
  fi
done < <(grep -E '^[A-Z_]+=' "$CRONTAB_FILE")

# Set defaults if not found in crontab
export BASE_DIR="${SGL_BENCHMARK_CI_DIR:-/mnt/raid/michael/sglang-ci}"
export HARDWARE_TYPE="${HARDWARE_TYPE:-mi30x}"
export TEAMS_WEBHOOK_URL="${TEAMS_WEBHOOK_URL:-}"
export GROK_MODEL_PATH="${GROK_MODEL_PATH:-}"
export GROK2_MODEL_PATH="${GROK2_MODEL_PATH:-}"
export GROK2_TOKENIZER_PATH="${GROK2_TOKENIZER_PATH:-}"
export DEEPSEEK_MODEL_PATH="${DEEPSEEK_MODEL_PATH:-}"
export GITHUB_REPO="${GITHUB_REPO:-}"
export GITHUB_TOKEN="${GITHUB_TOKEN:-}"

# Set default date if not provided
if [[ -z "$CHECK_DATE" ]]; then
  CHECK_DATE=$(date +%Y%m%d)
fi

CRON_LOG_DIR="${BASE_DIR}/cron/cron_log/${HARDWARE_TYPE}/${CHECK_DATE}"

echo ""
echo "[retry] Configuration:"
echo "[retry]   Base Directory: $BASE_DIR"
echo "[retry]   Hardware: $HARDWARE_TYPE"
echo "[retry]   Check Date: $CHECK_DATE"
echo "[retry]   Cron Log Directory: $CRON_LOG_DIR"
echo ""

###############################################################################
# Logging Functions
###############################################################################
log_info() {
  echo "[retry] $*"
}

log_error() {
  echo "[retry] ERROR: $*" >&2
}

log_section() {
  echo ""
  echo "[retry] =========================================="
  echo "[retry] $*"
  echo "[retry] =========================================="
}

###############################################################################
# Docker Image Check Functions
###############################################################################
check_docker_image_available() {
  local hardware="$1"
  log_info "Checking if Docker image is available for $hardware..."

  # Run the image check script (suppresses Teams notification)
  local check_output
  check_output=$(bash "${BASE_DIR}/scripts/nightly_image_check.sh" --date="$CHECK_DATE" 2>&1)

  if echo "$check_output" | grep -q "All expected images are available"; then
    log_info "✓ Docker image is now available for $hardware"
    return 0
  else
    log_info "✗ Docker image is still not available for $hardware"
    return 1
  fi
}

###############################################################################
# Database Query Functions
###############################################################################
query_failed_tests_from_database() {
  local date_str="$1"
  local hardware="$2"

  # Use Python to query the database (suppress stderr to avoid mixing with output)
  python3 -c "
import sys
sys.path.insert(0, '${BASE_DIR}/database')
from database import DashboardDatabase

db = DashboardDatabase()

# Get test run for this date and hardware
test_run = db.get_test_run('$date_str', '$hardware')

if not test_run:
    print('NO_TEST_RUN', flush=True)
    sys.exit(0)

# Get benchmark results
results = db.get_benchmark_results(test_run['id'])

failed_tests = []
for result in results:
    # Check if test failed due to Docker image unavailability
    status = result['status'] or ''
    error_msg = result['error_message'] or ''

    # Tests with 'not run' status or Docker-related errors
    if (status.lower() == 'not run' or
        'could not find' in error_msg.lower() or
        'image' in error_msg.lower() and 'not found' in error_msg.lower() or
        'failed to pull' in error_msg.lower()):

        failed_tests.append(result['benchmark_name'])

# Print results
for test_name in failed_tests:
    print(test_name, flush=True)
" 2>/dev/null
}

###############################################################################
# Test Definition Structure
###############################################################################
# Map benchmark names to log files and retry commands
get_test_command() {
  local benchmark_name="$1"

  case "$benchmark_name" in
    "Unit Tests")
      echo "bash scripts/test_nightly.sh --hardware=\"$HARDWARE_TYPE\" --teams-webhook-url=\"$TEAMS_WEBHOOK_URL\""
      ;;
    "PD Disaggregation Tests")
      echo "bash scripts/test_nightly.sh --test-type=pd --hardware=\"$HARDWARE_TYPE\" --teams-webhook-url=\"$TEAMS_WEBHOOK_URL\""
      ;;
    "Sanity Check"|"Sanity")
      echo "bash scripts/perf_nightly.sh --mode=sanity --hardware=\"$HARDWARE_TYPE\" --sanity-trials=3 --teams-webhook-url=\"$TEAMS_WEBHOOK_URL\""
      ;;
    "DeepSeek DP Attention Test")
      echo "HSA_ENABLE_COREDUMP=0 GITHUB_REPO=\"$GITHUB_REPO\" GITHUB_TOKEN=\"$GITHUB_TOKEN\" bash scripts/perf_nightly.sh --model=deepseek --model-path=\"$DEEPSEEK_MODEL_PATH\" --mode=online --hardware=\"$HARDWARE_TYPE\" --check-dp-attention --teams-webhook-url=\"$TEAMS_WEBHOOK_URL\""
      ;;
    "DeepSeek Torch Compile Test")
      echo "HSA_ENABLE_COREDUMP=0 GITHUB_REPO=\"$GITHUB_REPO\" GITHUB_TOKEN=\"$GITHUB_TOKEN\" bash scripts/perf_nightly.sh --model=deepseek --model-path=\"$DEEPSEEK_MODEL_PATH\" --mode=online --hardware=\"$HARDWARE_TYPE\" --enable-torch-compile --teams-webhook-url=\"$TEAMS_WEBHOOK_URL\""
      ;;
    "DeepSeek DP+Torch Compile")
      echo "HSA_ENABLE_COREDUMP=0 GITHUB_REPO=\"$GITHUB_REPO\" GITHUB_TOKEN=\"$GITHUB_TOKEN\" bash scripts/perf_nightly.sh --model=deepseek --model-path=\"$DEEPSEEK_MODEL_PATH\" --mode=online --hardware=\"$HARDWARE_TYPE\" --check-dp-attention --enable-torch-compile --teams-webhook-url=\"$TEAMS_WEBHOOK_URL\""
      ;;
    "Grok Online Benchmark")
      echo "HSA_ENABLE_COREDUMP=0 GITHUB_REPO=\"$GITHUB_REPO\" GITHUB_TOKEN=\"$GITHUB_TOKEN\" bash scripts/perf_nightly.sh --model=grok --model-path=\"$GROK_MODEL_PATH\" --mode=online --hardware=\"$HARDWARE_TYPE\" --teams-webhook-url=\"$TEAMS_WEBHOOK_URL\""
      ;;
    "Grok 2 Online Benchmark")
      echo "HSA_ENABLE_COREDUMP=0 GITHUB_REPO=\"$GITHUB_REPO\" GITHUB_TOKEN=\"$GITHUB_TOKEN\" bash scripts/perf_nightly.sh --model=grok2 --model-path=\"$GROK2_MODEL_PATH\" --tokenizer-path=\"$GROK2_TOKENIZER_PATH\" --mode=online --hardware=\"$HARDWARE_TYPE\" --teams-webhook-url=\"$TEAMS_WEBHOOK_URL\""
      ;;
    "DeepSeek Online Benchmark")
      echo "HSA_ENABLE_COREDUMP=0 GITHUB_REPO=\"$GITHUB_REPO\" GITHUB_TOKEN=\"$GITHUB_TOKEN\" bash scripts/perf_nightly.sh --model=deepseek --model-path=\"$DEEPSEEK_MODEL_PATH\" --mode=online --hardware=\"$HARDWARE_TYPE\" --teams-webhook-url=\"$TEAMS_WEBHOOK_URL\""
      ;;
    *)
      echo ""
      ;;
  esac
}

get_log_filename() {
  local benchmark_name="$1"

  case "$benchmark_name" in
    "Unit Tests") echo "test_nightly.log" ;;
    "PD Disaggregation Tests") echo "test_nightly_pd.log" ;;
    "Sanity Check"|"Sanity") echo "sanity_check_nightly.log" ;;
    "DeepSeek DP Attention Test") echo "deepseek_dp_attention.log" ;;
    "DeepSeek Torch Compile Test") echo "deepseek_torch_compile.log" ;;
    "DeepSeek DP+Torch Compile") echo "deepseek_dp_attention_torch_compile.log" ;;
    "Grok Online Benchmark") echo "grok_nightly.log" ;;
    "Grok 2 Online Benchmark") echo "grok2_nightly_online.log" ;;
    "DeepSeek Online Benchmark") echo "deepseek_nightly_online.log" ;;
    *) echo "" ;;
  esac
}

###############################################################################
# Main Logic
###############################################################################

# Step 1: Check if Docker image is now available
if ! check_docker_image_available "$HARDWARE_TYPE"; then
  log_info "Docker image is still not available. Skipping retries."
  exit 0
fi

# Step 2: Query database for failed tests
log_section "Querying Database for Failed Tests"
cd "$BASE_DIR" || exit 1

log_info "Querying database for tests with 'not run' status or Docker image errors..."
FAILED_TESTS_OUTPUT=$(query_failed_tests_from_database "$CHECK_DATE" "$HARDWARE_TYPE")

if [[ "$FAILED_TESTS_OUTPUT" == "NO_TEST_RUN" ]]; then
  log_error "No test run found in database for date $CHECK_DATE and hardware $HARDWARE_TYPE"
  exit 1
fi

# Convert to array
mapfile -t FAILED_TESTS <<< "$FAILED_TESTS_OUTPUT"

# Filter empty lines
FILTERED_FAILED_TESTS=()
for test in "${FAILED_TESTS[@]}"; do
  if [[ -n "$test" ]]; then
    FILTERED_FAILED_TESTS+=("$test")
  fi
done

# Step 3: Exit if no failed tests found
if [[ ${#FILTERED_FAILED_TESTS[@]} -eq 0 ]]; then
  log_section "No Tests to Retry"
  log_info "All tests either succeeded or failed for reasons other than Docker image unavailability."
  exit 0
fi

# Step 4: Display retry plan
log_section "Retry Plan"
log_info "Found ${#FILTERED_FAILED_TESTS[@]} test(s) to retry:"
for test_name in "${FILTERED_FAILED_TESTS[@]}"; do
  log_info "  - $test_name"
done
echo ""

# Step 5: Re-run failed tests in order
log_section "Re-running Failed Tests"

RETRY_SUCCESS_COUNT=0
RETRY_FAIL_COUNT=0

for test_name in "${FILTERED_FAILED_TESTS[@]}"; do
  test_command=$(get_test_command "$test_name")
  log_filename=$(get_log_filename "$test_name")

  if [[ -z "$test_command" ]]; then
    log_error "Unknown test: $test_name (no retry command defined)"
    continue
  fi

  # Determine log file path
  retry_log_file="${CRON_LOG_DIR}/${log_filename}"

  # Backup original log with timestamp
  original_backup="${CRON_LOG_DIR}/${log_filename}.failed_$(date +%H%M%S)"
  if [[ -f "$retry_log_file" ]]; then
    cp "$retry_log_file" "$original_backup"
    log_info "Backed up original log to: $(basename $original_backup)"
  fi

  log_info ""
  log_info "==================== Retrying: $test_name ===================="
  log_info "Command: $test_command"
  log_info "Log: $retry_log_file (replacing original)"
  log_info "Start Time: $(date '+%Y-%m-%d %H:%M:%S %Z')"

  # Run the test and capture exit code (replaces original log)
  set +e
  eval "$test_command" > "$retry_log_file" 2>&1
  exit_code=$?
  set -e

  log_info "End Time: $(date '+%Y-%m-%d %H:%M:%S %Z')"

  if [[ $exit_code -eq 0 ]]; then
    log_info "✓ Retry succeeded: $test_name"
    RETRY_SUCCESS_COUNT=$((RETRY_SUCCESS_COUNT + 1))
  else
    log_error "✗ Retry failed: $test_name (exit code: $exit_code)"
    RETRY_FAIL_COUNT=$((RETRY_FAIL_COUNT + 1))
  fi

  # Update database and upload logs after each test
  log_info "Updating database and uploading logs for $test_name..."
  bash cron/sync_and_ingest_database.sh "$CHECK_DATE" "$HARDWARE_TYPE" || true
  bash cron/github_log_upload.sh "$CHECK_DATE" "$HARDWARE_TYPE" cron || true

  # Upload specific test logs based on test type
  case "$test_name" in
    "Unit Tests")
      bash cron/github_log_upload.sh "" "$HARDWARE_TYPE" unit-test || true
      ;;
    "PD Disaggregation Tests")
      bash cron/github_log_upload.sh "" "$HARDWARE_TYPE" pd "$(ls -t test/pd/pd_log/$HARDWARE_TYPE/ 2>/dev/null | head -1)" || true
      ;;
    "Sanity Check"|"Sanity")
      bash cron/github_log_upload.sh "" "$HARDWARE_TYPE" sanity "$(ls -t test/sanity_check_log/$HARDWARE_TYPE/ 2>/dev/null | head -1)" || true
      ;;
    *"Online Benchmark"*|*"Grok"*|*"DeepSeek"*)
      # Extract model name
      if [[ "$test_name" =~ "Grok 2" ]]; then
        bash cron/github_log_upload.sh "" "$HARDWARE_TYPE" online GROK2 || true
      elif [[ "$test_name" =~ "Grok" ]]; then
        bash cron/github_log_upload.sh "" "$HARDWARE_TYPE" online GROK1 || true
      elif [[ "$test_name" =~ "DeepSeek" ]]; then
        bash cron/github_log_upload.sh "" "$HARDWARE_TYPE" online DeepSeek-V3-0324 || true
      fi
      ;;
  esac
done

# Step 6: Send final summary
log_section "Retry Summary"
log_info "Total tests retried: ${#FILTERED_FAILED_TESTS[@]}"
log_info "Successful retries: $RETRY_SUCCESS_COUNT"
log_info "Failed retries: $RETRY_FAIL_COUNT"
echo ""

# Send updated daily summary (replaces original summary in database)
log_section "Sending Final Daily Summary"
summary_log="${CRON_LOG_DIR}/daily_summary_alert_retry.log"

if [[ -n "$TEAMS_WEBHOOK_URL" ]]; then
  python3 team_alert/send_daily_summary_alert.py \
    --hardware="$HARDWARE_TYPE" \
    --base-dir="$BASE_DIR" \
    --teams-webhook-url="$TEAMS_WEBHOOK_URL" \
    --use-database \
    > "$summary_log" 2>&1 || true
  log_info "✓ Final daily summary sent to Teams"
else
  log_info "⚠ Teams webhook not configured, skipping Teams notification"
  python3 team_alert/send_daily_summary_alert.py \
    --hardware="$HARDWARE_TYPE" \
    --base-dir="$BASE_DIR" \
    --use-database \
    > "$summary_log" 2>&1 || true
fi

# Upload final logs
bash cron/github_log_upload.sh "$CHECK_DATE" "$HARDWARE_TYPE" cron || true

log_section "Retry Check Complete"
log_info "End Time: $(date '+%Y-%m-%d %H:%M:%S %Z')"

exit 0
