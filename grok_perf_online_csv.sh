#!/usr/bin/env bash
# ------------------------------------------------------------------------------
# grok_perf_online_csv.sh
#   Online-serving benchmark for GROK-1.
#   Now accepts --docker_image=<image[:tag]> like the offline script.
#
# USAGE:
#   bash grok_perf_online_csv.sh --docker_image=sgl-dev:20250331rc
#   bash grok_perf_online_csv.sh --docker_image=sgl-dev:20250429
# ------------------------------------------------------------------------------

set -euo pipefail

###############################################################################
# 0. Parse CLI flag  --docker_image=
###############################################################################
default_image="rocm/sgl-dev:20250331rc"
docker_image=""

for arg in "$@"; do
  case $arg in
    --docker_image=*|--docker-image=*)
      docker_image="${arg#*=}"
      shift
      ;;
  esac
done
docker_image="${docker_image:-${1:-$default_image}}"

###############################################################################
# 0-b. Normalise image (auto-add rocm/ prefix if absent)
###############################################################################
if [[ "$docker_image" != */* ]]; then
  FULL_IMAGE="rocm/${docker_image}"
else
  FULL_IMAGE="$docker_image"
fi

IMAGE_WITH_TAG="${FULL_IMAGE##*/}"        # sgl-dev:20250429
REPO="${IMAGE_WITH_TAG%%:*}"              # sgl-dev
LATEST_TAG="${IMAGE_WITH_TAG#*:}"         # 20250429

###############################################################################
# 1. Container management (only if not already inside)
###############################################################################
if [ -z "${INSIDE_CONTAINER:-}" ]; then
  if ! command -v docker >/dev/null 2>&1; then
    INSIDE_CONTAINER=1
  else
    CONTAINER_NAME="${REPO}_${LATEST_TAG}"
    echo "[online] Using container  ${CONTAINER_NAME}"
    echo "[online] Docker image    ${FULL_IMAGE}"

    if docker ps -a --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"; then
      docker start "${CONTAINER_NAME}" >/dev/null || true
    else
      docker pull "${FULL_IMAGE}"
      docker run -d --name "${CONTAINER_NAME}" \
        --shm-size 32g --ipc=host --cap-add=SYS_PTRACE --network=host \
        --device=/dev/kfd --device=/dev/dri --security-opt seccomp=unconfined \
        -v /mnt/raid/:/mnt/raid/ --group-add video --privileged \
        -w /sgl-workspace "${FULL_IMAGE}" tail -f /dev/null
    fi

    docker exec -e INSIDE_CONTAINER=1 -e LATEST_TAG="${LATEST_TAG}" \
      "${CONTAINER_NAME}" \
      bash /mnt/raid/michael/sgl_benchmark_ci/grok_perf_online_csv.sh \
           --docker_image="${FULL_IMAGE}"
    exit 0
  fi
fi

###############################################################################
# 2. Inside container → benchmark directory setup
###############################################################################
cd /mnt/raid/michael/sgl_benchmark_ci/ || {
  echo "cannot cd to benchmark dir"; exit 1; }

current_date=$(date +%Y%m%d)
folder="${current_date}_${LATEST_TAG}_GROK1_MOE-I4F8_online"
mkdir -p "$folder"
OUTPUT_CSV="${folder}/${current_date}_${LATEST_TAG}_GROK1_MOE-I4F8_online.csv"

NODE="dell300x-pla-t10-23"
THRESHOLD=0.8

###############################################################################
# 3. Helper: launch server (backend chosen by tag-type)
###############################################################################
launch_server() {
  local mode=$1          # "aiter" or "aiter_decode" (decode-only)
  SERVER_LOG="${folder}/server_output_${mode}.log"
  rm -f "$SERVER_LOG"

  if [[ "$LATEST_TAG" == *rc* ]]; then
    # --- RC image → original AITer path ---
    env_prefix="RCCL_MSCCL_ENABLE=0 CK_MOE=1 USE_INT4_WEIGHT=1"
    attn_backend="${mode}"
    extra_flags="--enable-torch-compile --torch-compile-max-bs 4"
  else
    # --- Nightly / prod image → Triton path ---
    env_prefix="SGLANG_AITER_MOE=1 SGLANG_INT4_WEIGHT=1 MOE_PADDING=0"
    attn_backend="triton"
    extra_flags=""
  fi

  echo "[online] Launching backend=${attn_backend}"
  eval "${env_prefix} python3 -m sglang.launch_server \
        --model /mnt/raid/models/huggingface/amd--grok-1-W4A8KV8/ \
        --tokenizer-path Xenova/grok-1-tokenizer \
        --tp 8 --quantization fp8 --trust-remote-code \
        --attention-backend ${attn_backend} ${extra_flags} \
        > \"${SERVER_LOG}\" 2>&1 &"
  SERVER_PID=$!

  while ! grep -q "The server is fired up and ready to roll!" "$SERVER_LOG"; do
    sleep 1
  done
  echo "[online] Server ready (PID ${SERVER_PID})"
}

shutdown_server() { kill "$SERVER_PID"; sleep 2; }
 
###############################################################################
# 4. GSM8K accuracy warm-up
###############################################################################
# This function runs the GSM8K test multiple times, computes the average accuracy,
# and returns 0 if the average meets the threshold (THRESHOLD), or 1 otherwise.
# It now accepts a mode parameter ("aiter" or "decode") to split the log file accordingly.
run_client_gsm8k() {
    local mode="$1"   # mode: either "aiter" or "decode"
    local total_accuracy=0
    local runs=5
    local count=0
    local run_accuracy=0
    local output
    # Set log file name based on mode.
    local gsm8k_log="${folder}/sglang_client_log_grok1_gsm8k_${mode}.log"
    
    # Run the test 'runs' times
    for i in $(seq 1 $runs); do
         echo "Executing GSM8K test Run $i for mode ${mode}..." | tee -a "$gsm8k_log"
         output=$(python3 /mnt/raid/michael/sglang/benchmark/gsm8k/bench_sglang.py --num-questions 2000 --parallel 2000 --num-shots 5 2>&1)
         echo "$output" | tee -a "$gsm8k_log"
         # Extract the accuracy value from the output; expects a line like "Accuracy: 0.820"
         run_accuracy=$(echo "$output" | grep -oP 'Accuracy:\s*\K[\d.]+' | head -n1)
         if [ -z "$run_accuracy" ]; then
            echo "Run $i: Accuracy not found, defaulting to 0" | tee -a "$gsm8k_log"
            run_accuracy=0
         fi
         echo "Run $i: Accuracy: $run_accuracy" | tee -a "$gsm8k_log"
         total_accuracy=$(awk -v t="$total_accuracy" -v a="$run_accuracy" 'BEGIN { printf "%.3f", t+a }')
         count=$((count+1))
    done
    local avg_accuracy
    avg_accuracy=$(awk -v total="$total_accuracy" -v runs="$runs" 'BEGIN { printf "%.3f", total/runs }')
    echo "Average Accuracy over $runs runs for mode ${mode}: $avg_accuracy" | tee -a "$gsm8k_log"
    if awk "BEGIN {exit !($avg_accuracy >= $THRESHOLD)}"; then
         echo "Average accuracy meets threshold ($THRESHOLD) for mode ${mode}. Continuing with this mode." | tee -a "$gsm8k_log"
         return 0
    else
         echo "Average accuracy ($avg_accuracy) is below threshold ($THRESHOLD) for mode ${mode}. Skipping this mode." | tee -a "$gsm8k_log"
         return 1
    fi
}

# ---------------------------
# 5. Client Benchmark (runs only missing logs)
# ---------------------------
run_client_benchmark() {
    local mode=$1
    TIMESTAMP=$(date +%Y%m%d_%H%M%S)
    REQUEST_RATES=(1 2 4 8 16)
    echo "Running client benchmark for mode ${mode}..."
    for RATE in "${REQUEST_RATES[@]}"; do
        for i in {1..3}; do
            # --------- change this line ----------
            existing_log=$(ls "${folder}/sglang_client_log_grok1_${mode}_${RATE}_run${i}"_*.log 2>/dev/null || true)
            # --------------------------------------
            if [ -n "$existing_log" ]; then
                echo "Log for mode ${mode}, rate ${RATE}, run ${i} already exists. Skipping."
                continue
            fi
            LOGFILE="${folder}/sglang_client_log_grok1_${mode}_${RATE}_run${i}_${TIMESTAMP}.log"
            echo "Running benchmark with request rate: $RATE (Run $i) for mode ${mode}" | tee -a "$LOGFILE"
            NUM_PROMPTS=$(( 300 * RATE ))
            [ "$NUM_PROMPTS" -gt 2400 ] && NUM_PROMPTS=2400
            CMD="python3 -m sglang.bench_serving --backend sglang --tokenizer Xenova/grok-1-tokenizer --dataset-name random --random-input 1024 --random-output 1024 --num-prompts $NUM_PROMPTS --request-rate $RATE --output-file online.jsonl"
            echo "Executing: $CMD" | tee -a "$LOGFILE"
            eval "$CMD" 2>&1 | tee -a "$LOGFILE"
        done
    done
}

# ---------------------------
# 6. Function to Select Best Metrics from Logs
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
# 7. Run Benchmarks for Each Mode
# ---------------------------
echo "Starting benchmarks for mode 'aiter' (prefill+decode)..."
launch_server "aiter"
if run_client_gsm8k "aiter"; then
    run_client_benchmark "aiter"
else
    echo "Skipping benchmarks for mode 'aiter' due to low GSM8K accuracy."
fi
shutdown_server

echo "Starting benchmarks for mode 'aiter_decode' (decode only)..."
launch_server "aiter_decode"
if run_client_gsm8k "decode"; then
    run_client_benchmark "decode"
else
    echo "Skipping benchmarks for mode 'aiter_decode' due to low GSM8K accuracy."
fi
shutdown_server

# ---------------------------
# 8. Parse Logs and Generate CSV Summary (with Ratio Rows)
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
  printf "MI300x-aiter (prefill+decode), $NODE"
  for rate in "${REQ_RATES[@]}"; do
      printf "\t%s" "${best_e2e_aiter[$rate]}"
  done
  echo ""
  printf "MI300x-aiter_decode (decode only), $NODE"
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
  printf "MI300x-aiter (prefill+decode), $NODE"
  for rate in "${REQ_RATES[@]}"; do
      printf "\t%s" "${best_ttft_aiter[$rate]}"
  done
  echo ""
  printf "MI300x-aiter_decode (decode only), $NODE"
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
  printf "MI300x-aiter (prefill+decode), $NODE"
  for rate in "${REQ_RATES[@]}"; do
      printf "\t%s" "${best_itl_aiter[$rate]}"
  done
  echo ""
  printf "MI300x-aiter_decode (decode only), $NODE"
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

# Reminder: If you encounter memory capacity errors, please ensure that
# any other processes occupying GPU memory are terminated or cleaned up.
