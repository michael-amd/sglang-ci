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
#     - rocm/sgl-dev:v0.5.5-rocm700-mi30x-20251110
#     - rocm/sgl-dev:v0.5.5-rocm700-mi35x-20251110
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
#   --test-type=TYPE         Test type: unit, pd [default: unit]
#   --image-date=YYYYMMDD    Specific date of Docker image to use [default: today, fallback to yesterday]
#   --teams-webhook-url=URL  Teams webhook URL for test result notifications
#   --help, -h               Show detailed help message
#
# EXAMPLES:
#   bash test_nightly.sh                          # Run unit test on latest mi30x image (today or yesterday)
#   bash test_nightly.sh --hardware=mi35x         # Run unit test on latest mi35x image
#   bash test_nightly.sh --test-type=pd           # Run PD disaggregation test on latest mi30x image
#   bash test_nightly.sh --image-date=20251020    # Run unit test on mi30x image from 20251020
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

# Test type configuration
TEST_TYPE="${TEST_TYPE:-unit}"

# Model path configuration (for PD tests)
MODEL_PATH_OVERRIDE="${MODEL_PATH_OVERRIDE:-}"

# Image date configuration (if not set, will use today/yesterday)
IMAGE_DATE="${IMAGE_DATE:-}"

# Teams notification configuration
TEAMS_WEBHOOK_URL="${TEAMS_WEBHOOK_URL:-}"

# ROCM version mapping based on hardware (for unit tests)
declare -A ROCM_VERSIONS=(
  ["mi30x"]="rocm700"
  ["mi35x"]="rocm700"
)

# Fallback ROCM versions if primary version not available
declare -A ROCM_FALLBACK_VERSIONS=(
  ["mi30x"]="rocm630"
  ["mi35x"]=""  # No fallback for mi35x
)

# ROCM version for PD tests (use rocm700 for both hardware types)
declare -A PD_ROCM_VERSIONS=(
  ["mi30x"]="rocm700"
  ["mi35x"]="rocm700"
)

# Fallback ROCM versions for PD tests
declare -A PD_ROCM_FALLBACK_VERSIONS=(
  ["mi30x"]="rocm630"
  ["mi35x"]=""  # No fallback for mi35x
)

# GPU monitoring thresholds
GPU_USAGE_THRESHOLD="${GPU_USAGE_THRESHOLD:-15}"
VRAM_USAGE_THRESHOLD="${VRAM_USAGE_THRESHOLD:-15}"
GPU_IDLE_WAIT_TIME="${GPU_IDLE_WAIT_TIME:-15}"

# Timezone for date calculations (San Francisco time)
TIME_ZONE="${TIME_ZONE:-America/Los_Angeles}"

# Test configuration (for unit tests)
TEST_DIR="${TEST_DIR:-/sgl-workspace/sglang/test/srt}"
TEST_LOG_BASE_DIR="${TEST_LOG_BASE_DIR:-${MOUNT_DIR}/test/unit-test-backend-8-gpu-CAR-amd}"
TEST_COMMAND="CUDA_VISIBLE_DEVICES=0,1,2,3,4,5,6,7 python3 -m unittest test_custom_allreduce.TestCustomAllReduce"

# PD test configuration
PD_TEST_DIR="${PD_TEST_DIR:-${MOUNT_DIR}/test/pd}"
PD_LOG_BASE_DIR="${PD_LOG_BASE_DIR:-${MOUNT_DIR}/test/pd/pd_log}"

# PD model paths by hardware type (can be overridden by PD_MODEL_PATH_OVERRIDE env var)
declare -A PD_MODEL_PATHS=(
  ["mi30x"]="/mnt/raid/models/huggingface/deepseek-ai/DeepSeek-V3-0324"
  ["mi35x"]="/mnt/raid/models/huggingface/amd/DeepSeek-R1-MXFP4-Preview"
)

declare -A PD_MODEL_NAMES=(
  ["mi30x"]="DeepSeek-V3-0324"
  ["mi35x"]="DeepSeek-R1-MXFP4-Preview"
)

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

check_conflicting_processes() {
  echo "[test] Checking for conflicting SGLang processes..."

  # Check for non-Docker SGLang processes
  local sglang_procs=$(ps aux | grep -E "python.*sglang\.(launch_server|bench_serving)" | grep -v grep || true)

  if [[ -n "$sglang_procs" ]]; then
    echo "[test] ERROR: Found running SGLang processes that may conflict with tests:"
    echo "$sglang_procs" | sed 's/^/[test]   /'
    echo "[test]"
    echo "[test] Please stop these processes before running tests:"
    echo "[test]   sudo pkill -f 'sglang.launch_server'"
    echo "[test]   sudo pkill -f 'sglang.bench_serving'"
    return 1
  fi

  echo "[test] No conflicting SGLang processes found."
  return 0
}

