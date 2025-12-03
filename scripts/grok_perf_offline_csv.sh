#!/usr/bin/env bash
# ------------------------------------------------------------------------------
# grok_perf_offline_csv.sh
#
# Offline Grok-1 and Grok-2 benchmark.  Supports --docker_image=<image[:tag]> override.
#
# USAGE:
#   bash grok_perf_offline_csv.sh --docker_image=rocm/sgl-dev:v0.5.5-rocm700-mi30x-20251110
#   bash grok_perf_offline_csv.sh --model-type=grok2 --model=/data2/grok-2/
#   bash grok_perf_offline_csv.sh --mode=long_context
#   bash grok_perf_offline_csv.sh --mode=dummy
#   bash grok_perf_offline_csv.sh --model-path=/raid/grok-1-W4A8KV8 --tokenizer=/raid/grok-1-W4A8KV8
#   bash grok_perf_offline_csv.sh --work-dir=/path/to/workdir --output-dir=/path/to/output
# ------------------------------------------------------------------------------

###############################################################################
# Configuration Variables - Override via environment variables if needed
###############################################################################

# Default image and model configuration
DOCKER_IMAGE_DEFAULT="${DEFAULT_DOCKER_IMAGE:-rocm/sgl-dev:v0.5.5-rocm700-mi30x-20251110}"

# Model type configuration (grok1 or grok2)
DEFAULT_MODEL_TYPE="${DEFAULT_MODEL_TYPE:-grok1}"

# Grok 1 defaults
GROK1_MODEL_NAME="${BENCHMARK_MODEL_NAME:-GROK1}"
GROK1_MODEL_VARIANT="${BENCHMARK_MODEL_VARIANT:-MOE-I4F8}"
GROK1_DEFAULT_MODEL="${DEFAULT_MODEL_PATH:-/mnt/raid/models/huggingface/amd--grok-1-W4A8KV8/}"
GROK1_DEFAULT_TOKENIZER="${DEFAULT_TOKENIZER_NAME:-Xenova/grok-1-tokenizer}"

# Grok 2 defaults
GROK2_MODEL_NAME="${BENCHMARK_MODEL_NAME:-GROK2}"
GROK2_MODEL_VARIANT="${BENCHMARK_MODEL_VARIANT:-MOE-I4F0}"
GROK2_DEFAULT_MODEL="${DEFAULT_MODEL_PATH:-/data2/grok-2/}"
GROK2_DEFAULT_TOKENIZER="${DEFAULT_TOKENIZER_NAME:-/data2/grok-2/tokenizer.tok.json}"

DEFAULT_DUMMY_MODEL="${DEFAULT_DUMMY_MODEL_PATH:-/mnt/raid/models/dummy_prod1/}"
DEFAULT_WORK_DIR="${DEFAULT_WORK_DIR:-/mnt/raid/michael/sglang-ci}"
DEFAULT_OUTPUT_DIR="${DEFAULT_OUTPUT_DIR:-}"  # If empty, will use work_dir

# Container configuration
CONTAINER_SHM_SIZE="${CONTAINER_SHM_SIZE:-32g}"
MOUNT_DIR="${MOUNT_DIR:-/mnt/raid/}"
WORK_DIR_CONTAINER="${WORK_DIR_CONTAINER:-/sgl-workspace}"

# Mode-specific configuration (can be overridden via environment)
# Normal mode
NORMAL_INPUT_LENGTHS="${NORMAL_INPUT_LENGTHS:-1024}"
NORMAL_OUTPUT_LENGTH="${NORMAL_OUTPUT_LENGTH:-128}"
NORMAL_TP_VALUES="${NORMAL_TP_VALUES:-8}"
NORMAL_BATCH_SIZES="${NORMAL_BATCH_SIZES:-1 2 4 8 16 32 64 128 256}"

