#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# perf_nightly.sh - SGL Automated Nightly Benchmark Runner
#
# DESCRIPTION:
#   Automatically discovers, pulls, and runs benchmarks on the latest Docker images
#   for SGL models (Grok, DeepSeek) with optional Teams notifications.
#   Optimized for efficiency with streamlined API calls and minimal dependencies.
#
# IMAGE DISCOVERY:
#   • Uses Docker Hub API with pagination to find images
#   • Searches for non-SRT images from today, then yesterday
#   • Supports mi30x (rocm700) and mi35x (rocm700) hardware variants
#   • Examples:
#     - rocm/sgl-dev:v0.5.5-rocm700-mi30x-20251110
#     - rocm/sgl-dev:v0.5.5-rocm700-mi35x-20251110
#   • Automatically excludes SRT variants (ends with -srt)
#
# MODEL DOWNLOAD:
#   • Supports automatic model download from HuggingFace Hub
#   • Downloads to <work-dir>/models/<model_name> when --work-dir is specified
#   • Otherwise downloads to WORK_DIR/models/<model_name> inside container
#   • Uses --download-model option (no default - must be explicitly specified)
#   • Requires huggingface_hub library (automatically installed in container)
#   • Only downloads when no custom --model-path is provided
#   • DISK SPACE REQUIREMENTS: DeepSeek models need 685GB, Grok models need 200GB
#   • Automatically checks available disk space before downloading
#   • Supports download resumption if interrupted (using huggingface-cli)
#
# TEAMS NOTIFICATIONS & PLOT HOSTING:
#   • Teams notifications require --teams-webhook-url
#   • Plot images are automatically uploaded to GitHub when GITHUB_REPO and GITHUB_TOKEN
#     environment variables are set (plots appear as GitHub URLs in Teams)
#   • If GitHub credentials are not set, falls back to local plot server URLs
#   • Set GITHUB_REPO=owner/repo and GITHUB_TOKEN=ghp_xxx for auto-upload
#
# USAGE:
#   perf_nightly.sh [OPTIONS]
#
# OPTIONS:
#   --model=MODEL        Model to benchmark: grok, grok2, deepseek, DeepSeek-V3, sanity [default: grok]
#   --model-path=PATH    Custom model path (overrides default model path)
#   --tokenizer-path=PATH Custom tokenizer path (for grok2, overrides default tokenizer)
#   --work-dir=PATH      Custom work directory (overrides default work directory)
#   --mode=MODE          Benchmark mode: online, offline, all, sanity [default: all]
#   --hardware=HW        Hardware type: mi30x, mi35x [default: mi35x]
#   --download-model=REPO  Download model from HuggingFace if not exists (no default)
#   --continue-run-days=N    Run benchmarks for last N days' images [default: 1]
#   --teams-webhook-url=URL  Enable Teams notifications with webhook URL
#   --teams-skip-analysis    Skip GSM8K accuracy and performance analysis
#   --teams-analysis-days=N  Days to look back for performance comparison [default: 7]
#   --check-dp-attention     Enable DP attention mode error checking (for DeepSeek)
#   --sanity-trials=N        Number of trials per model for sanity check [default: 1]
#   --help, -h           Show detailed help message
#
# EXAMPLES:
#   perf_nightly.sh                                   # Grok online+offline (default: mi35x)
#   perf_nightly.sh --hardware=mi30x                  # Grok online+offline (mi30x)
#   perf_nightly.sh --model=deepseek --mode=online     # DeepSeek online only (mi35x)
#   perf_nightly.sh --model=DeepSeek-V3 --mode=online  # DeepSeek-V3 online only (mi35x)
#   perf_nightly.sh --model=grok2 --mode=online        # Grok 2 online only (mi35x)
#   perf_nightly.sh --model=grok2 --model-path=/path/to/grok2 \   # Grok 2 with custom paths
#     --tokenizer-path=/path/to/tokenizer --mode=online
#   perf_nightly.sh --hardware=mi35x --mode=all        # Grok on mi35x hardware
#   perf_nightly.sh --model=grok --mode=all \          # Grok with Teams alerts
#     --teams-webhook-url="https://prod-99.westus.logic.azure.com/..."
#   perf_nightly.sh --model-path=/data/models/custom-deepseek  # Custom model path
#   perf_nightly.sh --work-dir=/tmp/benchmark-workspace       # Custom work directory
#   perf_nightly.sh --model=DeepSeek-V3 \                     # Combined custom paths
#     --model-path=/raid/deepseek-ai/DeepSeek-V3 --work-dir=/home/user/workspace
#   perf_nightly.sh --continue-run-days=7 --model=grok        # Run last 7 days' images
#   perf_nightly.sh --mode=sanity --hardware=mi30x --sanity-trials=2  # Sanity check all models
#   perf_nightly.sh --model=sanity --mode=sanity --hardware=mi35x     # Sanity check (alternative syntax)
# ---------------------------------------------------------------------------
set -euo pipefail

# Set timezone to PST/PDT for consistent logging
export TZ='America/Los_Angeles'

###############################################################################
# Command and execution logging
###############################################################################
echo "[nightly] =========================================="
echo "[nightly] SGL Nightly Benchmark Started"
echo "[nightly] =========================================="
echo "[nightly] Command: $0 $*"
echo "[nightly] Start time: $(date '+%Y-%m-%d %H:%M:%S %Z')"
echo "[nightly] Machine: $(hostname)"
echo "[nightly] Working directory: $(pwd)"
echo "[nightly] Script location: $(realpath "$0")"
echo "[nightly] Process ID: $$"

echo "[nightly] =========================================="
echo ""

###############################################################################
# Configuration Variables - Override via environment variables if needed
###############################################################################

# Base paths and directories - default to current working directory if not set
BENCHMARK_CI_DIR="${BENCHMARK_CI_DIR:-$(pwd)}"
MOUNT_DIR="${MOUNT_DIR:-/mnt/raid/}"
WORK_DIR="${WORK_DIR:-/sgl-workspace}"

# Docker configuration
IMAGE_REPO="${IMAGE_REPO:-rocm/sgl-dev}"  # Both models use same repo
CONTAINER_SHM_SIZE="${CONTAINER_SHM_SIZE:-32g}"

# Hardware configuration
HARDWARE_TYPE="${HARDWARE_TYPE:-mi35x}"  # Default to mi35x, can be mi30x or mi35x

# ROCM version mapping based on hardware
declare -A ROCM_VERSIONS=(
  ["mi30x"]="rocm700"
  ["mi35x"]="rocm700"
)

# Fallback ROCM versions if primary version not available
declare -A ROCM_FALLBACK_VERSIONS=(
  ["mi30x"]="rocm630"
  ["mi35x"]=""  # No fallback for mi35x
)

# Model configuration - will be set based on --model parameter
GROK_MODEL_NAME="${GROK_MODEL_NAME:-GROK1}"
GROK_MODEL_VARIANT="${GROK_MODEL_VARIANT:-MOE-I4F8}"
GROK2_MODEL_NAME="${GROK2_MODEL_NAME:-GROK2}"
GROK2_MODEL_VARIANT="${GROK2_MODEL_VARIANT:-FP8}"
DEEPSEEK_MODEL_NAME="${DEEPSEEK_MODEL_NAME:-DeepSeek-V3-0324}"
DEEPSEEK_MODEL_VARIANT="${DEEPSEEK_MODEL_VARIANT:-FP8}"

# HuggingFace model download configuration
# Note: No default model repository - downloads only when --download-model is explicitly specified

# GPU monitoring thresholds
GPU_USAGE_THRESHOLD="${GPU_USAGE_THRESHOLD:-15}"
VRAM_USAGE_THRESHOLD="${VRAM_USAGE_THRESHOLD:-15}"
GPU_IDLE_WAIT_TIME="${GPU_IDLE_WAIT_TIME:-15}"

# Timezone for date calculations (San Francisco time)
TIME_ZONE="${TIME_ZONE:-America/Los_Angeles}"

# Script paths - will be set after CLI parameter processing to use custom work directory if provided

# Teams configuration (disabled by default - requires explicit webhook URL)
TEAMS_WEBHOOK_URL="${TEAMS_WEBHOOK_URL:-}"  # Empty by default - set via --teams-webhook-url or environment
ENABLE_TEAMS_NOTIFICATIONS="${ENABLE_TEAMS_NOTIFICATIONS:-true}"  # Enabled if webhook URL is provided
TEAMS_NO_IMAGES="${TEAMS_NO_IMAGES:-false}"  # Set to true for text-only notifications (when plot server is not public)
TEAMS_SKIP_ANALYSIS="${TEAMS_SKIP_ANALYSIS:-false}"  # Set to true to skip GSM8K accuracy and performance analysis
TEAMS_ANALYSIS_DAYS="${TEAMS_ANALYSIS_DAYS:-7}"  # Number of days to look back for performance comparison
# Plot server configuration (only used when GITHUB_REPO/GITHUB_TOKEN are not set for auto-upload)
PLOT_SERVER_HOST="${PLOT_SERVER_HOST:-}"
PLOT_SERVER_PORT="${PLOT_SERVER_PORT:-8000}"
PLOT_SERVER_BASE_URL="${PLOT_SERVER_BASE_URL:-}"

# Output directories
OFFLINE_OUTPUT_DIR="${OFFLINE_OUTPUT_DIR:-${BENCHMARK_CI_DIR}/offline}"
ONLINE_OUTPUT_DIR="${ONLINE_OUTPUT_DIR:-${BENCHMARK_CI_DIR}/online}"

###############################################################################
# A. GPU idle check function
###############################################################################
check_gpu_idle() {
  if ! command -v rocm-smi &> /dev/null; then
    echo "[nightly] WARN: rocm-smi not found. Skipping GPU idle check."
    return 0
  fi
  # This awk script checks the last two columns, typically GPU% and VRAM%, for any non-zero usage.
  if rocm-smi | awk -v gpu_thresh="$GPU_USAGE_THRESHOLD" -v vram_thresh="$VRAM_USAGE_THRESHOLD" '
    NR > 2 && NF >= 2 {
      gpu_usage=gensub(/%/, "", "g", $(NF-1));
      vram_usage=gensub(/%/, "", "g", $NF);
      if (gpu_usage > gpu_thresh || vram_usage > vram_thresh) {
        exit 1;
      }
    }'; then
    return 0  # idle
  else
    return 1  # busy
  fi
}

ensure_gpu_idle() {
  if ! check_gpu_idle; then
    echo "[nightly] GPU is busy. Attempting to stop running Docker containers..."
    # Stop all running containers, ignoring errors if some are already stopped.
    if "${DOCKER_CMD[@]}" info >/dev/null 2>&1; then
      running_ids="$("${DOCKER_CMD[@]}" ps -q 2>/dev/null || true)"
      if [[ -n "$running_ids" ]]; then
        echo "[nightly] Stopping running containers: $(echo "$running_ids" | tr '\\n' ' ')"
        "${DOCKER_CMD[@]}" stop $running_ids >/dev/null 2>&1 || true
      else
        echo "[nightly] No running containers to stop."
      fi
    else
      echo "[nightly] WARN: Docker not accessible; skipping container stop."
      echo "[nightly] Docker error details:"
      "${DOCKER_CMD[@]}" info 2>&1 | sed 's/^/[nightly]   /' || true
    fi
    echo "[nightly] Waiting ${GPU_IDLE_WAIT_TIME}s for GPU to become idle..."
    sleep "$GPU_IDLE_WAIT_TIME"
  fi

  if check_gpu_idle; then
      echo "[nightly] GPU is idle. Proceeding..."
  else
      echo "[nightly] WARN: GPU may still be busy, but proceeding as requested."
  fi
}

