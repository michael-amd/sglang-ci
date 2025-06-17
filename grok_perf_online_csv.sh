#!/usr/bin/env bash
# ------------------------------------------------------------------------------
# grok_perf_online_csv.sh
#   Online-serving benchmark for GROK-1.
#   Now accepts --docker_image=<image[:tag]> like the offline script.
#
# USAGE:
#   bash grok_perf_online_csv.sh --docker_image=rocm/sgl-dev:20250331rc
#   bash grok_perf_online_csv.sh --docker_image=rocm/sgl-dev:20250429
#   bash grok_perf_online_csv.sh --docker_image=lmsysorg/sglang:v0.4.6.post3-rocm630
#   bash grok_perf_online_csv.sh --docker_image=lmsysorg/sglang:v0.4.7-rocm630
#   bash grok_perf_online_csv.sh --model=/path/to/model --tokenizer=tokenizer-name
#   bash grok_perf_online_csv.sh --work-dir=/path/to/workdir --output-dir=/path/to/output
#   bash grok_perf_online_csv.sh --gsm8k-script=/path/to/bench_sglang.py --node=node-name
# ------------------------------------------------------------------------------

set -euo pipefail

###############################################################################
# 0. Parse CLI flags
###############################################################################
default_image="lmsysorg/sglang:v0.4.7-rocm630"
docker_image=""

# Default paths - can be overridden
DEFAULT_MODEL="/mnt/raid/models/huggingface/amd--grok-1-W4A8KV8/"
DEFAULT_TOKENIZER="Xenova/grok-1-tokenizer"
DEFAULT_WORK_DIR="/mnt/raid/michael/sgl_benchmark_ci"
DEFAULT_OUTPUT_DIR=""  # If empty, will use work_dir
DEFAULT_GSM8K_SCRIPT="/mnt/raid/michael/sglang/benchmark/gsm8k/bench_sglang.py"
DEFAULT_NODE="dell300x-pla-t10-23"
DEFAULT_THRESHOLD="0.8"

# Initialize variables
MODEL=""
TOKENIZER=""
WORK_DIR=""
OUTPUT_DIR=""
GSM8K_SCRIPT=""
NODE=""
THRESHOLD=""
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
    --model=*)
      MODEL="${arg#*=}"
      shift
      ;;
    --tokenizer=*)
      TOKENIZER="${arg#*=}"
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
    --gsm8k-script=*)
      GSM8K_SCRIPT="${arg#*=}"
      shift
      ;;
    --node=*)
      NODE="${arg#*=}"
      shift
      ;;
    --threshold=*)
      THRESHOLD="${arg#*=}"
      shift
      ;;
    --help)
      echo "Usage: $0 [OPTIONS]"
      echo "Options:"
      echo "  --docker_image=IMAGE    Docker image to use (default: $default_image)"
      echo "  --model=PATH           Model path (default: $DEFAULT_MODEL)"
      echo "  --tokenizer=NAME       Tokenizer name (default: $DEFAULT_TOKENIZER)"
      echo "  --work-dir=PATH        Working directory (default: $DEFAULT_WORK_DIR)"
      echo "  --output-dir=PATH      Output directory (default: same as work-dir)"
      echo "  --gsm8k-script=PATH    Path to GSM8K benchmark script (default: $DEFAULT_GSM8K_SCRIPT)"
      echo "  --node=NAME            Node name for reporting (default: $DEFAULT_NODE)"
      echo "  --threshold=VALUE      GSM8K accuracy threshold (default: $DEFAULT_THRESHOLD)"
      echo "  --help                 Show this help message"
      exit 0
      ;;
  esac
done

# Set defaults if not provided
MODEL="${MODEL:-$DEFAULT_MODEL}"
TOKENIZER="${TOKENIZER:-$DEFAULT_TOKENIZER}"
WORK_DIR="${WORK_DIR:-$DEFAULT_WORK_DIR}"
OUTPUT_DIR="${OUTPUT_DIR:-$WORK_DIR}"
GSM8K_SCRIPT="${GSM8K_SCRIPT:-$DEFAULT_GSM8K_SCRIPT}"
NODE="${NODE:-$DEFAULT_NODE}"
THRESHOLD="${THRESHOLD:-$DEFAULT_THRESHOLD}"

docker_image="${docker_image:-${1:-$default_image}}"

