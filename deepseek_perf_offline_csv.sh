#!/usr/bin/env bash
# ------------------------------------------------------------------------------
# deepseek_perf_offline_csv.sh
#
# Offline-throughput benchmark for DeepSeek on TP=8 MI300x.
#
# USAGE:
#   bash deepseek_perf_offline_csv.sh --docker_image=rocm/sgl-dev:20250430
#   bash deepseek_perf_offline_csv.sh --docker_image=lmsysorg/sglang:v0.4.6.post3-rocm630
#   bash deepseek_perf_offline_csv.sh --docker_image=lmsysorg/sglang:v0.4.7-rocm630
#   bash deepseek_perf_offline_csv.sh --model=/path/to/model --model-name=DeepSeek-V3
#   bash deepseek_perf_offline_csv.sh --work-dir=/path/to/workdir --output-dir=/path/to/output
#   bash deepseek_perf_offline_csv.sh --gsm8k-script=/path/to/bench_sglang.py
# ------------------------------------------------------------------------------
set -euo pipefail

###############################################################################
# Configuration Variables - Override via environment variables if needed
###############################################################################

# Default image and model configuration
DOCKER_IMAGE_DEFAULT="${DEFAULT_DOCKER_IMAGE:-rocm/sgl-dev:20250430}"
MODEL_VARIANT="${BENCHMARK_MODEL_VARIANT:-FP8}"

# Default paths - can be overridden
DEFAULT_MODEL="${DEFAULT_MODEL_PATH:-/mnt/raid/models/huggingface/deepseek-ai/DeepSeek-V3-0324}"
DEFAULT_MODEL_NAME="${DEFAULT_MODEL_NAME:-DeepSeek-V3-0324}"
DEFAULT_HF_MODEL_ID="${DEFAULT_HF_MODEL_ID:-deepseek-ai/DeepSeek-V3-0324}"
DEFAULT_WORK_DIR="${DEFAULT_WORK_DIR:-/mnt/raid/michael/sgl_benchmark_ci}"
DEFAULT_OUTPUT_DIR="${DEFAULT_OUTPUT_DIR:-}"  # If empty, will use work_dir


# Container configuration
CONTAINER_SHM_SIZE="${CONTAINER_SHM_SIZE:-32g}"
MOUNT_DIR="${MOUNT_DIR:-/mnt/raid/}"
WORK_DIR_CONTAINER="${WORK_DIR_CONTAINER:-/sgl-workspace}"

# Benchmark configuration (can be overridden)
ILEN="${DEEPSEEK_INPUT_LENGTH:-128}"        # input tokens
OLEN="${DEEPSEEK_OUTPUT_LENGTH:-32}"        # output tokens
TP="${DEEPSEEK_TP:-8}"                      # tensor-parallel degree
BS="${DEEPSEEK_BATCH_SIZE:-32}"             # batch size



###############################################################################
# 0. Parse CLI flags
###############################################################################
docker_image=""

# Initialize variables
MODEL=""
MODEL_NAME=""
HF_MODEL_ID=""
WORK_DIR=""
OUTPUT_DIR=""
DOWNLOAD_MODEL="false"
SCRIPT_PATH="$0"  # Get the script path from how it was called

