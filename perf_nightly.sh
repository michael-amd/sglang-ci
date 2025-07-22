#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# perf_nightly.sh
#   • Pull appropriate Docker image based on model type
#   • Ensure container is up with proper mounts
#   • Invoke chosen benchmark script (offline or online) inside it,
#     forwarding --docker_image so the script knows which backend to use.
#
# USAGE:
#   bash perf_nightly.sh                              # runs grok online first then offline, if online GSM8K benchmark failed or accuracy below threshold. no need to run offline.
#   bash perf_nightly.sh --mode=offline               # run grok offline only
#   bash perf_nightly.sh --mode=online                # run grok online only
#   bash perf_nightly.sh --model=deepseek             # run deepseek online only
#   bash perf_nightly.sh --model=deepseek --mode=all  # run deepseek online first then offline, with same GSM8K gating logic
#   bash perf_nightly.sh --model=grok --mode=all      # run grok online first then offline, with GSM8K gating logic
# ---------------------------------------------------------------------------
set -euo pipefail

###############################################################################
# Configuration Variables - Override via environment variables if needed
###############################################################################

# Base paths and directories
BENCHMARK_CI_DIR="${BENCHMARK_CI_DIR:-/mnt/raid/michael/sgl_benchmark_ci}"
MOUNT_DIR="${MOUNT_DIR:-/mnt/raid/}"
WORK_DIR="${WORK_DIR:-/sgl-workspace}"

# Docker configuration
GROK_IMAGE_REPO="${GROK_IMAGE_REPO:-rocm/sgl-dev}"
DEEPSEEK_IMAGE_REPO="${DEEPSEEK_IMAGE_REPO:-rocm/sgl-dev}"
DEEPSEEK_IMAGE_TAG="${DEEPSEEK_IMAGE_TAG:-v0.4.8-rocm630}"
CONTAINER_SHM_SIZE="${CONTAINER_SHM_SIZE:-32g}"

# New Docker image format configuration
# Format: v{VERSION}-rocm{ROCM_VERSION}-{HARDWARE}-{DATE}
# Example: v0.4.9.post2-rocm630-mi30x-20250715
IMAGE_VERSION="${IMAGE_VERSION:-v0.4.9.post2}"
ROCM_VERSION="${ROCM_VERSION:-rocm630}"
HARDWARE_IDENTIFIER="${HARDWARE_IDENTIFIER:-mi30x}"

# Model configuration - will be set based on --model parameter
GROK_MODEL_NAME="${GROK_MODEL_NAME:-GROK1}"
GROK_MODEL_VARIANT="${GROK_MODEL_VARIANT:-MOE-I4F8}"
DEEPSEEK_MODEL_NAME="${DEEPSEEK_MODEL_NAME:-DeepSeek-V3-0324}"
DEEPSEEK_MODEL_VARIANT="${DEEPSEEK_MODEL_VARIANT:-FP8}"

# GPU monitoring thresholds
GPU_USAGE_THRESHOLD="${GPU_USAGE_THRESHOLD:-20}"
VRAM_USAGE_THRESHOLD="${VRAM_USAGE_THRESHOLD:-20}"
GPU_IDLE_WAIT_TIME="${GPU_IDLE_WAIT_TIME:-20}"

# Timezone for date calculations
TIME_ZONE="${TIME_ZONE:-America/Los_Angeles}"

# Script paths - will be set based on model type
GROK_OFFLINE_SCRIPT="${GROK_OFFLINE_SCRIPT:-${BENCHMARK_CI_DIR}/grok_perf_offline_csv.sh}"
GROK_ONLINE_SCRIPT="${GROK_ONLINE_SCRIPT:-${BENCHMARK_CI_DIR}/grok_perf_online_csv.sh}"
DEEPSEEK_OFFLINE_SCRIPT="${DEEPSEEK_OFFLINE_SCRIPT:-${BENCHMARK_CI_DIR}/deepseek_perf_offline_csv.sh}"
DEEPSEEK_ONLINE_SCRIPT="${DEEPSEEK_ONLINE_SCRIPT:-${BENCHMARK_CI_DIR}/deepseek_perf_online_csv.sh}"

# Python scripts for processing and plotting (combined)
PROCESS_AND_GENERATE_OFFLINE_PLOTS_SCRIPT="${PROCESS_AND_GENERATE_OFFLINE_PLOTS_SCRIPT:-${BENCHMARK_CI_DIR}/process_and_generate_offline_plots.py}"
PROCESS_AND_GENERATE_ONLINE_PLOTS_SCRIPT="${PROCESS_AND_GENERATE_ONLINE_PLOTS_SCRIPT:-${BENCHMARK_CI_DIR}/process_and_generate_online_plots.py}"

