#!/usr/bin/env bash
# ------------------------------------------------------------------------------
# perf_online_csv.sh
#
# This script benchmarks online serving performance for GROK1.
#
# It now supports being executed from outside a container by managing container
# startup and then re-invoking itself inside.
#
# Workflow:
#   1. If not inside a container (INSIDE_CONTAINER not set):
#         - Check if the docker command is available.
#             • If not available, assume we are inside the container and skip container management.
#         - Otherwise, extract REPO and LATEST_TAG from DOCKER_NAME.
#         - Build container name as "michael_${REPO}_${LATEST_TAG}".
#         - If a container with that name exists:
#               • Start it if not already running.
#         - Otherwise:
#               • Pull the image and start a new container.
#         - Run this script inside the container using docker exec,
#           passing INSIDE_CONTAINER=1 and LATEST_TAG.
#
#   2. Once inside the container (INSIDE_CONTAINER is set):
#         - Change directory to /mnt/raid/michael/sgl_benchmark_ci/.
#         - Create (or reuse) a run folder named:
#               {current_date}_{LATEST_TAG}_GROK1_CK-MOE-I4F8-AITER-DECODE-ATTN_online
#         - Run the online benchmark workflow.
#
# ------------------------------------------------------------------------------
 
# Set DOCKER_NAME variable
DOCKER_NAME="rocm/sgl-dev:20250331rc"
 