###############################################################################
# Disk space check function
###############################################################################
check_disk_space() {
  local target_dir="$1"
  local required_gb="${2:-50}"  # Default 50GB minimum

  # Get the parent directory for space check (in case target doesn't exist yet)
  local check_dir="$target_dir"
  while [[ ! -d "$check_dir" ]]; do
    check_dir="$(dirname "$check_dir")"
  done

  echo "[nightly] Checking available disk space at: ${check_dir}"

  # Get available space in GB using df
  local available_space_gb
  if command -v df &> /dev/null; then
    # Use df to get available space in 1K blocks, then convert to GB
    available_space_gb=$(df -P "$check_dir" | awk 'NR==2 {printf "%.1f", $4/1024/1024}')
    echo "[nightly] Available disk space: ${available_space_gb}GB"

    # Compare using awk for floating point comparison
    if awk "BEGIN {exit !($available_space_gb >= $required_gb)}"; then
      echo "[nightly] Sufficient disk space available (${available_space_gb}GB >= ${required_gb}GB)"
      return 0
    else
      echo "[nightly] ERROR: Insufficient disk space! Available: ${available_space_gb}GB, Required: ${required_gb}GB"
      return 1
    fi
  else
    echo "[nightly] WARN: df command not available. Skipping disk space check."
    return 0
  fi
}

###############################################################################
# Function to extract date from Docker image tag
###############################################################################
extract_date_from_tag() {
  local tag="$1"
  # Extract date from tag format like "v0.5.3rc0-rocm700-mi30x-20250922"
  # Use regex to find 8-digit date pattern (YYYYMMDD) at the end
  if [[ "$tag" =~ -([0-9]{8})$ ]]; then
    echo "${BASH_REMATCH[1]}"
    return 0
  else
    echo "[nightly] WARN: Could not extract date from tag: $tag" >&2
    return 1
  fi
}

###############################################################################
# HuggingFace model download function
###############################################################################
download_hf_model() {
  local hf_repo="$1"
  local target_dir="$2"
  local model_type="${3:-unknown}"  # Optional model type parameter

  echo "[nightly] Downloading model from HuggingFace: ${hf_repo}"
  echo "[nightly] Target directory: ${target_dir}"

  # Check if target directory already exists and has content
  if [[ -d "$target_dir" && -n "$(ls -A "$target_dir" 2>/dev/null)" ]]; then
    echo "[nightly] Model directory already exists and is not empty: ${target_dir}"
    return 0
  fi

  # Determine required disk space based on model type
  local required_space_gb=100  # Default for unknown models
  case "$model_type" in
    "deepseek")
      required_space_gb=685
      echo "[nightly] DeepSeek model detected - requiring ${required_space_gb}GB disk space"
      ;;
    "grok"|"grok2")
      required_space_gb=200  # Conservative estimate for Grok models
      echo "[nightly] Grok model detected - requiring ${required_space_gb}GB disk space"
      ;;
    *)
      # For unknown models or HF repos containing "deepseek", assume DeepSeek requirements
      if [[ "$hf_repo" == *"deepseek"* ]] || [[ "$hf_repo" == *"DeepSeek"* ]]; then
        required_space_gb=685
        echo "[nightly] DeepSeek model detected from repo name - requiring ${required_space_gb}GB disk space"
      else
        echo "[nightly] Unknown model type - requiring ${required_space_gb}GB disk space (default)"
      fi
      ;;
  esac

  # Check available disk space before downloading
  if ! check_disk_space "$target_dir" "$required_space_gb"; then
    echo "[nightly] ERROR: Insufficient disk space for ${model_type} model download (requires ${required_space_gb}GB)"
    return 1
  fi

  # Create target directory if it doesn't exist
  mkdir -p "$target_dir"

  # Download model using huggingface-cli with resume capability
  echo "[nightly] Installing huggingface_hub and downloading model with resume support..."
  DOWNLOAD_EXIT_CODE=0

  "${DOCKER_CMD[@]}" exec \
    -e INSIDE_CONTAINER=1 \
    "${CONTAINER_NAME}" \
    bash -c "pip install huggingface_hub > /dev/null 2>&1 && \
             echo '[download] Starting download of ${hf_repo}...' && \
             HF_HUB_ENABLE_HF_TRANSFER=1 huggingface-cli download '${hf_repo}' \
               --local-dir '${target_dir}' \
               --local-dir-use-symlinks False \
               --resume-download && \
             echo '[download] Model download completed successfully'" || DOWNLOAD_EXIT_CODE=$?

  if [ $DOWNLOAD_EXIT_CODE -eq 0 ]; then
    echo "[nightly] Model downloaded successfully to: ${target_dir}"
    return 0
  else
    echo "[nightly] ERROR: Failed to download model from HuggingFace (exit code: $DOWNLOAD_EXIT_CODE)"
    return 1
  fi
}

###############################################################################
# Teams error notification function
###############################################################################
send_error_notification() {
  local error_type="$1"
  local error_message="$2"
  local model="${3:-unknown}"
  local mode="${4:-unknown}"

  if [[ "$TEAMS_WEBHOOK_FROM_CLI" != "true" || -z "$TEAMS_WEBHOOK_URL" ]]; then
    echo "[nightly] Error notification skipped - no webhook URL configured"
    return 0
  fi

  echo "[nightly] Sending error notification to Teams..."

  # Create a simple error payload
  local timestamp=$(date -u '+%Y-%m-%dT%H:%M:%SZ')
  local hostname=$(hostname)

  # Build JSON payload
  local payload=$(cat <<EOF
{
  "@type": "MessageCard",
  "@context": "http://schema.org/extensions",
  "themeColor": "FF0000",
  "summary": "❌ Nightly Benchmark Error: ${error_type}",
  "sections": [{
    "activityTitle": "❌ Nightly Benchmark Error",
    "activitySubtitle": "${error_type}",
    "facts": [
      {"name": "Error Type", "value": "${error_type}"},
      {"name": "Model", "value": "${model}"},
      {"name": "Mode", "value": "${mode}"},
      {"name": "Machine", "value": "${hostname}"},
      {"name": "Hardware", "value": "${HARDWARE_TYPE}"},
      {"name": "Timestamp", "value": "${timestamp}"},
      {"name": "Details", "value": "${error_message}"}
    ],
    "markdown": true
  }]
}
EOF
)

  # Send notification using curl (more reliable than docker exec when container is having issues)
  curl -H "Content-Type: application/json" \
       -d "${payload}" \
       "${TEAMS_WEBHOOK_URL}" \
       -s -o /dev/null -w "%{http_code}" > /tmp/teams_error_response.txt 2>&1

  local http_code=$(cat /tmp/teams_error_response.txt 2>/dev/null || echo "000")
  if [[ "$http_code" == "200" ]]; then
    echo "[nightly] Error notification sent successfully (HTTP ${http_code})"
  else
    echo "[nightly] WARN: Error notification failed (HTTP ${http_code})"
  fi
  rm -f /tmp/teams_error_response.txt
}

###############################################################################
# Teams notification function
###############################################################################
send_teams_notification() {
  local model="$1"
  local mode="$2"

  if [[ "$TEAMS_WEBHOOK_FROM_CLI" != "true" ]]; then
    echo "[nightly] Teams notifications skipped - --teams-webhook-url not provided"
    return 0
  fi

  # Check if Teams notifications are enabled
  if [[ "$ENABLE_TEAMS_NOTIFICATIONS" != "true" ]]; then
    echo "[nightly] Teams notifications disabled (ENABLE_TEAMS_NOTIFICATIONS != true)"
    return 0
  fi

  # Check if webhook URL is configured
  if [[ -z "$TEAMS_WEBHOOK_URL" ]]; then
    echo "[nightly] Teams notifications disabled - no webhook URL configured"
    echo "[nightly] To enable: use --teams-webhook-url=URL or set TEAMS_WEBHOOK_URL environment variable"
    echo "[nightly] See team_alert/README.md for setup instructions"
    return 0
  fi

  # Check if notification script exists
  if [[ ! -f "$TEAMS_NOTIFICATION_SCRIPT" ]]; then
    echo "[nightly] WARN: Teams notification script not found: $TEAMS_NOTIFICATION_SCRIPT"
    return 0
  fi

  echo "[nightly] Sending Teams notification for ${model} ${mode} plots..."

  # Execute Teams notification inside the container
  TEAMS_EXIT_CODE=0

  # Build the command with directory parameters to match where plots are actually created
  PLOT_DIR_PATH="${BENCHMARK_CI_DIR}/plots_server"
  TEAMS_CMD="python3 -c 'import requests, pytz' 2>/dev/null || pip install requests pytz > /dev/null 2>&1; python3 '${TEAMS_NOTIFICATION_SCRIPT}' --model '${model}' --mode '${mode}' --plot-dir '${PLOT_DIR_PATH}' --benchmark-dir '${BENCHMARK_CI_DIR}' --hardware '${HARDWARE_TYPE}'"

  # Add benchmark date if available (extracted from SELECTED_TAG)
  if [[ -n "${SELECTED_TAG:-}" ]]; then
    BENCHMARK_DATE=$(extract_date_from_tag "${SELECTED_TAG}")
    if [[ $? -eq 0 && -n "$BENCHMARK_DATE" ]]; then
      TEAMS_CMD="${TEAMS_CMD} --benchmark-date '${BENCHMARK_DATE}'"
      echo "[nightly] Adding benchmark date to Teams notification: ${BENCHMARK_DATE}"
    fi
  fi

  echo "[nightly] Teams notification using plot directory: ${PLOT_DIR_PATH}"

  # Add GitHub upload if configured
  USE_GITHUB_UPLOAD=false
  if [[ "$TEAMS_NO_IMAGES" == "true" ]]; then
    echo "[nightly] Using text-only mode (no embedded images) - no upload flags added"
  elif [[ -n "${GITHUB_REPO:-}" && -n "${GITHUB_TOKEN:-}" ]]; then
    TEAMS_CMD="${TEAMS_CMD} --github-upload --github-repo '${GITHUB_REPO}' --github-token '${GITHUB_TOKEN}'"
    USE_GITHUB_UPLOAD=true
    echo "[nightly] Using GitHub upload for plot images (repo: ${GITHUB_REPO})"
    echo "[nightly] Images will be stored in main branch with structure: plot/${HARDWARE_TYPE}/model/mode/filename.png"
  else
    echo "[nightly] GitHub credentials not provided via environment - using plot server links only"
    echo "[nightly] To enable GitHub upload, set GITHUB_REPO and GITHUB_TOKEN environment variables"
  fi

  # Add analysis parameters
  if [[ "$TEAMS_SKIP_ANALYSIS" == "true" ]]; then
    TEAMS_CMD="${TEAMS_CMD} --skip-analysis"
    echo "[nightly] Skipping GSM8K accuracy and performance analysis"
  else
    TEAMS_CMD="${TEAMS_CMD} --analysis-days ${TEAMS_ANALYSIS_DAYS}"
    echo "[nightly] Including intelligent analysis (${TEAMS_ANALYSIS_DAYS} days lookback)"
  fi

  # Add DP attention flag for DeepSeek online mode
  if [[ "$CHECK_DP_ATTENTION" == "true" && "$model" == "deepseek" && "$mode" == "online" ]]; then
    TEAMS_CMD="${TEAMS_CMD} --check-dp-attention"
    echo "[nightly] Adding --check-dp-attention flag for Teams notification"
  fi

  # Add torch compile flag for DeepSeek online mode
  if [[ "$ENABLE_TORCH_COMPILE" == "true" && "$model" == "deepseek" && "$mode" == "online" ]]; then
    TEAMS_CMD="${TEAMS_CMD} --enable-torch-compile"
    echo "[nightly] Adding --enable-torch-compile flag for Teams notification"
  fi

  # Add DP test flag for DeepSeek online mode
  if [[ "$ENABLE_DP_TEST" == "true" && "$model" == "deepseek" && "$mode" == "online" ]]; then
    TEAMS_CMD="${TEAMS_CMD} --enable-dp-test"
    echo "[nightly] Adding --enable-dp-test flag for Teams notification"
  fi

  # Add MTP test flag for DeepSeek online mode
  if [[ "$ENABLE_MTP_TEST" == "true" && "$model" == "deepseek" && "$mode" == "online" ]]; then
    TEAMS_CMD="${TEAMS_CMD} --enable-mtp-test"
    echo "[nightly] Adding --enable-mtp-test flag for Teams notification"
  fi

  # Build docker exec command with appropriate environment variables
  # When using GitHub upload, don't set PLOT_SERVER_* to avoid fallback to local URLs
  if [[ "$USE_GITHUB_UPLOAD" == "true" ]]; then
    "${DOCKER_CMD[@]}" exec \
      -e INSIDE_CONTAINER=1 \
      -e TEAMS_WEBHOOK_URL="${TEAMS_WEBHOOK_URL}" \
      -e TEAMS_NO_IMAGES="${TEAMS_NO_IMAGES}" \
      -e GITHUB_REPO="${GITHUB_REPO:-}" \
      -e GITHUB_TOKEN="${GITHUB_TOKEN:-}" \
      "${CONTAINER_NAME}" \
      bash -c "${TEAMS_CMD}" || TEAMS_EXIT_CODE=$?
  else
    "${DOCKER_CMD[@]}" exec \
      -e INSIDE_CONTAINER=1 \
      -e TEAMS_WEBHOOK_URL="${TEAMS_WEBHOOK_URL}" \
      -e TEAMS_NO_IMAGES="${TEAMS_NO_IMAGES}" \
      -e PLOT_SERVER_HOST="${PLOT_SERVER_HOST}" \
      -e PLOT_SERVER_PORT="${PLOT_SERVER_PORT}" \
      -e PLOT_SERVER_BASE_URL="${PLOT_SERVER_BASE_URL}" \
      "${CONTAINER_NAME}" \
      bash -c "${TEAMS_CMD}" || TEAMS_EXIT_CODE=$?
  fi

  if [ $TEAMS_EXIT_CODE -eq 0 ]; then
    echo "[nightly] Teams notification sent successfully for ${model} ${mode}"
  else
    echo "[nightly] WARN: Teams notification failed for ${model} ${mode} (exit code: $TEAMS_EXIT_CODE)"
  fi
}

