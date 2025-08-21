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
#   • Supports mi30x (rocm630) and mi35x (rocm700) hardware variants
#   • Examples:
#     - rocm/sgl-dev:v0.4.9.post2-rocm630-mi30x-20250716
#     - rocm/sgl-dev:v0.4.9.post2-rocm700-mi35x-20250716
#   • Automatically excludes SRT variants (ends with -srt)
#
# MODEL DOWNLOAD:
#   • Supports automatic model download from HuggingFace Hub
#   • Downloads to <work-dir>/models/<model_name> when --work-dir is specified
#   • Otherwise downloads to WORK_DIR/models/<model_name> inside container
#   • Uses --download-model option with default: deepseek-ai/DeepSeek-V3-0324
#   • Requires huggingface_hub library (automatically installed in container)
#   • Only downloads when no custom --model-path is provided
#   • DISK SPACE REQUIREMENTS: DeepSeek models need 685GB, Grok models need 200GB
#   • Automatically checks available disk space before downloading
#   • Supports download resumption if interrupted (using huggingface-cli)
#
# USAGE:
#   perf_nightly.sh [OPTIONS]
#
# OPTIONS:
#   --model=MODEL        Model to benchmark: grok, deepseek, DeepSeek-V3 [default: grok]
#   --model-path=PATH    Custom model path (overrides default model path)
#   --work-dir=PATH      Custom work directory (overrides default work directory)
#   --mode=MODE          Benchmark mode: online, offline, all [default: all]
#   --hardware=HW        Hardware type: mi30x, mi35x [default: mi30x]
#   --download-model=REPO  Download model from HuggingFace if not exists [default: deepseek-ai/DeepSeek-V3-0324]
#   --continue-run-days=N    Run benchmarks for last N days' images [default: 1]
#   --teams-webhook-url=URL  Enable Teams notifications with webhook URL
#   --teams-skip-analysis    Skip GSM8K accuracy and performance analysis
#   --teams-analysis-days=N  Days to look back for performance comparison [default: 7]
#   --help, -h           Show detailed help message
#
# EXAMPLES:
#   perf_nightly.sh                                    # Grok online+offline (mi30x)
#   perf_nightly.sh --model=deepseek --mode=online     # DeepSeek online only (mi30x)
#   perf_nightly.sh --model=DeepSeek-V3 --mode=online  # DeepSeek-V3 online only (mi30x)
#   perf_nightly.sh --hardware=mi35x --mode=all        # Grok on mi35x hardware
#   perf_nightly.sh --model=grok --mode=all \          # Grok with Teams alerts
#     --teams-webhook-url="https://prod-99.westus.logic.azure.com/..."
#   perf_nightly.sh --model-path=/data/models/custom-deepseek  # Custom model path
#   perf_nightly.sh --work-dir=/tmp/benchmark-workspace       # Custom work directory
#   perf_nightly.sh --model=DeepSeek-V3 \                     # Combined custom paths
#     --model-path=/raid/deepseek-ai/DeepSeek-V3 --work-dir=/home/user/workspace
#   perf_nightly.sh --continue-run-days=7 --model=grok        # Run last 7 days' images
# ---------------------------------------------------------------------------
set -euo pipefail

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
HARDWARE_TYPE="${HARDWARE_TYPE:-mi30x}"  # Default to mi30x, can be mi30x or mi35x

# ROCM version mapping based on hardware
declare -A ROCM_VERSIONS=(
  ["mi30x"]="rocm630"
  ["mi35x"]="rocm700"
)

# Model configuration - will be set based on --model parameter
GROK_MODEL_NAME="${GROK_MODEL_NAME:-GROK1}"
GROK_MODEL_VARIANT="${GROK_MODEL_VARIANT:-MOE-I4F8}"
DEEPSEEK_MODEL_NAME="${DEEPSEEK_MODEL_NAME:-DeepSeek-V3-0324}"
DEEPSEEK_MODEL_VARIANT="${DEEPSEEK_MODEL_VARIANT:-FP8}"

