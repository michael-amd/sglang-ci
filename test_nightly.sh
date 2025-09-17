#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# test_nightly.sh - SGL Automated Nightly Unit Test Runner
#
# DESCRIPTION:
#   Automatically discovers, pulls, and runs unit tests on the latest Docker images
#   for SGL with support for mi30x and mi35x hardware variants.
#
# IMAGE DISCOVERY:
#   • Uses Docker Hub API with pagination to find images
#   • Searches for non-SRT images from today, then yesterday
#   • Supports mi30x and mi35x hardware variants
#   • Examples:
#     - rocm/sgl-dev:v0.5.2rc1-rocm630-mi30x-20250903
#     - rocm/sgl-dev:v0.5.2rc1-rocm700-mi35x-20250903
#   • Automatically excludes SRT variants (ends with -srt)
#
# UNIT TESTS:
#   • Runs test_custom_allreduce unit test on 8 GPUs
#   • Uses CUDA_VISIBLE_DEVICES=0,1,2,3,4,5,6,7
#   • Executes in /sgl-workspace/sglang/test/srt directory
#   • Logs output to structured directory with image name and timestamp
#
# USAGE:
#   test_nightly.sh [OPTIONS]
#
# OPTIONS:
#   --hardware=HW            Hardware type: mi30x, mi35x [default: mi30x]
#   --teams-webhook-url=URL  Teams webhook URL for test result notifications
#   --help, -h               Show detailed help message
#
# EXAMPLES:
#   test_nightly.sh                          # Test latest mi30x image
#   test_nightly.sh --hardware=mi35x         # Test latest mi35x image
# ---------------------------------------------------------------------------
set -euo pipefail

###############################################################################
# Command and execution logging
###############################################################################
echo "[test] =========================================="
echo "[test] SGL Nightly Unit Test Started"
echo "[test] =========================================="
echo "[test] Command: $0 $*"
echo "[test] Start time: $(date '+%Y-%m-%d %H:%M:%S %Z')"
echo "[test] Machine: $(hostname)"
echo "[test] Working directory: $(pwd)"
echo "[test] Script location: $(realpath "$0")"
echo "[test] Process ID: $$"

# Check for already running instances
LOCKFILE="/tmp/test_nightly.lock"
if [ -f "$LOCKFILE" ]; then
    EXISTING_PID=$(cat "$LOCKFILE" 2>/dev/null)
    if [ -n "$EXISTING_PID" ] && kill -0 "$EXISTING_PID" 2>/dev/null; then
        echo "[test] ERROR: Another instance is already running (PID: $EXISTING_PID)"
        echo "[test] If this is incorrect, remove $LOCKFILE and try again"
        exit 1
    else
        echo "[test] Removing stale lock file from PID $EXISTING_PID"
        rm -f "$LOCKFILE"
    fi
fi

# Create lock file
echo "$$" > "$LOCKFILE"
echo "[test] Created process lock: $LOCKFILE"

# Cleanup function
cleanup() {
    echo "[test] Cleaning up process lock..."
    rm -f "$LOCKFILE"
}
trap cleanup EXIT

echo "[test] =========================================="
echo ""

###############################################################################
# Configuration Variables
###############################################################################

# Base paths and directories
TEST_CI_DIR="${TEST_CI_DIR:-$(pwd)}"
MOUNT_DIR="${MOUNT_DIR:-/mnt/raid/michael/sglang-ci}"
WORK_DIR="${WORK_DIR:-/sgl-workspace}"

# Docker configuration
IMAGE_REPO="${IMAGE_REPO:-rocm/sgl-dev}"
CONTAINER_SHM_SIZE="${CONTAINER_SHM_SIZE:-32g}"

# Hardware configuration
HARDWARE_TYPE="${HARDWARE_TYPE:-mi30x}"

# Teams notification configuration
TEAMS_WEBHOOK_URL="${TEAMS_WEBHOOK_URL:-}"

