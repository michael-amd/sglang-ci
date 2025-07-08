#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# grok_perf_nightly.sh
#   • Pull rocm/sgl-dev:$YYYYMMDD (today PST; fallback yesterday).
#   • Ensure container  sgl-dev_$TAG  is up with proper mounts.
#   • Invoke chosen benchmark script (offline or online) inside it,
#     forwarding --docker_image so the script knows which backend to use.
#
# USAGE:
#   bash grok_perf_nightly.sh                 # runs offline then online
#   bash grok_perf_nightly.sh --mode=offline  # run offline only
#   bash grok_perf_nightly.sh --mode=online   # run online only
#   bash grok_perf_nightly.sh --mode=all      # run offline then online
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
IMAGE_REPO="${IMAGE_REPO:-rocm/sgl-dev}"
CONTAINER_SHM_SIZE="${CONTAINER_SHM_SIZE:-32g}"

# Model configuration
MODEL_NAME="${MODEL_NAME:-GROK1}"
MODEL_VARIANT="${MODEL_VARIANT:-MOE-I4F8}"

# GPU monitoring thresholds
GPU_USAGE_THRESHOLD="${GPU_USAGE_THRESHOLD:-20}"
VRAM_USAGE_THRESHOLD="${VRAM_USAGE_THRESHOLD:-20}"
GPU_IDLE_WAIT_TIME="${GPU_IDLE_WAIT_TIME:-20}"

# Timezone for date calculations
TIME_ZONE="${TIME_ZONE:-America/Los_Angeles}"

# Script paths
OFFLINE_SCRIPT="${OFFLINE_SCRIPT:-${BENCHMARK_CI_DIR}/grok_perf_offline_csv.sh}"
ONLINE_SCRIPT="${ONLINE_SCRIPT:-${BENCHMARK_CI_DIR}/grok_perf_online_csv.sh}"

# Python scripts for processing
PROCESS_OFFLINE_CSV_SCRIPT="${PROCESS_OFFLINE_CSV_SCRIPT:-${BENCHMARK_CI_DIR}/process_offline_csv.py}"
GENERATE_OFFLINE_PLOTS_SCRIPT="${GENERATE_OFFLINE_PLOTS_SCRIPT:-${BENCHMARK_CI_DIR}/generate_offline_plots.py}"
PROCESS_ONLINE_CSV_SCRIPT="${PROCESS_ONLINE_CSV_SCRIPT:-${BENCHMARK_CI_DIR}/process_online_csv.py}"
GENERATE_ONLINE_PLOTS_SCRIPT="${GENERATE_ONLINE_PLOTS_SCRIPT:-${BENCHMARK_CI_DIR}/generate_online_plots.py}"

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
# 0. Parse CLI flags
###############################################################################
MODE="all" # Default to run both offline and online
for arg in "$@"; do
  case $arg in
    --mode=*)
      MODE="${arg#*=}"
      shift ;;
  esac
done

MODES_TO_RUN=""
if [[ "$MODE" == "all" || "$MODE" == "" ]]; then
    MODES_TO_RUN="offline online"
elif [[ "$MODE" == "offline" ]]; then
    MODES_TO_RUN="offline"
elif [[ "$MODE" == "online" ]]; then
    MODES_TO_RUN="online"
else
    echo "[nightly] ERROR: Invalid --mode value. Must be 'offline', 'online', or 'all'."
    exit 1
fi

###############################################################################
# 1. Ensure GPU is idle before starting
###############################################################################
ensure_gpu_idle

###############################################################################
# 2. Pick image tag (PST date)
###############################################################################
date_pst() { TZ="$TIME_ZONE" date -d "-$1 day" +%Y%m%d; }

SELECTED_TAG=""
for offset in 0 1; do
  tag=$(date_pst "$offset")
  echo "[nightly] Trying ${IMAGE_REPO}:${tag} ..."
  if docker pull "${IMAGE_REPO}:${tag}" >/dev/null 2>&1; then
    SELECTED_TAG="$tag"; break
  fi
done
[[ -n "$SELECTED_TAG" ]] || { echo "[nightly] No nightly image found"; exit 1; }

