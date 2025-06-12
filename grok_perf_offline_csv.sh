#!/usr/bin/env bash
# ------------------------------------------------------------------------------
# grok_perf_offline_csv.sh
#
# Offline Grok-1 benchmark.  Supports --docker_image=<image[:tag]> override.
# Now also supports --mode=long_context and --mode=dummy for different test modes.
#
# USAGE:
#   bash grok_perf_offline_csv.sh --docker_image=rocm/sgl-dev:20250331rc
#   bash grok_perf_offline_csv.sh --docker_image=rocm/sgl-dev:20250429
#   bash grok_perf_offline_csv.sh --docker_image=lmsysorg/sglang:v0.4.6.post3-rocm630
#   bash grok_perf_offline_csv.sh --docker_image=lmsysorg/sglang:v0.4.7
#   bash grok_perf_offline_csv.sh --mode=long_context
#   bash grok_perf_offline_csv.sh --mode=dummy
#   bash grok_perf_offline_csv.sh --docker_image=rocm/sgl-dev:20250331rc --mode=long_context
# ------------------------------------------------------------------------------
 
###############################################################################
# Parse CLI options – --docker_image / --docker-image and --mode are supported.
###############################################################################
docker_image_default="rocm/sgl-dev:20250331rc"   # fall-back
docker_image=""
mode="normal"  # default mode (normal, long_context, or dummy)

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
  esac
done

# If not provided, also allow a positional 1st argument for backward-compat.
docker_image="${docker_image:-${1:-$docker_image_default}}"

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
      else
        echo "[csv] Starting existing container ..."
        docker start "${CONTAINER_NAME}"
      fi
    else
      echo "[csv] Pulling image and creating container ..."
      docker pull "${FULL_IMAGE}"
      docker run -d --name "${CONTAINER_NAME}" \
        --shm-size 32g --ipc=host --cap-add=SYS_PTRACE --network=host \
        --device=/dev/kfd --device=/dev/dri --security-opt seccomp=unconfined \
        -v /mnt/raid/:/mnt/raid/ --group-add video --privileged \
        -w /sgl-workspace "${FULL_IMAGE}" tail -f /dev/null
    fi

    # ---- 0.3 Re-invoke this script inside the container
    echo "[csv] Re-invoking inside ${CONTAINER_NAME} ..."
    docker exec \
      -e INSIDE_CONTAINER=1 \
      -e LATEST_TAG="${LATEST_TAG}" \
      "${CONTAINER_NAME}" \
      bash /mnt/raid/michael/sgl_benchmark_ci/grok_perf_offline_csv.sh \
           --docker_image="${FULL_IMAGE}" \
           --mode="${mode}"
    exit 0
  fi
fi

# ---------------------------
# 1. Inside Container: Setup Run Folder
# ---------------------------
cd /mnt/raid/michael/sgl_benchmark_ci/ || { echo "Cannot change to /mnt/raid/michael/sgl_benchmark_ci/ directory"; exit 1; }

