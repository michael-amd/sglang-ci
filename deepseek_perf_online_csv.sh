#!/usr/bin/env bash
# ------------------------------------------------------------------------------
# deepseek_perf_online_csv.sh
#
# Online-throughput benchmark for DeepSeek on TP=8 MI300x using GSM8K.
#
# USAGE:
#   bash deepseek_perf_online_csv.sh --docker_image=rocm/sgl-dev:20250707
#   bash deepseek_perf_online_csv.sh --docker_image=lmsysorg/sglang:v0.4.8-rocm630
#   bash deepseek_perf_online_csv.sh --model=/path/to/model --model-name=DeepSeek-V3
#   bash deepseek_perf_online_csv.sh --work-dir=/path/to/workdir --output-dir=/path/to/output
#   bash deepseek_perf_online_csv.sh --skip-gsm8k
# ------------------------------------------------------------------------------
set -euo pipefail

# Set timezone to PST/PDT
export TZ='America/Los_Angeles'

###############################################################################
# Configuration Variables - Override via environment variables if needed
###############################################################################

# Default image and model configuration
DOCKER_IMAGE_DEFAULT="${DEFAULT_DOCKER_IMAGE:-rocm/sgl-dev:20250430}"
MODEL_VARIANT="${BENCHMARK_MODEL_VARIANT:-FP8}"

# Default paths - can be overridden
DEFAULT_MODEL="${DEFAULT_MODEL_PATH:-/mnt/raid/models/huggingface/deepseek-ai/DeepSeek-V3-0324}"
DEFAULT_MODEL_NAME="${DEFAULT_MODEL_NAME:-DeepSeek-V3-0324}"
DEFAULT_HF_MODEL_ID="${DEFAULT_HF_MODEL_ID:-deepseek-ai/DeepSeek-V3-0324}"
DEFAULT_WORK_DIR="${DEFAULT_WORK_DIR:-/mnt/raid/michael/sgl_benchmark_ci}"
DEFAULT_OUTPUT_DIR="${DEFAULT_OUTPUT_DIR:-}"  # If empty, will use work_dir
DEFAULT_GSM8K_SCRIPT="${DEFAULT_GSM8K_SCRIPT:-/mnt/raid/michael/sgl-project/sglang/benchmark/gsm8k/bench_sglang.py}"
DEFAULT_THRESHOLD="${DEFAULT_GSM8K_THRESHOLD:-0.93}"

# Container configuration
CONTAINER_SHM_SIZE="${CONTAINER_SHM_SIZE:-32g}"
MOUNT_DIR="${MOUNT_DIR:-/mnt/raid/}"
WORK_DIR_CONTAINER="${WORK_DIR_CONTAINER:-/sgl-workspace}"

# Benchmark configuration (can be overridden)
TP="${DEEPSEEK_TP:-8}"                      # tensor-parallel degree

# GSM8K configuration
GSM8K_NUM_QUESTIONS="${GSM8K_NUM_QUESTIONS:-2000}"
GSM8K_PARALLEL="${GSM8K_PARALLEL:-2000}"
GSM8K_NUM_SHOTS="${GSM8K_NUM_SHOTS:-5}"
GSM8K_RUNS="${GSM8K_RUNS:-5}"
GSM8K_PORT="${GSM8K_PORT:-30000}"
GSM8K_HOST="${GSM8K_HOST:-http://127.0.0.1}"

# Server configuration
SERVER_MEM_FRACTION="${SERVER_MEM_FRACTION:-0.9}"
SERVER_MAX_REQUESTS="${SERVER_MAX_REQUESTS:-1024}"
SERVER_TIMEOUT="${SERVER_TIMEOUT:-900}"  # 15 minutes

# File to store background server PID
SERVER_PID_FILE=$(mktemp)

cleanup() {
    echo "Cleaning up..."
    if [ -f "$SERVER_PID_FILE" ] && [ -s "$SERVER_PID_FILE" ]; then
        BG_PID=$(cat "$SERVER_PID_FILE")
        # Check if process exists
        if ps -p $BG_PID > /dev/null 2>&1; then
            echo "Killing background SGLang server (PID: $BG_PID)..."
            kill $BG_PID
            wait $BG_PID 2>/dev/null || echo "Server process $BG_PID not found or already terminated."
        else
            echo "Background SGLang server (PID: $BG_PID) already stopped."
        fi
    fi
    rm -f "$SERVER_PID_FILE"
}
trap cleanup EXIT SIGINT SIGTERM

###############################################################################
# 0. Parse CLI flags
###############################################################################
docker_image=""

# Initialize variables
MODEL=""
MODEL_NAME=""
HF_MODEL_ID=""
WORK_DIR=""
OUTPUT_DIR=""
GSM8K_SCRIPT=""
THRESHOLD=""
DOWNLOAD_MODEL="false"
SKIP_GSM8K="false"
SCRIPT_PATH="$0"  # Get the script path from how it was called