DOCKER_IMAGE="${IMAGE_REPO}:${SELECTED_TAG}"
CONTAINER_NAME="sgl-dev_${SELECTED_TAG}"

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
for MODE in $MODES_TO_RUN; do
  echo "[nightly] === Starting nightly Grok-1 ${MODE} benchmark ==="

  # Determine which benchmark script to run
  if [ "$MODE" == "offline" ]; then
    SCRIPT="$OFFLINE_SCRIPT"
  else
    SCRIPT="$ONLINE_SCRIPT"
  fi
  echo "[nightly] Launching $(basename "$SCRIPT") inside ${CONTAINER_NAME}"

  # Note: The LATEST_TAG env var helps the scripts identify this as a non-RC build
  docker exec \
    -e INSIDE_CONTAINER=1 \
    -e LATEST_TAG="${SELECTED_TAG}" \
    -e FULL_IMAGE="${DOCKER_IMAGE}" \
    "${CONTAINER_NAME}" \
    bash "$SCRIPT" --docker_image="${DOCKER_IMAGE}"

  # Process CSV and Generate Plots
  if [ "$MODE" == "offline" ]; then
    # Construct the path to the log folder, similar to grok_perf_offline_csv.sh
    BENCHMARK_OUTPUT_FOLDER="${OFFLINE_OUTPUT_DIR}/${MODEL_NAME}/${SELECTED_TAG}_${MODEL_NAME}_${MODEL_VARIANT}_offline"

    PROCESS_CSV_LOG_FILE="${BENCHMARK_OUTPUT_FOLDER}/process_offline_csv.log"
    GENERATE_PLOTS_LOG_FILE="${BENCHMARK_OUTPUT_FOLDER}/generate_offline_plots.log"

    echo "[nightly] Processing offline CSV data... Logs will be saved to ${PROCESS_CSV_LOG_FILE}"
    docker exec \
      -e INSIDE_CONTAINER=1 \
      "${CONTAINER_NAME}" \
      bash -c "pip install pandas matplotlib > /dev/null 2>&1 && python3 '${PROCESS_OFFLINE_CSV_SCRIPT}' > '${PROCESS_CSV_LOG_FILE}' 2>&1"

    echo "[nightly] Generating offline plots... Logs will be saved to ${GENERATE_PLOTS_LOG_FILE}"
    docker exec \
      -e INSIDE_CONTAINER=1 \
      "${CONTAINER_NAME}" \
      bash -c "pip install pandas matplotlib > /dev/null 2>&1 && python3 '${GENERATE_OFFLINE_PLOTS_SCRIPT}' > '${GENERATE_PLOTS_LOG_FILE}' 2>&1"
  fi

  if [ "$MODE" == "online" ]; then
    # Construct the path to the log folder, similar to grok_perf_online_csv.sh
    BENCHMARK_OUTPUT_FOLDER="${ONLINE_OUTPUT_DIR}/${MODEL_NAME}/${SELECTED_TAG}_${MODEL_NAME}_${MODEL_VARIANT}_online"

    PROCESS_CSV_LOG_FILE="${BENCHMARK_OUTPUT_FOLDER}/process_online_csv.log"
    GENERATE_PLOTS_LOG_FILE="${BENCHMARK_OUTPUT_FOLDER}/generate_online_plots.log"

    echo "[nightly] Processing online CSV data... Logs will be saved to ${PROCESS_CSV_LOG_FILE}"
    docker exec \
      -e INSIDE_CONTAINER=1 \
      "${CONTAINER_NAME}" \
      bash -c "pip install pandas matplotlib > /dev/null 2>&1 && python3 '${PROCESS_ONLINE_CSV_SCRIPT}' > '${PROCESS_CSV_LOG_FILE}' 2>&1"

    echo "[nightly] Generating online plots... Logs will be saved to ${GENERATE_PLOTS_LOG_FILE}"
    docker exec \
      -e INSIDE_CONTAINER=1 \
      "${CONTAINER_NAME}" \
      bash -c "pip install pandas matplotlib > /dev/null 2>&1 && python3 '${GENERATE_ONLINE_PLOTS_SCRIPT}' > '${GENERATE_PLOTS_LOG_FILE}' 2>&1"
  fi

  echo "[nightly] === ${MODE^} benchmark dispatched; check logs in ${CONTAINER_NAME} ==="
done
