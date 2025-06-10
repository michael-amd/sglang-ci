#!/usr/bin/env bash
# ------------------------------------------------------------------------------
# grok_perf_offline_csv_long_context.sh
#
# This script runs the offline benchmark command for long context inputs
# (8K, 16K, 32K) using the FP8 grok model configuration with INT4 weight support.
#
# It:
#   1. Sets the model path to: /mnt/raid/models/huggingface/amd--grok-1-W4A8KV8
#   2. Defines a docker_image variable with a default value: rocm/sgl-dev:20250331rc.
#   3. Runs the benchmark for each input length (8K, 16K, 32K) with TP=8 and batch size=1.
#   4. Parses metrics from the final result block (after "Benchmark ..."):
#        - Prefill latency (s)
#        - Median decode latency (s)
#        - E2E latency (s)
#        - Prefill throughput (token/s)
#        - Median decode throughput (token/s)
#        - E2E throughput (token/s)
#   5. Appends a CSV row with columns:
#      TP, batch_size, IL, OL, Prefill_latency(s), Median_decode_latency(s), E2E_Latency(s),
#      Prefill_Throughput(token/s), Median_Decode_Throughput(token/s), E2E_Throughput(token/s)
#   6. If produced, the result.jsonl file is renamed and moved to the output folder.
#
# The config.json file will contain: {"docker": "rocm/sgl-dev:20250331rc"}
#
# ------------------------------------------------------------------------------
 
# Model and tokenizer paths
MODEL="/mnt/raid/models/huggingface/amd--grok-1-W4A8KV8"
TOKENIZER="Xenova/grok-1-tokenizer"

# Docker image variable with default value.
docker_image="rocm/sgl-dev:20250331rc"

# Input/Output lengths: testing long-context inputs.
INPUT_LENGTHS=(8192 16384 32768)
OLEN=10

# Use TP=8 and a fixed batch size of 1 for long context experiments.
TP_VALUES=(8)
BATCH_SIZES=(1)

# Get current date for folder naming.
current_date=$(date +%Y%m%d)
folder="${current_date}_GROK1_FP8_LONGCONTEXT_offline"
mkdir -p "$folder"
OUTPUT_CSV="${folder}/${current_date}_GROK1_FP8_LONGCONTEXT_offline.csv"

# Write config.json with docker image name from variable.
echo "{\"docker\": \"${docker_image}\"}" > "${folder}/config.json"
echo "Wrote config.json to ${folder}/config.json"

# Write the CSV header with the following ordering:
# TP,batch_size,IL,OL,Prefill_latency(s),Median_decode_latency(s),E2E_Latency(s),
# Prefill_Throughput(token/s),Median_Decode_Throughput(token/s),E2E_Throughput(token/s)
echo "TP,batch_size,IL,OL,Prefill_latency(s),Median_decode_latency(s),E2E_Latency(s),Prefill_Throughput(token/s),Median_Decode_Throughput(token/s),E2E_Throughput(token/s)" > "$OUTPUT_CSV"

# Loop over TP, batch sizes, and input lengths.
for tp in "${TP_VALUES[@]}"; do
  for bs in "${BATCH_SIZES[@]}"; do
    for ilen in "${INPUT_LENGTHS[@]}"; do
      echo "Running TP=${tp}, batch_size=${bs}, input_length=${ilen} ..."
      
      # Run the benchmark command and capture output.
      out=$(
        RCCL_MSCCL_ENABLE=0 SGLANG_AITER_MOE=1 SGLANG_MOE_PADDING=0 SGLANG_INT4_WEIGHT=1 \
        python3 -m sglang.bench_one_batch \
          --model "${MODEL}" \
          --tokenizer-path "${TOKENIZER}" \
          --tp "${tp}" \
          --batch-size "${bs}" \
          --input "${ilen}" \
          --output "${OLEN}" \
          --attention-backend aiter \
          --quantization fp8 \
          --trust-remote-code 2>&1
      )
      
      # Isolate the section after "Benchmark ..." (assumes final block of output)
      last_section=$(echo "$out" | awk '/Benchmark/ {flag=1; next} flag')
      
      # Parse metrics from the output.
      prefill_latency=$(echo "$last_section" | grep -oP 'Prefill\. latency:\s*\K[\d.]+' | tail -n 1)
      prefill_throughput=$(echo "$last_section" | grep -oP 'Prefill\. latency:.*throughput:\s*\K[\d.]+' | tail -n 1)
      
      decode_median_latency=$(echo "$last_section" | grep -oP 'Decode\.\s+median latency:\s*\K[\d.]+' | tail -n 1)
      decode_median_throughput=$(echo "$last_section" | grep -oP 'Decode\.\s+median latency:.*median throughput:\s*\K[\d.]+' | tail -n 1)
      
      total_latency=$(echo "$last_section" | grep -oP 'Total\. latency:\s*\K[\d.]+' | tail -n 1)
      e2e_throughput=$(echo "$last_section" | grep -oP 'Total\. latency:.*throughput:\s*\K[\d.]+' | tail -n 1)
      
      # Append the parsed results as a CSV row.
      echo "${tp},${bs},${ilen},${OLEN},${prefill_latency},${decode_median_latency},${total_latency},${prefill_throughput},${decode_median_throughput},${e2e_throughput}" >> "$OUTPUT_CSV"
      
      # If a result file (result.jsonl) is produced, rename and move it.
      if [ -f result.jsonl ]; then
        dest_json="${folder}/${current_date}_GROK1_FP8_LONGCONTEXT_offline.jsonl"
        mv result.jsonl "$dest_json"
        echo "Saved JSON result to ${dest_json}"
      fi
    done
  done
done

# (Optional) Append additional ratio rows if desired.
# ... (ratio computation code can be added here if needed)

echo "All done! Results saved to ${OUTPUT_CSV} and (if produced) the JSON result is stored as ${folder}/${current_date}_GROK1_FP8_LONGCONTEXT_offline.jsonl."
