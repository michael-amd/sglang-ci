#!/usr/bin/env bash
# ------------------------------------------------------------------------------
# grok_perf_online_csv.sh
#   Online-serving benchmark for GROK-1.
#
# USAGE:
#   bash grok_perf_online_csv.sh --docker_image=rocm/sgl-dev:v0.4.9.post2-rocm630-mi30x-20250716
#   bash grok_perf_online_csv.sh --model=/path/to/model --tokenizer=tokenizer-name
#   bash grok_perf_online_csv.sh --work-dir=/path/to/workdir --output-dir=/path/to/output
# ------------------------------------------------------------------------------

set -euo pipefail

# Set timezone to PST/PDT
export TZ='America/Los_Angeles'

###############################################################################
# Configuration Variables - Override via environment variables if needed
###############################################################################

# Default image and model configuration
DEFAULT_IMAGE="${DEFAULT_DOCKER_IMAGE:-lmsysorg/sglang:v0.4.7-rocm630}"
MODEL_NAME="${BENCHMARK_MODEL_NAME:-GROK1}"
MODEL_VARIANT="${BENCHMARK_MODEL_VARIANT:-MOE-I4F8}"

# Default paths - can be overridden
DEFAULT_MODEL="${DEFAULT_MODEL_PATH:-/mnt/raid/models/huggingface/amd--grok-1-W4A8KV8/}"
DEFAULT_TOKENIZER="${DEFAULT_TOKENIZER_NAME:-/mnt/raid/models/huggingface/amd--grok-1-W4A8KV8/}"
DEFAULT_WORK_DIR="${DEFAULT_WORK_DIR:-/mnt/raid/michael/sgl_benchmark_ci}"
DEFAULT_OUTPUT_DIR="${DEFAULT_OUTPUT_DIR:-}"  # If empty, will use work_dir
DEFAULT_GSM8K_SCRIPT="${DEFAULT_GSM8K_SCRIPT:-/mnt/raid/michael/sgl-project/sglang/benchmark/gsm8k/bench_sglang.py}"
DEFAULT_NODE="${DEFAULT_NODE_NAME:-dell300x-pla-t10-23}"
DEFAULT_THRESHOLD="${DEFAULT_GSM8K_THRESHOLD:-0.8}"

# Container configuration
CONTAINER_SHM_SIZE="${CONTAINER_SHM_SIZE:-32g}"
MOUNT_DIR="${MOUNT_DIR:-/mnt/raid/}"
WORK_DIR_CONTAINER="${WORK_DIR_CONTAINER:-/sgl-workspace}"

# Benchmark configuration
RANDOM_INPUT_LENGTH="${RANDOM_INPUT_LENGTH:-1024}"
RANDOM_OUTPUT_LENGTH="${RANDOM_OUTPUT_LENGTH:-1024}"
GSM8K_NUM_QUESTIONS="${GSM8K_NUM_QUESTIONS:-2000}"
GSM8K_PARALLEL="${GSM8K_PARALLEL:-2000}"
GSM8K_NUM_SHOTS="${GSM8K_NUM_SHOTS:-5}"
GSM8K_RUNS="${GSM8K_RUNS:-5}"
MAX_NUM_PROMPTS="${MAX_NUM_PROMPTS:-2400}"
PROMPTS_PER_RATE_MULTIPLIER="${PROMPTS_PER_RATE_MULTIPLIER:-300}"

# Request rates for benchmarking
REQUEST_RATES="${REQUEST_RATES:-1 2 4 8 16}"

# H100 baseline data (can be overridden via environment)
H100_E2E_VALUES="${H100_E2E_VALUES:-13209 13874 16613 44918 85049}"
H100_TTFT_VALUES="${H100_TTFT_VALUES:-99.1 102.0 113.4 170.7 520.9}"
H100_ITL_VALUES="${H100_ITL_VALUES:-23.0 24.4 25.9 63.9 108.6}"

###############################################################################
# 0. Parse CLI flags
###############################################################################
docker_image=""

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
      echo "  --docker_image=IMAGE    Docker image to use (default: $DEFAULT_IMAGE)"
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

