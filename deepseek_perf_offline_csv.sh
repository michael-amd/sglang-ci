#!/usr/bin/env bash
# ------------------------------------------------------------------------------
# deepseek_perf_offline_csv.sh
#
# Offline-throughput benchmark for DeepSeek-V3-0324 on TP=8 MI300x.
# ------------------------------------------------------------------------------
set -euo pipefail

## 1.  Model / tokenizer  -------------------------------------------------------
MODEL="deepseek-ai/DeepSeek-V3-0324"   # change to local path if mirrored

## 2.  Work-load sizes ----------------------------------------------------------
ILEN=128        # input tokens
OLEN=32         # output tokens
TP=8            # tensor-parallel degree
BS=32           # batch size

## 3.  Run-folder bookkeeping ---------------------------------------------------
current_date=$(date +%Y%m%d)
folder="${current_date}_DEEPSEEKV3_FP8_offline"
mkdir -p "$folder"
OUTPUT_CSV="${folder}/${current_date}_DEEPSEEKV3_FP8_offline.csv"

echo '{"docker": "lmsysorg/sglang:v0.4.5.post3-rocm630"}' > "${folder}/config.json"

# CSV header (only write if file is empty)
if [ ! -s "$OUTPUT_CSV" ]; then
  echo "TP,batch_size,IL,OL,Prefill_latency(s),Median_decode_latency(s),E2E_Latency(s),Prefill_Throughput(token/s),Median_Decode_Throughput(token/s),E2E_Throughput(token/s)" > "$OUTPUT_CSV"
fi

## 4.  Single-run benchmark -----------------------------------------------------
echo "=== TP=${TP}, BS=${BS} ==="
out=$(
  RCCL_MSCCL_ENABLE=0 CK_MOE=1 USE_INT4_WEIGHT=0 MOE_PADDING=0 \
  python3 -m sglang.bench_one_batch \
    --model "${MODEL}" \
    --tp "${TP}" \
    --batch-size "${BS}" \
    --input "${ILEN}" \
    --output "${OLEN}" \
    --disable-radix-cache \
    --trust-remote-code 2>&1
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

echo "${TP},${BS},${ILEN},${OLEN},${prefill_lat},${decode_lat},${total_lat},${prefill_tp},${decode_tp},${total_tp}" >> "$OUTPUT_CSV"

# Save raw JSONL if bench_one_batch produced one
if [ -f result.jsonl ]; then
  mv result.jsonl "${folder}/${current_date}_DEEPSEEKV3_offline.jsonl"
fi

echo "✅  Results written to ${OUTPUT_CSV}"