# HuggingFace model download configuration
DEFAULT_HF_MODEL_REPO="${DEFAULT_HF_MODEL_REPO:-deepseek-ai/DeepSeek-V3-0324}"

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
    if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
      running_ids="$(docker ps -q 2>/dev/null || true)"
      if [[ -n "$running_ids" ]]; then
        echo "[nightly] Stopping running containers: $(echo "$running_ids" | tr '\\n' ' ')"
        docker stop $running_ids >/dev/null 2>&1 || true
      else
        echo "[nightly] No running containers to stop."
      fi
    else
      echo "[nightly] WARN: Docker not accessible (missing or permission denied); skipping container stop."
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
    "grok")
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

  docker exec \
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
# Teams notification function
###############################################################################
send_teams_notification() {
  local model="$1"
  local mode="$2"

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

  # Build the command with optional no-images flag
  TEAMS_CMD="python3 -c 'import requests, pytz' 2>/dev/null || pip install requests pytz > /dev/null 2>&1; python3 '${TEAMS_NOTIFICATION_SCRIPT}' --model '${model}' --mode '${mode}'"

  # Add --no-images flag if configured
  if [[ "$TEAMS_NO_IMAGES" == "true" ]]; then
    TEAMS_CMD="${TEAMS_CMD} --no-images"
    echo "[nightly] Using text-only mode (no embedded images)"
  fi

  # Add analysis parameters
  if [[ "$TEAMS_SKIP_ANALYSIS" == "true" ]]; then
    TEAMS_CMD="${TEAMS_CMD} --skip-analysis"
    echo "[nightly] Skipping GSM8K accuracy and performance analysis"
  else
    TEAMS_CMD="${TEAMS_CMD} --analysis-days ${TEAMS_ANALYSIS_DAYS}"
    echo "[nightly] Including intelligent analysis (${TEAMS_ANALYSIS_DAYS} days lookback)"
  fi

  docker exec \
    -e INSIDE_CONTAINER=1 \
    -e TEAMS_WEBHOOK_URL="${TEAMS_WEBHOOK_URL}" \
    -e TEAMS_NO_IMAGES="${TEAMS_NO_IMAGES}" \
    -e PLOT_SERVER_HOST="${PLOT_SERVER_HOST}" \
    -e PLOT_SERVER_PORT="${PLOT_SERVER_PORT}" \
    -e PLOT_SERVER_BASE_URL="${PLOT_SERVER_BASE_URL}" \
    "${CONTAINER_NAME}" \
    bash -c "${TEAMS_CMD}" || TEAMS_EXIT_CODE=$?

  if [ $TEAMS_EXIT_CODE -eq 0 ]; then
    echo "[nightly] Teams notification sent successfully for ${model} ${mode}"
  else
    echo "[nightly] WARN: Teams notification failed for ${model} ${mode} (exit code: $TEAMS_EXIT_CODE)"
  fi
}