# Teams notification script
TEAMS_NOTIFICATION_SCRIPT="${TEAMS_NOTIFICATION_SCRIPT:-${BENCHMARK_CI_DIR}/send_teams_notification.py}"

# Teams configuration
TEAMS_WEBHOOK_URL="${TEAMS_WEBHOOK_URL:-}"
ENABLE_TEAMS_NOTIFICATIONS="${ENABLE_TEAMS_NOTIFICATIONS:-true}"
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
    if [[ -n "$(docker ps -q)" ]]; then
        echo "[nightly] Stopping running containers: $(docker ps -q | tr '\\n' ' ')"
        docker stop $(docker ps -q) >/dev/null 2>&1 || true
    else
        echo "[nightly] No running containers to stop."
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
    echo "[nightly] WARN: Teams webhook URL not configured (TEAMS_WEBHOOK_URL empty)"
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
  docker exec \
    -e INSIDE_CONTAINER=1 \
    -e TEAMS_WEBHOOK_URL="${TEAMS_WEBHOOK_URL}" \
    -e PLOT_SERVER_HOST="${PLOT_SERVER_HOST}" \
    -e PLOT_SERVER_PORT="${PLOT_SERVER_PORT}" \
    -e PLOT_SERVER_BASE_URL="${PLOT_SERVER_BASE_URL}" \
    "${CONTAINER_NAME}" \
    bash -c "pip install requests > /dev/null 2>&1 && \
             python3 '${TEAMS_NOTIFICATION_SCRIPT}' --model '${model}' --mode '${mode}'" || TEAMS_EXIT_CODE=$?

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

for arg in "$@"; do
  case $arg in
    --mode=*)
      MODE="${arg#*=}"
      shift ;;
    --model=*)
      MODEL="${arg#*=}"
      shift ;;
  esac
done

# Validate model parameter
if [[ "$MODEL" != "grok" && "$MODEL" != "deepseek" ]]; then
    echo "[nightly] ERROR: Invalid --model value. Must be 'grok' or 'deepseek'."
    exit 1
fi

# Set model-specific variables
if [[ "$MODEL" == "grok" ]]; then
    IMAGE_REPO="$GROK_IMAGE_REPO"
    MODEL_NAME="$GROK_MODEL_NAME"
    MODEL_VARIANT="$GROK_MODEL_VARIANT"
    OFFLINE_SCRIPT="$GROK_OFFLINE_SCRIPT"
    ONLINE_SCRIPT="$GROK_ONLINE_SCRIPT"
    USE_DATED_TAG=true
elif [[ "$MODEL" == "deepseek" ]]; then
    IMAGE_REPO="$DEEPSEEK_IMAGE_REPO"
    MODEL_NAME="$DEEPSEEK_MODEL_NAME"
    MODEL_VARIANT="$DEEPSEEK_MODEL_VARIANT"
    OFFLINE_SCRIPT="$DEEPSEEK_OFFLINE_SCRIPT"
    ONLINE_SCRIPT="$DEEPSEEK_ONLINE_SCRIPT"
    USE_DATED_TAG=true
fi

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

SELECTED_TAG=""
if [[ "$USE_DATED_TAG" == "true" ]]; then
  # Try dated tags (today and yesterday) with new format first, then fallback to old format
  for offset in 0 1; do
    date_suffix=$(date_pst "$offset")

    # Try new format first: v{VERSION}-rocm{ROCM_VERSION}-{HARDWARE}-{DATE}
    # Example: v0.4.9.post2-rocm630-mi30x-20250715
    new_format_tag="${IMAGE_VERSION}-${ROCM_VERSION}-${HARDWARE_IDENTIFIER}-${date_suffix}"
    echo "[nightly] Trying new format ${IMAGE_REPO}:${new_format_tag} ..."
    if docker pull "${IMAGE_REPO}:${new_format_tag}" >/dev/null 2>&1; then
      SELECTED_TAG="$new_format_tag"
      break
    fi

    # Fallback to old format: just the date (YYYYMMDD)
    echo "[nightly] Trying old format ${IMAGE_REPO}:${date_suffix} ..."
    if docker pull "${IMAGE_REPO}:${date_suffix}" >/dev/null 2>&1; then
      SELECTED_TAG="$date_suffix"
      break
    fi
  done
  [[ -n "$SELECTED_TAG" ]] || { echo "[nightly] No nightly image found for $MODEL (tried both new and old formats)"; exit 1; }