###############################################################################
# 0. Environment validation and CLI flags
###############################################################################

# Basic environment validation for cron issues
echo "[nightly] Runtime Environment:"
echo "[nightly] User: $(whoami) | Groups: $(id -nG | cut -d' ' -f1-3)... | Docker: $(which docker 2>/dev/null || echo 'not in PATH')"

# Validate Docker access early
DOCKER_CMD=(sudo /usr/bin/docker)  # Use absolute path for cron compatibility
if [[ ! -x "/usr/bin/docker" ]]; then
    echo "[nightly] ERROR: Docker executable not found at /usr/bin/docker"
    exit 1
fi

echo "[nightly] Testing Docker daemon access..."
if ! "${DOCKER_CMD[@]}" info >/dev/null 2>&1; then
    echo "[nightly] ERROR: Cannot access Docker daemon. Please check:"
    echo "[nightly]   1. Docker daemon is running"
    echo "[nightly]   2. Current user is in 'docker' group: sudo usermod -aG docker \$(whoami)"
    echo "[nightly]   3. If running via cron, ensure cron environment has docker group access"
    echo "[nightly] Docker info output:"
    "${DOCKER_CMD[@]}" info 2>&1 | sed 's/^/[nightly]   /'
    exit 1
fi
echo "[nightly] Docker daemon accessible - proceeding..."

###############################################################################
# 1. Parse CLI flags
###############################################################################
MODE="all" # Default to run both offline and online
MODEL="grok" # Default to grok
CLI_TEAMS_WEBHOOK_URL="" # Teams webhook URL from command line
TEAMS_WEBHOOK_FROM_CLI="false" # Track whether webhook flag was provided on CLI
CLI_TEAMS_SKIP_ANALYSIS="" # Skip analysis flag from command line
CLI_CHECK_DP_ATTENTION="" # DP attention mode flag from command line
CLI_ENABLE_TORCH_COMPILE="" # Torch compile mode flag from command line
CLI_ENABLE_MTP_TEST="" # MTP test mode flag from command line
CLI_ENABLE_DP_TEST="" # DP test mode flag from command line
CLI_WORK_DIR="" # Custom work directory from command line
CLI_MODEL_PATH="" # Custom model path from command line
CLI_MODEL_NAME="" # Custom model name from command line
CLI_TOKENIZER_PATH="" # Custom tokenizer path from command line
CLI_DOWNLOAD_MODEL="" # HuggingFace model repository to download from command line
CLI_CONTINUE_RUN_DAYS="" # Number of days to run benchmarks for from command line
CLI_SANITY_TRIALS="" # Number of trials per model for sanity check from command line

for arg in "$@"; do
  case $arg in
    --mode=*)
      MODE="${arg#*=}"
      ;;
    --model=*)
      MODEL="${arg#*=}"
      ;;
    --model-path=*)
      CLI_MODEL_PATH="${arg#*=}"
      ;;
    --model-name=*)
      CLI_MODEL_NAME="${arg#*=}"
      ;;
    --tokenizer-path=*)
      CLI_TOKENIZER_PATH="${arg#*=}"
      ;;
    --work-dir=*)
      CLI_WORK_DIR="${arg#*=}"
      ;;
    --hardware=*)
      HARDWARE_TYPE="${arg#*=}"
      ;;
    --download-model=*)
      CLI_DOWNLOAD_MODEL="${arg#*=}"
      ;;
    --continue-run-days=*)
      CLI_CONTINUE_RUN_DAYS="${arg#*=}"
      ;;
    --teams-webhook-url=*)
      CLI_TEAMS_WEBHOOK_URL="${arg#*=}"
      TEAMS_WEBHOOK_FROM_CLI="true"
      ;;
    --teams-skip-analysis)
      CLI_TEAMS_SKIP_ANALYSIS="true"
      ;;
    --teams-analysis-days=*)
      TEAMS_ANALYSIS_DAYS="${arg#*=}"
      ;;
    --check-dp-attention)
      CLI_CHECK_DP_ATTENTION="true"
      ;;
    --enable-torch-compile)
      CLI_ENABLE_TORCH_COMPILE="true"
      ;;
    --enable-mtp-test)
      CLI_ENABLE_MTP_TEST="true"
      ;;
    --enable-dp-test)
      CLI_ENABLE_DP_TEST="true"
      ;;
    --sanity-trials=*)
      CLI_SANITY_TRIALS="${arg#*=}"
      ;;
    --models-dir=*)
      CLI_MODELS_DIR="${arg#*=}"
      ;;
    --help|-h)
      echo "Usage: $0 [OPTIONS]"
      echo ""
      echo "Run SGL nightly benchmarks with optional Teams notifications"
      echo ""
      echo "Options:"
      echo "  --model=MODEL                    Model to benchmark (grok, grok2, deepseek, DeepSeek-V3, sanity) [default: grok]"
      echo "  --model-path=PATH                Custom model path (overrides default model path)"
      echo "  --model-name=NAME                Custom model name used for output directories"
      echo "  --tokenizer-path=PATH            Custom tokenizer path (for grok2, overrides default tokenizer path)"
      echo "  --work-dir=PATH                  Custom work directory (overrides default work directory)"
      echo "  --mode=MODE                      Benchmark mode (online, offline, all, sanity) [default: all]"
      echo "  --hardware=HW                    Hardware type (mi30x, mi35x) [default: mi30x]"
      echo "  --download-model=REPO            Download model from HuggingFace if not exists (no default)"
      echo "  --continue-run-days=DAYS         Run benchmarks for last N days' images [default: 1]"
      echo "  --teams-webhook-url=URL          Teams webhook URL to enable notifications [default: disabled]"
      echo "  --teams-skip-analysis            Skip GSM8K accuracy and performance regression analysis"
      echo "  --teams-analysis-days=DAYS       Days to look back for performance comparison [default: 7]"
      echo "  --check-dp-attention             Enable DP attention mode error checking (for DeepSeek)"
      echo "  --enable-torch-compile           Enable torch compile optimization (for DeepSeek)"
      echo "  --enable-mtp-test                Enable DeepSeek MTP throughput export (nightly online)"
      echo "  --enable-dp-test                 Enable DeepSeek DP throughput test (nightly online)"
      echo "  --sanity-trials=N                Number of trials per model for sanity check [default: 1]"
      echo "  --models-dir=PATH                Models directory for sanity check [default: /data]"
      echo "  --help, -h                       Show this help message"
      echo ""
      echo "Examples:"
      echo "  $0                                          # Run grok online+offline, no Teams (mi30x)"
      echo "  $0 --model=deepseek --mode=online           # Run deepseek online only, no Teams (mi30x)"
      echo "  $0 --model=DeepSeek-V3 --mode=online        # Run DeepSeek-V3 online only, no Teams (mi30x)"
      echo "  $0 --model=grok2 --mode=online              # Run grok2 online only, no Teams (mi30x)"
      echo "  $0 --model=grok2 --model-path=/path/to/grok2 --tokenizer-path=/path/to/tokenizer  # Run grok2 with custom model and tokenizer paths"
      echo "  $0 --hardware=mi35x --mode=all              # Run grok on mi35x hardware"
      echo "  $0 --model=grok --mode=all \\                # Run grok with Teams notifications"
      echo "     --teams-webhook-url='https://prod-99.westus.logic.azure.com/...'"
      echo "  $0 --model-path=/data/models/custom-grok    # Use custom model path"
      echo "  $0 --work-dir=/tmp/benchmark-run            # Use custom work directory"
      echo "  $0 --model=deepseek --work-dir=/home/user/workspace --download-model=deepseek-ai/DeepSeek-V3  # Download to work-dir/models/"
      echo "  $0 --teams-webhook-url='...' --teams-skip-analysis  # Teams with plots only (no analysis)"
      echo "  $0 --continue-run-days=7 --model=grok               # Run last 7 days' images sequentially"
      echo "  $0 --model=deepseek --mode=online --check-dp-attention  # DeepSeek online with DP attention error checking"
      echo "  $0 --model=deepseek --mode=online --enable-torch-compile # DeepSeek online with torch compile optimization"
      echo "  $0 --model=deepseek --mode=online --check-dp-attention --enable-torch-compile # DeepSeek online with both DP attention and torch compile"
      echo ""
      echo "Disk Space Requirements:"
      echo "  DeepSeek models: 685GB minimum free space required"
      echo "  Grok models:     200GB minimum free space required"
      echo "  Downloads support automatic resumption if interrupted"
      echo ""
      echo "Teams Integration:"
      echo "  Teams notifications are DISABLED by default."
      echo "  Use --teams-webhook-url to enable notifications."
      echo "  Analysis includes GSM8K accuracy checks and performance regression detection."
      echo "  Get webhook URL from Teams Power Automate or Incoming Webhook connector."
      echo "  See team_alert/README.md for detailed setup instructions."
      exit 0 ;;
    *)
      echo "Unknown argument: $arg"
      echo "Use --help for usage information"
      exit 1 ;;
  esac