###############################################################################
# 0. Parse CLI flags
###############################################################################
MODE="all" # Default to run both offline and online
MODEL="grok" # Default to grok
CLI_TEAMS_WEBHOOK_URL="" # Teams webhook URL from command line
CLI_TEAMS_SKIP_ANALYSIS="" # Skip analysis flag from command line
CLI_WORK_DIR="" # Custom work directory from command line
CLI_MODEL_PATH="" # Custom model path from command line
CLI_DOWNLOAD_MODEL="" # HuggingFace model repository to download from command line
CLI_CONTINUE_RUN_DAYS="" # Number of days to run benchmarks for from command line

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
      ;;
    --teams-skip-analysis)
      CLI_TEAMS_SKIP_ANALYSIS="true"
      ;;
    --teams-analysis-days=*)
      TEAMS_ANALYSIS_DAYS="${arg#*=}"
      ;;
    --help|-h)
      echo "Usage: $0 [OPTIONS]"
      echo ""
      echo "Run SGL nightly benchmarks with optional Teams notifications"
      echo ""
      echo "Options:"
      echo "  --model=MODEL                    Model to benchmark (grok, deepseek, DeepSeek-V3) [default: grok]"
      echo "  --model-path=PATH                Custom model path (overrides default model path)"
      echo "  --work-dir=PATH                  Custom work directory (overrides default work directory)"
      echo "  --mode=MODE                      Benchmark mode (online, offline, all) [default: all]"
      echo "  --hardware=HW                    Hardware type (mi30x, mi35x) [default: mi30x]"
      echo "  --download-model=REPO            Download model from HuggingFace if not exists [default: deepseek-ai/DeepSeek-V3-0324]"
      echo "  --continue-run-days=DAYS         Run benchmarks for last N days' images [default: 1]"
      echo "  --teams-webhook-url=URL          Teams webhook URL to enable notifications [default: disabled]"
      echo "  --teams-skip-analysis            Skip GSM8K accuracy and performance regression analysis"
      echo "  --teams-analysis-days=DAYS       Days to look back for performance comparison [default: 7]"
      echo "  --help, -h                       Show this help message"
      echo ""
      echo "Examples:"
      echo "  $0                                          # Run grok online+offline, no Teams (mi30x)"
      echo "  $0 --model=deepseek --mode=online           # Run deepseek online only, no Teams (mi30x)"
      echo "  $0 --model=DeepSeek-V3 --mode=online        # Run DeepSeek-V3 online only, no Teams (mi30x)"
      echo "  $0 --hardware=mi35x --mode=all              # Run grok on mi35x hardware"
      echo "  $0 --model=grok --mode=all \\                # Run grok with Teams notifications"
      echo "     --teams-webhook-url='https://prod-99.westus.logic.azure.com/...'"
      echo "  $0 --model-path=/data/models/custom-grok    # Use custom model path"
      echo "  $0 --work-dir=/tmp/benchmark-run            # Use custom work directory"
      echo "  $0 --model=deepseek --work-dir=/home/user/workspace  # Auto-download to work-dir/models/"
      echo "  $0 --teams-webhook-url='...' --teams-skip-analysis  # Teams with plots only (no analysis)"
      echo "  $0 --continue-run-days=7 --model=grok               # Run last 7 days' images sequentially"
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

# Override Teams settings if provided via command line
if [[ -n "$CLI_TEAMS_WEBHOOK_URL" ]]; then
  TEAMS_WEBHOOK_URL="$CLI_TEAMS_WEBHOOK_URL"
  echo "[nightly] Teams webhook URL provided via command line - notifications enabled"
fi

if [[ -n "$CLI_TEAMS_SKIP_ANALYSIS" ]]; then
  TEAMS_SKIP_ANALYSIS="$CLI_TEAMS_SKIP_ANALYSIS"
  echo "[nightly] Teams analysis disabled via command line"
fi

# Override work directory and model path if provided via command line
if [[ -n "$CLI_WORK_DIR" ]]; then
  BENCHMARK_CI_DIR="$CLI_WORK_DIR"
  echo "[nightly] Custom work directory provided: $BENCHMARK_CI_DIR"
fi

if [[ -n "$CLI_MODEL_PATH" ]]; then
  echo "[nightly] Custom model path provided: $CLI_MODEL_PATH"
fi

# Process HuggingFace model download option
HF_MODEL_REPO="$DEFAULT_HF_MODEL_REPO"
if [[ -n "$CLI_DOWNLOAD_MODEL" ]]; then
  HF_MODEL_REPO="$CLI_DOWNLOAD_MODEL"
  echo "[nightly] HuggingFace model repository provided: $HF_MODEL_REPO"
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

# Set script paths after CLI parameter processing (so custom work directory is used)
GROK_OFFLINE_SCRIPT="${GROK_OFFLINE_SCRIPT:-${BENCHMARK_CI_DIR}/grok_perf_offline_csv.sh}"
GROK_ONLINE_SCRIPT="${GROK_ONLINE_SCRIPT:-${BENCHMARK_CI_DIR}/grok_perf_online_csv.sh}"
DEEPSEEK_OFFLINE_SCRIPT="${DEEPSEEK_OFFLINE_SCRIPT:-${BENCHMARK_CI_DIR}/deepseek_perf_offline_csv.sh}"
DEEPSEEK_ONLINE_SCRIPT="${DEEPSEEK_ONLINE_SCRIPT:-${BENCHMARK_CI_DIR}/deepseek_perf_online_csv.sh}"

# Python scripts for processing and plotting (combined)
PROCESS_AND_GENERATE_OFFLINE_PLOTS_SCRIPT="${PROCESS_AND_GENERATE_OFFLINE_PLOTS_SCRIPT:-${BENCHMARK_CI_DIR}/process_and_generate_offline_plots.py}"
PROCESS_AND_GENERATE_ONLINE_PLOTS_SCRIPT="${PROCESS_AND_GENERATE_ONLINE_PLOTS_SCRIPT:-${BENCHMARK_CI_DIR}/process_and_generate_online_plots.py}"