# Long context mode
LONG_CONTEXT_INPUT_LENGTHS="${LONG_CONTEXT_INPUT_LENGTHS:-8192 16384 32768}"
LONG_CONTEXT_OUTPUT_LENGTH="${LONG_CONTEXT_OUTPUT_LENGTH:-10}"
LONG_CONTEXT_TP_VALUES="${LONG_CONTEXT_TP_VALUES:-8}"
LONG_CONTEXT_BATCH_SIZES="${LONG_CONTEXT_BATCH_SIZES:-1}"

# Dummy mode
DUMMY_INPUT_LENGTHS="${DUMMY_INPUT_LENGTHS:-256}"
DUMMY_OUTPUT_LENGTH="${DUMMY_OUTPUT_LENGTH:-4096}"
DUMMY_TP_VALUES="${DUMMY_TP_VALUES:-8}"
DUMMY_BATCH_SIZES="${DUMMY_BATCH_SIZES:-2}"

# Memory fraction configuration
BATCH_SIZE_128_MEM_FRACTION="${BATCH_SIZE_128_MEM_FRACTION:-0.85}"
BATCH_SIZE_256_MEM_FRACTION="${BATCH_SIZE_256_MEM_FRACTION:-0.75}"

###############################################################################
# Parse CLI options
###############################################################################
docker_image=""
mode="normal"  # default mode (normal, long_context, or dummy)

# Initialize variables with defaults
MODEL_TYPE=""
MODEL=""
TOKENIZER=""
DUMMY_MODEL=""
WORK_DIR=""
OUTPUT_DIR=""
SCRIPT_PATH="$0"  # Get the script path from how it was called

