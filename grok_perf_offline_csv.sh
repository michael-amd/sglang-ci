#!/usr/bin/env bash
# ------------------------------------------------------------------------------
# grok_perf_offline_csv.sh
#
# Offline Grok-1 benchmark.  Supports --docker_image=<image[:tag]> override.
#
# USAGE:
#   bash grok_perf_offline_csv.sh --docker_image=sgl-dev:20250331rc
#   bash grok_perf_offline_csv.sh --docker_image=sgl-dev:20250429 
# ------------------------------------------------------------------------------
 
###############################################################################
# Parse CLI options – only --docker_image / --docker-image is supported.
###############################################################################
docker_image_default="rocm/sgl-dev:20250331rc"   # fall-back
docker_image=""

for arg in "$@"; do
  case $arg in
    --docker_image=*|--docker-image=*)
      docker_image="${arg#*=}"
      shift
      ;;
  esac
done

# If not provided, also allow a positional 1st argument for backward-compat.
docker_image="${docker_image:-${1:-$docker_image_default}}"

# ---------------------------
# 0. Container Management (if applicable)
# ---------------------------
if [ -z "${INSIDE_CONTAINER:-}" ]; then
  if ! command -v docker >/dev/null 2>&1; then
    echo "[csv] Docker not found — already inside container."
    INSIDE_CONTAINER=1
  else
    # ---- 0.1 Normalise image name (auto-prefix "rocm/")
    if [[ "$docker_image" != */* ]]; then
      FULL_IMAGE="rocm/${docker_image}"
    else
      FULL_IMAGE="$docker_image"
    fi

    IMAGE_WITH_TAG="${FULL_IMAGE##*/}"          # sgl-dev:20250331rc
    REPO="${IMAGE_WITH_TAG%%:*}"                # sgl-dev
    LATEST_TAG="${IMAGE_WITH_TAG#*:}"           # 20250331rc
    CONTAINER_NAME="${REPO}_${LATEST_TAG}"

    echo "[csv] Target container : ${CONTAINER_NAME}"
    echo "[csv] Docker image     : ${FULL_IMAGE}"

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
           --docker_image="${FULL_IMAGE}"
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

folder="offline/${LATEST_TAG}_GROK1_MOE-I4F8_offline"
mkdir -p "$folder"

# ---------------------------
# Offline Benchmark Configuration and Execution
# ---------------------------
# Model and tokenizer paths
MODEL="/mnt/raid/models/huggingface/amd--grok-1-W4A8KV8/"
TOKENIZER="Xenova/grok-1-tokenizer"

# Input/Output lengths
ILEN=1024
OLEN=128

# Only use TP=8; offline benchmarks vary over batch sizes.
TP_VALUES=(8)
BATCH_SIZES=(1 2 4 8 16 32 64)

# Write CSV header with ordering:
echo "TP,batch_size,IL,OL,Prefill_latency(s),Median_decode_latency(s),E2E_Latency(s),Prefill_Throughput(token/s),Median_Decode_Throughput(token/s),E2E_Throughput(token/s)" > "${folder}/${LATEST_TAG}_GROK1_MOE-I4F8_offline.csv"

# Loop over batch sizes (TP fixed to 8)
for tp in "${TP_VALUES[@]}"; do
  for bs in "${BATCH_SIZES[@]}"; do
    echo "Running TP=${tp}, batch_size=${bs} ..."

    ## NEW: file to keep full stdout/stderr
    log_file="${folder}/tp${tp}_bs${bs}.log"

    # -----------------------------------------------------------------------
    # Select command variant depending on whether tag ends with 'rc'
    # -----------------------------------------------------------------------
    if [[ "$LATEST_TAG" == *rc* ]]; then
      # ---- RC image (original AITer backend) ----
      out=$(
        CK_MOE=1 USE_INT4_WEIGHT=1 MOE_PADDING=0 \
        python3 -m sglang.bench_one_batch \
          --model "${MODEL}" \
          --tokenizer-path "${TOKENIZER}" \
          --tp "${tp}" \
          --batch-size "${bs}" \
          --input "${ILEN}" \
          --output "${OLEN}" \
          --attention-backend aiter \
          --sampling-backend pytorch \
          --quantization fp8 \
          --trust-remote-code \
          --cuda-graph-max-bs 1024 2>&1 | tee "${log_file}"
      )
    else
      # ---- Non-RC image (Triton backend + updated env vars) ----
      out=$(
        SGLANG_AITER_MOE=1 SGLANG_INT4_WEIGHT=1 SGLANG_MOE_PADDING=0 \
        python3 -m sglang.bench_one_batch \
          --model "${MODEL}" \
          --tokenizer-path "${TOKENIZER}" \
          --tp "${tp}" \
          --batch-size "${bs}" \
          --input "${ILEN}" \
          --output "${OLEN}" \
          --attention-backend triton \
          --sampling-backend pytorch \
          --quantization fp8 \
          --trust-remote-code \
          --cuda-graph-max-bs 1024 2>&1 | tee "${log_file}"
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
    echo "${tp},${bs},${ILEN},${OLEN},${prefill_latency},${decode_median_latency},${total_latency},${prefill_throughput},${decode_median_throughput},${e2e_throughput}" >> "${folder}/${LATEST_TAG}_GROK1_MOE-I4F8_offline.csv"
    
    # If a result file (result.jsonl) is produced, rename it.
    if [ -f result.jsonl ]; then
      dest_json="${folder}/${LATEST_TAG}_GROK1_MOE-I4F8_offline.jsonl"
      mv result.jsonl "$dest_json"
      echo "Saved JSON result to ${dest_json}"
    fi
  done
done

echo "All done! Results saved to ${folder}/${LATEST_TAG}_GROK1_MOE-I4F8_offline.csv and JSON result stored as ${folder}/${LATEST_TAG}_GROK1_MOE-I4F8_offline.jsonl (if produced)."