# Teams notification script
TEAMS_NOTIFICATION_SCRIPT="${TEAMS_NOTIFICATION_SCRIPT:-${BENCHMARK_CI_DIR}/team_alert/send_teams_notification.py}"

# Validate model parameter
if [[ "$MODEL" != "grok" && "$MODEL" != "deepseek" && "$MODEL" != "DeepSeek-V3" ]]; then
    echo "[nightly] ERROR: Invalid --model value. Must be 'grok', 'deepseek', or 'DeepSeek-V3'."
    exit 1
fi

# Validate hardware parameter
if [[ "$HARDWARE_TYPE" != "mi30x" && "$HARDWARE_TYPE" != "mi35x" ]]; then
    echo "[nightly] ERROR: Invalid --hardware value. Must be 'mi30x' or 'mi35x'."
    exit 1
fi

# Set ROCM version based on hardware type
ROCM_VERSION="${ROCM_VERSIONS[$HARDWARE_TYPE]}"
echo "[nightly] Hardware: $HARDWARE_TYPE, ROCM Version: $ROCM_VERSION"

# Set model-specific variables
case "$MODEL" in
    "grok")
        MODEL_NAME="$GROK_MODEL_NAME"
        MODEL_VARIANT="$GROK_MODEL_VARIANT"
        OFFLINE_SCRIPT="$GROK_OFFLINE_SCRIPT"
        ONLINE_SCRIPT="$GROK_ONLINE_SCRIPT"
        ;;
    "deepseek"|"DeepSeek-V3")
        MODEL_NAME="$DEEPSEEK_MODEL_NAME"
        MODEL_VARIANT="$DEEPSEEK_MODEL_VARIANT"
        OFFLINE_SCRIPT="$DEEPSEEK_OFFLINE_SCRIPT"
        ONLINE_SCRIPT="$DEEPSEEK_ONLINE_SCRIPT"
        ;;
    *)
        echo "[nightly] ERROR: Invalid model '$MODEL'. Must be 'grok', 'deepseek', or 'DeepSeek-V3'."
        exit 1
        ;;
esac

# Determine modes to run based on user input
MODES_TO_RUN=""
if [[ "$MODE" == "all" || "$MODE" == "" ]]; then
    MODES_TO_RUN="online offline"
elif [[ "$MODE" == "offline" ]]; then
    MODES_TO_RUN="offline"
elif [[ "$MODE" == "online" ]]; then
    MODES_TO_RUN="online"
else
    echo "[nightly] ERROR: Invalid --mode value. Must be 'offline', 'online', or 'all'."
    exit 1
fi

echo "[nightly] Model: $MODEL, Mode(s): $MODES_TO_RUN"

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
  local repo="$1" target_date="$2"
  local next_url="https://hub.docker.com/v2/repositories/${repo}/tags/?page_size=100"
  local use_jq=$(command -v jq &> /dev/null && echo "true" || echo "false")
  local search_pattern="-${ROCM_VERSION}-${HARDWARE_TYPE}-${target_date}"

  echo "[nightly] Searching for non-SRT ${HARDWARE_TYPE} image in '${repo}' for date ${target_date}..." >&2

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

# Try each day from today going back CONTINUE_RUN_DAYS
for offset in $(seq 0 $((CONTINUE_RUN_DAYS - 1))); do
  date_suffix=$(date_pst "$offset")
  candidate_tag=$(find_image_for_date "$IMAGE_REPO" "$date_suffix") || continue

  echo "[nightly] Found candidate tag for day -${offset}: ${candidate_tag}"
  echo "[nightly] Attempting to pull ${IMAGE_REPO}:${candidate_tag}..."

  if docker pull "${IMAGE_REPO}:${candidate_tag}" >/dev/null 2>&1; then
    SELECTED_TAGS+=("$candidate_tag")
    echo "[nightly] Successfully pulled image for date ${date_suffix}: ${IMAGE_REPO}:${candidate_tag}"
  else
    echo "[nightly] WARN: Failed to pull candidate tag ${candidate_tag}. It may be private or invalid."
  fi
