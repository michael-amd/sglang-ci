#!/usr/bin/env bash
# ------------------------------------------------------------------------------
# deepseek_perf_offline_csv.sh
#
# Offline-throughput benchmark for DeepSeek on TP=8 MI300x.
#
# USAGE:

#   bash deepseek_perf_offline_csv.sh --docker_image=sgl-dev:20250429
# ------------------------------------------------------------------------------
set -euo pipefail

###############################################################################
# 0. Parse CLI flag --docker_image=
###############################################################################
docker_image_default="rocm/sgl-dev:20250429" # fall-back
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
# 0-b. Normalise image name and extract tag
###############################################################################
if [[ "$docker_image" != */* ]]; then # if no / is present, assume it's a rocm image
  FULL_IMAGE="rocm/${docker_image}"
else
  FULL_IMAGE="$docker_image"
fi

IMAGE_WITH_TAG="${FULL_IMAGE##*/}" # e.g., sgl-dev:20250429
LATEST_TAG="${IMAGE_WITH_TAG#*:}"   # e.g., 20250429

## 1.  Model / tokenizer  -------------------------------------------------------
MODEL="deepseek-ai/DeepSeek-V3-0324"   # change to local path if mirrored
MODEL_NAME="DeepSeek-V3-0324"

## 2.  Work-load sizes ----------------------------------------------------------
ILEN=128        # input tokens
OLEN=32         # output tokens
TP=8            # tensor-parallel degree
BS=32           # batch size

## 3.  Run-folder bookkeeping ---------------------------------------------------
folder="offline/${MODEL_NAME}/${LATEST_TAG}_${MODEL_NAME}_FP8_offline"
mkdir -p "$folder"
OUTPUT_CSV="${folder}/${LATEST_TAG}_${MODEL_NAME}_FP8_offline.csv"


# CSV header (only write if file is empty)
if [ ! -s "$OUTPUT_CSV" ]; then
  echo "TP,batch_size,IL,OL,Prefill_latency(s),Median_decode_latency(s),E2E_Latency(s),Prefill_Throughput(token/s),Median_Decode_Throughput(token/s),E2E_Throughput(token/s)" > "$OUTPUT_CSV"
fi

## 4.  Single-run benchmark -----------------------------------------------------
echo "=== TP=${TP}, BS=${BS} ==="
out=$(
  RCCL_MSCCL_ENABLE=0 SGLANG_AITER_MOE=1 SGLANG_INT4_WEIGHT=0 SGLANG_MOE_PADDING=0 \
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
  mv result.jsonl "${folder}/${LATEST_TAG}_${MODEL_NAME}_FP8_offline.jsonl"
fi

echo "✅  Results written to ${OUTPUT_CSV}"