docker_image="${docker_image:-${1:-$DEFAULT_IMAGE}}"

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
      echo "[online] Checking if image exists locally ..."
      # Check if image exists locally
      if docker images --format '{{.Repository}}:{{.Tag}}' | grep -q "^${FULL_IMAGE}$"; then
        echo "[online] Found local image: ${FULL_IMAGE}"
      else
        # For custom built images without repo prefix, check without the prefix
        if docker images --format '{{.Repository}}:{{.Tag}}' | grep -E "^${IMAGE_WITH_TAG}$|^${REPO}:latest$"; then
          echo "[online] Found local image: ${IMAGE_WITH_TAG}"
        else
          echo "[online] Image not found locally. Attempting to pull ..."
          if ! docker pull "${FULL_IMAGE}" 2>/dev/null; then
            echo "[online] WARNING: Failed to pull ${FULL_IMAGE}. Image might be a local build."
            echo "[online] Checking if it exists with a different tag ..."
            # Final check for the image
            if ! docker images | grep -q "${REPO}"; then
              echo "[online] ERROR: Image ${FULL_IMAGE} not found locally or remotely."
              exit 1
            fi
          fi
        fi
      fi

      echo "[online] Creating container ..."
      docker run -d --name "${CONTAINER_NAME}" \
        --shm-size "$CONTAINER_SHM_SIZE" --ipc=host --cap-add=SYS_PTRACE --network=host \
        --device=/dev/kfd --device=/dev/dri --security-opt seccomp=unconfined \
        -v "${MOUNT_DIR}:${MOUNT_DIR}" --group-add video --privileged \
        -w "$WORK_DIR_CONTAINER" "${FULL_IMAGE}" tail -f /dev/null
    fi

    docker exec -e INSIDE_CONTAINER=1 -e LATEST_TAG="${LATEST_TAG}" -e TZ='America/Los_Angeles' \
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
SCRIPT_START_TIME=$(date +%s)
echo "[online] Script started at: $(date '+%Y-%m-%d %H:%M:%S %Z')"

cd "${WORK_DIR}" || {
  echo "cannot cd to benchmark dir"; exit 1; }

folder="${OUTPUT_DIR}/online/${MODEL_NAME}/${LATEST_TAG}_${MODEL_NAME}_${MODEL_VARIANT}_online"
mkdir -p "$folder"
OUTPUT_CSV="${folder}/${LATEST_TAG}_${MODEL_NAME}_${MODEL_VARIANT}_online.csv"

# Create timing summary log
TIMING_LOG="${folder}/timing_summary_$(date +%Y%m%d_%H%M%S).log"
export TIMING_LOG  # Make it available to all functions
echo "TIMING SUMMARY LOG" > "$TIMING_LOG"
echo "==================" >> "$TIMING_LOG"
echo "Script started at: $(date '+%Y-%m-%d %H:%M:%S %Z')" >> "$TIMING_LOG"
echo "Timezone: $(date +%Z) ($(date +%z))" >> "$TIMING_LOG"
echo "Docker image: ${FULL_IMAGE}" >> "$TIMING_LOG"
echo "Model: ${MODEL}" >> "$TIMING_LOG"
echo "" >> "$TIMING_LOG"

###############################################################################
# 3. Helper: launch server (backend chosen by tag-type)
###############################################################################
# Global variable to store the actual attention backend being used
ATTENTION_BACKEND=""