# ---------------------------
# 0. Container Management (if applicable)
# ---------------------------
if [ -z "$INSIDE_CONTAINER" ]; then
    # If docker command is not available, assume we're inside a container.
    if ! command -v docker > /dev/null 2>&1; then
        echo "Docker command not found. Assuming script is running inside container. Proceeding..."
        INSIDE_CONTAINER=1
    else
        # Extract repository and LATEST_TAG from DOCKER_NAME.
        IMAGE_WITH_TAG=${DOCKER_NAME#*/}      # yields "sgl-dev:20250331rc"
        REPO=${IMAGE_WITH_TAG%%:*}             # yields "sgl-dev"
        LATEST_TAG=${IMAGE_WITH_TAG#*:}         # yields "20250331rc"
        
        # Build container name.
        CONTAINER_NAME="michael_${REPO}_${LATEST_TAG}"
        
        # Check if container exists (even if stopped)
        existing_container=$(docker ps -a --filter "name=^/${CONTAINER_NAME}$" --format "{{.Names}}")
        if [ -n "$existing_container" ]; then
            # If container exists, check if it is running.
            running_container=$(docker ps --filter "name=^/${CONTAINER_NAME}$" --format "{{.Names}}")
            if [ -z "$running_container" ]; then
                echo "Container ${CONTAINER_NAME} exists but is not running. Starting it..."
                docker start "$CONTAINER_NAME"
            else
                echo "Container ${CONTAINER_NAME} is already running."
            fi
        else
            echo "Container ${CONTAINER_NAME} does not exist. Pulling image ${DOCKER_NAME} and starting a new container..."
            docker pull "$DOCKER_NAME"
            # Run container in detached mode with a dummy command to keep it alive.
            docker run -d --name "$CONTAINER_NAME" "$DOCKER_NAME" tail -f /dev/null
        fi
        
        echo "Re-invoking the script inside container ${CONTAINER_NAME}..."
        # Execute this script inside the container; pass INSIDE_CONTAINER=1 and LATEST_TAG.
        docker exec -e INSIDE_CONTAINER=1 -e LATEST_TAG="$LATEST_TAG" "$CONTAINER_NAME" bash /mnt/raid/michael/sgl_benchmark_ci/perf_online_csv.sh
        exit 0
    fi
fi

# ---------------------------
# 1. Inside Container: Setup Run Folder
# ---------------------------
# We are now inside the container.
cd /mnt/raid/michael/sgl_benchmark_ci/ || { echo "Cannot change to /mnt/raid/michael/sgl_benchmark_ci/ directory"; exit 1; }

# Ensure LATEST_TAG is defined (if not, extract from DOCKER_NAME)
if [ -z "$LATEST_TAG" ]; then
    IMAGE_WITH_TAG=${DOCKER_NAME#*/} 
    LATEST_TAG=${IMAGE_WITH_TAG#*:}
fi

current_date=$(date +%Y%m%d)
folder="${current_date}_${LATEST_TAG}_GROK1_CK-MOE-I4F8-AITER-DECODE-ATTN_online"
if [ ! -d "$folder" ]; then
    mkdir -p "$folder"
    echo "{\"docker\": \"${DOCKER_NAME}\"}" > "${folder}/config.json"
    echo "Created folder and wrote config.json to ${folder}/config.json"
else
    echo "Folder ${folder} already exists. Checking for missing logs in subsequent runs."
fi
OUTPUT_CSV="${folder}/${current_date}_${LATEST_TAG}_GROK1_CK-MOE-I4F8-AITER-DECODE-ATTN_online.csv"
 
# ---------------------------
# 2. Functions to Launch and Shutdown Server per Mode
# ---------------------------
launch_server() {
    local backend=$1
    SERVER_LOG="${folder}/server_output_${backend}.log"
    rm -f "$SERVER_LOG"
    echo "Launching server with attention-backend ${backend}..."
    if [ "$backend" == "aiter" ]; then
        RCCL_MSCCL_ENABLE=0 CK_MOE=1 USE_INT4_WEIGHT=1 \
        python3 -m sglang.launch_server \
          --model /mnt/raid/models/huggingface/amd--grok-1-W4A8KV8/ \
          --tokenizer-path Xenova/grok-1-tokenizer \
          --tp 8 --quantization fp8 --trust-remote-code \
          --attention-backend aiter > "$SERVER_LOG" 2>&1 &
    elif [ "$backend" == "aiter_decode" ]; then
        RCCL_MSCCL_ENABLE=0 CK_MOE=1 USE_INT4_WEIGHT=1 \
        python3 -m sglang.launch_server \
          --model /mnt/raid/models/amd--grok-1-W4A8KV8/ \
          --tokenizer-path Xenova/grok-1-tokenizer \
          --tp 8 --quantization fp8 --trust-remote-code \
          --attention-backend aiter_decode > "$SERVER_LOG" 2>&1 &
    fi
    SERVER_PID=$!
    echo "Server launched (PID = $SERVER_PID) with backend ${backend}. Waiting for readiness..."
    while true; do
        if grep -q "The server is fired up and ready to roll!" "$SERVER_LOG"; then
            echo "Server with backend ${backend} is ready!"
            break
        fi
        sleep 1
    done
}

shutdown_server() {
    echo "Shutting down server (PID = $SERVER_PID)..."
    kill "$SERVER_PID"
    sleep 2
}

# ---------------------------
# 3. Embedded Client Benchmark Code (runs only missing logs)
# ---------------------------
run_client_benchmark() {
    local mode=$1
    TIMESTAMP=$(date +%Y%m%d_%H%M%S)
    REQUEST_RATES=(1 2 4 8 16)
    echo "Running client benchmark for mode ${mode}..."
    for RATE in "${REQUEST_RATES[@]}"; do
        for i in {1..3}; do
            existing_log=$(ls "${folder}/sglang_client_log_grok1_${mode}_${RATE}_run${i}"_*.log 2>/dev/null)
            if [ -n "$existing_log" ]; then
                echo "Log for mode ${mode}, rate ${RATE}, run ${i} already exists. Skipping."
                continue
            fi
            LOGFILE="${folder}/sglang_client_log_grok1_${mode}_${RATE}_run${i}_${TIMESTAMP}.log"
            echo "Running benchmark with request rate: $RATE (Run $i) for mode ${mode}" | tee -a "$LOGFILE"
            NUM_PROMPTS=$(( 300 * RATE ))
            if [ "$NUM_PROMPTS" -gt 2400 ]; then
                NUM_PROMPTS=2400
            fi
            CMD="python3 -m sglang.bench_serving --backend sglang --tokenizer Xenova/grok-1-tokenizer --dataset-name random --random-input 1024 --random-output 1024 --num-prompts $NUM_PROMPTS --request-rate $RATE --output-file online.jsonl"
            echo "Executing: $CMD" | tee -a "$LOGFILE"
            eval "$CMD" 2>&1 | tee -a "$LOGFILE"
        done
    done
}

# ---------------------------
# 4. Function to Select Best Metrics from Logs
# ---------------------------
get_best_metrics() {
    local mode=$1
    local rate=$2
    local best_e2e=""
    local best_ttft=""
    local best_itl=""
    local best_file=""
    for f in $(ls "${folder}/sglang_client_log_grok1_${mode}_${rate}_run"*".log" 2>/dev/null); do
        local e2e=$(grep -oP 'Median E2E Latency \(ms\):\s*\K[\d.]+' "$f" | head -n1)
        if [ -z "$e2e" ]; then
            continue
        fi
        if [ -z "$best_file" ]; then
            best_file="$f"
            best_e2e="$e2e"
        else
            cmp=$(awk -v a="$e2e" -v b="$best_e2e" 'BEGIN { print (a < b) ? 1 : 0 }')
            if [ "$cmp" -eq 1 ]; then
                best_file="$f"
                best_e2e="$e2e"
            fi
        fi
    done
    if [ -z "$best_file" ]; then
        echo "NA NA NA"
    else
        best_ttft=$(grep -oP 'Median TTFT \(ms\):\s*\K[\d.]+' "$best_file" | head -n1)
        best_itl=$(grep -oP 'Median ITL \(ms\):\s*\K[\d.]+' "$best_file" | head -n1)
        [ -z "$best_ttft" ] && best_ttft="NA"
        [ -z "$best_itl" ] && best_itl="NA"
        echo "$best_e2e $best_ttft $best_itl"
    fi
}

# ---------------------------
# 5. Run Benchmarks for Each Mode
# ---------------------------
echo "Starting benchmarks for mode 'aiter' (prefill+decode)..."
launch_server "aiter"
run_client_benchmark "aiter"
shutdown_server

echo "Starting benchmarks for mode 'aiter_decode' (decode only)..."
launch_server "aiter_decode"
run_client_benchmark "decode"
shutdown_server

# ---------------------------
# 6. Parse Logs and Generate CSV Summary (with Ratio Rows)
# ---------------------------
REQ_RATES=(1 2 4 8 16)
H100_E2E=(13209 13874 16613 44918 85049)
H100_TTFT=(99.1 102.0 113.4 170.7 520.9)
H100_ITL=(23.0 24.4 25.9 63.9 108.6)

declare -A best_e2e_aiter best_ttft_aiter best_itl_aiter
declare -A best_e2e_decode best_ttft_decode best_itl_decode

for rate in "${REQ_RATES[@]}"; do
    read e2e_a ttft_a itl_a < <(get_best_metrics "aiter" "$rate")
    best_e2e_aiter[$rate]="$e2e_a"
    best_ttft_aiter[$rate]="$ttft_a"
    best_itl_aiter[$rate]="$itl_a"
    
    read e2e_d ttft_d itl_d < <(get_best_metrics "decode" "$rate")
    best_e2e_decode[$rate]="$e2e_d"
    best_ttft_decode[$rate]="$ttft_d"
    best_itl_decode[$rate]="$itl_d"
done

compute_ratio() {
    local ref=$1
    local meas=$2
    if [[ "$meas" == "NA" || "$meas" == "0" ]]; then
        echo "NA"
    else
        awk -v r="$ref" -v m="$meas" 'BEGIN { printf "%d", (r/m)*100 }'
    fi
}

{
  echo "Online mode - GROK1 (${DOCKER_NAME})"
  echo ""
  echo "Median E2E Latency (ms, lower better)"
  printf "request rate"
  for rate in "${REQ_RATES[@]}"; do
      printf "\t%s" "$rate"
  done
  echo ""
  printf "H100"
  for val in "${H100_E2E[@]}"; do
      printf "\t%s" "$val"
  done
  echo ""
  printf "MI300x-aiter (prefill+decode), dell300x-pla-t10-17"
  for rate in "${REQ_RATES[@]}"; do
      printf "\t%s" "${best_e2e_aiter[$rate]}"
  done
  echo ""
  printf "MI300x-aiter_decode (decode only), dell300x-pla-t10-17"
  for rate in "${REQ_RATES[@]}"; do
      printf "\t%s" "${best_e2e_decode[$rate]}"
  done
  echo ""
  printf "H100/MI300x-aiter"
  for idx in "${!REQ_RATES[@]}"; do
      rate=${REQ_RATES[$idx]}
      ratio=$(compute_ratio "${H100_E2E[$idx]}" "${best_e2e_aiter[$rate]}")
      printf "\t%s%%" "$ratio"
  done
  echo ""
  printf "H100/MI300x-aiter_decode"
  for idx in "${!REQ_RATES[@]}"; do
      rate=${REQ_RATES[$idx]}
      ratio=$(compute_ratio "${H100_E2E[$idx]}" "${best_e2e_decode[$rate]}")
      printf "\t%s%%" "$ratio"
  done
  echo ""
  echo ""
  echo "Median TTFT (ms, lower better)"
  printf "request rate"
  for rate in "${REQ_RATES[@]}"; do
      printf "\t%s" "$rate"
  done
  echo ""
  printf "H100"
  for val in "${H100_TTFT[@]}"; do
      printf "\t%s" "$val"
  done
  echo ""
  printf "MI300x-aiter (prefill+decode), dell300x-pla-t10-17"
  for rate in "${REQ_RATES[@]}"; do
      printf "\t%s" "${best_ttft_aiter[$rate]}"
  done
  echo ""
  printf "MI300x-aiter_decode (decode only), dell300x-pla-t10-17"
  for rate in "${REQ_RATES[@]}"; do
      printf "\t%s" "${best_ttft_decode[$rate]}"
  done
  echo ""
  printf "H100/MI300x-aiter"
  for idx in "${!REQ_RATES[@]}"; do
      rate=${REQ_RATES[$idx]}
      ratio=$(compute_ratio "${H100_TTFT[$idx]}" "${best_ttft_aiter[$rate]}")
      printf "\t%s%%" "$ratio"
  done
  echo ""
  printf "H100/MI300x-aiter_decode"
  for idx in "${!REQ_RATES[@]}"; do
      rate=${REQ_RATES[$idx]}
      ratio=$(compute_ratio "${H100_TTFT[$idx]}" "${best_ttft_decode[$rate]}")
      printf "\t%s%%" "$ratio"
  done
  echo ""
  echo ""
  echo "Median ITL (ms, lower better)"
  printf "request rate"
  for rate in "${REQ_RATES[@]}"; do
      printf "\t%s" "$rate"
  done
  echo ""
  printf "H100"
  for val in "${H100_ITL[@]}"; do
      printf "\t%s" "$val"
  done
  echo ""
  printf "MI300x-aiter (prefill+decode), dell300x-pla-t10-17"
  for rate in "${REQ_RATES[@]}"; do
      printf "\t%s" "${best_itl_aiter[$rate]}"
  done
  echo ""
  printf "MI300x-aiter_decode (decode only), dell300x-pla-t10-17"
  for rate in "${REQ_RATES[@]}"; do
      printf "\t%s" "${best_itl_decode[$rate]}"
  done
  echo ""
  printf "H100/MI300x-aiter"
  for idx in "${!REQ_RATES[@]}"; do
      rate=${REQ_RATES[$idx]}
      ratio=$(compute_ratio "${H100_ITL[$idx]}" "${best_itl_aiter[$rate]}")
      printf "\t%s%%" "$ratio"
  done
  echo ""
  printf "H100/MI300x-aiter_decode"
  for idx in "${!REQ_RATES[@]}"; do
      rate=${REQ_RATES[$idx]}
      ratio=$(compute_ratio "${H100_ITL[$idx]}" "${best_itl_decode[$rate]}")
      printf "\t%s%%" "$ratio"
  done
  echo ""
} > "$OUTPUT_CSV"

echo "CSV summary saved to ${OUTPUT_CSV}"
echo "All done! Client logs and CSV summary are saved in ${folder}."
