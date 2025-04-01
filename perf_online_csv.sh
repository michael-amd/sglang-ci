#!/usr/bin/env bash
# ------------------------------------------------------------------------------
# perf_online_csv.sh
#
# This script benchmarks online serving performance for GROK1.
#
# It:
#   1. Creates a folder named {date}_GROK1_CK-MOE-I4F8-AITER-DECODE-ATTN_online.
#      It writes a config.json file in that folder with the docker image name from the variable DOCKER_NAME.
#   2. For each mode, launches the server with the appropriate attention backend,
#      runs embedded client benchmark code for various request rates (logging directly
#      into the created folder), and then shuts down the server.
#      • For mode "aiter": uses --attention-backend aiter.
#      • For mode "decode": uses --attention-backend aiter_decode.
#   3. The embedded client code runs each request rate (1,2,4,8,16) three times,
#      logging output to files named as:
#         sglang_client_log_grok1_${MODE}_${RATE}_run${i}_${TIMESTAMP}.log
#      These files are created directly in the run folder.
#   4. After both modes have run, the script parses the best (lowest Median E2E Latency)
#      metrics from the generated log files for each request rate and builds a CSV summary.
#
# The final CSV summary is stored in the run folder with a header line that uses the DOCKER_NAME.
# ------------------------------------------------------------------------------
 
# Set DOCKER_NAME variable
DOCKER_NAME="rocm/sgl-dev:20250318rc"
 
# ---------------------------
# 1. Create Folder for This Run
# ---------------------------
current_date=$(date +%Y%m%d)
folder="${current_date}_GROK1_CK-MOE-I4F8-AITER-DECODE-ATTN_online"
mkdir -p "$folder"
OUTPUT_CSV="${folder}/${current_date}_GROK1_CK-MOE-I4F8-AITER-DECODE-ATTN_online.csv"

# Write config.json with docker image name from DOCKER_NAME
echo "{\"docker\": \"${DOCKER_NAME}\"}" > "${folder}/config.json"
echo "Wrote config.json to ${folder}/config.json"

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
# 3. Embedded Client Benchmark Code
# ---------------------------
run_client_benchmark() {
    local mode=$1
    export MODE=$mode
    TIMESTAMP=$(date +%Y%m%d_%H%M%S)
    REQUEST_RATES=(1 2 4 8 16)
    echo "Running client benchmark for mode ${mode} with timestamp ${TIMESTAMP}..."
    for RATE in "${REQUEST_RATES[@]}"; do
        for i in {1..3}; do
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
# For mode "aiter" (prefill+decode)
launch_server "aiter"
run_client_benchmark "aiter"
shutdown_server

# For mode "decode" (server launched with aiter_decode)
launch_server "aiter_decode"
run_client_benchmark "decode"
shutdown_server

# ---------------------------
# 6. Parse Logs and Generate CSV Summary (with Ratio Rows)
# ---------------------------
REQ_RATES=(1 2 4 8 16)
# Hard-coded H100 reference arrays for online mode:
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
  # Ratio rows for E2E
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
  # Ratio rows for TTFT
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
  # Ratio rows for ITL
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