# ROCM version mapping based on hardware
declare -A ROCM_VERSIONS=(
  ["mi30x"]="rocm630"
  ["mi35x"]="rocm700"
)

# GPU monitoring thresholds
GPU_USAGE_THRESHOLD="${GPU_USAGE_THRESHOLD:-15}"
VRAM_USAGE_THRESHOLD="${VRAM_USAGE_THRESHOLD:-15}"
GPU_IDLE_WAIT_TIME="${GPU_IDLE_WAIT_TIME:-15}"

# Timezone for date calculations (San Francisco time)
TIME_ZONE="${TIME_ZONE:-America/Los_Angeles}"

# Test configuration
TEST_DIR="${TEST_DIR:-/sgl-workspace/sglang/test/srt}"
TEST_LOG_BASE_DIR="${TEST_LOG_BASE_DIR:-${MOUNT_DIR}/test/unit-test-backend-8-gpu-CAR-amd}"
TEST_COMMAND="CUDA_VISIBLE_DEVICES=0,1,2,3,4,5,6,7 python3 -m unittest test_custom_allreduce.TestCustomAllReduce"

###############################################################################
# GPU idle check function
###############################################################################
check_gpu_idle() {
  if ! command -v rocm-smi &> /dev/null; then
    echo "[test] WARN: rocm-smi not found. Skipping GPU idle check."
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
    echo "[test] GPU is busy. Attempting to stop running Docker containers..."
    # Stop all running containers, ignoring errors if some are already stopped.
    if "${DOCKER_CMD[@]}" info >/dev/null 2>&1; then
      running_ids="$("${DOCKER_CMD[@]}" ps -q 2>/dev/null || true)"
      if [[ -n "$running_ids" ]]; then
        echo "[test] Stopping running containers: $(echo "$running_ids" | tr '\\n' ' ')"
        "${DOCKER_CMD[@]}" stop $running_ids >/dev/null 2>&1 || true
      else
        echo "[test] No running containers to stop."
      fi
    else
      echo "[test] WARN: Docker not accessible; skipping container stop."
      echo "[test] Docker error details:"
      "${DOCKER_CMD[@]}" info 2>&1 | sed 's/^/[test]   /' || true
    fi
    echo "[test] Waiting ${GPU_IDLE_WAIT_TIME}s for GPU to become idle..."
    sleep "$GPU_IDLE_WAIT_TIME"
  fi

  if check_gpu_idle; then
      echo "[test] GPU is idle. Proceeding..."
  else
      echo "[test] WARN: GPU may still be busy, but proceeding as requested."
  fi
}

###############################################################################
# Environment validation and CLI flags
###############################################################################

# Basic environment validation for cron issues
echo "[test] Runtime Environment:"
echo "[test] User: $(whoami) | Groups: $(id -nG | cut -d' ' -f1-3)... | Docker: $(which docker 2>/dev/null || echo 'not in PATH')"

# Validate Docker access early
DOCKER_CMD=(sudo /usr/bin/docker)  # Use absolute path for cron compatibility
if [[ ! -x "/usr/bin/docker" ]]; then
    echo "[test] ERROR: Docker executable not found at /usr/bin/docker"
    exit 1
fi

echo "[test] Testing Docker daemon access..."
if ! "${DOCKER_CMD[@]}" info >/dev/null 2>&1; then
    echo "[test] ERROR: Cannot access Docker daemon. Please check:"
    echo "[test]   1. Docker daemon is running"
    echo "[test]   2. Current user is in 'docker' group: sudo usermod -aG docker \$(whoami)"
    echo "[test]   3. If running via cron, ensure cron environment has docker group access"
    echo "[test] Docker info output:"
    "${DOCKER_CMD[@]}" info 2>&1 | sed 's/^/[test]   /'
    exit 1
fi
echo "[test] Docker daemon accessible - proceeding..."

###############################################################################
# Parse CLI flags
###############################################################################