cleanup_docker_containers() {
  echo "[test] Cleaning up Docker containers..."

  if ! "${DOCKER_CMD[@]}" info >/dev/null 2>&1; then
    echo "[test] WARN: Docker not accessible; skipping container cleanup."
    return 1
  fi

  # Stop all running containers
  local running_ids="$("${DOCKER_CMD[@]}" ps -q 2>/dev/null || true)"
  if [[ -n "$running_ids" ]]; then
    echo "[test] Stopping running containers: $(echo "$running_ids" | tr '\\n' ' ')"
    "${DOCKER_CMD[@]}" stop $running_ids >/dev/null 2>&1 || true
  else
    echo "[test] No running containers to stop."
  fi

  # Remove stopped test containers to free up resources
  local stopped_test_containers="$("${DOCKER_CMD[@]}" ps -a --filter "name=sglang-pd-" --format "{{.Names}}" 2>/dev/null || true)"
  if [[ -n "$stopped_test_containers" ]]; then
    echo "[test] Removing stopped PD test containers: $(echo "$stopped_test_containers" | tr '\\n' ' ')"
    echo "$stopped_test_containers" | xargs -r "${DOCKER_CMD[@]}" rm -f >/dev/null 2>&1 || true
  fi

  echo "[test] Docker container cleanup complete."
  return 0
}

ensure_gpu_idle() {
  # Always clean up Docker containers first
  cleanup_docker_containers

  # Check for conflicting non-Docker processes
  if ! check_conflicting_processes; then
    echo "[test] ERROR: Cannot proceed with conflicting processes running."
    exit 1
  fi

  # Wait for GPU to become idle
  if ! check_gpu_idle; then
    echo "[test] GPU is still busy after cleanup. Waiting ${GPU_IDLE_WAIT_TIME}s for GPU to become idle..."
    sleep "$GPU_IDLE_WAIT_TIME"
  fi

  if check_gpu_idle; then
      echo "[test] GPU is idle. Proceeding..."
  else
      echo "[test] WARN: GPU may still be busy, but no conflicting processes found. Proceeding..."
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
    --test-type=*)
      TEST_TYPE="${arg#*=}"
      ;;
    --model-path=*)
      MODEL_PATH_OVERRIDE="${arg#*=}"
      ;;
    --image-date=*)
      IMAGE_DATE="${arg#*=}"
      ;;
    --teams-webhook-url=*)
      TEAMS_WEBHOOK_URL="${arg#*=}"
      ;;
    --help|-h)
      echo "Usage: $0 [OPTIONS]"
      echo ""
      echo "Run SGL nightly tests"
      echo ""
      echo "Options:"
      echo "  --hardware=HW                    Hardware type (mi30x, mi35x) [default: mi30x]"
      echo "  --test-type=TYPE                 Test type (unit, pd) [default: unit]"
      echo "  --model-path=PATH                Model path for PD tests (overrides hardware default)"
      echo "  --image-date=YYYYMMDD            Specific date of Docker image to use [default: today, fallback to yesterday]"
      echo "  --teams-webhook-url=URL          Teams webhook URL for test result notifications"
      echo "  --help, -h                       Show this help message"
      echo ""
      echo "Examples:"
      echo "  $0                                                     # Run unit test on latest mi30x image (today or yesterday)"
      echo "  $0 --hardware=mi35x                                    # Run unit test on latest mi35x image"
      echo "  $0 --test-type=pd                                      # Run PD test on latest mi30x image"
      echo "  $0 --test-type=pd --model-path=/path/to/model          # Run PD test with custom model path"
      echo "  $0 --image-date=20251020                               # Run unit test on mi30x image from 20251020"
      echo ""
      echo "Test Details:"
      echo "  Unit Test:"
      echo "    - Test: test_custom_allreduce.TestCustomAllReduce"
      echo "    - Test Directory: /sgl-workspace/sglang/test/srt"
      echo "    - GPUs Used: 0,1,2,3,4,5,6,7 (8 GPUs)"
      echo "    - Log Directory: \${MOUNT_DIR}/test/unit-test-backend-8-gpu-CAR-amd/[image-name].log"
      echo ""
      echo "  PD Test:"
      echo "    - Test: Prefill/Decode Disaggregation"
      echo "    - Default Models:"
      echo "      • mi30x: DeepSeek-V3-0324 (/mnt/raid/models/huggingface/deepseek-ai/DeepSeek-V3-0324)"
      echo "      • mi35x: DeepSeek-R1-MXFP4-Preview (/mnt/raid/models/huggingface/amd/DeepSeek-R1-MXFP4-Preview)"
      echo "    - Model Path Override: Use --model-path=PATH to override default model path"
      echo "    - GPUs Used: 0-3 (Prefill TP=4), 4-7 (Decode TP=4)"
      echo "    - Log Directory: \${MOUNT_DIR}/test/pd/pd_log/{hardware}/{docker_tag}/"
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

