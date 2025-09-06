#!/usr/bin/env bash
# ------------------------------------------------------------------------------
# grok_perf_online_csv.sh
#   Online-serving benchmark for GROK-1.
#
# USAGE:
#   bash grok_perf_online_csv.sh --docker_image=rocm/sgl-dev:v0.4.9.post2-rocm630-mi30x-20250716
#   bash grok_perf_online_csv.sh --model-path=/raid/grok-1-W4A8KV8
#   bash grok_perf_online_csv.sh --work-dir=/path/to/workdir
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
DEFAULT_MODEL="${DEFAULT_MODEL_PATH:-/mnt/raid/models/huggingface/amd--grok-1-W4A8KV8}"
DEFAULT_TOKENIZER="${DEFAULT_TOKENIZER_NAME:-/mnt/raid/models/huggingface/amd--grok-1-W4A8KV8}"
DEFAULT_WORK_DIR="${DEFAULT_WORK_DIR:-/mnt/raid/michael/sgl_benchmark_ci}"
DEFAULT_OUTPUT_DIR="${DEFAULT_OUTPUT_DIR:-}"  # If empty, will use work_dir
DEFAULT_GSM8K_SCRIPT="${DEFAULT_GSM8K_SCRIPT:-/sgl-workspace/sglang/benchmark/gsm8k/bench_sglang.py}"
# Node name will be read from hostname
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

# Baseline data variables removed

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
      ;;
    --model=*|--model-path=*)
      MODEL="${arg#*=}"
      ;;
    --tokenizer=*)
      TOKENIZER="${arg#*=}"
      ;;
    --work-dir=*)
      WORK_DIR="${arg#*=}"
      ;;
    --output-dir=*)
      OUTPUT_DIR="${arg#*=}"
      ;;
    --gsm8k-script=*)
      GSM8K_SCRIPT="${arg#*=}"
      ;;
    --threshold=*)
      THRESHOLD="${arg#*=}"
      ;;
    --help)
      echo "Usage: $0 [OPTIONS]"
      echo "Options:"
      echo "  --docker_image=IMAGE    Docker image to use (default: $DEFAULT_IMAGE)"
      echo "  --model=PATH           Model path (default: $DEFAULT_MODEL)"
      echo "  --model-path=PATH      Model path (alias for --model)"
      echo "  --tokenizer=NAME       Tokenizer name (default: $DEFAULT_TOKENIZER)"
      echo "  --work-dir=PATH        Working directory (default: $DEFAULT_WORK_DIR)"
      echo "  --output-dir=PATH      Output directory (default: same as work-dir)"
      echo "  --gsm8k-script=PATH    Path to GSM8K benchmark script (default: $DEFAULT_GSM8K_SCRIPT)"
      echo "  --threshold=VALUE      GSM8K accuracy threshold (default: $DEFAULT_THRESHOLD)"
      echo "  --help                 Show this help message"
      exit 0
      ;;
  esac
done

# Set defaults if not provided
MODEL="${MODEL:-$DEFAULT_MODEL}"

# If a custom model was provided but no tokenizer, use the model path as tokenizer path
# This avoids issues with the default tokenizer path containing problematic characters
if [[ -n "${MODEL:-}" && "${MODEL}" != "${DEFAULT_MODEL}" && -z "${TOKENIZER:-}" ]]; then
    TOKENIZER="${MODEL}"
    echo "[online] Using custom model path as tokenizer path: ${TOKENIZER}"
else
    TOKENIZER="${TOKENIZER:-$DEFAULT_TOKENIZER}"