for arg in "$@"; do
  case $arg in
    --hardware=*)
      HARDWARE_TYPE="${arg#*=}"
      ;;
    --teams-webhook-url=*)
      TEAMS_WEBHOOK_URL="${arg#*=}"
      ;;
    --help|-h)
      echo "Usage: $0 [OPTIONS]"
      echo ""
      echo "Run SGL nightly unit tests for test_custom_allreduce"
      echo ""
      echo "Options:"
      echo "  --hardware=HW                    Hardware type (mi30x, mi35x) [default: mi30x]"
      echo "  --teams-webhook-url=URL          Teams webhook URL for test result notifications"
      echo "  --help, -h                       Show this help message"
      echo ""
      echo "Examples:"
      echo "  $0                              # Test latest mi30x image"
      echo "  $0 --hardware=mi35x             # Test latest mi35x image"
      echo ""
      echo "Test Details:"
      echo "  Unit Test: test_custom_allreduce.TestCustomAllReduce"
      echo "  Test Directory: /sgl-workspace/sglang/test/srt"
      echo "  GPUs Used: 0,1,2,3,4,5,6,7 (8 GPUs)"
      echo "  Log Directory: \${MOUNT_DIR}/test/unit-test-backend-8-gpu-CAR-amd/[image-name].log"
      exit 0 ;;
    *)
      echo "Unknown argument: $arg"
      echo "Use --help for usage information"
      exit 1 ;;
  esac
done

# Validate hardware parameter
if [[ "$HARDWARE_TYPE" != "mi30x" && "$HARDWARE_TYPE" != "mi35x" ]]; then
    echo "[test] ERROR: Invalid --hardware value. Must be 'mi30x' or 'mi35x'."
    exit 1
fi

# Set ROCM version based on hardware type
ROCM_VERSION="${ROCM_VERSIONS[$HARDWARE_TYPE]}"
echo "[test] Hardware: $HARDWARE_TYPE, ROCM Version: $ROCM_VERSION"

###############################################################################
# Ensure GPU is idle before starting
###############################################################################
ensure_gpu_idle

###############################################################################
# Pick image tag based on date
###############################################################################
date_pst() { TZ="$TIME_ZONE" date -d "-$1 day" +%Y%m%d; }