# Get absolute path of the script
if [[ "$SCRIPT_PATH" != /* ]]; then
    SCRIPT_PATH="$(cd "$(dirname "$0")" && pwd)/$(basename "$0")"
fi

for arg in "$@"; do
  case $arg in
    --docker_image=*|--docker-image=*)
      docker_image="${arg#*=}"
      shift
      ;;
    --mode=*)
      mode="${arg#*=}"
      shift
      ;;
    --model=*|--model-path=*)
      MODEL="${arg#*=}"
      shift
      ;;
    --model-type=*)
      MODEL_TYPE="${arg#*=}"
      shift
      ;;
    --tokenizer=*)
      TOKENIZER="${arg#*=}"
      shift
      ;;
    --dummy-model=*)
      DUMMY_MODEL="${arg#*=}"
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
    --help)
      echo "Usage: $0 [OPTIONS]"
      echo "Options:"
      echo "  --docker_image=IMAGE    Docker image to use (default: $DOCKER_IMAGE_DEFAULT)"
      echo "  --mode=MODE            Mode: normal, long_context, or dummy (default: normal)"
      echo "  --model=PATH           Model path"
      echo "  --model-type=TYPE      Model type: grok1 or grok2 (default: $DEFAULT_MODEL_TYPE)"
      echo "  --tokenizer=NAME       Tokenizer name"
      echo "  --dummy-model=PATH     Dummy model path for dummy mode (default: $DEFAULT_DUMMY_MODEL)"
      echo "  --work-dir=PATH        Working directory (default: $DEFAULT_WORK_DIR)"
      echo "  --output-dir=PATH      Output directory (default: same as work-dir)"
      echo "  --help                 Show this help message"
      exit 0
      ;;
  esac
done

# Auto-detect model type if not explicitly provided
if [[ -z "${MODEL_TYPE}" ]]; then
    MODEL_TYPE="${DEFAULT_MODEL_TYPE}"
    # Auto-detect based on model path if provided
    if [[ -n "${MODEL}" ]]; then
        if [[ "${MODEL}" == *"grok-2"* ]] || [[ "${MODEL}" == *"grok2"* ]]; then
            MODEL_TYPE="grok2"
            echo "[csv] Auto-detected model type: grok2 from path: ${MODEL}"
        elif [[ "${MODEL}" == *"grok-1"* ]] || [[ "${MODEL}" == *"grok1"* ]]; then
            MODEL_TYPE="grok1"
            echo "[csv] Auto-detected model type: grok1 from path: ${MODEL}"
        fi
    fi
fi

# Set model-specific defaults based on model type
if [[ "${MODEL_TYPE}" == "grok2" ]]; then
    MODEL_NAME="${GROK2_MODEL_NAME}"
    MODEL_VARIANT="${GROK2_MODEL_VARIANT}"
    DEFAULT_MODEL="${GROK2_DEFAULT_MODEL}"
    DEFAULT_TOKENIZER="${GROK2_DEFAULT_TOKENIZER}"
    echo "[csv] Using Grok 2 configuration"
else
    MODEL_NAME="${GROK1_MODEL_NAME}"
    MODEL_VARIANT="${GROK1_MODEL_VARIANT}"
    DEFAULT_MODEL="${GROK1_DEFAULT_MODEL}"
    DEFAULT_TOKENIZER="${GROK1_DEFAULT_TOKENIZER}"
    echo "[csv] Using Grok 1 configuration"
fi

# Set defaults if not provided
MODEL="${MODEL:-$DEFAULT_MODEL}"

# Handle tokenizer path logic
if [[ "${MODEL_TYPE}" == "grok2" ]]; then
    # For Grok 2, use specific tokenizer file if custom model provided
    if [[ -n "${MODEL:-}" && "${MODEL}" != "${DEFAULT_MODEL}" && -z "${TOKENIZER:-}" ]]; then
        TOKENIZER="${MODEL}/tokenizer.tok.json"
        echo "[csv] Using custom Grok 2 tokenizer file: ${TOKENIZER}"
    else
        TOKENIZER="${TOKENIZER:-$DEFAULT_TOKENIZER}"
        echo "[csv] Using default Grok 2 tokenizer: ${TOKENIZER}"
    fi
else
    # For Grok 1, use existing logic
    TOKENIZER="${TOKENIZER:-$DEFAULT_TOKENIZER}"
fi

DUMMY_MODEL="${DUMMY_MODEL:-$DEFAULT_DUMMY_MODEL}"
WORK_DIR="${WORK_DIR:-$DEFAULT_WORK_DIR}"
OUTPUT_DIR="${OUTPUT_DIR:-$WORK_DIR}"

# If not provided, also allow a positional 1st argument for backward-compat.
docker_image="${docker_image:-${1:-$DOCKER_IMAGE_DEFAULT}}"

# Validate mode
if [[ "$mode" != "normal" && "$mode" != "long_context" && "$mode" != "dummy" ]]; then
  echo "[csv] Invalid mode: $mode. Must be one of: normal, long_context, dummy"
  exit 1
fi

# ---------------------------
# 0. Container Management (if applicable)
# ---------------------------
if [ -z "${INSIDE_CONTAINER:-}" ]; then
  if ! command -v docker >/dev/null 2>&1; then
    echo "[csv] Docker not found — already inside container."
    INSIDE_CONTAINER=1
  else
    # ---- 0.1 Use the full image name as provided
    FULL_IMAGE="$docker_image"

    IMAGE_WITH_TAG="${FULL_IMAGE##*/}"          # sgl-dev:20250331rc
    REPO="${IMAGE_WITH_TAG%%:*}"                # sgl-dev
    LATEST_TAG="${IMAGE_WITH_TAG#*:}"           # 20250331rc
    CONTAINER_NAME="${REPO}_${LATEST_TAG}"

    echo "[csv] Target container : ${CONTAINER_NAME}"
    echo "[csv] Docker image     : ${FULL_IMAGE}"
    echo "[csv] Mode            : ${mode}"

    # ---- 0.2 Ensure container exists & running
    if docker ps -a --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"; then
      if docker ps --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"; then
        echo "[csv] Container already running."
        # Check if script and model are accessible inside the container
        if ! docker exec "${CONTAINER_NAME}" test -f "${SCRIPT_PATH}" 2>/dev/null; then
          echo "[csv] Script not accessible in existing container. Recreating container..."
          docker stop "${CONTAINER_NAME}" >/dev/null 2>&1
          docker rm "${CONTAINER_NAME}" >/dev/null 2>&1
        elif ! docker exec "${CONTAINER_NAME}" test -d "${MODEL}" 2>/dev/null; then
          echo "[csv] Model directory not accessible in existing container. Recreating container..."
          docker stop "${CONTAINER_NAME}" >/dev/null 2>&1
          docker rm "${CONTAINER_NAME}" >/dev/null 2>&1
        fi
      else
        echo "[csv] Starting existing container ..."
        docker start "${CONTAINER_NAME}"
        # Check if script and model are accessible inside the container after starting
        if ! docker exec "${CONTAINER_NAME}" test -f "${SCRIPT_PATH}" 2>/dev/null; then
          echo "[csv] Script not accessible in existing container. Recreating container..."
          docker stop "${CONTAINER_NAME}" >/dev/null 2>&1
          docker rm "${CONTAINER_NAME}" >/dev/null 2>&1
        elif ! docker exec "${CONTAINER_NAME}" test -d "${MODEL}" 2>/dev/null; then
          echo "[csv] Model directory not accessible in existing container. Recreating container..."
          docker stop "${CONTAINER_NAME}" >/dev/null 2>&1
          docker rm "${CONTAINER_NAME}" >/dev/null 2>&1
        fi
      fi
    fi

    # Create container if it doesn't exist or was removed due to validation failure
    if ! docker ps -a --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"; then
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

      # Get the directory containing the script
      script_dir="$(dirname "${SCRIPT_PATH}")"

      # Create mount arguments - always mount MOUNT_DIR, and also mount script directory if different
      mount_args="-v ${MOUNT_DIR}:${MOUNT_DIR}"

      # If script directory is not under MOUNT_DIR, mount it separately
      if [[ "$script_dir" != "${MOUNT_DIR}"* ]]; then
          echo "[csv] Script directory ${script_dir} is not under ${MOUNT_DIR}, mounting separately..."
          mount_args="${mount_args} -v ${script_dir}:${script_dir}"
      fi

      # If model directory is not under MOUNT_DIR, mount its parent directory
      model_dir="$(dirname "${MODEL}")"
      if [[ "$model_dir" != "${MOUNT_DIR}"* ]] && [[ "$model_dir" != "$script_dir"* ]]; then
          # For paths like /data/vmiriyal/deepseek-v3, mount /data
          mount_root=""
          if [[ "$MODEL" == /data/* ]]; then
              mount_root="/data"
          elif [[ "$MODEL" == /mnt/* ]]; then
              mount_root="/mnt"
          elif [[ "$MODEL" == /home/* ]]; then
              mount_root="/home"
          else
              # Fallback: mount the parent directory
              mount_root="$model_dir"
          fi

          if [[ "$mount_root" != "${MOUNT_DIR%/}" ]]; then
              echo "[csv] Model directory ${MODEL} requires mounting ${mount_root}..."
              mount_args="${mount_args} -v ${mount_root}:${mount_root}"
          fi
      fi

      docker run -d --name "${CONTAINER_NAME}" \
        --shm-size "$CONTAINER_SHM_SIZE" --ipc=host --cap-add=SYS_PTRACE --network=host \
        --device=/dev/kfd --device=/dev/dri --security-opt seccomp=unconfined \
        -e HSA_ENABLE_COREDUMP=0 \
        ${mount_args} --group-add video --privileged \
        -w "$WORK_DIR_CONTAINER" "${FULL_IMAGE}" tail -f /dev/null
    fi

    # ---- 0.3 Re-invoke this script inside the container
    echo "[csv] Re-invoking inside ${CONTAINER_NAME} ..."
    docker exec \
      -e INSIDE_CONTAINER=1 \
      -e LATEST_TAG="${LATEST_TAG}" \
      "${CONTAINER_NAME}" \
      bash "${SCRIPT_PATH}" \
           --docker_image="${FULL_IMAGE}" \
           --mode="${mode}" \
           --model="${MODEL}" \
           --model-type="${MODEL_TYPE}" \
           --tokenizer="${TOKENIZER}" \
           --dummy-model="${DUMMY_MODEL}" \
           --work-dir="${WORK_DIR}" \
           --output-dir="${OUTPUT_DIR}"
    exit 0
  fi
fi

# ---------------------------
# 1. Inside Container: Setup Run Folder
# ---------------------------
cd "${WORK_DIR}" || { echo "Cannot change to ${WORK_DIR} directory"; exit 1; }

# If LATEST_TAG is not already defined, extract it from docker_image.
if [ -z "$LATEST_TAG" ]; then
    IMAGE_WITH_TAG=${docker_image#*/}
    # Handle case where there's no repository prefix (e.g., "v0.4.7-rocm700")
    if [[ "$IMAGE_WITH_TAG" == "$docker_image" ]]; then
        # No slash found, so the whole thing is the tag
        LATEST_TAG="$docker_image"
    else
        LATEST_TAG=${IMAGE_WITH_TAG#*:}
    fi
fi

# ---------------------------
# GSM8K Accuracy Check (Local)
# ---------------------------
# Check for GSM8K results from online benchmark before proceeding
check_gsm8k_results() {
    local online_folder="${OUTPUT_DIR}/online/${MODEL_NAME}/${LATEST_TAG}_${MODEL_NAME}_${MODEL_VARIANT}_online"
    local gsm8k_log_pattern1="${online_folder}/sglang_client_log_${MODEL_NAME}_gsm8k_*.log"
    local gsm8k_log_pattern2="${online_folder}/sglang_client_log_${MODEL_NAME}_gsm8k.log"

    echo "[csv] Checking for local GSM8K results in: ${online_folder}"

    # Check both patterns - with and without backend suffix
    local found_logs=false
    local success_found=false

    # Check pattern with backend suffix (e.g., _aiter.log)
    if compgen -G "${gsm8k_log_pattern1}" > /dev/null 2>&1; then
        found_logs=true
        if grep -lq "Average accuracy meets threshold" ${gsm8k_log_pattern1} 2>/dev/null; then
            success_found=true
        fi
    fi

    # Check pattern without backend suffix (.log)
    if [[ -f "${gsm8k_log_pattern2}" ]]; then
        found_logs=true
        if grep -q "Average accuracy meets threshold" "${gsm8k_log_pattern2}" 2>/dev/null; then
            success_found=true
        fi
    fi

    if [[ "$found_logs" == "true" ]]; then
        if [[ "$success_found" == "true" ]]; then
            echo "[csv] Found GSM8K success message in log file(s). Proceeding with offline benchmark."
            return 0
        else
            echo "[csv] GSM8K log files found but accuracy threshold not met. Skipping offline benchmark."
            return 1
        fi
    else
        echo "[csv] No GSM8K log files found. Proceeding with offline benchmark (assuming first run or standalone execution)."
        return 0
    fi
}

# Perform GSM8K check
if ! check_gsm8k_results; then
    echo "[csv] Exiting due to GSM8K accuracy check failure."
    exit 0
fi

# ---------------------------
# Mode-specific Configuration
# ---------------------------
# Set mode suffix for folder/file names
mode_suffix=""
if [[ "$mode" != "normal" ]]; then
  mode_suffix="_${mode}"
fi

# Base folder structure
folder="${OUTPUT_DIR}/offline/${MODEL_NAME}/${LATEST_TAG}_${MODEL_NAME}_${MODEL_VARIANT}_offline${mode_suffix}"

if [[ "$mode" == "long_context" ]]; then
  # Long context mode configuration
  read -ra INPUT_LENGTHS <<< "$LONG_CONTEXT_INPUT_LENGTHS"
  OLEN="$LONG_CONTEXT_OUTPUT_LENGTH"
  read -ra TP_VALUES <<< "$LONG_CONTEXT_TP_VALUES"
  read -ra BATCH_SIZES <<< "$LONG_CONTEXT_BATCH_SIZES"
elif [[ "$mode" == "dummy" ]]; then
  # Dummy mode configuration - use the dummy model
  MODEL="${DUMMY_MODEL}"
  read -ra INPUT_LENGTHS <<< "$DUMMY_INPUT_LENGTHS"
  OLEN="$DUMMY_OUTPUT_LENGTH"
  read -ra TP_VALUES <<< "$DUMMY_TP_VALUES"
  read -ra BATCH_SIZES <<< "$DUMMY_BATCH_SIZES"
else
  # Normal mode configuration (default)
  read -ra INPUT_LENGTHS <<< "$NORMAL_INPUT_LENGTHS"
  OLEN="$NORMAL_OUTPUT_LENGTH"
  read -ra TP_VALUES <<< "$NORMAL_TP_VALUES"
  read -ra BATCH_SIZES <<< "$NORMAL_BATCH_SIZES"
fi

# Set output file names
OUTPUT_CSV="${folder}/${LATEST_TAG}_${MODEL_NAME}_${MODEL_VARIANT}_offline${mode_suffix}.csv"
# JSON output disabled - no longer generating .jsonl files

mkdir -p "$folder"

# Write config.json for all modes to maintain docker image info
echo "{\"docker\": \"${docker_image}\"}" > "${folder}/config.json"
echo "Wrote config.json to ${folder}/config.json"

# All supported images use aiter backend
ATTENTION_BACKEND="aiter"

echo "Using attention backend: ${ATTENTION_BACKEND}"

# Also save backend info to config.json
echo "{\"docker\": \"${docker_image}\", \"attention_backend\": \"${ATTENTION_BACKEND}\"}" > "${folder}/config.json"
echo "Wrote config.json to ${folder}/config.json"

# Write CSV header with ordering:
echo "TP,batch_size,IL,OL,Backend,Prefill_latency(s),Median_decode_latency(s),E2E_Latency(s),Prefill_Throughput(token/s),Median_Decode_Throughput(token/s),E2E_Throughput(token/s)" > "${OUTPUT_CSV}"

# Loop over TP, batch sizes, and input lengths
for tp in "${TP_VALUES[@]}"; do
  for bs in "${BATCH_SIZES[@]}"; do
    for ilen in "${INPUT_LENGTHS[@]}"; do
      echo "Running TP=${tp}, batch_size=${bs}, input_length=${ilen} ..."

      ## NEW: file to keep full stdout/stderr
      log_file="${folder}/tp${tp}_bs${bs}_il${ilen}.log"

      # -----------------------------------------------------------------------
      # Select command variant depending on mode and backend
      # -----------------------------------------------------------------------
      if [[ "$mode" == "dummy" ]]; then
        # Dummy mode command - always use aiter backend with model-specific env vars
        if [[ "${MODEL_TYPE}" == "grok2" ]]; then
          env_vars="RCCL_MSCCL_ENABLE=0 SGLANG_USE_AITER=1 SGLANG_INT4_WEIGHT=0"
        else
          env_vars="SGLANG_USE_AITER=1 SGLANG_INT4_WEIGHT=0"
        fi
        out=$(
          env ${env_vars} \
          python3 -m sglang.bench_one_batch \
            --model "${MODEL}" \
            --load-format dummy \
            --tokenizer-path "${TOKENIZER}" \
            --tp "${tp}" \
            --batch-size "${bs}" \
            --input "${ilen}" \
            --output "${OLEN}" \
            --attention-backend aiter \
            --torch-compile-max-bs 4 \
            --quantization fp8 \
            --trust-remote-code \
            --enable-torch-compile 2>&1 | tee "${log_file}"
        )
        cmd_exit_status=${PIPESTATUS[0]}
      elif [[ "$mode" == "long_context" ]]; then
        # Long context mode command - always use aiter backend with model-specific env vars
        if [[ "${MODEL_TYPE}" == "grok2" ]]; then
          env_vars="RCCL_MSCCL_ENABLE=0 SGLANG_USE_AITER=1 SGLANG_INT4_WEIGHT=0"
        else
          env_vars="SGLANG_USE_AITER=1 SGLANG_INT4_WEIGHT=1"
        fi
        out=$(
          env ${env_vars} \
          python3 -m sglang.bench_one_batch \
            --model "${MODEL}" \
            --tokenizer-path "${TOKENIZER}" \
            --tp "${tp}" \
            --batch-size "${bs}" \
            --input "${ilen}" \
            --output "${OLEN}" \
            --attention-backend aiter \
            --quantization fp8 \
            --trust-remote-code 2>&1 | tee "${log_file}"
        )
        cmd_exit_status=${PIPESTATUS[0]}
      else
        # Normal mode - always use aiter backend with model-specific env vars
        mem_fraction_arg=""
        if [[ "$bs" -eq 128 ]]; then
          mem_fraction_arg=" --mem-fraction-static $BATCH_SIZE_128_MEM_FRACTION"
        elif [[ "$bs" -eq 256 ]]; then
          mem_fraction_arg=" --mem-fraction-static $BATCH_SIZE_256_MEM_FRACTION"
        fi

        if [[ "${MODEL_TYPE}" == "grok2" ]]; then
          env_vars="RCCL_MSCCL_ENABLE=0 SGLANG_USE_AITER=1 SGLANG_INT4_WEIGHT=0"
        else
          env_vars="SGLANG_USE_AITER=1 SGLANG_INT4_WEIGHT=1"
        fi

        out=$(
          env ${env_vars} \
          python3 -m sglang.bench_one_batch \
            --model "${MODEL}" \
            --tokenizer-path "${TOKENIZER}" \
            --tp "${tp}" \
            --batch-size "${bs}" \
            --input "${ilen}" \
            --output "${OLEN}" \
            --attention-backend aiter \
            --sampling-backend pytorch \
            --quantization fp8 \
            --trust-remote-code \
            --cuda-graph-max-bs 1024${mem_fraction_arg} 2>&1 | tee "${log_file}"
        )
        cmd_exit_status=${PIPESTATUS[0]}

        # Clean up any accidentally generated JSONL files
        rm -f result.jsonl *.jsonl 2>/dev/null || true
      fi

      # Check if the command failed due to OOM
      if [[ ${cmd_exit_status:-0} -ne 0 ]] || echo "$out" | grep -q "OutOfMemoryError"; then
        echo "WARNING: Batch size ${bs} failed with OutOfMemoryError. Skipping..."
        # Write NA values to CSV for failed run
        echo "${tp},${bs},${ilen},${OLEN},${ATTENTION_BACKEND},NA,NA,NA,NA,NA,NA" >> "${OUTPUT_CSV}"
        continue
      fi

      # Isolate the section after "Benchmark ..." (assumes final block of output).
      last_section=$(echo "$out" | awk '/Benchmark/ {flag=1; next} flag')

      # Parse metrics:
      prefill_latency=$(echo "$last_section" | grep -oP 'Prefill\. latency:\s*\K[\d.]+' | tail -n 1)
      prefill_throughput=$(echo "$last_section" | grep -oP 'Prefill\. latency:.*throughput:\s*\K[\d.]+' | tail -n 1)

      decode_median_latency=$(echo "$last_section" | grep -oP 'Decode\.\s+median latency:\s*\K[\d.]+' | tail -n 1)
      decode_median_throughput=$(echo "$last_section" | grep -oP 'Decode\.\s+median latency:.*median throughput:\s*\K[\d.]+' | tail -n 1)

      total_latency=$(echo "$last_section" | grep -oP 'Total\. latency:\s*\K[\d.]+' | tail -n 1)
      e2e_throughput=$(echo "$last_section" | grep -oP 'Total\. latency:.*throughput:\s*\K[\d.]+' | tail -n 1)

      # Append CSV row:
      echo "${tp},${bs},${ilen},${OLEN},${ATTENTION_BACKEND},${prefill_latency},${decode_median_latency},${total_latency},${prefill_throughput},${decode_median_throughput},${e2e_throughput}" >> "${OUTPUT_CSV}"

      # Note: JSONL output has been disabled to prevent unnecessary file generation
    done
  done
done

echo "All done! Results saved to ${OUTPUT_CSV}."