done

# Override Teams settings if provided via command line.  Only enable
# notifications when the flag is explicitly passed with a non-empty value.
if [[ "$TEAMS_WEBHOOK_FROM_CLI" == "true" ]]; then
  if [[ -n "$CLI_TEAMS_WEBHOOK_URL" ]]; then
    TEAMS_WEBHOOK_URL="$CLI_TEAMS_WEBHOOK_URL"
    ENABLE_TEAMS_NOTIFICATIONS="true"
    echo "[nightly] Teams webhook URL provided via command line - notifications enabled"
  else
    echo "[nightly] WARN: --teams-webhook-url was provided without a value - notifications disabled"
    TEAMS_WEBHOOK_FROM_CLI="false"
    TEAMS_WEBHOOK_URL=""
    ENABLE_TEAMS_NOTIFICATIONS="false"
  fi
else
  if [[ -n "$TEAMS_WEBHOOK_URL" ]]; then
    echo "[nightly] INFO: Ignoring Teams webhook URL from environment (require --teams-webhook-url)"
  fi
  TEAMS_WEBHOOK_URL=""
  ENABLE_TEAMS_NOTIFICATIONS="false"
fi

if [[ -n "$CLI_TEAMS_SKIP_ANALYSIS" ]]; then
  TEAMS_SKIP_ANALYSIS="$CLI_TEAMS_SKIP_ANALYSIS"
  echo "[nightly] Teams analysis disabled via command line"
fi

# Process DP attention flag
CHECK_DP_ATTENTION="false"  # Default to false
if [[ -n "$CLI_CHECK_DP_ATTENTION" ]]; then
  CHECK_DP_ATTENTION="$CLI_CHECK_DP_ATTENTION"
  echo "[nightly] DP attention mode enabled via command line"
fi

# Process torch compile flag
ENABLE_TORCH_COMPILE="false"  # Default to false
if [[ -n "$CLI_ENABLE_TORCH_COMPILE" ]]; then
  ENABLE_TORCH_COMPILE="$CLI_ENABLE_TORCH_COMPILE"
  echo "[nightly] Torch compile mode enabled via command line"
fi

# Process MTP test flag
ENABLE_MTP_TEST="false"  # Default to false
if [[ -n "$CLI_ENABLE_MTP_TEST" ]]; then
  ENABLE_MTP_TEST="$CLI_ENABLE_MTP_TEST"
  echo "[nightly] DeepSeek MTP test enabled via command line"
fi

# Process DP test flag
ENABLE_DP_TEST="false"  # Default to false
if [[ -n "$CLI_ENABLE_DP_TEST" ]]; then
  ENABLE_DP_TEST="$CLI_ENABLE_DP_TEST"
  echo "[nightly] DeepSeek DP test enabled via command line"
  if [[ "$CHECK_DP_ATTENTION" != "true" ]]; then
    CHECK_DP_ATTENTION="true"
    echo "[nightly] DP test implies DP attention checks"
  fi
fi

# Override work directory and model path if provided via command line
if [[ -n "$CLI_WORK_DIR" ]]; then
  BENCHMARK_CI_DIR="$CLI_WORK_DIR"
  echo "[nightly] Custom work directory provided: $BENCHMARK_CI_DIR"
fi

if [[ -n "$CLI_MODEL_PATH" ]]; then
  echo "[nightly] Custom model path provided: $CLI_MODEL_PATH"
fi

if [[ -n "$CLI_TOKENIZER_PATH" ]]; then
  echo "[nightly] Custom tokenizer path provided: $CLI_TOKENIZER_PATH"
fi

# Process HuggingFace model download option
# Only enable HuggingFace downloads when explicitly requested via --download-model
if [[ -n "$CLI_DOWNLOAD_MODEL" ]]; then
  HF_MODEL_REPO="$CLI_DOWNLOAD_MODEL"
  echo "[nightly] HuggingFace model repository provided: $HF_MODEL_REPO"
else
  HF_MODEL_REPO=""  # No default HF downloads for any model - only when explicitly requested
fi

# Process continue run days option
CONTINUE_RUN_DAYS=1  # Default to 1 day (current behavior)
if [[ -n "$CLI_CONTINUE_RUN_DAYS" ]]; then
  CONTINUE_RUN_DAYS="$CLI_CONTINUE_RUN_DAYS"
  # Validate that it's a positive integer
  if ! [[ "$CONTINUE_RUN_DAYS" =~ ^[1-9][0-9]*$ ]]; then
    echo "[nightly] ERROR: --continue-run-days must be a positive integer (got: $CONTINUE_RUN_DAYS)"
    exit 1
  fi
  echo "[nightly] Will run benchmarks for last $CONTINUE_RUN_DAYS days' images"
fi

# Process sanity trials option
SANITY_TRIALS=1  # Default to 1 trial per model
if [[ -n "$CLI_SANITY_TRIALS" ]]; then
  SANITY_TRIALS="$CLI_SANITY_TRIALS"
  # Validate that it's a positive integer
  if ! [[ "$SANITY_TRIALS" =~ ^[1-9][0-9]*$ ]]; then
    echo "[nightly] ERROR: --sanity-trials must be a positive integer (got: $SANITY_TRIALS)"
    exit 1
  fi
  echo "[nightly] Will run $SANITY_TRIALS trials per model for sanity check"
fi

# Set script paths after CLI parameter processing (so custom work directory is used)
GROK_OFFLINE_SCRIPT="${GROK_OFFLINE_SCRIPT:-${BENCHMARK_CI_DIR}/scripts/grok_perf_offline_csv.sh}"
GROK_ONLINE_SCRIPT="${GROK_ONLINE_SCRIPT:-${BENCHMARK_CI_DIR}/scripts/grok_perf_online_csv.sh}"
DEEPSEEK_OFFLINE_SCRIPT="${DEEPSEEK_OFFLINE_SCRIPT:-${BENCHMARK_CI_DIR}/scripts/deepseek_perf_offline_csv.sh}"
DEEPSEEK_ONLINE_SCRIPT="${DEEPSEEK_ONLINE_SCRIPT:-${BENCHMARK_CI_DIR}/scripts/deepseek_perf_online_csv.sh}"
SANITY_CHECK_SCRIPT="${SANITY_CHECK_SCRIPT:-${BENCHMARK_CI_DIR}/test/sanity_check.py}"

# Python scripts for processing and plotting (combined)
PROCESS_AND_GENERATE_OFFLINE_PLOTS_SCRIPT="${PROCESS_AND_GENERATE_OFFLINE_PLOTS_SCRIPT:-${BENCHMARK_CI_DIR}/scripts/process_and_generate_offline_plots.py}"
PROCESS_AND_GENERATE_ONLINE_PLOTS_SCRIPT="${PROCESS_AND_GENERATE_ONLINE_PLOTS_SCRIPT:-${BENCHMARK_CI_DIR}/scripts/process_and_generate_online_plots.py}"

# Teams notification script
TEAMS_NOTIFICATION_SCRIPT="${TEAMS_NOTIFICATION_SCRIPT:-${BENCHMARK_CI_DIR}/team_alert/send_teams_notification.py}"

# Validate model parameter
if [[ "$MODEL" != "grok" && "$MODEL" != "grok2" && "$MODEL" != "deepseek" && "$MODEL" != "DeepSeek-V3" && "$MODEL" != "sanity" ]]; then
    echo "[nightly] ERROR: Invalid --model value '$MODEL'. Must be 'grok', 'grok2', 'deepseek', 'DeepSeek-V3', or 'sanity'."
    exit 1
fi

# Validate hardware parameter
if [[ "$HARDWARE_TYPE" != "mi30x" && "$HARDWARE_TYPE" != "mi35x" ]]; then
    echo "[nightly] ERROR: Invalid --hardware value. Must be 'mi30x' or 'mi35x'."
    exit 1
fi

# Set ROCM version based on hardware type
ROCM_VERSION="${ROCM_VERSIONS[$HARDWARE_TYPE]}"

# grok2 uses rocm700 as its standard ROCM version regardless of hardware type
if [[ "$MODEL" == "grok2" ]]; then
    ROCM_VERSION="rocm700"
    echo "[nightly] Using rocm700 for grok2 on ${HARDWARE_TYPE} hardware"
fi

# sanity check uses rocm700 for mi30x (better FP8 accuracy: 0.826 vs 0.750)
if [[ "$MODE" == "sanity" && "$HARDWARE_TYPE" == "mi30x" ]]; then
    ROCM_VERSION="rocm700"
    echo "[nightly] Using rocm700 for sanity check on ${HARDWARE_TYPE} hardware (improved FP8 accuracy)"
fi

echo "[nightly] Hardware: $HARDWARE_TYPE, ROCM Version: $ROCM_VERSION"

# Set model-specific variables
case "$MODEL" in
    "grok")
        MODEL_NAME="$GROK_MODEL_NAME"
        MODEL_VARIANT="$GROK_MODEL_VARIANT"
        OFFLINE_SCRIPT="$GROK_OFFLINE_SCRIPT"
        ONLINE_SCRIPT="$GROK_ONLINE_SCRIPT"
        ;;
    "grok2")
        MODEL_NAME="$GROK2_MODEL_NAME"
        MODEL_VARIANT="$GROK2_MODEL_VARIANT"
        OFFLINE_SCRIPT="$GROK_OFFLINE_SCRIPT"
        ONLINE_SCRIPT="$GROK_ONLINE_SCRIPT"
        ;;
    "deepseek"|"DeepSeek-V3")
        MODEL_NAME="$DEEPSEEK_MODEL_NAME"
        MODEL_VARIANT="$DEEPSEEK_MODEL_VARIANT"
        OFFLINE_SCRIPT="$DEEPSEEK_OFFLINE_SCRIPT"
        ONLINE_SCRIPT="$DEEPSEEK_ONLINE_SCRIPT"
        ;;
    "sanity")
        MODEL_NAME="SANITY"
        MODEL_VARIANT="CHECK"
        OFFLINE_SCRIPT=""  # Not applicable for sanity check
        ONLINE_SCRIPT=""   # Not applicable for sanity check
        ;;
    *)
        echo "[nightly] ERROR: Invalid model '$MODEL'. Must be 'grok', 'grok2', 'deepseek', 'DeepSeek-V3', or 'sanity'."
        exit 1
        ;;
esac

# Override model name when provided explicitly
if [[ -n "$CLI_MODEL_NAME" ]]; then
    MODEL_NAME="$CLI_MODEL_NAME"
    echo "[nightly] Custom model name provided: $MODEL_NAME"
fi

# Ensure DeepSeek output directory exists for online benchmarks
if [[ "$MODEL" == "deepseek" || "$MODEL" == "DeepSeek-V3" ]]; then
    MODEL_ONLINE_DIR="${BENCHMARK_CI_DIR}/online/${MODEL_NAME}"
    if [[ ! -d "$MODEL_ONLINE_DIR" ]]; then
        if mkdir -p "$MODEL_ONLINE_DIR" 2>/dev/null; then
            echo "[nightly] Created DeepSeek online output directory: $MODEL_ONLINE_DIR"
        else
            if command -v sudo >/dev/null 2>&1 && sudo mkdir -p "$MODEL_ONLINE_DIR" 2>/dev/null; then
                # Match ownership with current user so subsequent writes succeed
                sudo chown "$(id -u)":"$(id -g)" "$MODEL_ONLINE_DIR" 2>/dev/null || true
                echo "[nightly] Created DeepSeek online output directory with sudo: $MODEL_ONLINE_DIR"
            else
                echo "[nightly] WARN: Unable to create DeepSeek online directory ($MODEL_ONLINE_DIR); proceeding without host directory"
            fi
        fi
    fi
