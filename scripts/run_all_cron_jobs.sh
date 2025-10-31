#!/bin/bash

# Set environment variables from crontab_rules.txt
export SGL_BENCHMARK_CI_DIR="/mnt/raid/michael/sglang-ci"
export GROK_MODEL_PATH="/mnt/raid/models/huggingface/amd--grok-1-W4A8KV8"
export GROK2_MODEL_PATH="/mnt/raid/models/huggingface/grok-2"
export GROK2_TOKENIZER_PATH="/mnt/raid/models/huggingface/alvarobartt--grok-2-tokenizer"
export DEEPSEEK_MODEL_PATH="/data2/models/amd-DeepSeek-R1-MXFP4-Preview"
export HARDWARE_TYPE="mi35x"
export TEAMS_WEBHOOK_URL="https://prod-99.westus.logic.azure.com/workflows/44b23afb37e74288b6b006c60bbe65b2/triggers/manual/paths/invoke?api-version=2016-06-01&sp=%2Ftriggers%2Fmanual%2Frun&sv=1.0&sig=REDACTED"
export GITHUB_REPO="${GITHUB_REPO:-ROCm/sglang-ci}"
export GITHUB_LOG="https://github.com/${GITHUB_REPO}/tree/log"
export GITHUB_TOKEN="GITHUB_TOKEN_REMOVED"

cd "$SGL_BENCHMARK_CI_DIR"

echo "=========================================="
echo "Starting all cron jobs at $(date)"
echo "=========================================="

# Job 1: 6 AM PT (8 AM CT) - DeepSeek online serving benchmark
echo ""
echo "[$(date)] Running Job 1: DeepSeek online serving benchmark"
mkdir -p cron/cron_log/$HARDWARE_TYPE/$(date +%Y%m%d)
bash perf_nightly.sh --model=deepseek --model-path="$DEEPSEEK_MODEL_PATH" --model-name="DeepSeek-R1-MXFP4-Preview" --mode=online --hardware="$HARDWARE_TYPE" --teams-webhook-url="$TEAMS_WEBHOOK_URL" > cron/cron_log/$HARDWARE_TYPE/$(date +%Y%m%d)/deepseek_nightly_online.log 2>&1
bash cron/github_log_upload.sh
echo "[$(date)] Job 1 completed"

# Job 2: 8 AM PT (10 AM CT) - Docker image availability check
echo ""
echo "[$(date)] Running Job 2: Docker image availability check"
mkdir -p cron/cron_log/$HARDWARE_TYPE/$(date +%Y%m%d)
TEAMS_WEBHOOK_URL="$TEAMS_WEBHOOK_URL" bash nightly_image_check.sh > cron/cron_log/$HARDWARE_TYPE/$(date +%Y%m%d)/docker_image_check.log 2>&1
bash cron/github_log_upload.sh
echo "[$(date)] Job 2 completed"

# Job 3: 8:05 AM PT (10:05 AM CT) - Unit test nightly run
echo ""
echo "[$(date)] Running Job 3: Unit test nightly run"
mkdir -p cron/cron_log/$HARDWARE_TYPE/$(date +%Y%m%d)
bash test_nightly.sh --hardware="$HARDWARE_TYPE" --teams-webhook-url="$TEAMS_WEBHOOK_URL" > cron/cron_log/$HARDWARE_TYPE/$(date +%Y%m%d)/test_nightly.log 2>&1
bash cron/github_log_upload.sh
echo "[$(date)] Job 3 completed"

# Job 4: 8:15 AM PT (10:15 AM CT) - Grok 2 online serving benchmark
echo ""
echo "[$(date)] Running Job 4: Grok 2 online serving benchmark"
mkdir -p cron/cron_log/$HARDWARE_TYPE/$(date +%Y%m%d)
bash perf_nightly.sh --model=grok2 --model-path="$GROK2_MODEL_PATH" --tokenizer-path="$GROK2_TOKENIZER_PATH" --mode=online --hardware="$HARDWARE_TYPE" --teams-webhook-url="$TEAMS_WEBHOOK_URL" > cron/cron_log/$HARDWARE_TYPE/$(date +%Y%m%d)/grok2_nightly_online.log 2>&1
bash cron/github_log_upload.sh
echo "[$(date)] Job 4 completed"

# Job 5: 10 AM PT (12 PM CT) - Grok online benchmark
echo ""
echo "[$(date)] Running Job 5: Grok online benchmark"
mkdir -p cron/cron_log/$HARDWARE_TYPE/$(date +%Y%m%d)
bash perf_nightly.sh --model=grok --model-path="$GROK_MODEL_PATH" --mode=online --hardware="$HARDWARE_TYPE" --teams-webhook-url="$TEAMS_WEBHOOK_URL" > cron/cron_log/$HARDWARE_TYPE/$(date +%Y%m%d)/grok_nightly.log 2>&1
bash cron/github_log_upload.sh
echo "[$(date)] Job 5 completed"