# Validate test type parameter
if [[ "$TEST_TYPE" != "unit" && "$TEST_TYPE" != "pd" ]]; then
    echo "[test] ERROR: Invalid --test-type value. Must be 'unit' or 'pd'."
    exit 1
fi

# Set ROCM version based on hardware type and test type
if [[ "$TEST_TYPE" == "pd" ]]; then
  ROCM_VERSION="${PD_ROCM_VERSIONS[$HARDWARE_TYPE]}"
  echo "[test] Hardware: $HARDWARE_TYPE, ROCM Version: $ROCM_VERSION (PD test - using rocm700)"
else
  ROCM_VERSION="${ROCM_VERSIONS[$HARDWARE_TYPE]}"
  echo "[test] Hardware: $HARDWARE_TYPE, ROCM Version: $ROCM_VERSION"
fi
echo "[test] Test Type: $TEST_TYPE"

###############################################################################
# Lock file management (test-type specific to allow concurrent runs)
###############################################################################
LOCKFILE="/tmp/test_nightly_${TEST_TYPE}.lock"
if [ -f "$LOCKFILE" ]; then
    EXISTING_PID=$(cat "$LOCKFILE" 2>/dev/null)
    if [ -n "$EXISTING_PID" ] && kill -0 "$EXISTING_PID" 2>/dev/null; then
        echo "[test] ERROR: Another $TEST_TYPE test instance is already running (PID: $EXISTING_PID)"
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

###############################################################################
# Ensure GPU is idle before starting
###############################################################################
ensure_gpu_idle

###############################################################################
# Check yesterday's run status
###############################################################################
check_yesterday_run_status() {
  # Check if yesterday's run was successful
  # Returns: 0 if yesterday's run failed/didn't run/incomplete, 1 if successful
  local yesterday_date=$(date_pst 1)
  local yesterday_log_dir="${MOUNT_DIR}/cron/cron_log/${HARDWARE_TYPE}/${yesterday_date}"

  # Determine which log file to check based on test type
  local log_file=""
  if [[ "$TEST_TYPE" == "pd" ]]; then
    log_file="test_nightly_pd.log"
  else
    log_file="test_nightly.log"
  fi

  local yesterday_log="${yesterday_log_dir}/${log_file}"

  # If log doesn't exist, yesterday didn't run
  if [[ ! -f "$yesterday_log" ]]; then
    echo "[test] Yesterday's run log not found - yesterday did not run"
    return 0  # Allow fallback
  fi

  # Check if yesterday's run completed successfully
  if grep -q "Result: PASSED" "$yesterday_log" || grep -q "\[test\] Test completed for image:" "$yesterday_log"; then
    # Yesterday's run was successful
    echo "[test] Yesterday's run completed successfully"
    return 1  # Don't allow fallback - yesterday was successful
  else
    # Yesterday's run failed or didn't complete
    echo "[test] Yesterday's run failed or did not complete"
    return 0  # Allow fallback
  fi
}

###############################################################################
# Pick image tag based on date
###############################################################################
date_pst() { TZ="$TIME_ZONE" date -d "-$1 day" +%Y%m%d; }