fi

# Determine modes to run based on user input
MODES_TO_RUN=""
if [[ "$MODE" == "all" || "$MODE" == "" ]]; then
    if [[ "$MODEL" == "sanity" ]]; then
        MODES_TO_RUN="sanity"
    else
        MODES_TO_RUN="online offline"
    fi
elif [[ "$MODE" == "offline" ]]; then
    MODES_TO_RUN="offline"
elif [[ "$MODE" == "online" ]]; then
    MODES_TO_RUN="online"
elif [[ "$MODE" == "sanity" ]]; then
    MODES_TO_RUN="sanity"
else
    echo "[nightly] ERROR: Invalid --mode value. Must be 'offline', 'online', 'all', or 'sanity'."
    exit 1
fi

# Override model to sanity if mode is sanity (for convenience)
if [[ "$MODE" == "sanity" && "$MODEL" != "sanity" ]]; then
    echo "[nightly] Mode is 'sanity', setting model to 'sanity' as well"
    MODEL="sanity"
    MODEL_NAME="SANITY"
    MODEL_VARIANT="CHECK"
fi

echo "[nightly] Model: $MODEL, Mode(s): $MODES_TO_RUN"

###############################################################################
# Lock file management - Use separate lock files per model to prevent blocking
###############################################################################
# Use separate lock files for different modes to prevent unnecessary blocking
if [ "$MODE" = "sanity" ]; then
    LOCKFILE="/tmp/perf_nightly_sanity.lock"
else
    # Build lock file name with test-specific suffix to prevent conflicts
    LOCK_SUFFIX=""
    if [[ "$ENABLE_DP_TEST" == "true" && "$ENABLE_MTP_TEST" == "true" ]]; then
        LOCK_SUFFIX="_dp_mtp"
    elif [[ "$ENABLE_DP_TEST" == "true" ]]; then
        LOCK_SUFFIX="_dp"
    elif [[ "$ENABLE_MTP_TEST" == "true" ]]; then
        LOCK_SUFFIX="_mtp"
    fi
    LOCKFILE="/tmp/perf_nightly_${MODEL}${LOCK_SUFFIX}.lock"
fi

if [ -f "$LOCKFILE" ]; then
    EXISTING_PID=$(cat "$LOCKFILE" 2>/dev/null)
    if [ -n "$EXISTING_PID" ] && kill -0 "$EXISTING_PID" 2>/dev/null; then
        echo "[nightly] ERROR: Another instance is already running (PID: $EXISTING_PID)"
        echo "[nightly] Lock file: $LOCKFILE"
        echo "[nightly] If this is incorrect, remove $LOCKFILE and try again"

        # Send error notification about lock conflict
        send_error_notification \
          "Lock File Conflict" \
          "Another benchmark instance is already running (PID: $EXISTING_PID). Lock file: $LOCKFILE" \
          "$MODEL" \
          "$MODE"

        exit 1
    else
        echo "[nightly] Removing stale lock file from PID $EXISTING_PID"
        rm -f "$LOCKFILE"
    fi
fi

# Create lock file
echo "$$" > "$LOCKFILE"
echo "[nightly] Created process lock: $LOCKFILE"

# Cleanup function
cleanup() {
    echo "[nightly] Cleaning up process lock..."
    rm -f "$LOCKFILE"
}
trap cleanup EXIT

###############################################################################
# 1. Ensure GPU is idle before starting
###############################################################################
ensure_gpu_idle

###############################################################################
# 2. Pick image tag based on model type
###############################################################################
date_pst() { TZ="$TIME_ZONE" date -d "-$1 day" +%Y%m%d; }

# Find non-SRT Docker image for a specific date using Docker Hub API
find_image_for_date() {
  local repo="$1" target_date="$2" rocm_version="${3:-$ROCM_VERSION}"
  local next_url="https://hub.docker.com/v2/repositories/${repo}/tags/?page_size=100"
  local use_jq=$(command -v jq &> /dev/null && echo "true" || echo "false")
  local search_pattern="-${rocm_version}-${HARDWARE_TYPE}-${target_date}"

  echo "[nightly] Searching for non-SRT ${HARDWARE_TYPE} image (${rocm_version}) in '${repo}' for date ${target_date}..." >&2

  while [[ -n "$next_url" && "$next_url" != "null" ]]; do
    local response=$(curl -s --max-time 15 "$next_url")

    [[ -z "$response" || "$response" == *"not found"* || "$response" == *"error"* ]] && break

    # Extract and filter tags based on available tools
    local found_tag=""
    if [[ "$use_jq" == "true" ]]; then
      found_tag=$(echo "$response" | jq -r '.results[].name' | grep -- "${search_pattern}" | grep -v -- "-srt$" | head -1)
      next_url=$(echo "$response" | jq -r '.next // empty')
    else
      found_tag=$(echo "$response" | grep -o '"name":"[^"]*"' | cut -d'"' -f4 | grep -- "${search_pattern}" | grep -v -- "-srt$" | head -1)
      next_url=$(echo "$response" | grep -o '"next":"[^"]*"' | cut -d'"' -f4 | sed 's/null//' || true)
    fi

    [[ -n "$found_tag" ]] && { echo "$found_tag"; return 0; }
    [[ -z "$next_url" || "$next_url" == "null" ]] && break
  done
  return 1
}

# Find and pull Docker images for the last N days
echo "[nightly] Searching for non-SRT images for $MODEL for the last $CONTINUE_RUN_DAYS days..."
SELECTED_TAGS=()

# Check curl availability once
if ! command -v curl &> /dev/null; then
  echo "[nightly] ERROR: curl is required but not found." >&2
  exit 1
fi

###############################################################################
# Check yesterday's run status
###############################################################################
check_yesterday_run_status() {
  # Check if yesterday's run was successful for the specified model and mode
  # Returns: 0 if yesterday's run failed/didn't run/incomplete, 1 if successful
  local yesterday_date=$(date_pst 1)
  local yesterday_log_dir="${BENCHMARK_CI_DIR}/cron/cron_log/${HARDWARE_TYPE}/${yesterday_date}"

  # Determine which log file to check based on model
  local log_file=""
  if [[ "$MODE" == "sanity" ]]; then
    log_file="sanity_check_nightly.log"
  elif [[ "$MODEL" == "grok" ]]; then
    log_file="grok_nightly.log"
  elif [[ "$MODEL" == "grok2" ]]; then
    log_file="grok2_nightly_online.log"
  elif [[ "$MODEL" == "deepseek" ]]; then
    # Check for specific mode variants
    if [[ "$ENABLE_TORCH_COMPILE" == "true" && "$CHECK_DP_ATTENTION" == "true" ]]; then
      log_file="deepseek_dp_attention_torch_compile.log"
    elif [[ "$ENABLE_TORCH_COMPILE" == "true" ]]; then
      log_file="deepseek_torch_compile.log"
    elif [[ "$CHECK_DP_ATTENTION" == "true" ]]; then
      log_file="deepseek_dp_attention.log"
    else
      log_file="deepseek_nightly_online.log"
    fi
  else
    # Unknown model, allow fallback
    echo "[nightly] Unknown model for run status check - allowing fallback"
    return 0
  fi

  local yesterday_log="${yesterday_log_dir}/${log_file}"

  # If log doesn't exist, yesterday didn't run
  if [[ ! -f "$yesterday_log" ]]; then
    echo "[nightly] Yesterday's run log not found - yesterday did not run"
    return 0  # Allow fallback
  fi

  # Check if yesterday's run completed successfully
  if grep -q "OVERALL SCRIPT SUMMARY" "$yesterday_log" && grep -q "Total execution time:" "$yesterday_log"; then
    # Yesterday's run was successful
    echo "[nightly] Yesterday's run completed successfully"
    return 1  # Don't allow fallback - yesterday was successful
  else
    # Yesterday's run failed or didn't complete
    echo "[nightly] Yesterday's run failed or did not complete"
    return 0  # Allow fallback
  fi
}

###############################################################################
# Docker image management functions
###############################################################################

# Function to check if Docker image exists locally
check_local_image() {
  local image="$1"
  if "${DOCKER_CMD[@]}" image inspect "${image}" >/dev/null 2>&1; then
    echo "[nightly] Found local image: ${image}"
    return 0
  else
    return 1
  fi
}

# Function to check Docker Hub connectivity
check_docker_hub_connectivity() {
  if ! curl -s --max-time 30 https://registry-1.docker.io/v2/ >/dev/null 2>&1; then
    echo "[nightly] WARNING: Docker Hub connectivity test failed"
    return 1
  fi
  return 0
}

# Function to pull Docker image with retry logic
pull_image_with_retry() {
  local image="$1"
  local max_attempts=3
  local attempt=1

  # First check if image already exists locally
  if check_local_image "$image"; then
    return 0
  fi

  # Check Docker Hub connectivity before attempting pulls
  if ! check_docker_hub_connectivity; then
    echo "[nightly] WARNING: Docker Hub connectivity issues detected"
  fi

  while [ $attempt -le $max_attempts ]; do
    echo "[nightly] Pull attempt $attempt/$max_attempts for $image..."
    if "${DOCKER_CMD[@]}" pull "$image" 2>&1; then
      echo "[nightly] Successfully pulled image: $image"
      return 0
    fi

    if [ $attempt -lt $max_attempts ]; then
      local wait_time=$((30 * attempt))  # 30s, 60s, 90s delays
      echo "[nightly] Pull failed, retrying in ${wait_time}s..."
      sleep $wait_time
    else
      echo "[nightly] All pull attempts failed for $image"
    fi
    ((attempt++))
  done

  return 1
}

# Try each day from today going back CONTINUE_RUN_DAYS
for offset in $(seq 0 $((CONTINUE_RUN_DAYS - 1))); do
  date_suffix=$(date_pst "$offset")

  # Try primary ROCM version first
  candidate_tag=$(find_image_for_date "$IMAGE_REPO" "$date_suffix" "$ROCM_VERSION" || true)

  # If not found and fallback version exists for this hardware, try fallback
  if [[ -z "$candidate_tag" && -n "${ROCM_FALLBACK_VERSIONS[$HARDWARE_TYPE]}" ]]; then
    fallback_version="${ROCM_FALLBACK_VERSIONS[$HARDWARE_TYPE]}"
    echo "[nightly] Primary version ($ROCM_VERSION) not found, trying fallback ($fallback_version)..."
    candidate_tag=$(find_image_for_date "$IMAGE_REPO" "$date_suffix" "$fallback_version" || true)
    if [[ -n "$candidate_tag" ]]; then
      echo "[nightly] Using fallback ROCM version: $fallback_version"
    fi
  fi

  # Skip if still no candidate found
  [[ -z "$candidate_tag" ]] && continue

  echo "[nightly] Found candidate tag for day -${offset}: ${candidate_tag}"

  if pull_image_with_retry "${IMAGE_REPO}:${candidate_tag}"; then
    SELECTED_TAGS+=("$candidate_tag")
    echo "[nightly] Successfully obtained image for date ${date_suffix}: ${IMAGE_REPO}:${candidate_tag}"
  else
    echo "[nightly] WARN: Failed to obtain candidate tag ${candidate_tag}. It may be private or invalid."
  fi
done