###############################################################################
# 0-b. Use the full image name as provided (no auto-prefixing)
###############################################################################
FULL_IMAGE="$docker_image"

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
      bash "${SCRIPT_PATH}" \
           --docker_image="${FULL_IMAGE}" \
           --model="${MODEL}" \
           --tokenizer="${TOKENIZER}" \
           --work-dir="${WORK_DIR}" \
           --output-dir="${OUTPUT_DIR}" \
           --gsm8k-script="${GSM8K_SCRIPT}" \
           --node="${NODE}" \
           --threshold="${THRESHOLD}"
    exit 0
  fi
fi

###############################################################################
# 2. Inside container → benchmark directory setup
###############################################################################
cd "${WORK_DIR}" || {
  echo "cannot cd to benchmark dir"; exit 1; }

MODEL_NAME=GROK1
folder="${OUTPUT_DIR}/online/${MODEL_NAME}/${LATEST_TAG}_${MODEL_NAME}_MOE-I4F8_online"
mkdir -p "$folder"
OUTPUT_CSV="${folder}/${LATEST_TAG}_${MODEL_NAME}_MOE-I4F8_online.csv"

###############################################################################
# 3. Helper: launch server (backend chosen by tag-type)
###############################################################################
launch_server() {
  SERVER_LOG="${folder}/server_output_aiter.log"
  rm -f "$SERVER_LOG"

  if [[ "$LATEST_TAG" == *rc* ]]; then
    # --- RC image → original AITer path ---
    env_prefix="RCCL_MSCCL_ENABLE=0 CK_MOE=1 USE_INT4_WEIGHT=1"
    attn_backend="aiter"
    extra_flags="--enable-torch-compile --torch-compile-max-bs 4"
  else
    # --- Non-RC image → Triton path ---
    # Determine which environment variables to use based on image type
    if [[ "$FULL_IMAGE" =~ rocm/sgl-dev ]]; then
      # For rocm/sgl-dev images, determine which AITER variable based on date
      aiter_env_var="SGLANG_AITER_MOE"
      if [[ "$LATEST_TAG" =~ ^([0-9]{8}) ]]; then
        tag_date="${BASH_REMATCH[1]}"
        if [[ "$tag_date" -ge "20250606" ]]; then
          aiter_env_var="SGLANG_USE_AITER"
        fi
      fi
      env_prefix="${aiter_env_var}=1 SGLANG_INT4_WEIGHT=1 SGLANG_MOE_PADDING=0"
    elif [[ "$FULL_IMAGE" =~ lmsysorg/sglang:v([0-9]+)\.([0-9]+)\.([0-9]+)(\.post[0-9]+)? ]]; then
      # Original logic for lmsysorg/sglang images
      major="${BASH_REMATCH[1]}"
      minor="${BASH_REMATCH[2]}"
      patch="${BASH_REMATCH[3]}"
      aiter_env_var="SGLANG_USE_AITER"
      # Use SGLANG_AITER_MOE for versions before v0.4.7
      if [[ "$major" -eq 0 ]]; then
        if [[ "$minor" -lt 4 ]] || [[ "$minor" -eq 4 && "$patch" -lt 7 ]]; then
          aiter_env_var="SGLANG_AITER_MOE"
        fi
      fi
      env_prefix="${aiter_env_var}=1 SGLANG_INT4_WEIGHT=1 SGLANG_MOE_PADDING=0"
    else
      # Default to new env vars for other images
      env_prefix="SGLANG_USE_AITER=1 SGLANG_INT4_WEIGHT=1 SGLANG_MOE_PADDING=0"
    fi
    
    attn_backend="triton"
    extra_flags=""
  fi

  echo "[online] Launching backend=${attn_backend}"
  eval "${env_prefix} python3 -m sglang.launch_server \
        --model \"${MODEL}\" \
        --tokenizer-path \"${TOKENIZER}\" \
        --tp 8 --quantization fp8 --trust-remote-code \
        --attention-backend ${attn_backend} ${extra_flags} \
        --mem-fraction-static 0.85 \
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
run_client_gsm8k() {
    local mode="$1"   # mode: always "aiter" now
    local total_accuracy=0
    local runs=5
    local count=0
    local run_accuracy=0
    local output
    # Set log file name based on mode.
    local gsm8k_log="${folder}/sglang_client_log_${MODEL_NAME}_gsm8k_${mode}.log"
    
    # Run the test 'runs' times
    for i in $(seq 1 $runs); do
         echo "Executing GSM8K test Run $i for mode ${mode}..." | tee -a "$gsm8k_log"
         output=$(python3 "${GSM8K_SCRIPT}" --num-questions 2000 --parallel 2000 --num-shots 5 2>&1)
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
            existing_log=$(ls "${folder}/sglang_client_log_${MODEL_NAME}_${mode}_${RATE}_run${i}"_*.log 2>/dev/null || true)
            echo "[DEBUG] Checking for existing log pattern: ${folder}/sglang_client_log_${MODEL_NAME}_${mode}_${RATE}_run${i}_*.log"
            echo "[DEBUG] Found existing_log variable content: '${existing_log}'"
            # --------------------------------------
            if [ -n "$existing_log" ]; then
                echo "Log for mode ${mode}, rate ${RATE}, run ${i} (matched by pattern, files: '${existing_log}') already exists. Skipping."
                continue
            fi
            LOGFILE="${folder}/sglang_client_log_${MODEL_NAME}_${mode}_${RATE}_run${i}_${TIMESTAMP}.log"
            echo "Running benchmark with request rate: $RATE (Run $i) for mode ${mode}" | tee -a "$LOGFILE"
            NUM_PROMPTS=$(( 300 * RATE ))
            [ "$NUM_PROMPTS" -gt 2400 ] && NUM_PROMPTS=2400
            CMD="python3 -m sglang.bench_serving --backend sglang --tokenizer \"${TOKENIZER}\" --dataset-name random --random-input 1024 --random-output 1024 --num-prompts $NUM_PROMPTS --request-rate $RATE --output-file online.jsonl"
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
    for f in $(ls "${folder}/sglang_client_log_${MODEL_NAME}_${mode}_${rate}_run"*".log" 2>/dev/null); do
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
echo "Starting benchmarks for mode 'aiter'..."
launch_server
if run_client_gsm8k "aiter"; then
    run_client_benchmark "aiter"
else
    echo "Skipping benchmarks for mode 'aiter' due to low GSM8K accuracy."
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

for rate in "${REQ_RATES[@]}"; do
    read e2e_a ttft_a itl_a < <(get_best_metrics "aiter" "$rate")
    best_e2e_aiter[$rate]="$e2e_a"
    best_ttft_aiter[$rate]="$ttft_a"
    best_itl_aiter[$rate]="$itl_a"
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
  echo "Online mode - ${MODEL_NAME} (${LATEST_TAG})"
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
  printf "MI300x-aiter, $NODE"
  for rate in "${REQ_RATES[@]}"; do
      printf "\t%s" "${best_e2e_aiter[$rate]}"
  done
  echo ""
  printf "H100/MI300x-aiter"
  for idx in "${!REQ_RATES[@]}"; do
      rate=${REQ_RATES[$idx]}
      ratio=$(compute_ratio "${H100_E2E[$idx]}" "${best_e2e_aiter[$rate]}")
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
  printf "MI300x-aiter, $NODE"
  for rate in "${REQ_RATES[@]}"; do
      printf "\t%s" "${best_ttft_aiter[$rate]}"
  done
  echo ""
  printf "H100/MI300x-aiter"
  for idx in "${!REQ_RATES[@]}"; do
      rate=${REQ_RATES[$idx]}
      ratio=$(compute_ratio "${H100_TTFT[$idx]}" "${best_ttft_aiter[$rate]}")
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
  printf "MI300x-aiter, $NODE"
  for rate in "${REQ_RATES[@]}"; do
      printf "\t%s" "${best_itl_aiter[$rate]}"
  done
  echo ""
  printf "H100/MI300x-aiter"
  for idx in "${!REQ_RATES[@]}"; do
      rate=${REQ_RATES[$idx]}
      ratio=$(compute_ratio "${H100_ITL[$idx]}" "${best_itl_aiter[$rate]}")
      printf "\t%s%%" "$ratio"
  done
  echo ""
} > "$OUTPUT_CSV"

echo "CSV summary saved to ${OUTPUT_CSV}"
echo "All done! Client logs and CSV summary are saved in ${folder}."

# Reminder: If you encounter memory capacity errors, please ensure that
# any other processes occupying GPU memory are terminated or cleaned up.