# Find non-SRT Docker image for a specific date using Docker Hub API
find_image_for_date() {
  local repo="$1" target_date="$2"
  local next_url="https://hub.docker.com/v2/repositories/${repo}/tags/?page_size=100"
  local use_jq=$(command -v jq &> /dev/null && echo "true" || echo "false")
  local search_pattern="-${ROCM_VERSION}-${HARDWARE_TYPE}-${target_date}"

  echo "[test] Searching for non-SRT ${HARDWARE_TYPE} image in '${repo}' for date ${target_date}..." >&2

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

# Find and pull Docker image
echo "[test] Searching for latest non-SRT image..."

# Check curl availability once
if ! command -v curl &> /dev/null; then
  echo "[test] ERROR: curl is required but not found." >&2
  exit 1
fi

SELECTED_TAG=""

# Try today first
date_suffix=$(date_pst 0)
candidate_tag=$(find_image_for_date "$IMAGE_REPO" "$date_suffix") || true

if [[ -n "$candidate_tag" ]]; then
  echo "[test] Found candidate tag for today: ${candidate_tag}"
  echo "[test] Attempting to pull ${IMAGE_REPO}:${candidate_tag}..."
  if "${DOCKER_CMD[@]}" pull "${IMAGE_REPO}:${candidate_tag}" 2>&1; then
    SELECTED_TAG="$candidate_tag"
    echo "[test] Successfully pulled image for today: ${IMAGE_REPO}:${candidate_tag}"
  else
    echo "[test] WARN: Failed to pull today's tag ${candidate_tag}. Trying yesterday..."
  fi
fi

# If no image found for today, try yesterday as fallback
if [[ -z "$SELECTED_TAG" ]]; then
  echo "[test] No image found for today. Checking yesterday as a fallback..."
  date_suffix=$(date_pst 1)
  candidate_tag=$(find_image_for_date "$IMAGE_REPO" "$date_suffix" || true)
  if [[ -n "$candidate_tag" ]]; then
    echo "[test] Fallback found candidate tag: ${candidate_tag}"
    echo "[test] Attempting to pull ${IMAGE_REPO}:${candidate_tag}..."
    if "${DOCKER_CMD[@]}" pull "${IMAGE_REPO}:${candidate_tag}" 2>&1; then
      SELECTED_TAG="$candidate_tag"
      echo "[test] Successfully pulled fallback image for yesterday: ${IMAGE_REPO}:${candidate_tag}"
    else
      echo "[test] WARN: Failed to pull fallback tag ${candidate_tag}."
    fi
  else
    echo "[test] No fallback image found for yesterday either."
  fi
fi

if [[ -z "$SELECTED_TAG" ]]; then
  echo "[test] ERROR: Could not find and pull any valid non-SRT images for today or yesterday."
  exit 1
fi

echo "[test] Selected image to run tests on: ${IMAGE_REPO}:${SELECTED_TAG}"

###############################################################################
# Run tests on selected image
###############################################################################

echo ""
echo "[test] =========================================="
echo "[test] Starting unit tests for image: ${IMAGE_REPO}:${SELECTED_TAG}"
echo "[test] =========================================="

# Ensure GPU is idle before starting tests for this image
echo "[test] Checking GPU status before starting tests for ${IMAGE_REPO}:${SELECTED_TAG}..."
ensure_gpu_idle

DOCKER_IMAGE="${IMAGE_REPO}:${SELECTED_TAG}"
# Generate container name (replace special chars for Docker compatibility)
CONTAINER_NAME="test_${SELECTED_TAG//[:.]/_}"

echo "[test] Using Docker image: $DOCKER_IMAGE"
echo "[test] Container name: $CONTAINER_NAME"

###############################################################################
# Ensure container is running
###############################################################################
if "${DOCKER_CMD[@]}" ps -a --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"; then
  echo "[test] Reusing container ${CONTAINER_NAME}"
  "${DOCKER_CMD[@]}" start "${CONTAINER_NAME}" >/dev/null || true

  # Check if test directory is accessible inside the container
  if ! "${DOCKER_CMD[@]}" exec "${CONTAINER_NAME}" test -d "${TEST_DIR}" 2>/dev/null; then
    echo "[test] Test directory not accessible in existing container. Recreating container..."
    "${DOCKER_CMD[@]}" stop "${CONTAINER_NAME}" >/dev/null 2>&1
    "${DOCKER_CMD[@]}" rm "${CONTAINER_NAME}" >/dev/null 2>&1
  fi
fi

# Create container if it doesn't exist or was removed due to validation failure
if ! "${DOCKER_CMD[@]}" ps -a --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"; then
  echo "[test] Creating container ${CONTAINER_NAME}"

  # Create mount arguments - always mount MOUNT_DIR
  mount_args="-v ${MOUNT_DIR}:${MOUNT_DIR}"

  # If test CI directory is not under MOUNT_DIR, mount it separately
  if [[ "${TEST_CI_DIR}" != "${MOUNT_DIR}"* ]]; then
      echo "[test] Test CI directory ${TEST_CI_DIR} is not under ${MOUNT_DIR}, mounting separately..."
      mount_args="${mount_args} -v ${TEST_CI_DIR}:${TEST_CI_DIR}"
  fi

  "${DOCKER_CMD[@]}" run -d --name "${CONTAINER_NAME}" \
    --shm-size "$CONTAINER_SHM_SIZE" --ipc=host --cap-add=SYS_PTRACE --network=host \
    --device=/dev/kfd --device=/dev/dri --security-opt seccomp=unconfined \
    ${mount_args} --group-add video --privileged \
    -w "$WORK_DIR" "${DOCKER_IMAGE}" tail -f /dev/null
fi

###############################################################################
# Create log directory and run unit test
###############################################################################

# Create log file name with image tag only (will overwrite existing)
LOG_FILE="${TEST_LOG_BASE_DIR}/${SELECTED_TAG}.log"

# Ensure log directory exists both locally and in container
LOG_DIR=$(dirname "$LOG_FILE")
mkdir -p "$LOG_DIR"
"${DOCKER_CMD[@]}" exec "${CONTAINER_NAME}" mkdir -p "$LOG_DIR"

echo "[test] === Starting test_custom_allreduce unit test ==="
echo "[test] Test directory: ${TEST_DIR}"
echo "[test] Log file: ${LOG_FILE}"
echo "[test] Test command: ${TEST_COMMAND}"

# Execute the unit test and capture exit code
TEST_EXIT_CODE=0

echo "[test] Running unit test inside ${CONTAINER_NAME}..."

# Start logging with header information
{
  echo "=========================================="
  echo "SGL Unit Test Log"
  echo "=========================================="
  echo "Test: test_custom_allreduce.TestCustomAllReduce"
  echo "Image: ${DOCKER_IMAGE}"
  echo "Container: ${CONTAINER_NAME}"
  echo "Hardware: ${HARDWARE_TYPE}"
  echo "Start time: $(date '+%Y-%m-%d %H:%M:%S %Z')"
  echo "Test directory: ${TEST_DIR}"
  echo "Command: ${TEST_COMMAND}"
  echo "=========================================="
  echo ""
} > "$LOG_FILE"

# Run the test and append output to log file
"${DOCKER_CMD[@]}" exec \
  -e INSIDE_CONTAINER=1 \
  -e CUDA_VISIBLE_DEVICES=0,1,2,3,4,5,6,7 \
  "${CONTAINER_NAME}" \
  bash -c "cd '${TEST_DIR}' && ${TEST_COMMAND}" >> "$LOG_FILE" 2>&1 || TEST_EXIT_CODE=$?

# Add footer to log file
{
  echo ""
  echo "=========================================="
  echo "End time: $(date '+%Y-%m-%d %H:%M:%S %Z')"
  echo "Exit code: ${TEST_EXIT_CODE}"
  if [ $TEST_EXIT_CODE -eq 0 ]; then
    echo "Result: PASSED"
  else
    echo "Result: FAILED"
  fi
  echo "=========================================="
} >> "$LOG_FILE"

# Report results
if [ $TEST_EXIT_CODE -eq 0 ]; then
  echo "[test] === Unit test PASSED for ${DOCKER_IMAGE} ==="
  echo "[test] Log saved to: ${LOG_FILE}"
else
  echo "[test] === Unit test FAILED for ${DOCKER_IMAGE} (exit code: $TEST_EXIT_CODE) ==="
  echo "[test] Log saved to: ${LOG_FILE}"
  echo "[test] Check the log file for detailed error information"
fi

# Send Teams notification if webhook URL is provided
if [ -n "$TEAMS_WEBHOOK_URL" ]; then
  echo "[test] Sending Teams notification..."
  TEAMS_ALERT_SCRIPT="${TEST_CI_DIR}/team_alert/send_test_nightly_alert.py"

  if [ -f "$TEAMS_ALERT_SCRIPT" ]; then
    python3 "$TEAMS_ALERT_SCRIPT" \
      --webhook-url "$TEAMS_WEBHOOK_URL" \
      --log-file "$LOG_FILE" 2>/dev/null || {
        echo "[test] WARN: Failed to send Teams notification (script error)"
      }
  else
    echo "[test] WARN: Teams notification script not found at: $TEAMS_ALERT_SCRIPT"
  fi
else
  echo "[test] No Teams webhook URL provided - skipping notification"
fi

# Stop container to free up resources
echo "[test] Stopping container ${CONTAINER_NAME} to release resources..."
"${DOCKER_CMD[@]}" stop "${CONTAINER_NAME}" >/dev/null 2>&1 || true

echo "[test] =========================================="
echo "[test] Unit test completed for image: ${IMAGE_REPO}:${SELECTED_TAG}"
echo "[test] =========================================="