fi
WORK_DIR="${WORK_DIR:-$DEFAULT_WORK_DIR}"
OUTPUT_DIR="${OUTPUT_DIR:-$WORK_DIR}"
GSM8K_SCRIPT="${GSM8K_SCRIPT:-$DEFAULT_GSM8K_SCRIPT}"
NODE="$(hostname)"  # Get node name from hostname
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
      if docker ps --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"; then
        echo "[online] Container already running."
        # Check if script and model are accessible inside the container
        if ! docker exec "${CONTAINER_NAME}" test -f "${SCRIPT_PATH}" 2>/dev/null; then
          echo "[online] Script not accessible in existing container. Recreating container..."
          docker stop "${CONTAINER_NAME}" >/dev/null 2>&1
          docker rm "${CONTAINER_NAME}" >/dev/null 2>&1
        elif ! docker exec "${CONTAINER_NAME}" test -d "${MODEL}" 2>/dev/null; then
          echo "[online] Model directory not accessible in existing container. Recreating container..."
          docker stop "${CONTAINER_NAME}" >/dev/null 2>&1
          docker rm "${CONTAINER_NAME}" >/dev/null 2>&1
        fi
      else
        echo "[online] Starting existing container ..."
        docker start "${CONTAINER_NAME}" >/dev/null || true
        # Check if script and model are accessible inside the container after starting
        if ! docker exec "${CONTAINER_NAME}" test -f "${SCRIPT_PATH}" 2>/dev/null; then
          echo "[online] Script not accessible in existing container. Recreating container..."
          docker stop "${CONTAINER_NAME}" >/dev/null 2>&1
          docker rm "${CONTAINER_NAME}" >/dev/null 2>&1
        elif ! docker exec "${CONTAINER_NAME}" test -d "${MODEL}" 2>/dev/null; then
          echo "[online] Model directory not accessible in existing container. Recreating container..."
          docker stop "${CONTAINER_NAME}" >/dev/null 2>&1
          docker rm "${CONTAINER_NAME}" >/dev/null 2>&1
        fi
      fi
    fi

    # Create container if it doesn't exist or was removed due to validation failure
    if ! docker ps -a --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"; then
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

      # Get the directory containing the script
      script_dir="$(dirname "${SCRIPT_PATH}")"

      # Create mount arguments - always mount MOUNT_DIR, and also mount script directory if different
      mount_args="-v ${MOUNT_DIR}:${MOUNT_DIR}"

      # If script directory is not under MOUNT_DIR, mount it separately
      if [[ "$script_dir" != "${MOUNT_DIR}"* ]]; then
          echo "[online] Script directory ${script_dir} is not under ${MOUNT_DIR}, mounting separately..."
          mount_args="${mount_args} -v ${script_dir}:${script_dir}"
      fi

      # If work directory is not under MOUNT_DIR or script directory, mount it separately
      if [[ "${WORK_DIR}" != "${MOUNT_DIR}"* ]] && [[ "${WORK_DIR}" != "$script_dir"* ]]; then
          echo "[online] Work directory ${WORK_DIR} is not under ${MOUNT_DIR}, mounting separately..."
          mount_args="${mount_args} -v ${WORK_DIR}:${WORK_DIR}"
      fi

      # If model directory is not under MOUNT_DIR, mount its parent directory
      model_dir="$(dirname "${MODEL}")"
      if [[ "$model_dir" != "${MOUNT_DIR}"* ]] && [[ "$model_dir" != "$script_dir"* ]]; then
          # For paths like /data/vmiriyal/model, mount /data
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
              echo "[online] Model directory ${MODEL} requires mounting ${mount_root}..."
              mount_args="${mount_args} -v ${mount_root}:${mount_root}"
          fi
      fi

      docker run -d --name "${CONTAINER_NAME}" \
        --shm-size "$CONTAINER_SHM_SIZE" --ipc=host --cap-add=SYS_PTRACE --network=host \
        --device=/dev/kfd --device=/dev/dri --security-opt seccomp=unconfined \
        ${mount_args} --group-add video --privileged \
        -w "$WORK_DIR_CONTAINER" "${FULL_IMAGE}" tail -f /dev/null
    fi

    # Build arguments to pass to the container
    CONTAINER_ARGS="--docker_image=\"${FULL_IMAGE}\" --model=\"${MODEL}\" --tokenizer=\"${TOKENIZER}\" --work-dir=\"${WORK_DIR}\" --output-dir=\"${OUTPUT_DIR}\" --gsm8k-script=\"${GSM8K_SCRIPT}\" --threshold=\"${THRESHOLD}\""

    # Add current directory if it was provided
    if [[ -n "${CURRENT_DIR}" ]]; then
      CONTAINER_ARGS="${CONTAINER_ARGS} --current-dir=\"${CURRENT_DIR}\""
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
        # Check if log already exists and is complete using glob pattern
        existing_log=""
        for log_file in "${folder}/sglang_client_log_${MODEL_NAME}_${mode}_${RATE}_run${i}"_*.log; do
            if [[ -f "$log_file" ]]; then
                # Check if the run actually completed by looking for "Run completed at:" in the log
                if grep -q "Run completed at:" "$log_file"; then
                    existing_log="$log_file"
                    break
                else
                    echo "Found incomplete log file: $log_file (missing 'Run completed at:' marker)"
                    echo "Removing incomplete log and re-running..."
                    rm -f "$log_file"
                fi
            fi
        done
        if [ -n "$existing_log" ]; then
            echo "Complete log for mode ${mode}, rate ${RATE}, run ${i} already exists. Skipping."
            # Update progress even for skipped runs
            update_progress "$RATE" "$i"
            continue
        fi

        LOGFILE="${folder}/sglang_client_log_${MODEL_NAME}_${mode}_${RATE}_run${i}_${TIMESTAMP}.log"
        echo "Running benchmark with request rate: $RATE (Run $i) for mode ${mode}" | tee -a "$LOGFILE"

        local run_start_time=$(date +%s)
        echo "Run started at: $(date '+%Y-%m-%d %H:%M:%S %Z')" | tee -a "$LOGFILE"

        NUM_PROMPTS=$(( PROMPTS_PER_RATE_MULTIPLIER * RATE ))
        [ "$NUM_PROMPTS" -gt "$MAX_NUM_PROMPTS" ] && NUM_PROMPTS="$MAX_NUM_PROMPTS"

        CMD="python3 -m sglang.bench_serving --backend sglang --tokenizer \"${TOKENIZER}\" --dataset-name random --random-input $RANDOM_INPUT_LENGTH --random-output $RANDOM_OUTPUT_LENGTH --num-prompts $NUM_PROMPTS --request-rate $RATE"
        echo "Executing: $CMD" | tee -a "$LOGFILE"
        eval "$CMD" 2>&1 | tee -a "$LOGFILE"

        # Clean up any accidentally generated JSONL files
        rm -f online_*.jsonl result.jsonl *.jsonl 2>/dev/null || true

        local run_end_time=$(date +%s)
        local run_duration=$((run_end_time - run_start_time))
        echo "Run completed at: $(date '+%Y-%m-%d %H:%M:%S %Z')" | tee -a "$LOGFILE"
        echo "Run duration: ${run_duration} seconds" | tee -a "$LOGFILE"
        echo "----------------------------------------" | tee -a "$LOGFILE"

        # Log to timing summary
        echo "  Rate ${RATE}, Run ${i}: ${run_duration} seconds" >> "$TIMING_LOG"

        # Update progress after each run completes
        update_progress "$RATE" "$i"

        # Add sleep between runs to avoid memory access faults (except after the last run)
        if [ "$i" -lt 3 ]; then
            # Special handling for rate 16 - needs longer recovery time and memory cleanup
            if [ "$RATE" -eq 16 ]; then
                echo "Rate 16 detected - sleeping 20 seconds and clearing memory before next run..."
                sleep 20

                # Clear GPU memory cache and force garbage collection
                echo "Clearing GPU memory cache..."
                if command -v curl &> /dev/null; then
                    # Try to clear server cache if possible
                    curl -s -X POST "http://0.0.0.0:30000/flush_cache" >/dev/null 2>&1 || true
                fi

                # Force Python garbage collection on next server request
                echo "Memory cleanup completed"
            else
                echo "Sleeping 10 seconds between runs to avoid memory access faults..."
                sleep 10
            fi
        fi
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