launch_server() {
  SERVER_LOG="${folder}/server_output_aiter.log"
  rm -f "$SERVER_LOG"

  # All supported images use aiter backend with SGLANG_USE_AITER
  attn_backend="aiter"
  aiter_env_var="SGLANG_USE_AITER"
  env_prefix="SGLANG_USE_AITER=1 SGLANG_INT4_WEIGHT=1"
  extra_flags=""

  # Store the backend globally for CSV output
  ATTENTION_BACKEND="$attn_backend"

  echo "[online] Launching backend=${attn_backend}"
  echo "Attention backend: ${attn_backend}" >> "$TIMING_LOG"

  # Build command with proper env handling
  if [[ "$attn_backend" == "aiter" ]]; then
    cmd="env '${aiter_env_var}=1' SGLANG_INT4_WEIGHT=1"
  else
    cmd="env SGLANG_INT4_WEIGHT=1"
  fi

  cmd="${cmd} python3 -m sglang.launch_server \
        --model '${MODEL}' \
        --tokenizer-path '${TOKENIZER}' \
        --tp 8 --quantization fp8 --trust-remote-code \
        --attention-backend ${attn_backend} ${extra_flags} \
        --mem-fraction-static 0.85 \
        > '${SERVER_LOG}' 2>&1 &"

  eval "$cmd"
  SERVER_PID=$!

  # Wait for server to be ready with timeout
  local timeout=600  # 10 minutes timeout
  local elapsed=0
  echo "[online] Waiting for server to be ready (timeout: ${timeout}s)..."
  while ! grep -q "The server is fired up and ready to roll!" "$SERVER_LOG"; do
    sleep 1
    elapsed=$((elapsed + 1))
    if [ $elapsed -ge $timeout ]; then
      echo "[online] ERROR: Server failed to start within ${timeout} seconds"
      echo "[online] Last 50 lines of server log:"
      tail -50 "$SERVER_LOG"
      kill "$SERVER_PID" 2>/dev/null || true
      exit 1
    fi
    # Show progress every 10 seconds
    if [ $((elapsed % 10)) -eq 0 ]; then
      echo "[online] Still waiting... (${elapsed}s elapsed)"
    fi
  done
  echo "[online] Server ready after ${elapsed} seconds (PID ${SERVER_PID})"
  echo "Server startup time: ${elapsed} seconds" >> "$TIMING_LOG"
}

shutdown_server() {
    echo "[online] Shutting down server (PID ${SERVER_PID})..."
    local shutdown_start=$(date +%s)
    kill "$SERVER_PID"
    sleep 2
    local shutdown_end=$(date +%s)
    local shutdown_duration=$((shutdown_end - shutdown_start))
    echo "[online] Server shutdown completed in ${shutdown_duration} seconds"
    if [ -n "$TIMING_LOG" ]; then
        echo "Server shutdown time: ${shutdown_duration} seconds" >> "$TIMING_LOG"
    fi
}