# If LATEST_TAG is not already defined, extract it from docker_image.
if [ -z "$LATEST_TAG" ]; then
    IMAGE_WITH_TAG=${docker_image#*/} 
    LATEST_TAG=${IMAGE_WITH_TAG#*:}
fi

# ---------------------------
# Mode-specific Configuration
# ---------------------------
MODEL_NAME=GROK1

# Common configuration
MODEL="/mnt/raid/models/huggingface/amd--grok-1-W4A8KV8/"
TOKENIZER="Xenova/grok-1-tokenizer"

# Set mode suffix for folder/file names
mode_suffix=""
if [[ "$mode" != "normal" ]]; then
  mode_suffix="_${mode}"
fi

# Base folder structure
folder="offline/${MODEL_NAME}/${LATEST_TAG}_${MODEL_NAME}_MOE-I4F8_offline${mode_suffix}"

if [[ "$mode" == "long_context" ]]; then
  # Long context mode configuration
  INPUT_LENGTHS=(8192 16384 32768)
  OLEN=10
  TP_VALUES=(8)
  BATCH_SIZES=(1)
elif [[ "$mode" == "dummy" ]]; then
  # Dummy mode configuration
  MODEL="/mnt/raid/models/dummy_prod1/"
  INPUT_LENGTHS=(256)
  OLEN=4096
  TP_VALUES=(8)
  BATCH_SIZES=(2)
else
  # Normal mode configuration (default)
  INPUT_LENGTHS=(1024)
  OLEN=128
  TP_VALUES=(8)
  BATCH_SIZES=(1 2 4 8 16 32 64 128 256)
fi

# Set output file names
OUTPUT_CSV="${folder}/${LATEST_TAG}_${MODEL_NAME}_MOE-I4F8_offline${mode_suffix}.csv"
JSON_NAME="${LATEST_TAG}_${MODEL_NAME}_MOE-I4F8_offline${mode_suffix}.jsonl"

mkdir -p "$folder"

# Write config.json for all modes to maintain docker image info
echo "{\"docker\": \"${docker_image}\"}" > "${folder}/config.json"
echo "Wrote config.json to ${folder}/config.json"

# Write CSV header with ordering:
echo "TP,batch_size,IL,OL,Prefill_latency(s),Median_decode_latency(s),E2E_Latency(s),Prefill_Throughput(token/s),Median_Decode_Throughput(token/s),E2E_Throughput(token/s)" > "${OUTPUT_CSV}"

# Loop over TP, batch sizes, and input lengths
for tp in "${TP_VALUES[@]}"; do
  for bs in "${BATCH_SIZES[@]}"; do
    for ilen in "${INPUT_LENGTHS[@]}"; do
      echo "Running TP=${tp}, batch_size=${bs}, input_length=${ilen} ..."

      ## NEW: file to keep full stdout/stderr
      log_file="${folder}/tp${tp}_bs${bs}_il${ilen}.log"

      # -----------------------------------------------------------------------
      # Select command variant depending on mode and tag
      # -----------------------------------------------------------------------
      if [[ "$mode" == "dummy" ]]; then
        # Dummy mode command
        # For dummy mode, we need to check if this is an RC image or not
        if [[ "$LATEST_TAG" == *rc* ]]; then
          # RC image - use aiter backend
          out=$(
            RCCL_MSCCL_ENABLE=0 CK_MOE=1 USE_INT4_WEIGHT=0 MOE_PADDING=0 \
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
        else
          # Non-RC image - use triton backend
          out=$(
            RCCL_MSCCL_ENABLE=0 CK_MOE=1 USE_INT4_WEIGHT=0 MOE_PADDING=0 \
            python3 -m sglang.bench_one_batch \
              --model "${MODEL}" \
              --load-format dummy \
              --tokenizer-path "${TOKENIZER}" \
              --tp "${tp}" \
              --batch-size "${bs}" \
              --input "${ilen}" \
              --output "${OLEN}" \
              --attention-backend triton \
              --torch-compile-max-bs 4 \
              --quantization fp8 \
              --trust-remote-code \
              --enable-torch-compile 2>&1 | tee "${log_file}"
          )
        fi
      elif [[ "$mode" == "long_context" ]]; then
        # Long context mode command
        # For long_context mode, also check RC vs non-RC
        if [[ "$LATEST_TAG" == *rc* ]]; then
          # RC image - use aiter backend
          out=$(
            RCCL_MSCCL_ENABLE=0 CK_MOE=1 MOE_PADDING=0 USE_INT4_WEIGHT=1 \
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
        else
          # Non-RC image - use triton backend
          out=$(
            RCCL_MSCCL_ENABLE=0 CK_MOE=1 MOE_PADDING=0 USE_INT4_WEIGHT=1 \
            python3 -m sglang.bench_one_batch \
              --model "${MODEL}" \
              --tokenizer-path "${TOKENIZER}" \
              --tp "${tp}" \
              --batch-size "${bs}" \
              --input "${ilen}" \
              --output "${OLEN}" \
              --attention-backend triton \
              --quantization fp8 \
              --trust-remote-code 2>&1 | tee "${log_file}"
          )
        fi
      elif [[ "$LATEST_TAG" == *rc* ]]; then
        # ---- RC image (original AITer backend) ----
        out=$(
          CK_MOE=1 USE_INT4_WEIGHT=1 MOE_PADDING=0 \
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
            --cuda-graph-max-bs 1024 2>&1 | tee "${log_file}"
        )
      else
        # ---- Non-RC image (Triton backend + updated env vars) ----
        mem_fraction_arg=""
        if [[ "$bs" -eq 128 ]]; then
          mem_fraction_arg=" --mem-fraction-static 0.85"
        elif [[ "$bs" -eq 256 ]]; then
          mem_fraction_arg=" --mem-fraction-static 0.8"
        fi
        
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
        
        out=$(
          env ${aiter_env_var}=1 SGLANG_INT4_WEIGHT=1 SGLANG_MOE_PADDING=0 \
          python3 -m sglang.bench_one_batch \
            --model "${MODEL}" \
            --tokenizer-path "${TOKENIZER}" \
            --tp "${tp}" \
            --batch-size "${bs}" \
            --input "${ilen}" \
            --output "${OLEN}" \
            --attention-backend triton \
            --sampling-backend pytorch \
            --quantization fp8 \
            --trust-remote-code \
            --cuda-graph-max-bs 1024${mem_fraction_arg} 2>&1 | tee "${log_file}"
        )
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
      echo "${tp},${bs},${ilen},${OLEN},${prefill_latency},${decode_median_latency},${total_latency},${prefill_throughput},${decode_median_throughput},${e2e_throughput}" >> "${OUTPUT_CSV}"
      
      # If a result file (result.jsonl) is produced, rename it.
      if [ -f result.jsonl ]; then
        dest_json="${folder}/${JSON_NAME}"
        mv result.jsonl "$dest_json"
        echo "Saved JSON result to ${dest_json}"
      fi
    done
  done
done

echo "All done! Results saved to ${OUTPUT_CSV} and JSON result stored as ${folder}/${JSON_NAME} (if produced)."