# Get absolute path of the script
if [[ "$SCRIPT_PATH" != /* ]]; then
    SCRIPT_PATH="$(cd "$(dirname "$0")" && pwd)/$(basename "$0")"
fi

for arg in "$@"; do
  case $arg in
    --docker_image=*|--docker-image=*) # Handle both --docker_image and --docker-image
      docker_image="${arg#*=}"
      shift # Remove parsed argument
      ;;
    --model=*)
      MODEL="${arg#*=}"
      shift
      ;;
    --model-name=*)
      MODEL_NAME="${arg#*=}"
      shift
      ;;
    --hf-model-id=*)
      HF_MODEL_ID="${arg#*=}"
      shift
      ;;
    --work-dir=*)
      WORK_DIR="${arg#*=}"
      shift
      ;;
    --output-dir=*)
      OUTPUT_DIR="${arg#*=}"
      shift
      ;;
    --download-model)
      DOWNLOAD_MODEL="true"
      shift
      ;;
    --help)
      echo "Usage: $0 [OPTIONS]"
      echo "Options:"
      echo "  --docker_image=IMAGE    Docker image to use (default: $DOCKER_IMAGE_DEFAULT)"
      echo "  --model=PATH           Model path (default: $DEFAULT_MODEL)"
      echo "  --model-name=NAME      Model name for output files (default: $DEFAULT_MODEL_NAME)"
      echo "  --hf-model-id=ID       HuggingFace model ID for download (default: $DEFAULT_HF_MODEL_ID)"
      echo "  --work-dir=PATH        Working directory (default: $DEFAULT_WORK_DIR)"
      echo "  --output-dir=PATH      Output directory (default: same as work-dir)"
      echo "  --download-model       Download model if not present (default: false)"
      echo "  --help                 Show this help message"
      echo ""
      echo "Environment Variables:"
      echo "  DEFAULT_DOCKER_IMAGE      Default Docker image"
      echo "  DEFAULT_MODEL_PATH        Default model path"
      echo "  DEFAULT_MODEL_NAME        Default model name"
      echo "  DEEPSEEK_INPUT_LENGTH     Input length (default: $ILEN)"
      echo "  DEEPSEEK_OUTPUT_LENGTH    Output length (default: $OLEN)"
      echo "  DEEPSEEK_TP               Tensor parallel degree (default: $TP)"
      echo "  DEEPSEEK_BATCH_SIZE       Batch size (default: $BS)"
      exit 0
      ;;
  esac
done

# Set defaults if not provided
MODEL="${MODEL:-$DEFAULT_MODEL}"
MODEL_NAME="${MODEL_NAME:-$DEFAULT_MODEL_NAME}"
HF_MODEL_ID="${HF_MODEL_ID:-$DEFAULT_HF_MODEL_ID}"
WORK_DIR="${WORK_DIR:-$DEFAULT_WORK_DIR}"
OUTPUT_DIR="${OUTPUT_DIR:-$WORK_DIR}"

# If not provided by flag, use positional argument or default
docker_image="${docker_image:-${1:-$DOCKER_IMAGE_DEFAULT}}"

###############################################################################
# 0-b. Use the full image name as provided (no auto-prefixing)
###############################################################################
FULL_IMAGE="$docker_image"

IMAGE_WITH_TAG="${FULL_IMAGE##*/}" # e.g., sgl-dev:20250429
LATEST_TAG="${IMAGE_WITH_TAG#*:}"   # e.g., 20250429

# ---------------------------
# 0-c. Container Management (if applicable)
# ---------------------------
if [ -z "${INSIDE_CONTAINER:-}" ]; then
  if ! command -v docker >/dev/null 2>&1; then
    echo "[csv] Docker not found — already inside container."
    INSIDE_CONTAINER=1
  else
    IMAGE_WITH_TAG_FOR_CONTAINER_NAME="${FULL_IMAGE##*/}"      # sgl-dev:20250429
    REPO="${IMAGE_WITH_TAG_FOR_CONTAINER_NAME%%:*}"            # sgl-dev
    TAG_FOR_CONTAINER_NAME="${IMAGE_WITH_TAG_FOR_CONTAINER_NAME#*:}"       # 20250429
    CONTAINER_NAME="${REPO}_${TAG_FOR_CONTAINER_NAME}"

    echo "[csv] Target container : ${CONTAINER_NAME}"
    echo "[csv] Docker image     : ${FULL_IMAGE}"

    if docker ps -a --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"; then
      if docker ps --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"; then
        echo "[csv] Container already running."
      else
        echo "[csv] Starting existing container ..."
        docker start "${CONTAINER_NAME}"
      fi
    else
      echo "[csv] Checking if image exists locally ..."
      # Check if image exists locally
      if docker images --format '{{.Repository}}:{{.Tag}}' | grep -q "^${FULL_IMAGE}$"; then
        echo "[csv] Found local image: ${FULL_IMAGE}"
      else
        # For custom built images without repo prefix, check without the prefix
        if docker images --format '{{.Repository}}:{{.Tag}}' | grep -E "^${IMAGE_WITH_TAG}$|^${REPO}:latest$"; then
          echo "[csv] Found local image: ${IMAGE_WITH_TAG}"
        else
          echo "[csv] Image not found locally. Attempting to pull ..."
          if ! docker pull "${FULL_IMAGE}" 2>/dev/null; then
            echo "[csv] WARNING: Failed to pull ${FULL_IMAGE}. Image might be a local build."
            echo "[csv] Checking if it exists with a different tag ..."
            # Final check for the image
            if ! docker images | grep -q "${REPO}"; then
              echo "[csv] ERROR: Image ${FULL_IMAGE} not found locally or remotely."
              exit 1
            fi
          fi
        fi
      fi

      echo "[csv] Creating container ..."
      docker run -d --name "${CONTAINER_NAME}" \
        --shm-size "$CONTAINER_SHM_SIZE" --ipc=host --cap-add=SYS_PTRACE --network=host \
        --device=/dev/kfd --device=/dev/dri --security-opt seccomp=unconfined \
        -v "${MOUNT_DIR}:${MOUNT_DIR}" --group-add video --privileged \
        -w "$WORK_DIR_CONTAINER" "${FULL_IMAGE}" tail -f /dev/null
    fi

    echo "[csv] Re-invoking inside ${CONTAINER_NAME} ..."
    docker exec \
      -e INSIDE_CONTAINER=1 \
      -e LATEST_TAG="${LATEST_TAG}" \
      "${CONTAINER_NAME}" \
      bash "${SCRIPT_PATH}" \
           --docker_image="${FULL_IMAGE}" \
           --model="${MODEL}" \
           --model-name="${MODEL_NAME}" \
           --hf-model-id="${HF_MODEL_ID}" \
           --work-dir="${WORK_DIR}" \
           --output-dir="${OUTPUT_DIR}" \
           $([ "$DOWNLOAD_MODEL" = "true" ] && echo "--download-model")
    exit 0
  fi
fi

# ---------------------------
# 1. Inside Container: Setup Run Folder
# ---------------------------
cd "${WORK_DIR}" || { echo "Cannot change to ${WORK_DIR} directory"; exit 1; }

# If LATEST_TAG is not already defined (e.g. when script is re-invoked inside container), extract it.
if [ -z "$LATEST_TAG" ]; then
    IMAGE_WITH_TAG_FROM_ARG=${docker_image#*/}
    LATEST_TAG=${IMAGE_WITH_TAG_FROM_ARG#*:}
fi

# ---- Download model if requested and not present (inside container) ----
if [ -n "${INSIDE_CONTAINER}" ] && [ "$DOWNLOAD_MODEL" = "true" ]; then # Only run download logic if inside the container and requested
  if command -v huggingface-cli >/dev/null 2>&1; then
    echo "[csv] huggingface-cli found. Ensuring model ${HF_MODEL_ID} is downloaded to ${MODEL}..."
    # Ensure parent directory of MODEL exists, huggingface-cli creates the final dir.
    mkdir -p "$(dirname "${MODEL}")"
    huggingface-cli download "${HF_MODEL_ID}" \
      --repo-type model \
      --local-dir "${MODEL}" \
      --local-dir-use-symlinks False \
      --resume-download
      # For private models or to ensure a specific user context for downloads,
      # you might need to pass --token $YOUR_HF_TOKEN or ensure `huggingface-cli login` was done.
      # For public models like deepseek, token is usually not strictly needed.
    echo "[csv] Model download/verification attempt complete for ${MODEL}."
    if [ ! -d "${MODEL}" ] || [ -z "$(ls -A "${MODEL}")" ]; then
      echo "[csv] ERROR: Model directory ${MODEL} is still missing or empty after download attempt."
      echo "[csv] Please check for errors from huggingface-cli output above."
      echo "[csv] Also ensure you have network access and permissions to write to the target path."
      echo "[csv] Contents of $(dirname "${MODEL}"):"
      ls -la "$(dirname "${MODEL}")"
      exit 1
    else
      echo "[csv] Model files appear to be present in ${MODEL}."
    fi
  else
    echo "[csv] WARNING: huggingface-cli not found. Assuming model ${HF_MODEL_ID} is already present at ${MODEL}."
    if [ ! -d "${MODEL}" ] || [ -z "$(ls -A "${MODEL}")" ]; then
      echo "[csv] ERROR: Model directory ${MODEL} is missing or empty, and huggingface-cli is not available to download it."
      exit 1
    fi
  fi
fi
# ---- End model download ----

## 2.  Run-folder bookkeeping ---------------------------------------------------
folder="${OUTPUT_DIR}/offline/${MODEL_NAME}/${LATEST_TAG}_${MODEL_NAME}_${MODEL_VARIANT}_offline"
mkdir -p "$folder"
OUTPUT_CSV="${folder}/${LATEST_TAG}_${MODEL_NAME}_${MODEL_VARIANT}_offline.csv"
LOG_FILE="${folder}/tp${TP}_bs${BS}.log" # Define log file path

# CSV header (only write if file is empty)
if [ ! -s "$OUTPUT_CSV" ]; then
  echo "TP,batch_size,IL,OL,Prefill_latency(s),Median_decode_latency(s),E2E_Latency(s),Prefill_Throughput(token/s),Median_Decode_Throughput(token/s),E2E_Throughput(token/s)" > "$OUTPUT_CSV"
fi

## 4.  Single-run benchmark -----------------------------------------------------
echo "=== TP=${TP}, BS=${BS} ==="

# Determine which environment variable to use based on version
# Extract version from image tag if it's an lmsysorg/sglang image
aiter_env_var="SGLANG_USE_AITER"
if [[ "$FULL_IMAGE" =~ lmsysorg/sglang:v([0-9]+)\.([0-9]+)\.([0-9]+)(\.post[0-9]+)? ]]; then
  major="${BASH_REMATCH[1]}"
  minor="${BASH_REMATCH[2]}"
  patch="${BASH_REMATCH[3]}"
  # Use SGLANG_AITER_MOE for versions before v0.4.7
  if [[ "$major" -eq 0 ]]; then
    if [[ "$minor" -lt 4 ]] || [[ "$minor" -eq 4 && "$patch" -lt 7 ]]; then
      aiter_env_var="SGLANG_AITER_MOE"
    fi
  fi
fi

echo "[DEBUG] Using environment variable: ${aiter_env_var}"

out=$(
  env RCCL_MSCCL_ENABLE=0 ${aiter_env_var}=1 SGLANG_INT4_WEIGHT=0 SGLANG_MOE_PADDING=0 \
  python3 -m sglang.bench_one_batch \
    --model "${MODEL}" \
    --tp "${TP}" \
    --batch-size "${BS}" \
    --input "${ILEN}" \
    --output "${OLEN}" \
    --disable-radix-cache \
    --trust-remote-code 2>&1 | tee "${LOG_FILE}"
)

## 5.  Parse metrics and append CSV --------------------------------------------
# Isolate the section after the literal "Benchmark ..."
last_section=$(printf '%s\n' "$out" | awk '/^\s*Benchmark[. ]/{flag=1;next} flag')

if [[ -z "$last_section" ]]; then
  echo "ERROR: Benchmark block not found in output"
  exit 1
fi

prefill_lat=$(echo "$last_section" | grep -oP 'Prefill\.\s+latency:\s*\K[\d.]+'             | tail -1)
prefill_tp=$( echo "$last_section" | grep -oP 'Prefill\.\s+latency:.*throughput:\s*\K[\d.]+' | tail -1)
decode_lat=$( echo "$last_section" | grep -oP 'Decode\.\s+median latency:\s*\K[\d.]+'        | tail -1)
decode_tp=$(  echo "$last_section" | grep -oP 'Decode\.\s+median latency:.*median throughput:\s*\K[\d.]+' | tail -1)
total_lat=$(  echo "$last_section" | grep -oP 'Total\.\s+latency:\s*\K[\d.]+'               | tail -1)
total_tp=$(   echo "$last_section" | grep -oP 'Total\.\s+latency:.*throughput:\s*\K[\d.]+'   | tail -1)

# Check if metrics were successfully parsed
if [[ -z "$prefill_lat" || -z "$decode_lat" || -z "$total_lat" ]]; then
  echo "ERROR: Failed to parse one or more metrics from the benchmark output."
  echo "Output was:"
  echo "$out"
  echo "Last section parsed was:"
  echo "$last_section"
  echo "Please check the log file: ${LOG_FILE}"
  exit 1
fi

echo "${TP},${BS},${ILEN},${OLEN},${prefill_lat},${decode_lat},${total_lat},${prefill_tp},${decode_tp},${total_tp}" >> "$OUTPUT_CSV"

# Save raw JSONL if bench_one_batch produced one
if [ -f result.jsonl ]; then
  mv result.jsonl "${folder}/${LATEST_TAG}_${MODEL_NAME}_${MODEL_VARIANT}_offline.jsonl"
fi

echo "✅  Results written to ${OUTPUT_CSV}"
echo "Full log saved to ${LOG_FILE}"
