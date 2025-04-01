#!/usr/bin/env bash
# ------------------------------------------------------------------------------
# perf_offline_csv.sh
#
# This script runs the offline benchmark command for TP=8 and multiple batch sizes.
#
# It:
#   1. Creates a folder named {date}_GROK1_CK-MOE-I4F8-AITER-DECODE-ATTN_offline.
#      A config.json file is written in that folder with the docker image name.
#   2. For each batch size, runs the bench_one_batch command and captures output.
#   3. Parses the following metrics from the final result block (after "Benchmark ..."):
#        - Prefill latency (s)
#        - Median decode latency (s)
#        - E2E latency (s)
#        - Prefill throughput (token/s)
#        - Median decode throughput (token/s)
#        - E2E throughput (token/s)
#   4. Appends a CSV row (with columns in order: TP, batch_size, IL, OL,
#      Prefill_latency(s), Median_decode_latency(s), E2E_Latency(s),
#      Prefill_Throughput(token/s), Median_Decode_Throughput(token/s), E2E_Throughput(token/s)).
#   5. After all runs, additional rows are appended which compute the ratio 
#      (H100 reference / measured * 100) for each metric.
#
# The config.json file will contain: {"docker": "rocm/sgl-dev:20250318rc"}
#
# ------------------------------------------------------------------------------
 
# Model and tokenizer paths
MODEL="/mnt/raid/models/huggingface/amd--grok-1-W4A8KV8/"
TOKENIZER="Xenova/grok-1-tokenizer"

# Input/Output lengths
ILEN=1024
OLEN=128

# Only use TP=8; offline benchmarks vary over batch sizes.
TP_VALUES=(8)
BATCH_SIZES=(1 2 4 8 16 32 64 128 256)

# Get current date for naming
current_date=$(date +%Y%m%d)
folder="${current_date}_GROK1_CK-MOE-I4F8-AITER-DECODE-ATTN_offline"
mkdir -p "$folder"
OUTPUT_CSV="${folder}/${current_date}_GROK1_CK-MOE-I4F8-AITER-DECODE-ATTN_offline.csv"

# Write config.json with docker image name
echo '{"docker": "rocm/sgl-dev:20250318rc"}' > "${folder}/config.json"
echo "Wrote config.json to ${folder}/config.json"

# Write CSV header with ordering:
# TP, batch_size, IL, OL, Prefill_latency(s), Median_decode_latency(s), E2E_Latency(s),
# Prefill_Throughput(token/s), Median_Decode_Throughput(token/s), E2E_Throughput(token/s)
echo "TP,batch_size,IL,OL,Prefill_latency(s),Median_decode_latency(s),E2E_Latency(s),Prefill_Throughput(token/s),Median_Decode_Throughput(token/s),E2E_Throughput(token/s)" > "$OUTPUT_CSV"

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
    
    # Isolate the section after "Benchmark ..." (assumes final block of output)
    last_section=$(echo "$out" | awk '/Benchmark/ {flag=1; next} flag')
    
    # Parse metrics:
    prefill_latency=$(echo "$last_section" | grep -oP 'Prefill\. latency:\s*\K[\d.]+' | tail -n 1)
    prefill_throughput=$(echo "$last_section" | grep -oP 'Prefill\. latency:.*throughput:\s*\K[\d.]+' | tail -n 1)
    
    decode_median_latency=$(echo "$last_section" | grep -oP 'Decode\.\s+median latency:\s*\K[\d.]+' | tail -n 1)
    decode_median_throughput=$(echo "$last_section" | grep -oP 'Decode\.\s+median latency:.*median throughput:\s*\K[\d.]+' | tail -n 1)
    
    total_latency=$(echo "$last_section" | grep -oP 'Total\. latency:\s*\K[\d.]+' | tail -n 1)
    e2e_throughput=$(echo "$last_section" | grep -oP 'Total\. latency:.*throughput:\s*\K[\d.]+' | tail -n 1)
    
    # Append CSV row:
    echo "${tp},${bs},${ILEN},${OLEN},${prefill_latency},${decode_median_latency},${total_latency},${prefill_throughput},${decode_median_throughput},${e2e_throughput}" >> "$OUTPUT_CSV"
    
    # If a result file (result.jsonl) is produced, rename it to a fixed name.
    if [ -f result.jsonl ]; then
      dest_json="${folder}/${current_date}_GROK1_CK-MOE-I4F8-AITER-DECODE-ATTN_offline.jsonl"
      mv result.jsonl "$dest_json"
      echo "Saved JSON result to ${dest_json}"
    fi
  done
done

# (Optional) Append additional ratio rows if desired.
# ... (ratio computation code can be added here if needed)

echo "All done! Results saved to ${OUTPUT_CSV} and JSON result stored as ${folder}/${current_date}_GROK1_CK-MOE-I4F8-AITER-DECODE-ATTN_offline.jsonl (if produced)."
