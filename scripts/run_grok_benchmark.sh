#!/usr/bin/env bash
# ------------------------------------------------------------------------------
# run_grok_benchmark.sh
#   Wrapper script to run GROK online performance benchmark with configurable parameters
#
# USAGE:
#   # Run with defaults
#   bash run_grok_benchmark.sh
#
#   # Override specific variables
#   MODEL_PATH="/path/to/your/model" bash run_grok_benchmark.sh
#   DOCKER_IMAGE="your/custom:image" bash run_grok_benchmark.sh
#   WORK_DIR="/your/work/dir" bash run_grok_benchmark.sh
#   TOKENIZER_PATH="/different/tokenizer" bash run_grok_benchmark.sh
#
#   # Override multiple variables
#   MODEL_PATH="/custom/model" WORK_DIR="/custom/work" bash run_grok_benchmark.sh
# ------------------------------------------------------------------------------

set -euo pipefail

# Configuration variables - can be overridden via environment variables
IMAGE_NAME="${DOCKER_IMAGE:-lmsysorg/sglang:v0.4.9.post2-rocm630-mi30x}"
MODEL_PATH="${MODEL_PATH:-/mnt/raid/models/huggingface/amd--grok-1-W4A8KV8}"
WORK_DIR="${WORK_DIR:-/mnt/raid/michael/sglang-ci}"
TOKENIZER_PATH="${TOKENIZER_PATH:-$MODEL_PATH}"  # Use model path as default for tokenizer

echo "Configuration:"
echo "  Docker Image: ${IMAGE_NAME}"
echo "  Model Path: ${MODEL_PATH}"
echo "  Tokenizer Path: ${TOKENIZER_PATH}"
echo "  Work Directory: ${WORK_DIR}"
echo ""

# Run the GROK performance benchmark
bash "$(dirname "$0")/grok_perf_online_csv.sh" \
    --docker_image="${IMAGE_NAME}" \
    --model="${MODEL_PATH}" \
    --tokenizer="${TOKENIZER_PATH}" \
    --work-dir="${WORK_DIR}"