else
  # This branch is currently unused as both models use dated tags
  SELECTED_TAG="$FIXED_TAG"
  echo "[nightly] Using fixed tag ${IMAGE_REPO}:${SELECTED_TAG} for $MODEL ..."
  if ! docker pull "${IMAGE_REPO}:${SELECTED_TAG}" >/dev/null 2>&1; then
    echo "[nightly] Failed to pull ${IMAGE_REPO}:${SELECTED_TAG}"
    exit 1
  fi
fi

DOCKER_IMAGE="${IMAGE_REPO}:${SELECTED_TAG}"
if [[ "$MODEL" == "grok" ]]; then
    CONTAINER_NAME="sgl-dev_${SELECTED_TAG}"
else
    CONTAINER_NAME="${MODEL}_${SELECTED_TAG//[:.]/_}"  # Replace : and . with _ for valid container name
fi

echo "[nightly] Using Docker image: $DOCKER_IMAGE"
echo "[nightly] Container name: $CONTAINER_NAME"

###############################################################################
# 2. Ensure container is running
###############################################################################
if docker ps -a --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"; then
  echo "[nightly] Reusing container ${CONTAINER_NAME}"
  docker start "${CONTAINER_NAME}" >/dev/null || true
else
  echo "[nightly] Creating container ${CONTAINER_NAME}"
  docker run -d --name "${CONTAINER_NAME}" \
    --shm-size "$CONTAINER_SHM_SIZE" --ipc=host --cap-add=SYS_PTRACE --network=host \
    --device=/dev/kfd --device=/dev/dri --security-opt seccomp=unconfined \
    -v "${MOUNT_DIR}:${MOUNT_DIR}" --group-add video --privileged \
    -w "$WORK_DIR" "${DOCKER_IMAGE}" tail -f /dev/null
fi

###############################################################################
# 3. Run benchmarks for each mode
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
  if [[ "$MODEL" == "deepseek" ]]; then
    # For DeepSeek, pass additional parameters if needed
    docker exec \
      -e INSIDE_CONTAINER=1 \
      -e LATEST_TAG="${SELECTED_TAG}" \
      -e FULL_IMAGE="${DOCKER_IMAGE}" \
      -e TZ='America/Los_Angeles' \
      "${CONTAINER_NAME}" \
      bash "$SCRIPT" --docker_image="${DOCKER_IMAGE}" || BENCHMARK_EXIT_CODE=$?
  else
    # For Grok, use the existing command structure
    docker exec \
      -e INSIDE_CONTAINER=1 \
      -e LATEST_TAG="${SELECTED_TAG}" \
      -e FULL_IMAGE="${DOCKER_IMAGE}" \
      "${CONTAINER_NAME}" \
      bash "$SCRIPT" --docker_image="${DOCKER_IMAGE}" || BENCHMARK_EXIT_CODE=$?
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
    docker exec \
      -e INSIDE_CONTAINER=1 \
      "${CONTAINER_NAME}" \
      bash -c "pip install pandas matplotlib > /dev/null 2>&1 && \
               python3 '${PROCESS_AND_GENERATE_OFFLINE_PLOTS_SCRIPT}' --model '${MODEL}' > '${COMBINED_LOG_FILE}' 2>&1"

    # Send Teams notification for offline plots
    send_teams_notification "${MODEL}" "offline"
  fi

  if [ "$MODE_TO_RUN" == "online" ]; then
    # Construct the path to the log folder for online benchmarks
    BENCHMARK_OUTPUT_FOLDER="${ONLINE_OUTPUT_DIR}/${MODEL_NAME}/${SELECTED_TAG}_${MODEL_NAME}_${MODEL_VARIANT}_online"

    COMBINED_LOG_FILE="${BENCHMARK_OUTPUT_FOLDER}/process_and_generate_online_plots.log"

    echo "[nightly] Processing online CSV data and generating plots... Logs will be saved to ${COMBINED_LOG_FILE}"
    docker exec \
      -e INSIDE_CONTAINER=1 \
      "${CONTAINER_NAME}" \
      bash -c "pip install pandas matplotlib > /dev/null 2>&1 && \
               python3 '${PROCESS_AND_GENERATE_ONLINE_PLOTS_SCRIPT}' --model '${MODEL}' > '${COMBINED_LOG_FILE}' 2>&1"

    # Send Teams notification for online plots
    send_teams_notification "${MODEL}" "online"
  fi

  echo "[nightly] === ${MODE_TO_RUN^} benchmark dispatched for ${MODEL^^}; check logs in ${CONTAINER_NAME} ==="
done