# Get absolute path of the script
if [[ "$SCRIPT_PATH" != /* ]]; then
    SCRIPT_PATH="$(cd "$(dirname "$0")" && pwd)/$(basename "$0")"
fi

for arg in "$@"; do
  case $arg in
    --docker_image=*|--docker-image=*) # Handle both --docker_image and --docker-image
      docker_image="${arg#*=}"
      shift # Remove parsed argument
      ;;
    --model=*)
      MODEL="${arg#*=}"
      shift
      ;;
    --model-name=*)
      MODEL_NAME="${arg#*=}"
      shift
      ;;
    --hf-model-id=*)
      HF_MODEL_ID="${arg#*=}"
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
    --threshold=*)
      THRESHOLD="${arg#*=}"
      shift
      ;;
    --download-model)
      DOWNLOAD_MODEL="true"
      shift
      ;;
    --skip-gsm8k)
      SKIP_GSM8K="true"
      shift
      ;;
    --help)
      echo "Usage: $0 [OPTIONS]"
      echo "Options:"
      echo "  --docker_image=IMAGE    Docker image to use (default: $DOCKER_IMAGE_DEFAULT)"
      echo "  --model=PATH           Model path (default: $DEFAULT_MODEL)"
      echo "  --model-name=NAME      Model name for output files (default: $DEFAULT_MODEL_NAME)"
      echo "  --hf-model-id=ID       HuggingFace model ID for download (default: $DEFAULT_HF_MODEL_ID)"
      echo "  --work-dir=PATH        Working directory (default: $DEFAULT_WORK_DIR)"
      echo "  --output-dir=PATH      Output directory (default: same as work-dir)"
      echo "  --gsm8k-script=PATH    Path to GSM8K benchmark script (default: $DEFAULT_GSM8K_SCRIPT)"
      echo "  --threshold=VALUE      GSM8K accuracy threshold (default: $DEFAULT_THRESHOLD)"
      echo "  --download-model       Download model if not present (default: false)"
      echo "  --skip-gsm8k           Skip GSM8K accuracy test and go straight to serving benchmarks"
      echo "  --help                 Show this help message"
      echo ""
      echo "Environment Variables:"
      echo "  DEFAULT_DOCKER_IMAGE      Default Docker image"
      echo "  DEFAULT_MODEL_PATH        Default model path"
      echo "  DEFAULT_MODEL_NAME        Default model name"
      echo "  DEEPSEEK_TP               Tensor parallel degree (default: $TP)"
      echo "  GSM8K_NUM_QUESTIONS       GSM8K questions count (default: $GSM8K_NUM_QUESTIONS)"
      echo "  GSM8K_RUNS               GSM8K test runs (default: $GSM8K_RUNS)"
      echo "  SERVER_TIMEOUT            Server startup timeout (default: $SERVER_TIMEOUT seconds)"
      exit 0
      ;;
  esac
done

# Set defaults if not provided
MODEL="${MODEL:-$DEFAULT_MODEL}"
MODEL_NAME="${MODEL_NAME:-$DEFAULT_MODEL_NAME}"
HF_MODEL_ID="${HF_MODEL_ID:-$DEFAULT_HF_MODEL_ID}"
WORK_DIR="${WORK_DIR:-$DEFAULT_WORK_DIR}"
OUTPUT_DIR="${OUTPUT_DIR:-$WORK_DIR}"
GSM8K_SCRIPT="${GSM8K_SCRIPT:-$DEFAULT_GSM8K_SCRIPT}"
THRESHOLD="${THRESHOLD:-$DEFAULT_THRESHOLD}"

# If not provided by flag, use positional argument or default
docker_image="${docker_image:-${1:-$DOCKER_IMAGE_DEFAULT}}"

###############################################################################
# 0-b. Use the full image name as provided (no auto-prefixing)
###############################################################################
FULL_IMAGE="$docker_image"

IMAGE_WITH_TAG="${FULL_IMAGE##*/}" # e.g., sgl-dev:20250708
LATEST_TAG="${IMAGE_WITH_TAG#*:}"   # e.g., 20250708

# ---------------------------
# 0-c. Container Management (if applicable)
# ---------------------------
if [ -z "${INSIDE_CONTAINER:-}" ]; then
  if ! command -v docker >/dev/null 2>&1; then
    echo "[csv] Docker not found — already inside container."
    INSIDE_CONTAINER=1
  else
    IMAGE_WITH_TAG_FOR_CONTAINER_NAME="${FULL_IMAGE##*/}"      # sgl-dev:20250708
    REPO="${IMAGE_WITH_TAG_FOR_CONTAINER_NAME%%:*}"            # sgl-dev
    TAG_FOR_CONTAINER_NAME="${IMAGE_WITH_TAG_FOR_CONTAINER_NAME#*:}"       # 20250708
    CONTAINER_NAME="${REPO}_${TAG_FOR_CONTAINER_NAME}"

    echo "[csv] Target container : ${CONTAINER_NAME}"
    echo "[csv] Docker image     : ${FULL_IMAGE}"

    if docker ps -a --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"; then
      if docker ps --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"; then
        echo "[csv] Container already running."
      else
        echo "[csv] Starting existing container ..."
        docker start "${CONTAINER_NAME}"
      fi
    else
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
      docker run -d --name "${CONTAINER_NAME}" \
        --shm-size "$CONTAINER_SHM_SIZE" --ipc=host --cap-add=SYS_PTRACE --network=host \
        --device=/dev/kfd --device=/dev/dri --security-opt seccomp=unconfined \
        -v "${MOUNT_DIR}:${MOUNT_DIR}" --group-add video --privileged \
        -w "$WORK_DIR_CONTAINER" "${FULL_IMAGE}" tail -f /dev/null
    fi

    echo "[csv] Re-invoking inside ${CONTAINER_NAME} ..."
    docker exec \
      -e INSIDE_CONTAINER=1 \
      -e LATEST_TAG="${LATEST_TAG}" \
      -e TZ='America/Los_Angeles' \
      "${CONTAINER_NAME}" \
      bash "${SCRIPT_PATH}" \
           --docker_image="${FULL_IMAGE}" \
           --model="${MODEL}" \
           --model-name="${MODEL_NAME}" \
           --hf-model-id="${HF_MODEL_ID}" \
           --work-dir="${WORK_DIR}" \
           --output-dir="${OUTPUT_DIR}" \
           --gsm8k-script="${GSM8K_SCRIPT}" \
           --threshold="${THRESHOLD}" \
           $([ "$DOWNLOAD_MODEL" = "true" ] && echo "--download-model") \
           $([ "$SKIP_GSM8K" = "true" ] && echo "--skip-gsm8k")
    exit 0
  fi
fi

# ---------------------------
# 1. Inside Container: Setup Run Folder
# ---------------------------
cd "${WORK_DIR}" || { echo "Cannot change to ${WORK_DIR} directory"; exit 1; }

# If LATEST_TAG is not already defined (e.g. when script is re-invoked inside container), extract it.
if [ -z "$LATEST_TAG" ]; then
    IMAGE_WITH_TAG_FROM_ARG=${docker_image#*/}
    LATEST_TAG=${IMAGE_WITH_TAG_FROM_ARG#*:}
fi

# ---- Download model if requested and not present (inside container) ----
if [ -n "${INSIDE_CONTAINER}" ] && [ "$DOWNLOAD_MODEL" = "true" ]; then # Only run download logic if inside the container and requested
  if command -v huggingface-cli >/dev/null 2>&1; then
    echo "[csv] huggingface-cli found. Ensuring model ${HF_MODEL_ID} is downloaded to ${MODEL}..."
    # Ensure parent directory of MODEL exists, huggingface-cli creates the final dir.
    mkdir -p "$(dirname "${MODEL}")"
    huggingface-cli download "${HF_MODEL_ID}" \
      --repo-type model \
      --local-dir "${MODEL}" \
      --local-dir-use-symlinks False \
      --resume-download
      # For private models or to ensure a specific user context for downloads,
      # you might need to pass --token $YOUR_HF_TOKEN or ensure `huggingface-cli login` was done.
      # For public models like deepseek, token is usually not strictly needed.
    echo "[csv] Model download/verification attempt complete for ${MODEL}."
    if [ ! -d "${MODEL}" ] || [ -z "$(ls -A "${MODEL}")" ]; then
      echo "[csv] ERROR: Model directory ${MODEL} is still missing or empty after download attempt."
      echo "[csv] Please check for errors from huggingface-cli output above."
      echo "[csv] Also ensure you have network access and permissions to write to the target path."
      echo "[csv] Contents of $(dirname "${MODEL}"):"
      ls -la "$(dirname "${MODEL}")"
      exit 1
    else
      echo "[csv] Model files appear to be present in ${MODEL}."
    fi
  else
    echo "[csv] WARNING: huggingface-cli not found. Assuming model ${HF_MODEL_ID} is already present at ${MODEL}."
    if [ ! -d "${MODEL}" ] || [ -z "$(ls -A "${MODEL}")" ]; then
      echo "[csv] ERROR: Model directory ${MODEL} is missing or empty, and huggingface-cli is not available to download it."
      exit 1
    fi
  fi
fi
# ---- End model download ----

## 2.  Run-folder bookkeeping ---------------------------------------------------
SCRIPT_START_TIME=$(date +%s)
echo "[online] Script started at: $(date '+%Y-%m-%d %H:%M:%S %Z')"

folder="${OUTPUT_DIR}/online/${MODEL_NAME}/${LATEST_TAG}_${MODEL_NAME}_${MODEL_VARIANT}_online"
mkdir -p "$folder"
OUTPUT_CSV="${folder}/${LATEST_TAG}_${MODEL_NAME}_${MODEL_VARIANT}_online.csv"
SERVER_LOG_FILE="${folder}/sglang_server.log" # Define server log path
GSM8K_LOG_FILE="${folder}/sglang_client_log_${MODEL_NAME}_gsm8k.log" # Define GSM8K log path

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
# 3. GSM8K Online Benchmark Function
# This function runs the GSM8K test multiple times, computes the average accuracy,
# and collects performance metrics for CSV output.
###############################################################################
run_gsm8k_benchmark() {
    local gsm8k_start_time=$(date +%s)
    local total_accuracy=0
    local runs=$GSM8K_RUNS
    local count=0
    local run_accuracy=0
    local output
    local all_outputs=""

    echo "Starting GSM8K Online Benchmark..."
    echo "Running $runs test iterations..."
    echo "Starting GSM8K accuracy test at: $(date '+%Y-%m-%d %H:%M:%S %Z')" > "$GSM8K_LOG_FILE"

    # Run the test 'runs' times
    for i in $(seq 1 $runs); do
         local run_start_time=$(date +%s)
         echo "Executing GSM8K test Run $i ..." | tee -a "$GSM8K_LOG_FILE"
         output=$(python3 "${GSM8K_SCRIPT}" --num-questions "$GSM8K_NUM_QUESTIONS" --parallel "$GSM8K_PARALLEL" --num-shots "$GSM8K_NUM_SHOTS" --port "$GSM8K_PORT" --host "$GSM8K_HOST" 2>&1)
         local run_end_time=$(date +%s)
         local run_duration=$((run_end_time - run_start_time))
         echo "$output" | tee -a "$GSM8K_LOG_FILE"
         echo "Run $i completed in ${run_duration} seconds" | tee -a "$GSM8K_LOG_FILE"
         all_outputs="$all_outputs$output"$'\n'

         # Extract the accuracy value from the output; expects a line like "Accuracy: 0.820"
         run_accuracy=$(echo "$output" | tr '\r' '\n' | grep '^Accuracy: ' | head -n 1 | awk '{print $2}')
         if [ -z "$run_accuracy" ]; then
            echo "Run $i: Accuracy not found, defaulting to 0" | tee -a "$GSM8K_LOG_FILE"
            run_accuracy=0
         fi
         echo "Run $i: Accuracy: $run_accuracy" | tee -a "$GSM8K_LOG_FILE"
         total_accuracy=$(awk -v t="$total_accuracy" -v a="$run_accuracy" 'BEGIN { printf "%.3f", t+a }')
         count=$((count+1))
    done

    local avg_accuracy
    avg_accuracy=$(awk -v total="$total_accuracy" -v runs="$runs" 'BEGIN { printf "%.3f", total/runs }')
    local gsm8k_end_time=$(date +%s)
    local gsm8k_duration=$((gsm8k_end_time - gsm8k_start_time))
    echo "GSM8K test completed in ${gsm8k_duration} seconds" | tee -a "$GSM8K_LOG_FILE"
    echo "Average Accuracy over $runs runs: $avg_accuracy" | tee -a "$GSM8K_LOG_FILE"

    # Log to timing summary
    echo "" >> "$TIMING_LOG"
    echo "GSM8K Test Results:" >> "$TIMING_LOG"
    echo "  Total duration: ${gsm8k_duration} seconds" >> "$TIMING_LOG"
    echo "  Average accuracy: $avg_accuracy" >> "$TIMING_LOG"
    echo "  Number of runs: $runs" >> "$TIMING_LOG"

    # Extract performance metrics from the combined output
    # Look for throughput and latency metrics in GSM8K output format
    # Expected format: "Output throughput: XX.XXX token/s" and "Latency: XX.XXX s"
    local avg_throughput=$(echo "$all_outputs" | grep -oP 'Output throughput:\s*\K[\d.]+' | awk '{sum+=$1} END {if(NR>0) print sum/NR; else print "0"}')
    local avg_latency=$(echo "$all_outputs" | grep -oP 'Latency:\s*\K[\d.]+(?=\s*s)' | awk '{sum+=$1} END {if(NR>0) print sum/NR; else print "0"}')

    # If no specific throughput/latency metrics found, set to N/A
    if [ -z "$avg_throughput" ] || [ "$avg_throughput" = "0" ]; then
        avg_throughput="N/A"
    fi
    if [ -z "$avg_latency" ] || [ "$avg_latency" = "0" ]; then
        avg_latency="N/A"
    fi

    # Write results to CSV in structured format
    echo "Results" >> "$OUTPUT_CSV"
    echo "Average Accuracy\t${avg_accuracy}" >> "$OUTPUT_CSV"
    echo "Average Throughput (tokens/s)\t${avg_throughput}" >> "$OUTPUT_CSV"
    echo "Average Latency (s)\t${avg_latency}" >> "$OUTPUT_CSV"

    # Check if accuracy meets threshold
    if awk "BEGIN {exit !($avg_accuracy >= $THRESHOLD)}"; then
         echo "Average accuracy meets threshold ($THRESHOLD)." | tee -a "$GSM8K_LOG_FILE"
         return 0
    else
         echo "Average accuracy ($avg_accuracy) is below threshold ($THRESHOLD)." | tee -a "$GSM8K_LOG_FILE"
         return 1
    fi
}

###############################################################################
# 4. Main Online Benchmark
###############################################################################
# Server startup will be conditional based on whether benchmarks are needed

###############################################################################
# 5. Function to Get Best Metrics from Multiple Runs
###############################################################################
get_best_metrics() {
    local concurrency=$1
    local best_e2e=""
    local best_ttft=""
    local best_itl=""
    local best_file=""
    local best_output_throughput=""

    for f in "${folder}/sglang_serving_benchmark_concurrency_${concurrency}_run"*".log"; do
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
        echo "NA NA NA NA"
    else
        best_ttft=$(grep -oP 'Median TTFT \(ms\):\s*\K[\d.]+' "$best_file" | head -n1)
        best_itl=$(grep -oP 'Median ITL \(ms\):\s*\K[\d.]+' "$best_file" | head -n1)
        best_output_throughput=$(grep -oP 'Output token throughput \(tok/s\):\s*\K[\d.]+' "$best_file" | head -n1)
        [ -z "$best_ttft" ] && best_ttft="NA"
        [ -z "$best_itl" ] && best_itl="NA"
        [ -z "$best_output_throughput" ] && best_output_throughput="NA"
        echo "$best_e2e $best_ttft $best_itl $best_output_throughput"
    fi
}

###############################################################################
# 6. Serving Benchmark with Different Concurrency Levels
###############################################################################

# Setup serving benchmark CSV
SERVING_CSV="${folder}/${LATEST_TAG}_${MODEL_NAME}_${MODEL_VARIANT}_serving.csv"

# Global arrays for storing metrics per concurrency
declare -A best_e2e_metrics best_ttft_metrics best_itl_metrics

# Concurrency levels for organized output
concurrency_values=(128 64 16 4 1)

# Initialize CSV with structured format like grok
init_serving_csv() {
    echo "Online Serving Benchmark - ${MODEL_NAME} (${LATEST_TAG})" > "$SERVING_CSV"
    echo "" >> "$SERVING_CSV"

    # E2E Latency section
    echo "Median E2E Latency (ms, lower better)" >> "$SERVING_CSV"
    printf "concurrency" >> "$SERVING_CSV"
    for conc in "${concurrency_values[@]}"; do
        printf "\t%s" "$conc" >> "$SERVING_CSV"
    done
    echo "" >> "$SERVING_CSV"

    # Placeholder for results
    printf "${MODEL_NAME}-${MODEL_VARIANT}" >> "$SERVING_CSV"
    for conc in "${concurrency_values[@]}"; do
        printf "\t" >> "$SERVING_CSV"
    done
    echo "" >> "$SERVING_CSV"
    echo "" >> "$SERVING_CSV"

    # TTFT section
    echo "Median TTFT (ms, lower better)" >> "$SERVING_CSV"
    printf "concurrency" >> "$SERVING_CSV"
    for conc in "${concurrency_values[@]}"; do
        printf "\t%s" "$conc" >> "$SERVING_CSV"
    done
    echo "" >> "$SERVING_CSV"

    # Placeholder for results
    printf "${MODEL_NAME}-${MODEL_VARIANT}" >> "$SERVING_CSV"
    for conc in "${concurrency_values[@]}"; do
        printf "\t" >> "$SERVING_CSV"
    done
    echo "" >> "$SERVING_CSV"
    echo "" >> "$SERVING_CSV"

    # ITL section
    echo "Median ITL (ms, lower better)" >> "$SERVING_CSV"
    printf "concurrency" >> "$SERVING_CSV"
    for conc in "${concurrency_values[@]}"; do
        printf "\t%s" "$conc" >> "$SERVING_CSV"
    done
    echo "" >> "$SERVING_CSV"

    # Placeholder for results
    printf "${MODEL_NAME}-${MODEL_VARIANT}" >> "$SERVING_CSV"
    for conc in "${concurrency_values[@]}"; do
        printf "\t" >> "$SERVING_CSV"
    done
    echo "" >> "$SERVING_CSV"
    echo "" >> "$SERVING_CSV"



    echo "[online] Structured CSV initialized at ${SERVING_CSV}"
}



# Update CSV with results for a specific concurrency
update_serving_csv_for_concurrency() {
    local concurrency=$1

    # Get metrics for this concurrency
    read e2e ttft itl output_throughput < <(get_best_metrics "$concurrency")

    # Store metrics
    best_e2e_metrics[$concurrency]="$e2e"
    best_ttft_metrics[$concurrency]="$ttft"
    best_itl_metrics[$concurrency]="$itl"

    echo "[online] Updating CSV for concurrency ${concurrency}: E2E=${e2e}ms, TTFT=${ttft}ms, ITL=${itl}ms"

    # Rebuild the entire CSV with current data
    {
        echo "Online Serving Benchmark - ${MODEL_NAME} (${LATEST_TAG})"
        echo ""

        # E2E Latency section
        echo "Median E2E Latency (ms, lower better)"
        printf "concurrency"
        for conc in "${concurrency_values[@]}"; do
            printf "\t%s" "$conc"
        done
        echo ""

        printf "${MODEL_NAME}-${MODEL_VARIANT}"
        for conc in "${concurrency_values[@]}"; do
            printf "\t%s" "${best_e2e_metrics[$conc]:-}"
        done
        echo ""
        echo ""

        # TTFT section
        echo "Median TTFT (ms, lower better)"
        printf "concurrency"
        for conc in "${concurrency_values[@]}"; do
            printf "\t%s" "$conc"
        done
        echo ""

        printf "${MODEL_NAME}-${MODEL_VARIANT}"
        for conc in "${concurrency_values[@]}"; do
            printf "\t%s" "${best_ttft_metrics[$conc]:-}"
        done
        echo ""
        echo ""

        # ITL section
        echo "Median ITL (ms, lower better)"
        printf "concurrency"
        for conc in "${concurrency_values[@]}"; do
            printf "\t%s" "$conc"
        done
        echo ""

        printf "${MODEL_NAME}-${MODEL_VARIANT}"
        for conc in "${concurrency_values[@]}"; do
            printf "\t%s" "${best_itl_metrics[$conc]:-}"
        done
        echo ""
    } > "$SERVING_CSV"

    echo "[online] CSV updated with results for concurrency ${concurrency}"
}

# Function to run a single benchmark run
run_single_concurrency_benchmark() {
    local concurrency=$1
    local run_number=$2
    local timestamp=$3
    local concurrency_log_file="${folder}/sglang_serving_benchmark_concurrency_${concurrency}_run${run_number}_${timestamp}.log"

    # Check if log already exists and completed successfully using glob pattern
    existing_log=""
    for log_file in "${folder}/sglang_serving_benchmark_concurrency_${concurrency}_run${run_number}"_*.log; do
        if [[ -f "$log_file" ]]; then
            # Check if the run completed successfully by looking for completion marker
            if grep -q "Run completed at:" "$log_file"; then
                existing_log="$log_file"
                break
            else
                echo "Found incomplete log file: $log_file - will re-run this benchmark"
            fi
        fi
    done
    if [ -n "$existing_log" ]; then
        echo "Log for concurrency ${concurrency}, run ${run_number} already exists and completed successfully. Skipping."
        return 0
    fi

    # Determine number of prompts based on concurrency level
    local num_prompts
    if [ "$concurrency" -le 16 ]; then
        num_prompts=128
    else
        num_prompts=500
    fi

    echo "Running serving benchmark with concurrency ${concurrency} (Run ${run_number}) - ${num_prompts} prompts..."
    local run_start_time=$(date +%s)
    echo "Run started at: $(date '+%Y-%m-%d %H:%M:%S %Z')" > "$concurrency_log_file"
    echo "Using ${num_prompts} prompts for concurrency ${concurrency}" >> "$concurrency_log_file"

    # Run bench_serving and capture output
    local output=$(python3 -m sglang.bench_serving \
        --backend sglang \
        --dataset-name random \
        --random-range-ratio 1 \
        --num-prompts "${num_prompts}" \
        --random-input-len 3200 \
        --random-output-len 800 \
        --max-concurrency "${concurrency}" \
        --port "$GSM8K_PORT" \
        --host "127.0.0.1" 2>&1)

    local run_end_time=$(date +%s)
    local run_duration=$((run_end_time - run_start_time))

    echo "$output" >> "$concurrency_log_file"
    echo "Run completed at: $(date '+%Y-%m-%d %H:%M:%S %Z')" >> "$concurrency_log_file"
    echo "Run duration: ${run_duration} seconds" >> "$concurrency_log_file"

    # Parse metrics from output for immediate feedback
    local successful_requests=$(echo "$output" | grep -oP 'Successful requests:\s*\K\d+' | tail -1)
    local output_throughput=$(echo "$output" | grep -oP 'Output token throughput \(tok/s\):\s*\K[\d.]+' | tail -1)
    local median_e2e_latency=$(echo "$output" | grep -oP 'Median E2E Latency \(ms\):\s*\K[\d.]+' | tail -1)

    echo "Concurrency ${concurrency}, Run ${run_number} completed: ${successful_requests:-0} requests, ${output_throughput:-0} tok/s output throughput, ${median_e2e_latency:-0}ms E2E latency (${run_duration}s)"

    # Log to timing summary
    echo "  Concurrency ${concurrency}, Run ${run_number}: ${run_duration} seconds" >> "$TIMING_LOG"

    return 0
}

# Function to run all 3 runs for a concurrency level and update CSV
run_concurrency_benchmark() {
    local concurrency=$1
    local concurrency_start_time=$(date +%s)
    local timestamp=$(date +%Y%m%d_%H%M%S)

    echo "Processing concurrency ${concurrency} with 3 runs..."

    # Run 3 benchmark runs for this concurrency
    for i in {1..3}; do
        run_single_concurrency_benchmark "$concurrency" "$i" "$timestamp"
        sleep 2  # Brief pause between runs
    done

    # Get best metrics from the 3 runs
    read best_e2e best_ttft best_itl best_output_throughput < <(get_best_metrics "$concurrency")

    if [ "$best_e2e" != "NA" ]; then
        # Find the best file to extract all metrics
        local best_file=""
        for f in "${folder}/sglang_serving_benchmark_concurrency_${concurrency}_run"*".log"; do
            [[ -f "$f" ]] || continue
            local e2e=$(grep -oP 'Median E2E Latency \(ms\):\s*\K[\d.]+' "$f" | head -n1)
            if [ "$e2e" = "$best_e2e" ]; then
                best_file="$f"
                break
            fi
        done

        if [ -n "$best_file" ]; then
            echo "✅ Best results for concurrency ${concurrency}: E2E=${best_e2e}ms, TTFT=${best_ttft}ms, ITL=${best_itl}ms, Output=${best_output_throughput} tok/s"
        fi

        # Update the structured CSV with this concurrency's results
        update_serving_csv_for_concurrency "$concurrency"
    else
        echo "❌ No valid results found for concurrency ${concurrency}"
    fi

    local concurrency_end_time=$(date +%s)
    local concurrency_duration=$((concurrency_end_time - concurrency_start_time))
    echo "Completed concurrency ${concurrency} - Total time: ${concurrency_duration} seconds" >> "$TIMING_LOG"
}

# Function to check if all required logs exist and are complete
check_all_logs_complete() {
    echo "Scanning existing logs to check if all benchmarks are already complete..."
    local all_complete=true
    local missing_runs=0
    local total_runs=0
    local gsm8k_complete=true

    # Check GSM8K logs if GSM8K is not skipped
    if [ "$SKIP_GSM8K" = "false" ]; then
        echo "Checking GSM8K log status..."
        if [ ! -f "$GSM8K_LOG_FILE" ]; then
            echo "Missing: GSM8K log file ($GSM8K_LOG_FILE)"
            gsm8k_complete=false
        elif [ ! -s "$GSM8K_LOG_FILE" ]; then
            echo "Empty: GSM8K log file ($GSM8K_LOG_FILE)"
            gsm8k_complete=false
        else
            # Check if GSM8K test completed successfully by looking for the final summary
            if grep -q "Average Accuracy over 5 runs:" "$GSM8K_LOG_FILE" && grep -q "Average accuracy meets threshold\|Average accuracy.*is below threshold" "$GSM8K_LOG_FILE"; then
                echo "✅ GSM8K log file is complete with final accuracy summary"
            else
                echo "Incomplete: GSM8K log file missing final accuracy summary ($GSM8K_LOG_FILE)"
                gsm8k_complete=false
            fi
        fi
    else
        echo "GSM8K benchmark is skipped - not checking GSM8K logs"
    fi

    # Check serving benchmark logs
    echo "Checking serving benchmark logs..."
    for concurrency in "${concurrency_values[@]}"; do
        for run_number in {1..3}; do
            total_runs=$((total_runs + 1))
            local log_found=false

            # Check if log already exists and completed successfully using glob pattern
            for log_file in "${folder}/sglang_serving_benchmark_concurrency_${concurrency}_run${run_number}"_*.log; do
                if [[ -f "$log_file" ]]; then
                    # Check if the run completed successfully by looking for completion marker
                    if grep -q "Run completed at:" "$log_file"; then
                        log_found=true
                        break
                    else
                        echo "Found incomplete log: $log_file"
                    fi
                fi
            done

            if [ "$log_found" = false ]; then
                echo "Missing: Concurrency ${concurrency}, Run ${run_number}"
                all_complete=false
                missing_runs=$((missing_runs + 1))
            fi
        done
    done

    echo "Scan complete: ${total_runs} total runs needed, ${missing_runs} missing/incomplete"

    # All benchmarks are complete only if both serving logs AND GSM8K logs (if required) are complete
    if [ "$all_complete" = true ] && [ "$gsm8k_complete" = true ]; then
        echo "✅ All benchmark logs (serving + GSM8K) are present and complete! No server startup needed."
        return 0
    else
        if [ "$all_complete" = false ]; then
            echo "❌ Missing ${missing_runs} serving benchmark runs."
        fi
        if [ "$gsm8k_complete" = false ] && [ "$SKIP_GSM8K" = "false" ]; then
            echo "❌ GSM8K benchmark is missing, empty, or incomplete."
        fi
        echo "Server startup required."
        return 1
    fi
}

# Check if all logs are already complete
if check_all_logs_complete; then
    echo "Skipping server startup and benchmark execution - generating CSV from existing logs..."
    echo "All logs already complete - skipping server startup" >> "$TIMING_LOG"

    # Initialize the structured CSV
    init_serving_csv

    # Extract metrics from existing logs and update CSV
    for concurrency in "${concurrency_values[@]}"; do
        echo "Extracting metrics for concurrency ${concurrency} from existing logs..."
        update_serving_csv_for_concurrency "$concurrency"
    done

    echo "✅ CSV generated from existing logs successfully."
    echo "Serving benchmark results written to ${SERVING_CSV}"
    if [ "$SKIP_GSM8K" = "false" ]; then
        echo "Note: Both GSM8K and serving benchmarks were already complete"
    else
        echo "Note: Only serving benchmarks processed (GSM8K was skipped)"
    fi

    serving_benchmark_start_time=$(date +%s)
    serving_benchmark_end_time=$(date +%s)
    serving_benchmark_duration=0

else
    # Not all logs complete - proceed with normal server startup and benchmarking
    echo "Clearing previous logs..."
    if [ "$SKIP_GSM8K" = "false" ]; then
        > "$GSM8K_LOG_FILE" # Clear/truncate the GSM8K log file
    fi
    > "$SERVER_LOG_FILE" # Clear/truncate the server log file

    if [ "$SKIP_GSM8K" = "true" ]; then
        echo "Starting SGLang server for serving benchmarks (GSM8K skipped)..."
    else
        echo "Starting SGLang server for online GSM8K benchmark..."
    fi

    # Determine which environment variable to use based on version
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

    echo "[DEBUG] Using environment variable: ${aiter_env_var}"

    # Start server in background using the online command format
    env ${aiter_env_var}=1 python3 -m sglang.launch_server \
        --model-path "${MODEL}" \
        --tp-size "${TP}" \
        --port "$GSM8K_PORT" \
        --trust-remote-code \
        --mem-fraction-static "$SERVER_MEM_FRACTION" \
        --max-running-requests "$SERVER_MAX_REQUESTS" > "$SERVER_LOG_FILE" 2>&1 &
    echo $! > "$SERVER_PID_FILE"
    SERVER_PID=$(cat "$SERVER_PID_FILE")

    echo "Waiting for SGLang server (PID: $SERVER_PID) to start... (Max 15 minutes)"
    echo "Server logs are being written to: $SERVER_LOG_FILE"

    # Wait for server to be ready - poll get_model_info endpoint
    server_startup_start=$(date +%s)
    if ! timeout "${SERVER_TIMEOUT}s" bash -c "until curl -s -f ${GSM8K_HOST}:${GSM8K_PORT}/get_model_info > /dev/null 2>&1; do echo -n '.'; sleep 5; done"; then
        echo "" # Newline after dots
        echo "SGLang server failed to start in time. Check $SERVER_LOG_FILE for details. Killing server (if any) and exiting."
        exit 1
    fi
    server_startup_end=$(date +%s)
    server_startup_duration=$((server_startup_end - server_startup_start))
    echo "" # Newline after dots
    echo "SGLang server started successfully after ${server_startup_duration} seconds."
    echo "Server startup time: ${server_startup_duration} seconds" >> "$TIMING_LOG"

    # Run GSM8K benchmark if not skipped
    if [ "$SKIP_GSM8K" = "false" ]; then
        # Initialize GSM8K CSV with structured format
        echo "GSM8K Accuracy Test - ${MODEL_NAME} (${LATEST_TAG})" > "$OUTPUT_CSV"
        echo "" >> "$OUTPUT_CSV"
        echo "Test Configuration" >> "$OUTPUT_CSV"
        echo "TP\t${TP}" >> "$OUTPUT_CSV"
        echo "Questions\t${GSM8K_NUM_QUESTIONS}" >> "$OUTPUT_CSV"
        echo "Parallel\t${GSM8K_PARALLEL}" >> "$OUTPUT_CSV"
        echo "Shots\t${GSM8K_NUM_SHOTS}" >> "$OUTPUT_CSV"
        echo "Runs\t${GSM8K_RUNS}" >> "$OUTPUT_CSV"
        echo "" >> "$OUTPUT_CSV"

        echo "=== Online GSM8K Benchmark: TP=${TP}, Questions=${GSM8K_NUM_QUESTIONS}, Parallel=${GSM8K_PARALLEL}, Shots=${GSM8K_NUM_SHOTS} ==="

        # Run the main GSM8K benchmark
        if run_gsm8k_benchmark; then
            echo "✅ GSM8K benchmark completed successfully."
            echo "Results written to ${OUTPUT_CSV}"
            echo "GSM8K log saved to ${GSM8K_LOG_FILE}"
            echo "Server log saved to ${SERVER_LOG_FILE}"
        else
            echo "❌ GSM8K benchmark failed or accuracy below threshold."
            exit 1
        fi
    else
        echo "GSM8K benchmark skipped as requested."
    fi

    echo "Starting serving benchmark tests with different concurrency levels..."
    serving_benchmark_start_time=$(date +%s)

    # Initialize the structured CSV
    init_serving_csv

    # Log to timing summary
    echo "" >> "$TIMING_LOG"
    echo "Serving Benchmark Results:" >> "$TIMING_LOG"
    echo "  Start time: $(date '+%Y-%m-%d %H:%M:%S %Z')" >> "$TIMING_LOG"

    # Run benchmark with different concurrency values (largest to smallest)
    for concurrency in "${concurrency_values[@]}"; do
        run_concurrency_benchmark "$concurrency"
    done
fi

serving_benchmark_end_time=$(date +%s)
serving_benchmark_duration=$((serving_benchmark_end_time - serving_benchmark_start_time))

echo "✅ Serving benchmark completed successfully in ${serving_benchmark_duration} seconds."
echo "Structured serving benchmark results written to ${SERVING_CSV}"
echo "Individual concurrency logs saved to ${folder}/sglang_serving_benchmark_concurrency_*_run*.log"

# Log to timing summary
echo "  End time: $(date '+%Y-%m-%d %H:%M:%S %Z')" >> "$TIMING_LOG"
echo "  Total duration: ${serving_benchmark_duration} seconds" >> "$TIMING_LOG"



# Add performance summary to timing log
echo "" >> "$TIMING_LOG"
echo "Performance Summary:" >> "$TIMING_LOG"
echo "===================" >> "$TIMING_LOG"
for conc in "${concurrency_values[@]}"; do
    echo "Concurrency ${conc}:" >> "$TIMING_LOG"
    echo "  E2E Latency: ${best_e2e_metrics[$conc]:-NA} ms" >> "$TIMING_LOG"
    echo "  TTFT: ${best_ttft_metrics[$conc]:-NA} ms" >> "$TIMING_LOG"
    echo "  ITL: ${best_itl_metrics[$conc]:-NA} ms" >> "$TIMING_LOG"
    echo "" >> "$TIMING_LOG"
done

if [ "$SKIP_GSM8K" = "false" ]; then
    echo "GSM8K accuracy results written to ${OUTPUT_CSV}"
fi

###############################################################################
# 7. Final Cleanup - Shutdown Server
###############################################################################
echo "Shutting down SGLang server..."
shutdown_start=$(date +%s)
# The cleanup trap will handle this, but we can also do it explicitly here
shutdown_end=$(date +%s)
shutdown_duration=$((shutdown_end - shutdown_start))
echo "Server shutdown completed in ${shutdown_duration} seconds"
echo "Server shutdown time: ${shutdown_duration} seconds" >> "$TIMING_LOG"

###############################################################################
# 8. Final Timing Summary
###############################################################################
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
echo "Serving CSV: ${SERVING_CSV}"
echo "Timing log: ${TIMING_LOG}"
echo "==========================================="
