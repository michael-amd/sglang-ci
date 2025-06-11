#!/usr/bin/env bash
# ------------------------------------------------------------------------------
# deepseek_perf_offline_csv.sh
#
# Offline-throughput benchmark for DeepSeek on TP=8 MI300x.
#
# USAGE:
#   bash deepseek_perf_offline_csv.sh --docker_image=rocm/sgl-dev:20250430
#   bash deepseek_perf_offline_csv.sh --docker_image=lmsysorg/sglang:v0.4.6.post3-rocm630
#   bash deepseek_perf_offline_csv.sh --docker_image=lmsysorg/sglang:v0.4.7-rocm630
# ------------------------------------------------------------------------------
set -euo pipefail

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
# 0. Parse CLI flag --docker_image=
###############################################################################
docker_image_default="rocm/sgl-dev:20250430" # fall-back
docker_image=""

for arg in "$@"; do
  case $arg in
    --docker_image=*|--docker-image=*) # Handle both --docker_image and --docker-image
      docker_image="${arg#*=}"
      shift # Remove parsed argument
      ;;
  esac
done

# If not provided by flag, use positional argument or default
docker_image="${docker_image:-${1:-$docker_image_default}}"

###############################################################################
# 0-b. Use the full image name as provided (no auto-prefixing)
###############################################################################
FULL_IMAGE="$docker_image"

IMAGE_WITH_TAG="${FULL_IMAGE##*/}" # e.g., sgl-dev:20250429
LATEST_TAG="${IMAGE_WITH_TAG#*:}"   # e.g., 20250429

# ---------------------------
# 0-c. Container Management (if applicable)
# ---------------------------
if [ -z "${INSIDE_CONTAINER:-}" ]; then
  if ! command -v docker >/dev/null 2>&1; then
    echo "[csv] Docker not found — already inside container."
    INSIDE_CONTAINER=1
  else
    IMAGE_WITH_TAG_FOR_CONTAINER_NAME="${FULL_IMAGE##*/}"      # sgl-dev:20250429
    REPO="${IMAGE_WITH_TAG_FOR_CONTAINER_NAME%%:*}"            # sgl-dev
    TAG_FOR_CONTAINER_NAME="${IMAGE_WITH_TAG_FOR_CONTAINER_NAME#*:}"       # 20250429
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
      echo "[csv] Pulling image and creating container ..."
      docker pull "${FULL_IMAGE}"
      docker run -d --name "${CONTAINER_NAME}" \
        --shm-size 32g --ipc=host --cap-add=SYS_PTRACE --network=host \
        --device=/dev/kfd --device=/dev/dri --security-opt seccomp=unconfined \
        -v /mnt/raid/:/mnt/raid/ --group-add video --privileged \
        -w /sgl-workspace "${FULL_IMAGE}" tail -f /dev/null
    fi

    echo "[csv] Re-invoking inside ${CONTAINER_NAME} ..."
    docker exec \
      -e INSIDE_CONTAINER=1 \
      -e LATEST_TAG="${LATEST_TAG}" \
      "${CONTAINER_NAME}" \
      bash /mnt/raid/michael/sgl_benchmark_ci/deepseek_perf_offline_csv.sh \
           --docker_image="${FULL_IMAGE}"
    exit 0
  fi
fi

# ---------------------------
# 1. Inside Container: Setup Run Folder
# ---------------------------
cd /mnt/raid/michael/sgl_benchmark_ci/ || { echo "Cannot change to /mnt/raid/michael/sgl_benchmark_ci/ directory"; exit 1; }