# Function to check if all benchmark runs and GSM8K test are complete
check_all_logs_complete() {
    echo "Scanning existing logs to check if all benchmarks are already complete..."
    local all_complete=true
    local gsm8k_complete=true

    # 1. Check GSM8K log completion
    echo "Checking GSM8K log status..."
    local gsm8k_log="${folder}/sglang_client_log_${MODEL_NAME}_gsm8k_${ATTENTION_BACKEND}.log"
    if [ ! -f "$gsm8k_log" ]; then
        echo "Missing: GSM8K log file ($gsm8k_log)"
        gsm8k_complete=false
    elif [ ! -s "$gsm8k_log" ]; then
        echo "Empty: GSM8K log file ($gsm8k_log)"
        gsm8k_complete=false
    else
        # Check if GSM8K test completed successfully
        if grep -q "Average Accuracy over $GSM8K_RUNS runs for mode ${ATTENTION_BACKEND}" "$gsm8k_log" && \
           grep -q "Average accuracy meets threshold\|Average accuracy.*is below threshold" "$gsm8k_log"; then
            echo "✅ GSM8K log file is complete."
        else
            echo "Incomplete: GSM8K log file missing final summary ($gsm8k_log)"
            gsm8k_complete=false
        fi
    fi

    # 2. Check client benchmark logs completion
    echo "Checking client benchmark logs..."
    local expected_runs_per_rate=3
    local total_expected_logs=$((${#REQ_RATES[@]} * expected_runs_per_rate))
    local actual_completed_logs=0

    for rate in "${REQ_RATES[@]}"; do
        for i in $(seq 1 $expected_runs_per_rate); do
            local log_found=false
            for log_file in "${folder}/sglang_client_log_${MODEL_NAME}_${ATTENTION_BACKEND}_${rate}_run${i}"_*.log; do
                if [ -f "$log_file" ] && grep -q "Run completed at:" "$log_file"; then
                    log_found=true
                    break
                fi
            done
            if [ "$log_found" = true ]; then
                actual_completed_logs=$((actual_completed_logs + 1))
            fi
        done
    done

    echo "Scan complete: ${actual_completed_logs}/${total_expected_logs} client benchmark runs are complete."

    if [ "$actual_completed_logs" -lt "$total_expected_logs" ]; then
        all_complete=false
    fi

    if [ "$all_complete" = true ] && [ "$gsm8k_complete" = true ]; then
        echo "✅ All benchmark logs (client + GSM8K) are present and complete! No server startup needed."
        return 0 # Success
    else
        if [ "$all_complete" = false ]; then
            echo "❌ Missing or incomplete client benchmark runs."
        fi
        if [ "$gsm8k_complete" = false ]; then
            echo "❌ GSM8K benchmark is missing, empty, or incomplete."
        fi
        echo "Server startup required."
        return 1 # Failure
    fi
}

# ---------------------------
# 6b. CSV Generation Functions
# ---------------------------
# Global arrays for storing metrics
declare -A best_e2e_aiter best_ttft_aiter best_itl_aiter

# Request rates array
read -ra REQ_RATES <<< "$REQUEST_RATES"

# ---------------------------
# 6c. Progress Tracking
# ---------------------------
# Global variables for progress tracking
TOTAL_RUNS=0
CURRENT_RUN=0

# Function to calculate total number of runs
calculate_total_runs() {
    local rates_array
    read -ra rates_array <<< "$REQUEST_RATES"
    TOTAL_RUNS=$((${#rates_array[@]} * 3))  # 3 runs per rate
    echo "[progress] Total benchmark runs to execute: ${TOTAL_RUNS}"
}

# Function to display progress bar
show_progress() {
    local current=$1
    local total=$2
    local width=50
    local percentage=$((current * 100 / total))
    local filled=$((current * width / total))
    local empty=$((width - filled))

    printf "\r[progress] ["
    printf "%*s" "$filled" | tr ' ' '='
    printf "%*s" "$empty" | tr ' ' '-'
    printf "] %d/%d (%d%%) " "$current" "$total" "$percentage"

    if [ "$current" -eq "$total" ]; then
        echo "✅ Complete!"
    fi
}

# Function to update progress with optional run details
update_progress() {
    local rate=${1:-}
    local run_num=${2:-}
    CURRENT_RUN=$((CURRENT_RUN + 1))
    show_progress "$CURRENT_RUN" "$TOTAL_RUNS"
    if [ -n "$rate" ] && [ -n "$run_num" ]; then
        echo " | Rate: $rate, Run: $run_num"
    elif [ "$CURRENT_RUN" -lt "$TOTAL_RUNS" ]; then
        echo ""  # New line for next progress update
    fi
}

# Compute ratio function removed since H100 baseline data is no longer used

# Initialize the CSV with headers
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

    # Placeholder for MI300x results
    printf "MI300x-${ATTENTION_BACKEND}, $NODE" >> "$OUTPUT_CSV"
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

    # Placeholder for MI300x results
    printf "MI300x-${ATTENTION_BACKEND}, $NODE" >> "$OUTPUT_CSV"
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

    # Placeholder for MI300x results
    printf "MI300x-${ATTENTION_BACKEND}, $NODE" >> "$OUTPUT_CSV"
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

        printf "MI300x-${ATTENTION_BACKEND}, $NODE"
        for r in "${REQ_RATES[@]}"; do
            printf "\t%s" "${best_e2e_aiter[$r]:-}"
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

        printf "MI300x-${ATTENTION_BACKEND}, $NODE"
        for r in "${REQ_RATES[@]}"; do
            printf "\t%s" "${best_ttft_aiter[$r]:-}"
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

        printf "MI300x-${ATTENTION_BACKEND}, $NODE"
        for r in "${REQ_RATES[@]}"; do
            printf "\t%s" "${best_itl_aiter[$r]:-}"
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

    # Initialize progress tracking
    calculate_total_runs
    CURRENT_RUN=0
    show_progress 0 "$TOTAL_RUNS"
    echo ""

    # Initialize CSV at the start
    init_csv

    # Sequential execution only - model uses all 8 GPUs
    echo "Running benchmarks sequentially..."
    echo "" >> "$TIMING_LOG"
    echo "Client Benchmark Results:" >> "$TIMING_LOG"
    echo "  Start time: $(date '+%Y-%m-%d %H:%M:%S %Z')" >> "$TIMING_LOG"

    local rate_count=0
    local total_rates=${#REQUEST_RATES_ARRAY[@]}

    for RATE in "${REQUEST_RATES_ARRAY[@]}"; do
        rate_count=$((rate_count + 1))
        run_single_rate_benchmark "$mode" "$RATE" "$TIMESTAMP"
        # Update CSV after each rate completes all runs
        update_csv_for_rate "$RATE"

        # Add 3 second sleep between different request rates (except after the last rate)
        if [ "$rate_count" -lt "$total_rates" ]; then
            echo "Sleeping 3 seconds before next request rate..."
            sleep 3
        fi
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
ATTENTION_BACKEND="aiter" # Set backend before log check

if check_all_logs_complete; then
    echo "Skipping server startup and benchmark execution - generating CSV from existing logs..."
    echo "All logs already complete - skipping server startup" >> "$TIMING_LOG"

    # Initialize and populate CSV from existing logs
    init_csv
    for rate in "${REQ_RATES[@]}"; do
        update_csv_for_rate "$rate"
    done

    echo "✅ CSV generated from existing logs successfully."

else
    echo "Starting benchmarks using ${ATTENTION_BACKEND} backend..."
    launch_server

    if run_client_gsm8k "${ATTENTION_BACKEND}"; then
        run_client_benchmark "${ATTENTION_BACKEND}"
    else
        echo "Skipping benchmarks for ${ATTENTION_BACKEND} backend due to low GSM8K accuracy."
    fi

    shutdown_server
fi

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