# Find non-SRT Docker image for a specific date using Docker Hub API
find_image_for_date() {
  local repo="$1" target_date="$2" rocm_version="${3:-$ROCM_VERSION}"
  local next_url="https://hub.docker.com/v2/repositories/${repo}/tags/?page_size=100"
  local use_jq=$(command -v jq &> /dev/null && echo "true" || echo "false")
  local search_pattern="-${rocm_version}-${HARDWARE_TYPE}-${target_date}"

  echo "[test] Searching for non-SRT ${HARDWARE_TYPE} image (${rocm_version}) in '${repo}' for date ${target_date}..." >&2

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
if [[ -n "$IMAGE_DATE" ]]; then
  echo "[test] Searching for non-SRT image for specific date: ${IMAGE_DATE}..."
else
  echo "[test] Searching for latest non-SRT image..."
fi

# Check curl availability once
if ! command -v curl &> /dev/null; then
  echo "[test] ERROR: curl is required but not found." >&2
  exit 1
fi

SELECTED_TAG=""

if [[ -n "$IMAGE_DATE" ]]; then
  # If IMAGE_DATE is specified, use that specific date
  echo "[test] Looking for image with date: ${IMAGE_DATE}"

  # Try primary ROCM version first
  candidate_tag=$(find_image_for_date "$IMAGE_REPO" "$IMAGE_DATE" "$ROCM_VERSION" || true)

  # If not found and fallback version exists for this hardware, try fallback
  if [[ -z "$candidate_tag" && -n "${ROCM_FALLBACK_VERSIONS[$HARDWARE_TYPE]}" ]]; then
    fallback_version="${ROCM_FALLBACK_VERSIONS[$HARDWARE_TYPE]}"
    echo "[test] Primary version ($ROCM_VERSION) not found, trying fallback ($fallback_version)..."
    candidate_tag=$(find_image_for_date "$IMAGE_REPO" "$IMAGE_DATE" "$fallback_version" || true)
    if [[ -n "$candidate_tag" ]]; then
      echo "[test] Using fallback ROCM version: $fallback_version"
    fi
  fi

  if [[ -n "$candidate_tag" ]]; then
    echo "[test] Found candidate tag for ${IMAGE_DATE}: ${candidate_tag}"
    echo "[test] Attempting to pull ${IMAGE_REPO}:${candidate_tag}..."
    if "${DOCKER_CMD[@]}" pull "${IMAGE_REPO}:${candidate_tag}" 2>&1; then
      SELECTED_TAG="$candidate_tag"
      echo "[test] Successfully pulled image for ${IMAGE_DATE}: ${IMAGE_REPO}:${candidate_tag}"
    else
      echo "[test] WARN: Failed to pull tag ${candidate_tag}."
    fi
  else
    echo "[test] No image found for date ${IMAGE_DATE}."
  fi
else
  # Try today first
  date_suffix=$(date_pst 0)

  # Try primary ROCM version first
  candidate_tag=$(find_image_for_date "$IMAGE_REPO" "$date_suffix" "$ROCM_VERSION" || true)

  # If not found and fallback version exists for this hardware, try fallback
  if [[ -z "$candidate_tag" && -n "${ROCM_FALLBACK_VERSIONS[$HARDWARE_TYPE]}" ]]; then
    fallback_version="${ROCM_FALLBACK_VERSIONS[$HARDWARE_TYPE]}"
    echo "[test] Primary version ($ROCM_VERSION) not found, trying fallback ($fallback_version)..."
    candidate_tag=$(find_image_for_date "$IMAGE_REPO" "$date_suffix" "$fallback_version" || true)
    if [[ -n "$candidate_tag" ]]; then
      echo "[test] Using fallback ROCM version: $fallback_version"
    fi
  fi

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

  # If no image found for today, check if we should try yesterday as fallback
  if [[ -z "$SELECTED_TAG" ]]; then
    echo "[test] No image found for today. Checking if yesterday's image should be used as fallback..."

    # Only use yesterday's image if yesterday's run failed/didn't run/didn't complete
    if check_yesterday_run_status; then
      echo "[test] Proceeding with yesterday's image fallback..."
      date_suffix=$(date_pst 1)

      # For yesterday fallback, only try primary ROCM version (rocm700)
      # Do not fallback to rocm630 for yesterday - user wants yesterday's rocm700 only
      candidate_tag=$(find_image_for_date "$IMAGE_REPO" "$date_suffix" "$ROCM_VERSION" || true)

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
    else
      echo "[test] Skipping yesterday's image fallback - yesterday's run was successful"
      echo "[test] Status: SKIPPED (prerequisites not met)"
    fi
  fi
fi

if [[ -z "$SELECTED_TAG" ]]; then
  if [[ -n "$IMAGE_DATE" ]]; then
    echo "[test] ERROR: Could not find and pull any valid non-SRT images for date ${IMAGE_DATE}."
  else
    echo "[test] ERROR: Could not find and pull any valid non-SRT images for today or yesterday."
  fi
  exit 1
fi

echo "[test] Selected image to run tests on: ${IMAGE_REPO}:${SELECTED_TAG}"

###############################################################################
# Run tests on selected image
###############################################################################

echo ""
echo "[test] =========================================="
echo "[test] Starting tests for image: ${IMAGE_REPO}:${SELECTED_TAG}"
echo "[test] Test type: ${TEST_TYPE}"
echo "[test] =========================================="

# Ensure GPU is idle before starting tests for this image
echo "[test] Checking GPU status before starting tests for ${IMAGE_REPO}:${SELECTED_TAG}..."
ensure_gpu_idle

DOCKER_IMAGE="${IMAGE_REPO}:${SELECTED_TAG}"
# Generate container name using image tag only (model-agnostic to share containers across models)
# Extract repo name from IMAGE_REPO (e.g., "rocm/sgl-dev" -> "sgl-dev")
REPO_NAME="${IMAGE_REPO##*/}"
CONTAINER_NAME="${REPO_NAME}_${SELECTED_TAG//:/_}"

echo "[test] Using Docker image: $DOCKER_IMAGE"
echo "[test] Container name: $CONTAINER_NAME"

###############################################################################
# Run tests based on test type
###############################################################################

if [[ "$TEST_TYPE" == "pd" ]]; then
  ###############################################################################
  # PD Test Execution
  ###############################################################################
  echo "[test] =========================================="
  echo "[test] Running PD Disaggregation Test"
  echo "[test] =========================================="

  # For PD tests, we run components inside Docker containers
  # The PD test script will handle Docker image passing via DOCKER_IMAGE env var
  export DOCKER_IMAGE="${IMAGE_REPO}:${SELECTED_TAG}"

  # Set model path and name based on hardware type
  # Allow --model-path to override the default path
  if [[ -n "$MODEL_PATH_OVERRIDE" ]]; then
    PD_MODEL_PATH="$MODEL_PATH_OVERRIDE"
  else
    PD_MODEL_PATH="${PD_MODEL_PATHS[$HARDWARE_TYPE]}"
  fi
  PD_MODEL_NAME="${PD_MODEL_NAMES[$HARDWARE_TYPE]}"

  echo "[test] Hardware type: ${HARDWARE_TYPE}"
  echo "[test] Model path: ${PD_MODEL_PATH}"
  echo "[test] Model name: ${PD_MODEL_NAME}"

  # Create PD log base directory
  mkdir -p "$PD_LOG_BASE_DIR"

  # Run Docker-based PD test script
  TEST_EXIT_CODE=0
  PD_TEST_SCRIPT="${PD_TEST_DIR}/run_pd_docker.sh"

  if [ ! -f "$PD_TEST_SCRIPT" ]; then
    echo "[test] ERROR: PD test script not found: $PD_TEST_SCRIPT"
    exit 1
  fi

  echo "[test] Running PD test script: $PD_TEST_SCRIPT"
  echo "[test] Model: ${PD_MODEL_NAME} (${PD_MODEL_PATH})"
  echo "[test] Docker Image: ${DOCKER_IMAGE}"

  bash "$PD_TEST_SCRIPT" "$PD_MODEL_PATH" "$PD_MODEL_NAME" || TEST_EXIT_CODE=$?

  # Find the PD log directory (structure: pd_log/{hardware}/{docker_tag})
  LATEST_PD_LOG="${PD_LOG_BASE_DIR}/${HARDWARE_TYPE}/${SELECTED_TAG}"

  if [ -d "$LATEST_PD_LOG" ]; then
    LOG_FILE="${LATEST_PD_LOG}/test_summary.txt"
    echo "[test] PD test log directory: ${LATEST_PD_LOG}"
  else
    LOG_FILE="${PD_LOG_BASE_DIR}/${HARDWARE_TYPE}/pd_test_${SELECTED_TAG}.log"
    echo "[test] WARNING: Could not find PD log directory, using default log file"
  fi

  # Report results
  if [ $TEST_EXIT_CODE -eq 0 ]; then
    echo "[test] === PD test PASSED for ${DOCKER_IMAGE} ==="
    echo "[test] Logs saved to: ${LATEST_PD_LOG}"
  else
    echo "[test] === PD test FAILED for ${DOCKER_IMAGE} (exit code: $TEST_EXIT_CODE) ==="
    echo "[test] Logs saved to: ${LATEST_PD_LOG}"
    echo "[test] Check the log directory for detailed error information"
  fi

else
  ###############################################################################
  # Unit Test Execution (original logic)
  ###############################################################################

  # Ensure container is running
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

  # Stop container to free up resources
  echo "[test] Stopping container ${CONTAINER_NAME} to release resources..."
  "${DOCKER_CMD[@]}" stop "${CONTAINER_NAME}" >/dev/null 2>&1 || true
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

echo "[test] =========================================="
echo "[test] Test completed for image: ${IMAGE_REPO}:${SELECTED_TAG}"
echo "[test] Test type: ${TEST_TYPE}"
echo "[test] =========================================="