# If LATEST_TAG is not already defined (e.g. when script is re-invoked inside container), extract it.
if [ -z "$LATEST_TAG" ]; then
    IMAGE_WITH_TAG_FROM_ARG=${docker_image#*/}
    LATEST_TAG=${IMAGE_WITH_TAG_FROM_ARG#*:}
fi

## 1.  Model / tokenizer  -------------------------------------------------------
# t10-23
MODEL="/mnt/raid/models/huggingface/deepseek-ai/DeepSeek-V3-0324"   # change to local path if mirrored
MODEL_NAME="DeepSeek-V3-0324" # Used for folder naming, etc.
HF_MODEL_ID="deepseek-ai/DeepSeek-V3-0324" # Actual Hugging Face model ID for download

# t12-38
# MODEL="/mnt/raid/models/DeepSeek-V3"   # change to local path if mirrored
# MODEL_NAME="DeepSeek-V3" # Used for folder naming, etc.
# HF_MODEL_ID="deepseek-ai/DeepSeek-V3" # Actual Hugging Face model ID for download

# ---- Download model if not present (inside container) ----
if [ -n "${INSIDE_CONTAINER}" ]; then # Only run download logic if inside the container
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
else
  echo "[csv] Skipping model download check as we are not inside the container yet."
fi
# ---- End model download ----

## 2.  Work-load sizes ----------------------------------------------------------
ILEN=128        # input tokens
OLEN=32         # output tokens
TP=8            # tensor-parallel degree
BS=32           # batch size

###############################################################################
# 3. Define GSM8K Accuracy Threshold
###############################################################################
THRESHOLD=0.93 # Define the accuracy threshold for GSM8K. Adjust as needed.

## 3.  Run-folder bookkeeping ---------------------------------------------------
folder="offline/${MODEL_NAME}/${LATEST_TAG}_${MODEL_NAME}_FP8_offline"
mkdir -p "$folder"
OUTPUT_CSV="${folder}/${LATEST_TAG}_${MODEL_NAME}_FP8_offline.csv"
LOG_FILE="${folder}/tp${TP}_bs${BS}.log" # Define log file path
GSM8K_LOG_FILE="${folder}/sglang_client_log_${MODEL_NAME}_gsm8k.log" # Define GSM8K log path
WARMUP_SERVER_LOG_FILE="${folder}/sglang_warmup_server.log" # Define warm-up server log path

###############################################################################
# 4. GSM8K accuracy warm-up
# This function runs the GSM8K test multiple times, computes the average accuracy,
# and returns 0 if the average meets the threshold (THRESHOLD), or 1 otherwise.
# It now accepts a mode parameter ("aiter" or "decode") to split the log file accordingly.
run_client_gsm8k() {
    local mode="$1" # Assign the first argument to the mode variable
    # The gsm8k_log variable is now defined globally as GSM8K_LOG_FILE
    # local gsm8k_log="${folder}/sglang_client_log_${MODEL_NAME}_gsm8k.log" # This was local
    
    local total_accuracy=0
    local runs=5
    local count=0
    local run_accuracy=0
    local output
    
    # Run the test 'runs' times
    for i in $(seq 1 $runs); do
         echo "Executing GSM8K test Run $i ..." | tee -a "$GSM8K_LOG_FILE"
         output=$(python3 /mnt/raid/michael/sglang/benchmark/gsm8k/bench_sglang.py --num-questions 2000 --parallel 2000 --num-shots 5 --port 30000 --host http://127.0.0.1 2>&1)
         echo "$output" | tee -a "$GSM8K_LOG_FILE"
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
    echo "Average Accuracy over $runs runs for mode ${mode}: $avg_accuracy" | tee -a "$GSM8K_LOG_FILE"
    if awk "BEGIN {exit !($avg_accuracy >= $THRESHOLD)}"; then
         echo "Average accuracy meets threshold ($THRESHOLD)." | tee -a "$GSM8K_LOG_FILE"
         return 0
    else
         echo "Average accuracy ($avg_accuracy) is below threshold ($THRESHOLD)." | tee -a "$GSM8K_LOG_FILE"
         return 1
    fi
}

###############################################################################
# Call GSM8K warm-up before the main benchmark
###############################################################################
echo "Clearing previous GSM8K log..."
> "$GSM8K_LOG_FILE" # Clear/truncate the log file

echo "Starting SGLang server for GSM8K warm-up... (TorchInductor autotuning disabled)"
# Start server in background. Use trust-remote-code like bench_one_batch.
# Use --log-level warning to reduce noise, adjust if more verbosity is needed.
# Disable Triton autotuning for TorchInductor for faster startup during warm-up.
TORCHINDUCTOR_AUTOTUNE_ENABLE=0 python3 -m sglang.launch_server \
    --model-path "${MODEL}" \
    --tp-size "${TP}" \
    --port 30000 \
    --trust-remote-code \
    --mem-fraction-static 0.7 \
    --max-running-requests 1024 > "$WARMUP_SERVER_LOG_FILE" 2>&1 &
echo $! > "$SERVER_PID_FILE"
SERVER_PID=$(cat "$SERVER_PID_FILE")

echo "Waiting for SGLang server (PID: $SERVER_PID) to start... (Max 15 minutes)"
echo "Server logs are being written to: $WARMUP_SERVER_LOG_FILE"
# Wait for server to be ready - poll get_model_info endpoint
# Timeout after 900 seconds (15 minutes)
if ! timeout 900s bash -c 'until curl -s -f http://127.0.0.1:30000/get_model_info > /dev/null 2>&1; do echo -n "."; sleep 5; done'; then
    echo "" # Newline after dots
    echo "SGLang server failed to start in time. Check $WARMUP_SERVER_LOG_FILE for details. Killing server (if any) and exiting."
    # Cleanup trap will handle killing the server
    exit 1
fi
echo "" # Newline after dots
echo "SGLang server started successfully."

echo "Starting GSM8K warm-up..."
if run_client_gsm8k "aiter"; then
    echo "GSM8K warm-up successful."
else
    echo "GSM8K warm-up failed or accuracy below threshold. Exiting."
    # Cleanup trap will handle killing the server
    exit 1
fi
# End GSM8K warm-up call

# Ensure warm-up server is stopped before main benchmark
# The trap will handle this on exit, but explicit kill here is also fine if script doesn't exit above.
if [ -f "$SERVER_PID_FILE" ] && [ -s "$SERVER_PID_FILE" ]; then
    BG_PID_TO_KILL=$(cat "$SERVER_PID_FILE")
    if ps -p "$BG_PID_TO_KILL" > /dev/null 2>&1; then # Check if PID exists
        echo "Stopping SGLang warm-up server (PID: $BG_PID_TO_KILL)..."
        kill "$BG_PID_TO_KILL"
        wait "$BG_PID_TO_KILL" 2>/dev/null || echo "Warm-up server PID $BG_PID_TO_KILL already stopped or not found."
    else
        echo "Warm-up server (PID: $BG_PID_TO_KILL) already stopped."
    fi
    > "$SERVER_PID_FILE" # Clear PID file as server is stopped
fi


# CSV header (only write if file is empty)
if [ ! -s "$OUTPUT_CSV" ]; then
  echo "TP,batch_size,IL,OL,Prefill_latency(s),Median_decode_latency(s),E2E_Latency(s),Prefill_Throughput(token/s),Median_Decode_Throughput(token/s),E2E_Throughput(token/s)" > "$OUTPUT_CSV"
fi

## 4.  Single-run benchmark -----------------------------------------------------
echo "=== TP=${TP}, BS=${BS} ==="
out=$(
  RCCL_MSCCL_ENABLE=0 SGLANG_USE_AITER=1 SGLANG_INT4_WEIGHT=0 SGLANG_MOE_PADDING=0 \
  python3 -m sglang.bench_one_batch \
    --model "${MODEL}" \
    --tp "${TP}" \
    --batch-size "${BS}" \
    --input "${ILEN}" \
    --output "${OLEN}" \
    --disable-radix-cache \
    --trust-remote-code 2>&1 | tee "${LOG_FILE}"
)

## 5.  Parse metrics and append CSV --------------------------------------------
# Isolate the section after the literal "Benchmark ..."
last_section=$(printf '%s\n' "$out" | awk '/^\s*Benchmark[. ]/{flag=1;next} flag')

if [[ -z "$last_section" ]]; then
  echo "ERROR: Benchmark block not found in output"
  exit 1
fi

prefill_lat=$(echo "$last_section" | grep -oP 'Prefill\.\s+latency:\s*\K[\d.]+'             | tail -1)
prefill_tp=$( echo "$last_section" | grep -oP 'Prefill\.\s+latency:.*throughput:\s*\K[\d.]+' | tail -1)
decode_lat=$( echo "$last_section" | grep -oP 'Decode\.\s+median latency:\s*\K[\d.]+'        | tail -1)
decode_tp=$(  echo "$last_section" | grep -oP 'Decode\.\s+median latency:.*median throughput:\s*\K[\d.]+' | tail -1)
total_lat=$(  echo "$last_section" | grep -oP 'Total\.\s+latency:\s*\K[\d.]+'               | tail -1)
total_tp=$(   echo "$last_section" | grep -oP 'Total\.\s+latency:.*throughput:\s*\K[\d.]+'   | tail -1)

# Check if metrics were successfully parsed
if [[ -z "$prefill_lat" || -z "$decode_lat" || -z "$total_lat" ]]; then
  echo "ERROR: Failed to parse one or more metrics from the benchmark output."
  echo "Output was:"
  echo "$out"
  echo "Last section parsed was:"
  echo "$last_section"
  echo "Please check the log file: ${LOG_FILE}"
  exit 1
fi

echo "${TP},${BS},${ILEN},${OLEN},${prefill_lat},${decode_lat},${total_lat},${prefill_tp},${decode_tp},${total_tp}" >> "$OUTPUT_CSV"

# Save raw JSONL if bench_one_batch produced one
if [ -f result.jsonl ]; then
  mv result.jsonl "${folder}/${LATEST_TAG}_${MODEL_NAME}_FP8_offline.jsonl"
fi

echo "✅  Results written to ${OUTPUT_CSV}"
echo "Full log saved to ${LOG_FILE}"
