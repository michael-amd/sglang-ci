#!/usr/bin/env bash
# ------------------------------------------------------------------------------
# grok_perf_offline_csv.sh
#
# This script runs the offline benchmark command for TP=8 and multiple batch sizes.
#
# It:
#   1. Container Management:
#         - If not inside a container (INSIDE_CONTAINER not set):
#             • Checks if the docker command is available.
#             • Otherwise, extracts REPO and LATEST_TAG from docker_image.
#             • Builds container name as "michael_${REPO}_${LATEST_TAG}".
#             • If a container with that name exists, starts it if not running.
#             • Otherwise, pulls the image and starts a new container.
#             • Re-invokes this script inside the container using docker exec,
#               passing INSIDE_CONTAINER=1 and LATEST_TAG.
#
#   2. Once inside the container (INSIDE_CONTAINER is set):
#         - Changes directory to /mnt/raid/michael/sgl_benchmark_ci/.
#         - Creates (or reuses) a run folder named:
#               {current_date}_{LATEST_TAG}_GROK1_CK-MOE-I4F8-AITER-DECODE-ATTN_offline
#         - Runs the offline benchmark workflow.
#
# ------------------------------------------------------------------------------
 
# Docker image variable with default value.
docker_image="rocm/sgl-dev:20250331rc"

# ---------------------------
# 0. Container Management (if applicable)
# ---------------------------
if [ -z "$INSIDE_CONTAINER" ]; then
    if ! command -v docker > /dev/null 2>&1; then
        echo "Docker command not found. Assuming script is running inside container. Proceeding..."
        INSIDE_CONTAINER=1
    else
        # Extract REPO and LATEST_TAG from docker_image.
        IMAGE_WITH_TAG=${docker_image#*/}  # e.g., "sgl-dev:20250318rc"
        REPO=${IMAGE_WITH_TAG%%:*}           # e.g., "sgl-dev"
        LATEST_TAG=${IMAGE_WITH_TAG#*:}       # e.g., "20250318rc"
        
        # Build container name.
        CONTAINER_NAME="michael_${REPO}_${LATEST_TAG}"
        
        # Check if a container with that name exists (even if stopped).
        existing_container=$(docker ps -a --filter "name=^/${CONTAINER_NAME}$" --format "{{.Names}}")
        if [ -n "$existing_container" ]; then
            # Container exists; check if it is running.
            running_container=$(docker ps --filter "name=^/${CONTAINER_NAME}$" --format "{{.Names}}")
            if [ -z "$running_container" ]; then
                echo "Container ${CONTAINER_NAME} exists but is not running. Starting it..."
                docker start "$CONTAINER_NAME"
            else
                echo "Container ${CONTAINER_NAME} is already running."
            fi
        else
            echo "Container ${CONTAINER_NAME} does not exist. Pulling image ${docker_image} and starting a new container..."
            docker pull "$docker_image"
            docker run -d --name "$CONTAINER_NAME" "$docker_image" tail -f /dev/null
        fi
        
        echo "Re-invoking the script inside container ${CONTAINER_NAME}..."
        docker exec -e INSIDE_CONTAINER=1 -e LATEST_TAG="$LATEST_TAG" "$CONTAINER_NAME" bash /mnt/raid/michael/sgl_benchmark_ci/grok_perf_offline_csv.sh
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

current_date=$(date +%Y%m%d)
folder="${current_date}_${LATEST_TAG}_GROK1_CK-MOE-I4F8-AITER-DECODE-ATTN_offline"
mkdir -p "$folder"

# Write config.json with docker image name from the variable.
echo "{\"docker\": \"${docker_image}\"}" > "${folder}/config.json"
echo "Wrote config.json to ${folder}/config.json"

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
BATCH_SIZES=(1 2 4 8 16 32 64 128 256)

# Write CSV header with ordering:
echo "TP,batch_size,IL,OL,Prefill_latency(s),Median_decode_latency(s),E2E_Latency(s),Prefill_Throughput(token/s),Median_Decode_Throughput(token/s),E2E_Throughput(token/s)" > "${folder}/${current_date}_GROK1_CK-MOE-I4F8-AITER-DECODE-ATTN_offline.csv"

# Loop over batch sizes (TP fixed to 8)
for tp in "${TP_VALUES[@]}"; do
  for bs in "${BATCH_SIZES[@]}"; do
    echo "Running TP=${tp}, batch_size=${bs} ..."
    
    # Run the benchmark command and capture output.
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
        --cuda-graph-max-bs 1024 2>&1
    )
    
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
    echo "${tp},${bs},${ILEN},${OLEN},${prefill_latency},${decode_median_latency},${total_latency},${prefill_throughput},${decode_median_throughput},${e2e_throughput}" >> "${folder}/${current_date}_GROK1_CK-MOE-I4F8-AITER-DECODE-ATTN_offline.csv"
    
    # If a result file (result.jsonl) is produced, rename it.
    if [ -f result.jsonl ]; then
      dest_json="${folder}/${current_date}_GROK1_CK-MOE-I4F8-AITER-DECODE-ATTN_offline.jsonl"
      mv result.jsonl "$dest_json"
      echo "Saved JSON result to ${dest_json}"
    fi
  done
done

echo "All done! Results saved to ${folder}/${current_date}_GROK1_CK-MOE-I4F8-AITER-DECODE-ATTN_offline.csv and JSON result stored as ${folder}/${current_date}_GROK1_CK-MOE-I4F8-AITER-DECODE-ATTN_offline.jsonl (if produced)."