done

if [[ ${#SELECTED_TAGS[@]} -eq 0 && "$CONTINUE_RUN_DAYS" -eq 1 ]]; then
  echo "[nightly] No image found for today. Checking yesterday as a fallback..."
  date_suffix=$(date_pst 1)
  candidate_tag=$(find_image_for_date "$IMAGE_REPO" "$date_suffix" || true)
  if [[ -n "$candidate_tag" ]]; then
    echo "[nightly] Fallback found candidate tag: ${candidate_tag}"
    echo "[nightly] Attempting to pull ${IMAGE_REPO}:${candidate_tag}..."
    if docker pull "${IMAGE_REPO}:${candidate_tag}" >/dev/null 2>&1; then
      SELECTED_TAGS+=("$candidate_tag")
      echo "[nightly] Successfully pulled fallback image for date ${date_suffix}: ${IMAGE_REPO}:${candidate_tag}"
    else
      echo "[nightly] WARN: Failed to pull fallback tag ${candidate_tag}. It may be private or invalid."
    fi
  else
    echo "[nightly] No fallback image found for yesterday either."
  fi
fi

[[ ${#SELECTED_TAGS[@]} -eq 0 ]] && {
  echo "[nightly] ERROR: Could not find and pull any valid non-SRT images for the last $CONTINUE_RUN_DAYS days."
  exit 1
}

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
  # Generate container name (replace special chars for Docker compatibility)
  CONTAINER_NAME="${MODEL}_${SELECTED_TAG//[:.]/_}"

  # Check if all runs for the specified modes are already complete for this image
  all_modes_complete=true
  if [[ -z "$MODES_TO_RUN" ]]; then
    all_modes_complete=false
  fi

  for mode_to_check in $MODES_TO_RUN; do
    # Define expected number of runs based on model and mode
    expected_runs=0
    if [[ "$mode_to_check" == "online" ]]; then
      if [[ "$MODEL" == "grok" || "$MODEL" == "deepseek" ]]; then
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
      BENCHMARK_OUTPUT_FOLDER="${BENCHMARK_CI_DIR}/online/${MODEL_NAME}/${SELECTED_TAG}_${MODEL_NAME}_${MODEL_VARIANT}_online"
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
    echo "[nightly] SKIP: All runs for all requested modes are complete for ${DOCKER_IMAGE}. Skipping to next tag."
    continue
  fi
  
  echo "[nightly] Using Docker image: $DOCKER_IMAGE"
  echo "[nightly] Container name: $CONTAINER_NAME"

###############################################################################
# 2.1. Ensure container is running
###############################################################################
if docker ps -a --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"; then
  echo "[nightly] Reusing container ${CONTAINER_NAME}"
  docker start "${CONTAINER_NAME}" >/dev/null || true

  # Check if benchmark CI directory is accessible inside the container
  if ! docker exec "${CONTAINER_NAME}" test -d "${BENCHMARK_CI_DIR}" 2>/dev/null; then
    echo "[nightly] Benchmark CI directory not accessible in existing container. Recreating container..."
    docker stop "${CONTAINER_NAME}" >/dev/null 2>&1
    docker rm "${CONTAINER_NAME}" >/dev/null 2>&1
  # Check if custom model path is accessible inside the container (if provided)
  elif [[ -n "$CLI_MODEL_PATH" ]] && ! docker exec "${CONTAINER_NAME}" test -d "${CLI_MODEL_PATH}" 2>/dev/null; then
    echo "[nightly] Custom model path not accessible in existing container. Recreating container..."
    docker stop "${CONTAINER_NAME}" >/dev/null 2>&1
    docker rm "${CONTAINER_NAME}" >/dev/null 2>&1
  fi
fi

# Create container if it doesn't exist or was removed due to validation failure
if ! docker ps -a --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"; then
  echo "[nightly] Creating container ${CONTAINER_NAME}"

  # Create mount arguments - always mount MOUNT_DIR, and also mount benchmark CI directory if different
  mount_args="-v ${MOUNT_DIR}:${MOUNT_DIR}"

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

  docker run -d --name "${CONTAINER_NAME}" \
    --shm-size "$CONTAINER_SHM_SIZE" --ipc=host --cap-add=SYS_PTRACE --network=host \
    --device=/dev/kfd --device=/dev/dri --security-opt seccomp=unconfined \
    ${mount_args} --group-add video --privileged \
    -w "$WORK_DIR" "${DOCKER_IMAGE}" tail -f /dev/null
fi

###############################################################################
# 3. Handle model download if needed
###############################################################################
# Check if model needs to be downloaded from HuggingFace
if [[ -z "$CLI_MODEL_PATH" ]]; then
  # Only attempt download if no custom model path is provided
  # Extract model name from HF repo for directory naming
  MODEL_DIR_NAME=$(basename "$HF_MODEL_REPO")

  # Use the benchmark CI directory (work-dir) for model storage when provided
  if [[ -n "$CLI_WORK_DIR" ]]; then
    DOWNLOADED_MODEL_PATH="${BENCHMARK_CI_DIR}/models/${MODEL_DIR_NAME}"
  else
    DOWNLOADED_MODEL_PATH="${WORK_DIR}/models/${MODEL_DIR_NAME}"
  fi

  # Check if model directory exists and is not empty
  if ! docker exec "${CONTAINER_NAME}" test -d "${DOWNLOADED_MODEL_PATH}" 2>/dev/null || \
     [[ -z "$(docker exec "${CONTAINER_NAME}" ls -A "${DOWNLOADED_MODEL_PATH}" 2>/dev/null)" ]]; then

    echo "[nightly] Model not found at ${DOWNLOADED_MODEL_PATH}, attempting download from HuggingFace..."

    # Ensure models directory exists - create it locally first, then in container if needed
    if [[ -n "$CLI_WORK_DIR" ]]; then
      mkdir -p "${BENCHMARK_CI_DIR}/models"
      docker exec "${CONTAINER_NAME}" mkdir -p "${BENCHMARK_CI_DIR}/models"
    else
      docker exec "${CONTAINER_NAME}" mkdir -p "${WORK_DIR}/models"
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
fi

###############################################################################
# 4. Run benchmarks for each mode
###############################################################################
ONLINE_SUCCEEDED=false
RUNNING_ALL_MODES=false

# Check if we're running both online and offline (all mode)
if [[ "$MODES_TO_RUN" == "online offline" ]]; then
    RUNNING_ALL_MODES=true
fi

for MODE_TO_RUN in $MODES_TO_RUN; do
  # Note: Offline benchmark now handles GSM8K checking internally, so we always attempt it

  echo "[nightly] === Starting nightly ${MODEL^^} ${MODE_TO_RUN} benchmark ==="

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
  
  # Add custom work directory if provided
  if [[ -n "$CLI_WORK_DIR" ]]; then
    SCRIPT_ARGS="${SCRIPT_ARGS} --work-dir='${CLI_WORK_DIR}'"
  fi

  # Add custom model path if provided
  if [[ -n "$CLI_MODEL_PATH" ]]; then
    if [[ "$MODEL" == "deepseek" ]]; then
      SCRIPT_ARGS="${SCRIPT_ARGS} --model='${CLI_MODEL_PATH}'"
    else
      # For Grok
      SCRIPT_ARGS="${SCRIPT_ARGS} --model='${CLI_MODEL_PATH}'"
    fi
  fi

  if [[ "$MODEL" == "deepseek" ]]; then
    # For DeepSeek, pass additional parameters if needed
    docker exec \
      -e INSIDE_CONTAINER=1 \
      -e LATEST_TAG="${SELECTED_TAG}" \
      -e FULL_IMAGE="${DOCKER_IMAGE}" \
      -e TZ='America/Los_Angeles' \
      "${CONTAINER_NAME}" \
      bash -c "'$SCRIPT' $SCRIPT_ARGS" || BENCHMARK_EXIT_CODE=$?
  else
    # For Grok, use the existing command structure
    docker exec \
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

  # Process CSV and Generate Plots (Combined)
  if [ "$MODE_TO_RUN" == "offline" ]; then
    # Construct the path to the log folder for offline benchmarks
    BENCHMARK_OUTPUT_FOLDER="${OFFLINE_OUTPUT_DIR}/${MODEL_NAME}/${SELECTED_TAG}_${MODEL_NAME}_${MODEL_VARIANT}_offline"

    COMBINED_LOG_FILE="${BENCHMARK_OUTPUT_FOLDER}/process_and_generate_offline_plots.log"

    echo "[nightly] Processing offline CSV data and generating plots... Logs will be saved to ${COMBINED_LOG_FILE}"

    # Build Python script arguments with custom directories when work-dir is provided
    PYTHON_ARGS="--model '${MODEL}'"
    if [[ -n "$CLI_WORK_DIR" ]]; then
      PYTHON_ARGS="${PYTHON_ARGS} --base-dir '${BENCHMARK_CI_DIR}'"
      PYTHON_ARGS="${PYTHON_ARGS} --data-dir '${BENCHMARK_CI_DIR}/offline/${MODEL_NAME}'"
      PYTHON_ARGS="${PYTHON_ARGS} --plot-dir '${BENCHMARK_CI_DIR}/plots_server/${MODEL_NAME}/offline'"
    fi

    # Ensure log directory exists before redirecting output
    docker exec "${CONTAINER_NAME}" mkdir -p "$(dirname '${COMBINED_LOG_FILE}')"

    docker exec \
      -e INSIDE_CONTAINER=1 \
      "${CONTAINER_NAME}" \
      bash -c "pip install pandas matplotlib > /dev/null 2>&1 && \
               python3 '${PROCESS_AND_GENERATE_OFFLINE_PLOTS_SCRIPT}' ${PYTHON_ARGS} > '${COMBINED_LOG_FILE}' 2>&1"

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
    # Determine the correct directory name for DeepSeek-V3
    if [[ "$MODEL" == "DeepSeek-V3" ]]; then
      DIRECTORY_NAME="DeepSeek-V3"
    else
      DIRECTORY_NAME="${MODEL_NAME}"
    fi
    
    if [[ -n "$CLI_WORK_DIR" ]]; then
      PYTHON_ARGS="${PYTHON_ARGS} --base-dir '${BENCHMARK_CI_DIR}'"
      PYTHON_ARGS="${PYTHON_ARGS} --data-dir '${BENCHMARK_CI_DIR}/online/${DIRECTORY_NAME}'"
      PYTHON_ARGS="${PYTHON_ARGS} --plot-dir '${BENCHMARK_CI_DIR}/plots_server'"
      # Use custom work directory for log file as well
      BENCHMARK_OUTPUT_FOLDER="${BENCHMARK_CI_DIR}/online/${DIRECTORY_NAME}/${SELECTED_TAG}_${MODEL_NAME}_${MODEL_VARIANT}_online"
    else
      # Use default directories
      BENCHMARK_OUTPUT_FOLDER="${ONLINE_OUTPUT_DIR}/${DIRECTORY_NAME}/${SELECTED_TAG}_${MODEL_NAME}_${MODEL_VARIANT}_online"
    fi

    # Ensure log directory exists before redirecting output
    docker exec "${CONTAINER_NAME}" mkdir -p "$(dirname '${COMBINED_LOG_FILE}')"

    docker exec \
      -e INSIDE_CONTAINER=1 \
      "${CONTAINER_NAME}" \
      bash -c "pip install pandas matplotlib > /dev/null 2>&1 && \
               python3 '${PROCESS_AND_GENERATE_ONLINE_PLOTS_SCRIPT}' ${PYTHON_ARGS} > '${COMBINED_LOG_FILE}' 2>&1"

    # Send Teams notification for online plots
    send_teams_notification "${MODEL}" "online"
  fi

  echo "[nightly] === ${MODE_TO_RUN^} benchmark dispatched for ${MODEL^^}; check logs in ${CONTAINER_NAME} ==="
done

  echo "[nightly] =========================================="
  echo "[nightly] Completed benchmarks for image: ${IMAGE_REPO}:${SELECTED_TAG}"
  echo "[nightly] Stopping container ${CONTAINER_NAME} to release resources..."
  docker stop "${CONTAINER_NAME}" >/dev/null 2>&1 || true
  echo "[nightly] =========================================="
done

echo "[nightly] =========================================="
echo "[nightly] All benchmarks completed for ${#SELECTED_TAGS[@]} image(s)"
echo "[nightly] =========================================="