# Job 6: 12 PM PT (2 PM CT) - DeepSeek online with torch compile
echo ""
echo "[$(date)] Running Job 6: DeepSeek online with torch compile"
mkdir -p cron/cron_log/$HARDWARE_TYPE/$(date +%Y%m%d)
bash perf_nightly.sh --model=deepseek --model-path="$DEEPSEEK_MODEL_PATH" --model-name="DeepSeek-R1-MXFP4-Preview" --mode=online --hardware="$HARDWARE_TYPE" --enable-torch-compile --teams-webhook-url="$TEAMS_WEBHOOK_URL" > cron/cron_log/$HARDWARE_TYPE/$(date +%Y%m%d)/deepseek_torch_compile.log 2>&1
bash cron/github_log_upload.sh
echo "[$(date)] Job 6 completed"

# Job 7: 12:30 PM PT (2:30 PM CT) - DeepSeek online with DP attention + torch compile
echo ""
echo "[$(date)] Running Job 7: DeepSeek online with DP attention + torch compile"
mkdir -p cron/cron_log/$HARDWARE_TYPE/$(date +%Y%m%d)
bash perf_nightly.sh --model=deepseek --model-path="$DEEPSEEK_MODEL_PATH" --model-name="DeepSeek-R1-MXFP4-Preview" --mode=online --hardware="$HARDWARE_TYPE" --check-dp-attention --enable-torch-compile --teams-webhook-url="$TEAMS_WEBHOOK_URL" > cron/cron_log/$HARDWARE_TYPE/$(date +%Y%m%d)/deepseek_dp_attention_torch_compile.log 2>&1
bash cron/github_log_upload.sh
echo "[$(date)] Job 7 completed"

# Job 8: 1 PM PT (3 PM CT) - Sanity check with latest nightly image
echo ""
echo "[$(date)] Running Job 8: Sanity check with latest nightly image"
mkdir -p cron/cron_log/$HARDWARE_TYPE/$(date +%Y%m%d)
bash perf_nightly.sh --mode=sanity --hardware="$HARDWARE_TYPE" --sanity-trials=3 > cron/cron_log/$HARDWARE_TYPE/$(date +%Y%m%d)/sanity_check_nightly.log 2>&1
bash cron/github_log_upload.sh
echo "[$(date)] Job 8 completed"

# Job 9: 2 PM PT (4 PM CT) - DeepSeek-R1-MXFP4-Preview MTP test
echo ""
echo "[$(date)] Running Job 9: DeepSeek-R1-MXFP4-Preview MTP test"
mkdir -p cron/cron_log/$HARDWARE_TYPE/$(date +%Y%m%d)
bash perf_nightly.sh --model=deepseek --model-path="$DEEPSEEK_MODEL_PATH" --model-name="DeepSeek-R1-MXFP4-Preview" --mode=online --hardware="$HARDWARE_TYPE" --enable-mtp-test --teams-webhook-url="$TEAMS_WEBHOOK_URL" > cron/cron_log/$HARDWARE_TYPE/$(date +%Y%m%d)/deepseek_r1_mxfp4_mtp.log 2>&1
bash cron/github_log_upload.sh
echo "[$(date)] Job 9 completed"

# Job 10: 2:50 PM PT (4:50 PM CT) - DeepSeek-R1-MXFP4-Preview DP test
echo ""
echo "[$(date)] Running Job 10: DeepSeek-R1-MXFP4-Preview DP test"
mkdir -p cron/cron_log/$HARDWARE_TYPE/$(date +%Y%m%d)
bash perf_nightly.sh --model=deepseek --model-path="$DEEPSEEK_MODEL_PATH" --model-name="DeepSeek-R1-MXFP4-Preview" --mode=online --hardware="$HARDWARE_TYPE" --enable-dp-test --teams-webhook-url="$TEAMS_WEBHOOK_URL" > cron/cron_log/$HARDWARE_TYPE/$(date +%Y%m%d)/deepseek_r1_mxfp4_dp_test.log 2>&1
bash cron/github_log_upload.sh
echo "[$(date)] Job 10 completed"

# Job 11: 4 PM PT (6 PM CT) - DeepSeek-R1-MXFP4-Preview DP test with MTP
echo ""
echo "[$(date)] Running Job 11: DeepSeek-R1-MXFP4-Preview DP test with MTP"
mkdir -p cron/cron_log/$HARDWARE_TYPE/$(date +%Y%m%d)
bash perf_nightly.sh --model=deepseek --model-path="$DEEPSEEK_MODEL_PATH" --model-name="DeepSeek-R1-MXFP4-Preview" --mode=online --hardware="$HARDWARE_TYPE" --enable-dp-test --enable-mtp-test --teams-webhook-url="$TEAMS_WEBHOOK_URL" > cron/cron_log/$HARDWARE_TYPE/$(date +%Y%m%d)/deepseek_r1_mxfp4_dp_mtp.log 2>&1
bash cron/github_log_upload.sh
echo "[$(date)] Job 11 completed"

echo ""
echo "=========================================="
echo "All cron jobs completed at $(date)"
echo "=========================================="