###############################################################################
# 4. GSM8K accuracy warm-up
###############################################################################
# This function runs the GSM8K test multiple times, computes the average accuracy,
# and returns 0 if the average meets the threshold (THRESHOLD), or 1 otherwise.
run_client_gsm8k() {
    local mode="$1"   # mode: always "aiter" now
    local gsm8k_start_time=$(date +%s)
    local total_accuracy=0
    local runs=$GSM8K_RUNS
    local count=0
    local run_accuracy=0
    local output
    # Set log file name based on mode.
    local gsm8k_log="${folder}/sglang_client_log_${MODEL_NAME}_gsm8k_${mode}.log"

    echo "Starting GSM8K accuracy test at: $(date '+%Y-%m-%d %H:%M:%S %Z')" | tee -a "$gsm8k_log"

    # Run the test 'runs' times
    for i in $(seq 1 $runs); do
         local run_start_time=$(date +%s)
         echo "Executing GSM8K test Run $i for mode ${mode}..." | tee -a "$gsm8k_log"
         output=$(python3 "${GSM8K_SCRIPT}" --num-questions "$GSM8K_NUM_QUESTIONS" --parallel "$GSM8K_PARALLEL" --num-shots "$GSM8K_NUM_SHOTS" 2>&1)
         local run_end_time=$(date +%s)
         local run_duration=$((run_end_time - run_start_time))
         echo "$output" | tee -a "$gsm8k_log"
         echo "Run $i completed in ${run_duration} seconds" | tee -a "$gsm8k_log"
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
    local gsm8k_end_time=$(date +%s)
    local gsm8k_duration=$((gsm8k_end_time - gsm8k_start_time))
    echo "GSM8K test completed in ${gsm8k_duration} seconds" | tee -a "$gsm8k_log"
    echo "Average Accuracy over $runs runs for mode ${mode}: $avg_accuracy" | tee -a "$gsm8k_log"

    # Log to timing summary
    echo "" >> "$TIMING_LOG"
    echo "GSM8K Test Results:" >> "$TIMING_LOG"
    echo "  Total duration: ${gsm8k_duration} seconds" >> "$TIMING_LOG"
    echo "  Average accuracy: $avg_accuracy" >> "$TIMING_LOG"
    echo "  Number of runs: $runs" >> "$TIMING_LOG"

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
run_single_rate_benchmark() {
    local mode=$1
    local RATE=$2
    local TIMESTAMP=$3
    local rate_start_time=$(date +%s)

    echo "Processing request rate ${RATE} for mode ${mode}..."
    for i in {1..3}; do
        # Check if log already exists using glob pattern
        existing_log=""
        for log_file in "${folder}/sglang_client_log_${MODEL_NAME}_${mode}_${RATE}_run${i}"_*.log; do
            if [[ -f "$log_file" ]]; then
                existing_log="$log_file"
                break
            fi
        done
        if [ -n "$existing_log" ]; then
            echo "Log for mode ${mode}, rate ${RATE}, run ${i} already exists. Skipping."
            continue
        fi

        LOGFILE="${folder}/sglang_client_log_${MODEL_NAME}_${mode}_${RATE}_run${i}_${TIMESTAMP}.log"
        echo "Running benchmark with request rate: $RATE (Run $i) for mode ${mode}" | tee -a "$LOGFILE"

        local run_start_time=$(date +%s)
        echo "Run started at: $(date '+%Y-%m-%d %H:%M:%S %Z')" | tee -a "$LOGFILE"

        NUM_PROMPTS=$(( PROMPTS_PER_RATE_MULTIPLIER * RATE ))
        [ "$NUM_PROMPTS" -gt "$MAX_NUM_PROMPTS" ] && NUM_PROMPTS="$MAX_NUM_PROMPTS"

        CMD="python3 -m sglang.bench_serving --backend sglang --tokenizer \"${TOKENIZER}\" --dataset-name random --random-input $RANDOM_INPUT_LENGTH --random-output $RANDOM_OUTPUT_LENGTH --num-prompts $NUM_PROMPTS --request-rate $RATE --output-file online_${RATE}.jsonl"
        echo "Executing: $CMD" | tee -a "$LOGFILE"
        eval "$CMD" 2>&1 | tee -a "$LOGFILE"

        local run_end_time=$(date +%s)
        local run_duration=$((run_end_time - run_start_time))
        echo "Run completed at: $(date '+%Y-%m-%d %H:%M:%S %Z')" | tee -a "$LOGFILE"
        echo "Run duration: ${run_duration} seconds" | tee -a "$LOGFILE"
        echo "----------------------------------------" | tee -a "$LOGFILE"

        # Log to timing summary
        echo "  Rate ${RATE}, Run ${i}: ${run_duration} seconds" >> "$TIMING_LOG"
    done

    # Calculate total time for this rate
    local rate_end_time=$(date +%s)
    local rate_total_duration=$((rate_end_time - rate_start_time))
    echo "Completed rate ${RATE} - Total time: ${rate_total_duration} seconds" >> "$TIMING_LOG"
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
    for f in "${folder}/sglang_client_log_${MODEL_NAME}_${mode}_${rate}_run"*".log"; do
        # Skip if no files match the pattern
        [[ -f "$f" ]] || continue
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
# 6b. CSV Generation Functions
# ---------------------------
# Global arrays for storing metrics
declare -A best_e2e_aiter best_ttft_aiter best_itl_aiter

# H100 baseline data - convert from environment variables to arrays
read -ra REQ_RATES <<< "$REQUEST_RATES"
read -ra H100_E2E <<< "$H100_E2E_VALUES"
read -ra H100_TTFT <<< "$H100_TTFT_VALUES"
read -ra H100_ITL <<< "$H100_ITL_VALUES"

compute_ratio() {
    local ref=$1
    local meas=$2
    if [[ "$meas" == "NA" || "$meas" == "0" ]]; then
        echo "NA"
    else
        awk -v r="$ref" -v m="$meas" 'BEGIN { printf "%d", (r/m)*100 }'
    fi
}

# Initialize the CSV with headers and baseline data
init_csv() {
    echo "Online mode - ${MODEL_NAME} (${LATEST_TAG})" > "$OUTPUT_CSV"
    echo "" >> "$OUTPUT_CSV"

    # E2E Latency section
    echo "Median E2E Latency (ms, lower better)" >> "$OUTPUT_CSV"
    printf "request rate" >> "$OUTPUT_CSV"
    for rate in "${REQ_RATES[@]}"; do
        printf "\t%s" "$rate" >> "$OUTPUT_CSV"
    done
    echo "" >> "$OUTPUT_CSV"

    printf "H100" >> "$OUTPUT_CSV"
    for val in "${H100_E2E[@]}"; do
        printf "\t%s" "$val" >> "$OUTPUT_CSV"
    done
    echo "" >> "$OUTPUT_CSV"

    # Placeholder for MI300x results
    printf "MI300x-${ATTENTION_BACKEND}, $NODE" >> "$OUTPUT_CSV"
    for rate in "${REQ_RATES[@]}"; do
        printf "\t" >> "$OUTPUT_CSV"
    done
    echo "" >> "$OUTPUT_CSV"

    # Placeholder for ratios
    printf "H100/MI300x-${ATTENTION_BACKEND}" >> "$OUTPUT_CSV"
    for rate in "${REQ_RATES[@]}"; do
        printf "\t" >> "$OUTPUT_CSV"
    done
    echo "" >> "$OUTPUT_CSV"
    echo "" >> "$OUTPUT_CSV"

    # TTFT section
    echo "Median TTFT (ms, lower better)" >> "$OUTPUT_CSV"
    printf "request rate" >> "$OUTPUT_CSV"
    for rate in "${REQ_RATES[@]}"; do
        printf "\t%s" "$rate" >> "$OUTPUT_CSV"
    done
    echo "" >> "$OUTPUT_CSV"

    printf "H100" >> "$OUTPUT_CSV"
    for val in "${H100_TTFT[@]}"; do
        printf "\t%s" "$val" >> "$OUTPUT_CSV"
    done
    echo "" >> "$OUTPUT_CSV"

    # Placeholder for MI300x results
    printf "MI300x-${ATTENTION_BACKEND}, $NODE" >> "$OUTPUT_CSV"
    for rate in "${REQ_RATES[@]}"; do
        printf "\t" >> "$OUTPUT_CSV"
    done
    echo "" >> "$OUTPUT_CSV"

    # Placeholder for ratios
    printf "H100/MI300x-${ATTENTION_BACKEND}" >> "$OUTPUT_CSV"
    for rate in "${REQ_RATES[@]}"; do
        printf "\t" >> "$OUTPUT_CSV"
    done
    echo "" >> "$OUTPUT_CSV"
    echo "" >> "$OUTPUT_CSV"

    # ITL section
    echo "Median ITL (ms, lower better)" >> "$OUTPUT_CSV"
    printf "request rate" >> "$OUTPUT_CSV"
    for rate in "${REQ_RATES[@]}"; do
        printf "\t%s" "$rate" >> "$OUTPUT_CSV"
    done
    echo "" >> "$OUTPUT_CSV"

    printf "H100" >> "$OUTPUT_CSV"
    for val in "${H100_ITL[@]}"; do
        printf "\t%s" "$val" >> "$OUTPUT_CSV"
    done
    echo "" >> "$OUTPUT_CSV"

    # Placeholder for MI300x results
    printf "MI300x-${ATTENTION_BACKEND}, $NODE" >> "$OUTPUT_CSV"
    for rate in "${REQ_RATES[@]}"; do
        printf "\t" >> "$OUTPUT_CSV"
    done
    echo "" >> "$OUTPUT_CSV"

    # Placeholder for ratios
    printf "H100/MI300x-${ATTENTION_BACKEND}" >> "$OUTPUT_CSV"
    for rate in "${REQ_RATES[@]}"; do
        printf "\t" >> "$OUTPUT_CSV"
    done
    echo "" >> "$OUTPUT_CSV"

    echo "[online] CSV initialized at ${OUTPUT_CSV}"
}

# Update CSV with results for a specific rate
update_csv_for_rate() {
    local rate=$1

    # Get metrics for this rate
    read e2e_a ttft_a itl_a < <(get_best_metrics "${ATTENTION_BACKEND}" "$rate")
    best_e2e_aiter[$rate]="$e2e_a"
    best_ttft_aiter[$rate]="$ttft_a"
    best_itl_aiter[$rate]="$itl_a"

    echo "[online] Updating CSV for rate ${rate}: E2E=${e2e_a}ms, TTFT=${ttft_a}ms, ITL=${itl_a}ms"

    # Rebuild the entire CSV with current data
    {
        echo "Online mode - ${MODEL_NAME} (${LATEST_TAG})"
        echo ""

        # E2E Latency section
        echo "Median E2E Latency (ms, lower better)"
        printf "request rate"
        for r in "${REQ_RATES[@]}"; do
            printf "\t%s" "$r"
        done
        echo ""

        printf "H100"
        for val in "${H100_E2E[@]}"; do
            printf "\t%s" "$val"
        done
        echo ""

        printf "MI300x-${ATTENTION_BACKEND}, $NODE"
        for r in "${REQ_RATES[@]}"; do
            printf "\t%s" "${best_e2e_aiter[$r]:-}"
        done
        echo ""

        printf "H100/MI300x-${ATTENTION_BACKEND}"
        for idx in "${!REQ_RATES[@]}"; do
            r=${REQ_RATES[$idx]}
            if [ -n "${best_e2e_aiter[$r]:-}" ]; then
                ratio=$(compute_ratio "${H100_E2E[$idx]}" "${best_e2e_aiter[$r]}")
                printf "\t%s%%" "$ratio"
            else
                printf "\t"
            fi
        done
        echo ""
        echo ""

        # TTFT section
        echo "Median TTFT (ms, lower better)"
        printf "request rate"
        for r in "${REQ_RATES[@]}"; do
            printf "\t%s" "$r"
        done
        echo ""

        printf "H100"
        for val in "${H100_TTFT[@]}"; do
            printf "\t%s" "$val"
        done
        echo ""

        printf "MI300x-${ATTENTION_BACKEND}, $NODE"
        for r in "${REQ_RATES[@]}"; do
            printf "\t%s" "${best_ttft_aiter[$r]:-}"
        done
        echo ""

        printf "H100/MI300x-${ATTENTION_BACKEND}"
        for idx in "${!REQ_RATES[@]}"; do
            r=${REQ_RATES[$idx]}
            if [ -n "${best_ttft_aiter[$r]:-}" ]; then
                ratio=$(compute_ratio "${H100_TTFT[$idx]}" "${best_ttft_aiter[$r]}")
                printf "\t%s%%" "$ratio"
            else
                printf "\t"
            fi
        done
        echo ""
        echo ""

        # ITL section
        echo "Median ITL (ms, lower better)"
        printf "request rate"
        for r in "${REQ_RATES[@]}"; do
            printf "\t%s" "$r"
        done
        echo ""

        printf "H100"
        for val in "${H100_ITL[@]}"; do
            printf "\t%s" "$val"
        done
        echo ""

        printf "MI300x-${ATTENTION_BACKEND}, $NODE"
        for r in "${REQ_RATES[@]}"; do
            printf "\t%s" "${best_itl_aiter[$r]:-}"
        done
        echo ""

        printf "H100/MI300x-${ATTENTION_BACKEND}"
        for idx in "${!REQ_RATES[@]}"; do
            r=${REQ_RATES[$idx]}
            if [ -n "${best_itl_aiter[$r]:-}" ]; then
                ratio=$(compute_ratio "${H100_ITL[$idx]}" "${best_itl_aiter[$r]}")
                printf "\t%s%%" "$ratio"
            else
                printf "\t"
            fi
        done
        echo ""
    } > "$OUTPUT_CSV"

    echo "[online] CSV updated with results for rate ${rate}"
}

run_client_benchmark() {
    local mode=$1
    local benchmark_start_time=$(date +%s)
    TIMESTAMP=$(date +%Y%m%d_%H%M%S)
    read -ra REQUEST_RATES_ARRAY <<< "$REQUEST_RATES"
    echo "Starting client benchmark for mode ${mode} at: $(date '+%Y-%m-%d %H:%M:%S %Z')..."

    # Initialize CSV at the start
    init_csv

    # Sequential execution only - model uses all 8 GPUs
    echo "Running benchmarks sequentially..."
    echo "" >> "$TIMING_LOG"
    echo "Client Benchmark Results:" >> "$TIMING_LOG"
    echo "  Start time: $(date '+%Y-%m-%d %H:%M:%S %Z')" >> "$TIMING_LOG"

    for RATE in "${REQUEST_RATES_ARRAY[@]}"; do
        run_single_rate_benchmark "$mode" "$RATE" "$TIMESTAMP"
        # Update CSV after each rate completes all runs
        update_csv_for_rate "$RATE"
    done

    local benchmark_end_time=$(date +%s)
    local benchmark_duration=$((benchmark_end_time - benchmark_start_time))
    echo "Client benchmark completed in ${benchmark_duration} seconds"

    # Log to timing summary
    echo "  End time: $(date '+%Y-%m-%d %H:%M:%S %Z')" >> "$TIMING_LOG"
    echo "  Total duration: ${benchmark_duration} seconds" >> "$TIMING_LOG"
}

# ---------------------------
# 7. Run Benchmarks for Each Mode
# ---------------------------
echo "Starting benchmarks using ${ATTENTION_BACKEND} backend..."
launch_server

if run_client_gsm8k "${ATTENTION_BACKEND}"; then
    run_client_benchmark "${ATTENTION_BACKEND}"
else
    echo "Skipping benchmarks for ${ATTENTION_BACKEND} backend due to low GSM8K accuracy."
fi

shutdown_server

# ---------------------------
# 8. Parse Logs and Generate CSV Summary (with Ratio Rows)
# ---------------------------
# Function to extract throughput from log files
extract_throughput() {
    local mode=$1
    local rate=$2
    # Use glob pattern instead of ls - find first matching file
    local log_file=""
    for f in "${folder}/sglang_client_log_${MODEL_NAME}_${mode}_${rate}_run"*".log"; do
        if [[ -f "$f" ]]; then
            log_file="$f"
            break
        fi
    done
    if [ -n "$log_file" ]; then
        local throughput=$(grep -oP 'Throughput:\s*\K[\d.]+' "$log_file" | head -n1)
        if [ -n "$throughput" ]; then
            echo "$throughput"
        else
            echo "N/A"
        fi
    else
        echo "N/A"
    fi
}

echo "CSV summary saved to ${OUTPUT_CSV}"
echo "All done! Client logs and CSV summary are saved in ${folder}."

# Add performance summary to timing log
echo "" >> "$TIMING_LOG"
echo "Performance Summary:" >> "$TIMING_LOG"
echo "===================" >> "$TIMING_LOG"
for rate in "${REQ_RATES[@]}"; do
    echo "Request Rate ${rate}:" >> "$TIMING_LOG"
    echo "  E2E Latency: ${best_e2e_aiter[$rate]} ms" >> "$TIMING_LOG"
    echo "  TTFT: ${best_ttft_aiter[$rate]} ms" >> "$TIMING_LOG"
    echo "  ITL: ${best_itl_aiter[$rate]} ms" >> "$TIMING_LOG"
    throughput=$(extract_throughput "${ATTENTION_BACKEND}" "$rate")
    echo "  Throughput: ${throughput} requests/s" >> "$TIMING_LOG"
    echo "" >> "$TIMING_LOG"
done

# Final timing summary
SCRIPT_END_TIME=$(date +%s)
TOTAL_DURATION=$((SCRIPT_END_TIME - SCRIPT_START_TIME))

# Write final summary to timing log
echo "" >> "$TIMING_LOG"
echo "========================================" >> "$TIMING_LOG"
echo "OVERALL SCRIPT SUMMARY" >> "$TIMING_LOG"
echo "========================================" >> "$TIMING_LOG"
echo "Script ended at: $(date '+%Y-%m-%d %H:%M:%S %Z')" >> "$TIMING_LOG"
echo "Total execution time: ${TOTAL_DURATION} seconds ($(($TOTAL_DURATION / 60)) minutes)" >> "$TIMING_LOG"
echo "========================================" >> "$TIMING_LOG"

# Display summary on console
echo ""
echo "=========================================="
echo "SCRIPT EXECUTION SUMMARY"
echo "=========================================="
echo "Script completed at: $(date '+%Y-%m-%d %H:%M:%S %Z')"
echo "Total execution time: ${TOTAL_DURATION} seconds ($(($TOTAL_DURATION / 60)) minutes)"
echo "Output directory: ${folder}"
echo "CSV file: ${OUTPUT_CSV}"
echo "Timing log: ${TIMING_LOG}"
echo "=========================================="

# Reminder: If you encounter memory capacity errors, please ensure that
# any other processes occupying GPU memory are terminated or cleaned up.