if [[ ${#SELECTED_TAGS[@]} -eq 0 && "$CONTINUE_RUN_DAYS" -eq 1 ]]; then
  echo "[nightly] No image found for today. Checking if yesterday's image should be used as fallback..."

  # Only use yesterday's image if yesterday's run failed/didn't run/didn't complete
  if check_yesterday_run_status; then
    echo "[nightly] Proceeding with yesterday's image fallback..."
    date_suffix=$(date_pst 1)

    # For yesterday fallback, only try primary ROCM version (rocm700)
    # Do not fallback to rocm630 for yesterday - user wants yesterday's rocm700 only
    candidate_tag=$(find_image_for_date "$IMAGE_REPO" "$date_suffix" "$ROCM_VERSION" || true)

    if [[ -n "$candidate_tag" ]]; then
      echo "[nightly] Fallback found candidate tag: ${candidate_tag}"
      if pull_image_with_retry "${IMAGE_REPO}:${candidate_tag}"; then
        SELECTED_TAGS+=("$candidate_tag")
        echo "[nightly] Successfully obtained fallback image for date ${date_suffix}: ${IMAGE_REPO}:${candidate_tag}"
      else
        echo "[nightly] WARN: Failed to obtain fallback tag ${candidate_tag}. It may be private or invalid."
      fi
    else
      echo "[nightly] No fallback image found for yesterday either."
    fi
  else
    echo "[nightly] Skipping yesterday's image fallback - yesterday's run was successful"
    echo "[nightly] Status: SKIPPED (prerequisites not met)"
  fi
fi

if [[ ${#SELECTED_TAGS[@]} -eq 0 ]]; then
  echo "[nightly] ERROR: Could not find and obtain any valid non-SRT images for the last $CONTINUE_RUN_DAYS days."
  exit 1
fi

echo "[nightly] Found ${#SELECTED_TAGS[@]} valid image(s) to run benchmarks on:"
for tag in "${SELECTED_TAGS[@]}"; do
  echo "[nightly]   - ${IMAGE_REPO}:${tag}"
done

###############################################################################
# 2. Loop through each selected tag and run benchmarks
###############################################################################

for SELECTED_TAG in "${SELECTED_TAGS[@]}"; do
  echo ""
  echo "[nightly] =========================================="
  echo "[nightly] Starting benchmarks for image: ${IMAGE_REPO}:${SELECTED_TAG}"
  echo "[nightly] =========================================="

  # Ensure GPU is idle before starting benchmarks for this image
  echo "[nightly] Checking GPU status before starting benchmarks for ${IMAGE_REPO}:${SELECTED_TAG}..."
  ensure_gpu_idle

  DOCKER_IMAGE="${IMAGE_REPO}:${SELECTED_TAG}"
  # Generate container name using image tag only (model-agnostic to share containers across models)
  # Extract repo name from IMAGE_REPO (e.g., "rocm/sgl-dev" -> "sgl-dev")
  REPO_NAME="${IMAGE_REPO##*/}"
  CONTAINER_NAME="${REPO_NAME}_${SELECTED_TAG//:/_}"

  # Check if all runs for the specified modes are already complete for this image
  all_modes_complete=true
  if [[ -z "$MODES_TO_RUN" ]]; then
    all_modes_complete=false
  fi

  for mode_to_check in $MODES_TO_RUN; do
    # Define expected number of runs based on model and mode
    expected_runs=0
    if [[ "$mode_to_check" == "online" ]]; then
      if [[ "$MODEL" == "grok" || "$MODEL" == "grok2" || "$MODEL" == "deepseek" ]]; then
        expected_runs=15 # Based on 5 request rates * 3 runs per rate
      fi
    fi
    # NOTE: Offline completion check is not implemented as log format is unknown.
    # It will be treated as incomplete, preventing premature skipping of tags.

    if [[ "$expected_runs" -eq 0 ]]; then
      echo "[nightly] INFO: No completion check defined for mode '${mode_to_check}'. Assuming not complete."
      all_modes_complete=false
      break
    fi

    # Determine output folder for the mode
    BENCHMARK_OUTPUT_FOLDER=""
    if [[ "$mode_to_check" == "online" ]]; then
      # Determine the correct directory name for DeepSeek-V3
      if [[ "$MODEL" == "DeepSeek-V3" ]]; then
        check_directory_name="DeepSeek-V3"
      else
        check_directory_name="${MODEL_NAME}"
      fi
      BENCHMARK_OUTPUT_FOLDER="${BENCHMARK_CI_DIR}/online/${check_directory_name}/${SELECTED_TAG}_${MODEL_NAME}_${MODEL_VARIANT}_online"
    else
      # Should not happen due to expected_runs check, but as a safeguard:
      all_modes_complete=false
      break
    fi

    # Count actual completed runs (not just existing log files)
    actual_runs=0
    if [[ -d "$BENCHMARK_OUTPUT_FOLDER" ]]; then
      echo "[nightly] Checking benchmark completion in: $BENCHMARK_OUTPUT_FOLDER"
      # Use simpler approach - count files that contain "Run completed at:"
      # Exclude GSM8K logs and only count logs that actually finished
      actual_runs=$(find "$BENCHMARK_OUTPUT_FOLDER" -type f -name "sglang_client_log_*.log" ! -name "*gsm8k*" -exec grep -l "Run completed at:" {} \; 2>/dev/null | wc -l)
      echo "[nightly] Found ${actual_runs} completed benchmark runs"
    fi

    if [[ "$actual_runs" -lt "$expected_runs" ]]; then
      echo "[nightly] INFO: Found ${actual_runs}/${expected_runs} completed runs for ${DOCKER_IMAGE} (${mode_to_check}). Proceeding with benchmarks."
      all_modes_complete=false
      break
    else
      echo "[nightly] INFO: All ${expected_runs} runs for ${DOCKER_IMAGE} (${mode_to_check}) are already complete."
    fi
  done

  if [[ "$all_modes_complete" == "true" ]]; then
    echo "[nightly] INFO: All runs for all requested modes are complete for ${DOCKER_IMAGE}. Skipping benchmark execution but proceeding with post-processing."
  fi

  echo "[nightly] Using Docker image: $DOCKER_IMAGE"
  echo "[nightly] Container name: $CONTAINER_NAME"

###############################################################################
# 2.1. Ensure container is running
###############################################################################
if "${DOCKER_CMD[@]}" ps -a --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"; then
  echo "[nightly] Reusing container ${CONTAINER_NAME}"
  "${DOCKER_CMD[@]}" start "${CONTAINER_NAME}" >/dev/null || true

  # Check if benchmark CI directory is accessible inside the container
  if ! "${DOCKER_CMD[@]}" exec "${CONTAINER_NAME}" test -d "${BENCHMARK_CI_DIR}" 2>/dev/null; then
    echo "[nightly] Benchmark CI directory not accessible in existing container. Recreating container..."
    "${DOCKER_CMD[@]}" stop "${CONTAINER_NAME}" >/dev/null 2>&1
    "${DOCKER_CMD[@]}" rm "${CONTAINER_NAME}" >/dev/null 2>&1
  # Check if models directory is accessible inside the container (required for sanity checks)
  elif ! "${DOCKER_CMD[@]}" exec "${CONTAINER_NAME}" test -d "/mnt/raid/models" 2>/dev/null; then
    echo "[nightly] Models directory not accessible in existing container. Recreating container..."
    "${DOCKER_CMD[@]}" stop "${CONTAINER_NAME}" >/dev/null 2>&1
    "${DOCKER_CMD[@]}" rm "${CONTAINER_NAME}" >/dev/null 2>&1
  # Check if custom model path is accessible inside the container (if provided)
  elif [[ -n "$CLI_MODEL_PATH" ]] && ! "${DOCKER_CMD[@]}" exec "${CONTAINER_NAME}" test -d "${CLI_MODEL_PATH}" 2>/dev/null; then
    echo "[nightly] Custom model path not accessible in existing container. Recreating container..."
    "${DOCKER_CMD[@]}" stop "${CONTAINER_NAME}" >/dev/null 2>&1
    "${DOCKER_CMD[@]}" rm "${CONTAINER_NAME}" >/dev/null 2>&1
  fi
fi

# Create container if it doesn't exist or was removed due to validation failure
if ! "${DOCKER_CMD[@]}" ps -a --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"; then
  echo "[nightly] Creating container ${CONTAINER_NAME}"

  # Create mount arguments - always mount MOUNT_DIR, and also mount benchmark CI directory if different
  mount_args="-v ${MOUNT_DIR}:${MOUNT_DIR}"

  # Always mount the models directory explicitly to ensure accessibility for sanity checks
  mount_args="${mount_args} -v /mnt/raid/models:/mnt/raid/models"

  # Always mount /data and /data2 for sanity checks (models are typically stored there)
  if [[ -d "/data" ]]; then
      mount_args="${mount_args} -v /data:/data"
  fi
  if [[ -d "/data2" ]]; then
      mount_args="${mount_args} -v /data2:/data2"
  fi

  # If benchmark CI directory is not under MOUNT_DIR, mount it separately
  if [[ "${BENCHMARK_CI_DIR}" != "${MOUNT_DIR}"* ]]; then
      echo "[nightly] Benchmark CI directory ${BENCHMARK_CI_DIR} is not under ${MOUNT_DIR}, mounting separately..."
      mount_args="${mount_args} -v ${BENCHMARK_CI_DIR}:${BENCHMARK_CI_DIR}"
  fi

  # If custom model path is provided and not under existing mounts, mount its parent directory
  if [[ -n "$CLI_MODEL_PATH" ]]; then
      model_dir="$(dirname "${CLI_MODEL_PATH}")"
      if [[ "$model_dir" != "${MOUNT_DIR}"* ]] && [[ "$model_dir" != "${BENCHMARK_CI_DIR}"* ]]; then
          # For paths like /data/models/deepseek-v3, mount /data
          mount_root=""
          if [[ "$CLI_MODEL_PATH" == /data/* ]]; then
              mount_root="/data"
          elif [[ "$CLI_MODEL_PATH" == /mnt/* ]]; then
              mount_root="/mnt"
          elif [[ "$CLI_MODEL_PATH" == /home/* ]]; then
              mount_root="/home"
          elif [[ "$CLI_MODEL_PATH" == /opt/* ]]; then
              mount_root="/opt"
          else
              # Fallback: mount the parent directory
              mount_root="$model_dir"
          fi

          if [[ "$mount_root" != "${MOUNT_DIR%/}" ]]; then
              echo "[nightly] Custom model path ${CLI_MODEL_PATH} requires mounting ${mount_root}..."
              mount_args="${mount_args} -v ${mount_root}:${mount_root}"
          fi
      fi
  fi

  "${DOCKER_CMD[@]}" run -d --name "${CONTAINER_NAME}" \
    --shm-size "$CONTAINER_SHM_SIZE" --ipc=host --cap-add=SYS_PTRACE --network=host \
    --device=/dev/kfd --device=/dev/dri --security-opt seccomp=unconfined \
    -e HSA_ENABLE_COREDUMP=0 \
    ${mount_args} --group-add video --privileged \
    -w "$WORK_DIR" "${DOCKER_IMAGE}" tail -f /dev/null
fi

###############################################################################
# 3. Handle model download if needed
###############################################################################
# Check if model needs to be downloaded from HuggingFace
if [[ -z "$CLI_MODEL_PATH" && -n "$HF_MODEL_REPO" ]]; then
  # Only attempt download if no custom model path is provided AND HF repo is configured
  # Extract model name from HF repo for directory naming
  MODEL_DIR_NAME=$(basename "$HF_MODEL_REPO")

  # Use the benchmark CI directory (work-dir) for model storage when provided
  if [[ -n "$CLI_WORK_DIR" ]]; then
    DOWNLOADED_MODEL_PATH="${BENCHMARK_CI_DIR}/models/${MODEL_DIR_NAME}"
  else
    DOWNLOADED_MODEL_PATH="${WORK_DIR}/models/${MODEL_DIR_NAME}"
  fi

  # Check if model directory exists and is not empty
  if ! "${DOCKER_CMD[@]}" exec "${CONTAINER_NAME}" test -d "${DOWNLOADED_MODEL_PATH}" 2>/dev/null || \
     [[ -z "$("${DOCKER_CMD[@]}" exec "${CONTAINER_NAME}" ls -A "${DOWNLOADED_MODEL_PATH}" 2>/dev/null)" ]]; then

    echo "[nightly] Model not found at ${DOWNLOADED_MODEL_PATH}, attempting download from HuggingFace..."

    # Ensure models directory exists - create it locally first, then in container if needed
    if [[ -n "$CLI_WORK_DIR" ]]; then
      mkdir -p "${BENCHMARK_CI_DIR}/models"
      "${DOCKER_CMD[@]}" exec "${CONTAINER_NAME}" mkdir -p "${BENCHMARK_CI_DIR}/models"
    else
      "${DOCKER_CMD[@]}" exec "${CONTAINER_NAME}" mkdir -p "${WORK_DIR}/models"
    fi

    # Download the model
    if download_hf_model "$HF_MODEL_REPO" "$DOWNLOADED_MODEL_PATH" "$MODEL"; then
      echo "[nightly] Model downloaded successfully, using path: ${DOWNLOADED_MODEL_PATH}"
      CLI_MODEL_PATH="$DOWNLOADED_MODEL_PATH"
    else
      echo "[nightly] WARN: Failed to download model from HuggingFace. Proceeding with default model path..."
    fi
  else
    echo "[nightly] Model already exists at ${DOWNLOADED_MODEL_PATH}, using existing model"
    CLI_MODEL_PATH="$DOWNLOADED_MODEL_PATH"
  fi
elif [[ -z "$CLI_MODEL_PATH" && -z "$HF_MODEL_REPO" ]]; then
  echo "[nightly] No custom model path provided. Using default model path from benchmark script for $MODEL model."
fi

###############################################################################
# 4. Run benchmarks for each mode (only if not already complete)
###############################################################################
ONLINE_SUCCEEDED=false
RUNNING_ALL_MODES=false

# Check if we're running both online and offline (all mode)
if [[ "$MODES_TO_RUN" == "online offline" ]]; then
    RUNNING_ALL_MODES=true
fi

  # Only run benchmarks if they're not already complete
  if [[ "$all_modes_complete" != "true" ]]; then
    echo "[nightly] === Starting benchmark execution ==="
    for MODE_TO_RUN in $MODES_TO_RUN; do
    # Note: Offline benchmark now handles GSM8K checking internally, so we always attempt it

    echo "[nightly] === Starting nightly ${MODEL^^} ${MODE_TO_RUN} benchmark ==="

    # Handle sanity check mode
    if [ "$MODE_TO_RUN" == "sanity" ]; then
      echo "[nightly] Launching sanity check inside ${CONTAINER_NAME}"

      SANITY_EXIT_CODE=0

      # Build sanity check arguments
      SANITY_ARGS="--docker-image='${DOCKER_IMAGE}' --hardware='${HARDWARE_TYPE}' --trials=${SANITY_TRIALS}"

      # Add custom work directory if provided
      if [[ -n "$CLI_WORK_DIR" ]]; then
        SANITY_ARGS="${SANITY_ARGS} --work-dir='${CLI_WORK_DIR}'"
        # Use work directory for sanity check logs as well
        SANITY_ARGS="${SANITY_ARGS} --log-dir='${CLI_WORK_DIR}/test/sanity_check_log/${HARDWARE_TYPE}'"
      else
        # Use default log directory structure
        SANITY_ARGS="${SANITY_ARGS} --log-dir='${BENCHMARK_CI_DIR}/test/sanity_check_log/${HARDWARE_TYPE}'"
      fi

      # Add custom models directory if provided
      if [[ -n "${CLI_MODELS_DIR:-}" ]]; then
        SANITY_ARGS="${SANITY_ARGS} --models-dir='${CLI_MODELS_DIR}'"
      fi

      echo "[nightly] Executing: python3 '${SANITY_CHECK_SCRIPT}' ${SANITY_ARGS}"

      "${DOCKER_CMD[@]}" exec \
        -e INSIDE_CONTAINER=1 \
        -e LATEST_TAG="${SELECTED_TAG}" \
        -e FULL_IMAGE="${DOCKER_IMAGE}" \
        -e TZ='America/Los_Angeles' \
        "${CONTAINER_NAME}" \
        bash -c "python3 '${SANITY_CHECK_SCRIPT}' ${SANITY_ARGS}" || SANITY_EXIT_CODE=$?

      if [ $SANITY_EXIT_CODE -eq 0 ]; then
        echo "[nightly] === Sanity check completed successfully ==="
      else
        echo "[nightly] === Sanity check failed (exit code: $SANITY_EXIT_CODE) ==="
      fi

      # Upload handled by cron/github_log_upload.sh called from crontab after this script.
      # Send Teams notification for sanity check if webhook URL is configured
      if [[ "$TEAMS_WEBHOOK_FROM_CLI" == "true" && -n "$TEAMS_WEBHOOK_URL" ]]; then
        echo "[nightly] Sending Teams notification for sanity check results..."

        SANITY_TEAMS_EXIT_CODE=0

        # Execute Teams notification inside the container
        "${DOCKER_CMD[@]}" exec \
          -e INSIDE_CONTAINER=1 \
          -e TEAMS_WEBHOOK_URL="${TEAMS_WEBHOOK_URL}" \
          "${CONTAINER_NAME}" \
          bash -c "pip install requests pytz > /dev/null 2>&1; python3 '${TEAMS_NOTIFICATION_SCRIPT}' --mode sanity --docker-image '${SELECTED_TAG}'" || SANITY_TEAMS_EXIT_CODE=$?

        if [ $SANITY_TEAMS_EXIT_CODE -eq 0 ]; then
          echo "[nightly] Teams notification sent successfully for sanity check"
        else
          echo "[nightly] WARN: Teams notification failed for sanity check (exit code: $SANITY_TEAMS_EXIT_CODE)"
        fi
      else
        echo "[nightly] Teams notifications disabled - --teams-webhook-url not provided"
      fi

      continue
    fi

    # Determine which benchmark script to run
    if [ "$MODE_TO_RUN" == "offline" ]; then
      SCRIPT="$OFFLINE_SCRIPT"
    else
      SCRIPT="$ONLINE_SCRIPT"
    fi

    if [ -z "$SCRIPT" ]; then
      echo "[nightly] ERROR: No ${MODE_TO_RUN} script available for ${MODEL}"
      continue
    fi

  echo "[nightly] Launching $(basename "$SCRIPT") inside ${CONTAINER_NAME}"

  # Execute the benchmark script and capture exit code
  BENCHMARK_EXIT_CODE=0

  # Build common parameters
  SCRIPT_ARGS="--docker_image='${DOCKER_IMAGE}'"

  # Add current directory parameter (always pass it to override default paths)
  SCRIPT_ARGS="${SCRIPT_ARGS} --current-dir='${BENCHMARK_CI_DIR}'"

  # Add nightly command, hardware, and ROCM version info for timing logs
  SCRIPT_ARGS="${SCRIPT_ARGS} --nightly-command='$0 $*'"
  SCRIPT_ARGS="${SCRIPT_ARGS} --hardware='${HARDWARE_TYPE}'"
  SCRIPT_ARGS="${SCRIPT_ARGS} --rocm-version='${ROCM_VERSION}'"

  # Add custom work directory if provided
  if [[ -n "$CLI_WORK_DIR" ]]; then
    SCRIPT_ARGS="${SCRIPT_ARGS} --work-dir='${CLI_WORK_DIR}'"
  fi

  # Add custom model path if provided
  if [[ -n "$CLI_MODEL_PATH" ]]; then
    if [[ "$MODEL" == "deepseek" ]]; then
      SCRIPT_ARGS="${SCRIPT_ARGS} --model='${CLI_MODEL_PATH}'"
    else
      # For Grok and Grok2
      SCRIPT_ARGS="${SCRIPT_ARGS} --model='${CLI_MODEL_PATH}'"
    fi
  fi

  # Add model-type parameter for grok2
  if [[ "$MODEL" == "grok2" ]]; then
    SCRIPT_ARGS="${SCRIPT_ARGS} --model-type=grok2"
    echo "[nightly] Adding --model-type=grok2 flag for Grok 2 benchmark"

    # Add custom tokenizer path for grok2 if provided
    if [[ -n "$CLI_TOKENIZER_PATH" ]]; then
      SCRIPT_ARGS="${SCRIPT_ARGS} --tokenizer='${CLI_TOKENIZER_PATH}'"
      echo "[nightly] Adding --tokenizer='${CLI_TOKENIZER_PATH}' flag for Grok 2 benchmark"
    fi
  fi

  if [[ "$MODEL" == "deepseek" || "$MODEL" == "DeepSeek-V3" ]]; then
    # For DeepSeek, pass additional parameters if needed
    SCRIPT_ARGS="${SCRIPT_ARGS} --model-name='${MODEL_NAME}'"
    echo "[nightly] Passing --model-name='${MODEL_NAME}' to DeepSeek benchmark"
    # Add --check-dp-attention flag if enabled and running online mode
    if [[ "$CHECK_DP_ATTENTION" == "true" && "$MODE_TO_RUN" == "online" ]]; then
      SCRIPT_ARGS="${SCRIPT_ARGS} --check-dp-attention"
      echo "[nightly] Adding --check-dp-attention flag for DeepSeek online benchmark"
    fi

    # Add --enable-torch-compile flag if enabled and running online mode
    if [[ "$ENABLE_TORCH_COMPILE" == "true" && "$MODE_TO_RUN" == "online" ]]; then
      SCRIPT_ARGS="${SCRIPT_ARGS} --enable-torch-compile"
      echo "[nightly] Adding --enable-torch-compile flag for DeepSeek online benchmark"
    fi

    # Add --enable-dp-test flag if enabled and running online mode
    if [[ "$ENABLE_DP_TEST" == "true" && "$MODE_TO_RUN" == "online" ]]; then
      SCRIPT_ARGS="${SCRIPT_ARGS} --enable-dp-test"
      echo "[nightly] Adding --enable-dp-test flag for DeepSeek online benchmark"
    fi

    # Add --enable-mtp-test flag if enabled and running online mode
    if [[ "$ENABLE_MTP_TEST" == "true" && "$MODE_TO_RUN" == "online" ]]; then
      SCRIPT_ARGS="${SCRIPT_ARGS} --enable-mtp-test"
      echo "[nightly] Adding --enable-mtp-test flag for DeepSeek online benchmark"
    fi

    "${DOCKER_CMD[@]}" exec \
      -e INSIDE_CONTAINER=1 \
      -e LATEST_TAG="${SELECTED_TAG}" \
      -e FULL_IMAGE="${DOCKER_IMAGE}" \
      -e TZ='America/Los_Angeles' \
      $([ -n "${SERVER_MEM_FRACTION:-}" ] && echo "-e SERVER_MEM_FRACTION=${SERVER_MEM_FRACTION}") \
      $([ -n "${CUDA_GRAPH_MAX_BS:-}" ] && echo "-e CUDA_GRAPH_MAX_BS=${CUDA_GRAPH_MAX_BS}") \
      $([ "${DISABLE_CUDA_GRAPH:-false}" = "true" ] && echo "-e DISABLE_CUDA_GRAPH=true") \
      "${CONTAINER_NAME}" \
      bash -c "'$SCRIPT' $SCRIPT_ARGS" || BENCHMARK_EXIT_CODE=$?
  else
    # For Grok and Grok2, use the existing command structure
    "${DOCKER_CMD[@]}" exec \
      -e INSIDE_CONTAINER=1 \
      -e LATEST_TAG="${SELECTED_TAG}" \
      -e FULL_IMAGE="${DOCKER_IMAGE}" \
      "${CONTAINER_NAME}" \
      bash -c "'$SCRIPT' $SCRIPT_ARGS" || BENCHMARK_EXIT_CODE=$?
  fi

  # Track success of online benchmark for gating offline
  if [[ "$MODE_TO_RUN" == "online" && "$RUNNING_ALL_MODES" == "true" ]]; then
    if [ $BENCHMARK_EXIT_CODE -eq 0 ]; then
      ONLINE_SUCCEEDED=true
      echo "[nightly] === Online benchmark script finished successfully, offline benchmark will proceed ==="
    else
      ONLINE_SUCCEEDED=false
      echo "[nightly] === Online benchmark script failed (exit code: $BENCHMARK_EXIT_CODE). Offline benchmark will still be attempted (GSM8K check moved to offline script). ==="
    fi
  fi

  echo "[nightly] === ${MODE_TO_RUN^} benchmark dispatched for ${MODEL^^}; check logs in ${CONTAINER_NAME} ==="
done
else
  echo "[nightly] === Skipping benchmark execution - all benchmarks already complete ==="
fi

###############################################################################
# 5. Post-processing: CSV Processing, Plot Generation, and Teams Notifications
###############################################################################
echo "[nightly] === Starting post-processing (CSV processing, plot generation, Teams notifications) ==="

# Process and generate plots for each mode
for MODE_TO_RUN in $MODES_TO_RUN; do
  # Process CSV and Generate Plots (Combined)
  if [ "$MODE_TO_RUN" == "offline" ]; then
    # Construct the path to the log folder for offline benchmarks
    BENCHMARK_OUTPUT_FOLDER="${OFFLINE_OUTPUT_DIR}/${MODEL_NAME}/${SELECTED_TAG}_${MODEL_NAME}_${MODEL_VARIANT}_offline"

    COMBINED_LOG_FILE="${BENCHMARK_OUTPUT_FOLDER}/process_and_generate_offline_plots.log"

    echo "[nightly] Processing offline CSV data and generating plots... Logs will be saved to ${COMBINED_LOG_FILE}"

    # Build Python script arguments with custom directories when work-dir is provided
    PYTHON_ARGS="--model '${MODEL}'"

    # Pass model-name to Python script for proper directory naming
    if [[ -n "$MODEL_NAME" ]]; then
      PYTHON_ARGS="${PYTHON_ARGS} --model-name '${MODEL_NAME}'"
    fi

    # Determine the correct directory name for DeepSeek-V3
    if [[ "$MODEL" == "DeepSeek-V3" ]]; then
      DIRECTORY_NAME="DeepSeek-V3"
    else
      DIRECTORY_NAME="${MODEL_NAME}"
    fi

    if [[ -n "$CLI_WORK_DIR" ]]; then
      PYTHON_ARGS="${PYTHON_ARGS} --data-dir '${BENCHMARK_CI_DIR}/offline/${DIRECTORY_NAME}'"
      PYTHON_ARGS="${PYTHON_ARGS} --plot-dir '${BENCHMARK_CI_DIR}/plots_server/${DIRECTORY_NAME}/offline'"
      # Use custom work directory for log file as well
      BENCHMARK_OUTPUT_FOLDER="${BENCHMARK_CI_DIR}/offline/${DIRECTORY_NAME}/${SELECTED_TAG}_${MODEL_NAME}_${MODEL_VARIANT}_offline"
    else
      # Use default directories
      BENCHMARK_OUTPUT_FOLDER="${OFFLINE_OUTPUT_DIR}/${DIRECTORY_NAME}/${SELECTED_TAG}_${MODEL_NAME}_${MODEL_VARIANT}_offline"
    fi

    COMBINED_LOG_FILE="${BENCHMARK_OUTPUT_FOLDER}/process_and_generate_offline_plots.log"
    echo "[nightly] Processing offline CSV data and generating plots... Logs will be saved to ${COMBINED_LOG_FILE}"

    # Ensure log directory exists before redirecting output
    if ! "${DOCKER_CMD[@]}" exec "${CONTAINER_NAME}" bash -c "mkdir -p \"\$(dirname \"${COMBINED_LOG_FILE}\")\""; then
      echo "[nightly] ERROR: Failed to create log directory in container"
      send_error_notification \
        "Docker Exec Failure" \
        "Failed to create log directory in container ${CONTAINER_NAME}. Container may be unhealthy." \
        "${MODEL}" \
        "offline"
      continue
    fi

    # Run post-processing with error handling
    POST_PROCESS_EXIT_CODE=0
    "${DOCKER_CMD[@]}" exec \
      -e INSIDE_CONTAINER=1 \
      "${CONTAINER_NAME}" \
      bash -c "pip install pandas matplotlib > /dev/null 2>&1 && \
               python3 '${PROCESS_AND_GENERATE_OFFLINE_PLOTS_SCRIPT}' ${PYTHON_ARGS} > '${COMBINED_LOG_FILE}' 2>&1" || POST_PROCESS_EXIT_CODE=$?

    if [ $POST_PROCESS_EXIT_CODE -ne 0 ]; then
      echo "[nightly] ERROR: Offline post-processing failed (exit code: $POST_PROCESS_EXIT_CODE)"
      send_error_notification \
        "Post-Processing Failure" \
        "Offline plot generation failed with exit code ${POST_PROCESS_EXIT_CODE}. Check ${COMBINED_LOG_FILE} for details." \
        "${MODEL}" \
        "offline"
      # Continue to next iteration instead of sending normal notification
      continue
    fi

    # Send Teams notification for offline plots
    send_teams_notification "${MODEL}" "offline"
  fi

  if [ "$MODE_TO_RUN" == "online" ]; then
    # Construct the path to the log folder for online benchmarks
    BENCHMARK_OUTPUT_FOLDER="${ONLINE_OUTPUT_DIR}/${MODEL_NAME}/${SELECTED_TAG}_${MODEL_NAME}_${MODEL_VARIANT}_online"

    COMBINED_LOG_FILE="${BENCHMARK_OUTPUT_FOLDER}/process_and_generate_online_plots.log"

    echo "[nightly] Processing online CSV data and generating plots... Logs will be saved to ${COMBINED_LOG_FILE}"

    # Build Python script arguments with custom directories when work-dir is provided
    PYTHON_ARGS="--model '${MODEL}'"

    # Pass model-name to Python script for proper directory naming
    if [[ -n "$MODEL_NAME" ]]; then
      PYTHON_ARGS="${PYTHON_ARGS} --model-name '${MODEL_NAME}'"
    fi

    # Extract date from image tag for plot filename
    PLOT_DATE=$(extract_date_from_tag "${SELECTED_TAG}")
    if [[ $? -eq 0 && -n "$PLOT_DATE" ]]; then
      PYTHON_ARGS="${PYTHON_ARGS} --plot-date '${PLOT_DATE}'"
      echo "[nightly] Using plot date from image tag: ${PLOT_DATE}"
    else
      echo "[nightly] Could not extract date from tag ${SELECTED_TAG}, using current date for plots"
    fi

    # For MI355 hardware, exclude rate 16 for grok/grok2 (known scheduler timeout limitation)
    if [[ "${HARDWARE_TYPE}" == *"mi35"* && ("${MODEL}" == "grok" || "${MODEL}" == "grok2") ]]; then
      PYTHON_ARGS="${PYTHON_ARGS} --request-rates '1,2,4,8'"
      echo "[nightly] MI355 hardware detected for ${MODEL} - using request rates [1, 2, 4, 8] (excluding 16)"
    fi

    # Determine the correct directory name for DeepSeek-V3
    if [[ "$MODEL" == "DeepSeek-V3" ]]; then
      DIRECTORY_NAME="DeepSeek-V3"
    else
      DIRECTORY_NAME="${MODEL_NAME}"
    fi

    if [[ -n "$CLI_WORK_DIR" ]]; then
      PYTHON_ARGS="${PYTHON_ARGS} --base-dir '${BENCHMARK_CI_DIR}'"
      PYTHON_ARGS="${PYTHON_ARGS} --data-dir '${BENCHMARK_CI_DIR}/online/${DIRECTORY_NAME}'"
      PYTHON_ARGS="${PYTHON_ARGS} --plot-dir '${BENCHMARK_CI_DIR}/plots_server/${DIRECTORY_NAME}/online'"
      # Use custom work directory for log file as well
      BENCHMARK_OUTPUT_FOLDER="${BENCHMARK_CI_DIR}/online/${DIRECTORY_NAME}/${SELECTED_TAG}_${MODEL_NAME}_${MODEL_VARIANT}_online"
    else
      # Use default directories
      BENCHMARK_OUTPUT_FOLDER="${ONLINE_OUTPUT_DIR}/${DIRECTORY_NAME}/${SELECTED_TAG}_${MODEL_NAME}_${MODEL_VARIANT}_online"
    fi

    # Ensure log directory exists before redirecting output
    if ! "${DOCKER_CMD[@]}" exec "${CONTAINER_NAME}" bash -c "mkdir -p \"\$(dirname \"${COMBINED_LOG_FILE}\")\""; then
      echo "[nightly] ERROR: Failed to create log directory in container"
      send_error_notification \
        "Docker Exec Failure" \
        "Failed to create log directory in container ${CONTAINER_NAME}. Container may be unhealthy." \
        "${MODEL}" \
        "online"
      continue
    fi

    # Run post-processing with error handling
    POST_PROCESS_EXIT_CODE=0
    "${DOCKER_CMD[@]}" exec \
      -e INSIDE_CONTAINER=1 \
      "${CONTAINER_NAME}" \
      bash -c "pip install pandas matplotlib > /dev/null 2>&1 && \
               python3 '${PROCESS_AND_GENERATE_ONLINE_PLOTS_SCRIPT}' ${PYTHON_ARGS} > '${COMBINED_LOG_FILE}' 2>&1" || POST_PROCESS_EXIT_CODE=$?

    if [ $POST_PROCESS_EXIT_CODE -ne 0 ]; then
      echo "[nightly] ERROR: Online post-processing failed (exit code: $POST_PROCESS_EXIT_CODE)"
      send_error_notification \
        "Post-Processing Failure" \
        "Online plot generation failed with exit code ${POST_PROCESS_EXIT_CODE}. Check ${COMBINED_LOG_FILE} for details." \
        "${MODEL}" \
        "online"
      # Continue to next iteration instead of sending normal notification
      continue
    fi

    # Send Teams notification for online plots
    send_teams_notification "${MODEL}" "online"
  fi

  echo "[nightly] === ${MODE_TO_RUN^} post-processing completed for ${MODEL^^} ==="
done

  echo "[nightly] =========================================="
  echo "[nightly] Completed benchmarks for image: ${IMAGE_REPO}:${SELECTED_TAG}"
  echo "[nightly] Stopping container ${CONTAINER_NAME} to release resources..."
  "${DOCKER_CMD[@]}" stop "${CONTAINER_NAME}" >/dev/null 2>&1 || true
  echo "[nightly] =========================================="
done

echo "[nightly] =========================================="
echo "[nightly] All benchmarks completed for ${#SELECTED_TAGS[@]} image(s)"
echo "[nightly] =========================================="
