#!/bin/bash

# Re-run failed tasks from 20251030
# This script re-runs only the failed tasks and saves logs to 20251030 directory

# Set environment variables from crontab_rules.txt
export SGL_BENCHMARK_CI_DIR="/mnt/raid/michael/sglang-ci"
export GROK_MODEL_PATH="/data/amd--grok-1-W4A8KV8"
export GROK2_MODEL_PATH="/mnt/raid/models/huggingface/grok-2"
export GROK2_TOKENIZER_PATH="/mnt/raid/models/huggingface/alvarobartt--grok-2-tokenizer"
export DEEPSEEK_MODEL_PATH="/mnt/raid/models/deepseek-ai/amd-DeepSeek-R1-MXFP4-Preview"
export HARDWARE_TYPE="mi35x"
export TEAMS_WEBHOOK_URL="https://prod-99.westus.logic.azure.com/workflows/44b23afb37e74288b6b006c60bbe65b2/triggers/manual/paths/invoke?api-version=2016-06-01&sp=%2Ftriggers%2Fmanual%2Frun&sv=1.0&sig=REDACTED"
export GITHUB_REPO="${GITHUB_REPO:-ROCm/sglang-ci}"

# Fixed date for re-running failed 20251030 tasks
LOG_DATE="20251030"

cd "$SGL_BENCHMARK_CI_DIR"

echo "=========================================="
echo "Re-running FAILED tasks from $LOG_DATE at $(date)"
echo "Logs will be saved to: cron/cron_log/$HARDWARE_TYPE/$LOG_DATE/"
echo "=========================================="
echo ""
echo "Failed tasks to re-run:"
echo "  1. sanity_check_nightly.log (Python error - now fixed)"
echo "  2. test_nightly_pd.log (PD test failures)"
echo "  3. deepseek_torch_compile.log (No accelerator error)"
echo "  4. deepseek_dp_attention_torch_compile.log (No accelerator error)"
echo "  5. deepseek_r1_mxfp4_mtp.log (No accelerator error)"
echo "  6. deepseek_r1_mxfp4_dp_test.log (No accelerator error)"
echo "  7. deepseek_r1_mxfp4_dp_mtp.log (No accelerator error)"
echo "  8. grok2_nightly_online.log (No accelerator error)"
echo "  9. grok_nightly.log (No accelerator error)"
echo " 10. deepseek_nightly_online.log (No accelerator error)"
echo ""
echo "=========================================="

# Job 1: Sanity check (was failing due to subprocess.run bug - now fixed)
echo ""
echo "[$(date)] Running Job 1: Sanity check with latest nightly image"
mkdir -p cron/cron_log/$HARDWARE_TYPE/$LOG_DATE
bash scripts/perf_nightly.sh --mode=sanity --hardware="$HARDWARE_TYPE" --sanity-trials=3 --teams-webhook-url="$TEAMS_WEBHOOK_URL" > cron/cron_log/$HARDWARE_TYPE/$LOG_DATE/sanity_check_nightly.log 2>&1
bash cron/github_log_upload.sh $LOG_DATE $HARDWARE_TYPE cron
bash cron/github_log_upload.sh "" $HARDWARE_TYPE sanity $(ls -t test/sanity_check_log/$HARDWARE_TYPE/ | head -1) 2>/dev/null || true
echo "[$(date)] Job 1 completed - check log for status"

# Job 2: PD disaggregation test
echo ""
echo "[$(date)] Running Job 2: PD disaggregation test nightly run"
mkdir -p cron/cron_log/$HARDWARE_TYPE/$LOG_DATE
bash scripts/test_nightly.sh --model-path="$DEEPSEEK_MODEL_PATH" --test-type=pd --hardware="$HARDWARE_TYPE" --teams-webhook-url="$TEAMS_WEBHOOK_URL" > cron/cron_log/$HARDWARE_TYPE/$LOG_DATE/test_nightly_pd.log 2>&1
bash cron/github_log_upload.sh
echo "[$(date)] Job 2 completed - check log for status"

# Job 3: DeepSeek online with torch compile
echo ""
echo "[$(date)] Running Job 3: DeepSeek online with torch compile"
mkdir -p cron/cron_log/$HARDWARE_TYPE/$LOG_DATE
bash scripts/perf_nightly.sh --model=deepseek --model-path="$DEEPSEEK_MODEL_PATH" --model-name="DeepSeek-R1-MXFP4-Preview" --mode=online --hardware="$HARDWARE_TYPE" --enable-torch-compile --teams-webhook-url="$TEAMS_WEBHOOK_URL" > cron/cron_log/$HARDWARE_TYPE/$LOG_DATE/deepseek_torch_compile.log 2>&1
bash cron/github_log_upload.sh
echo "[$(date)] Job 3 completed - check log for status"

# Job 4: DeepSeek online with DP attention + torch compile
echo ""
echo "[$(date)] Running Job 4: DeepSeek online with DP attention + torch compile"
mkdir -p cron/cron_log/$HARDWARE_TYPE/$LOG_DATE
bash scripts/perf_nightly.sh --model=deepseek --model-path="$DEEPSEEK_MODEL_PATH" --model-name="DeepSeek-R1-MXFP4-Preview" --mode=online --hardware="$HARDWARE_TYPE" --check-dp-attention --enable-torch-compile --teams-webhook-url="$TEAMS_WEBHOOK_URL" > cron/cron_log/$HARDWARE_TYPE/$LOG_DATE/deepseek_dp_attention_torch_compile.log 2>&1
bash cron/github_log_upload.sh
echo "[$(date)] Job 4 completed - check log for status"

# Job 5: DeepSeek-R1-MXFP4-Preview MTP test
echo ""
echo "[$(date)] Running Job 5: DeepSeek-R1-MXFP4-Preview MTP test"
mkdir -p cron/cron_log/$HARDWARE_TYPE/$LOG_DATE
bash scripts/perf_nightly.sh --model=deepseek --model-path="$DEEPSEEK_MODEL_PATH" --model-name="DeepSeek-R1-MXFP4-Preview" --mode=online --hardware="$HARDWARE_TYPE" --enable-mtp-test --teams-webhook-url="$TEAMS_WEBHOOK_URL" > cron/cron_log/$HARDWARE_TYPE/$LOG_DATE/deepseek_r1_mxfp4_mtp.log 2>&1
bash cron/github_log_upload.sh
echo "[$(date)] Job 5 completed - check log for status"

# Job 6: DeepSeek-R1-MXFP4-Preview DP test
echo ""
echo "[$(date)] Running Job 6: DeepSeek-R1-MXFP4-Preview DP test"
mkdir -p cron/cron_log/$HARDWARE_TYPE/$LOG_DATE
bash scripts/perf_nightly.sh --model=deepseek --model-path="$DEEPSEEK_MODEL_PATH" --model-name="DeepSeek-R1-MXFP4-Preview" --mode=online --hardware="$HARDWARE_TYPE" --enable-dp-test --teams-webhook-url="$TEAMS_WEBHOOK_URL" > cron/cron_log/$HARDWARE_TYPE/$LOG_DATE/deepseek_r1_mxfp4_dp_test.log 2>&1
bash cron/github_log_upload.sh
echo "[$(date)] Job 6 completed - check log for status"

# Job 7: DeepSeek-R1-MXFP4-Preview DP test with MTP
echo ""
echo "[$(date)] Running Job 7: DeepSeek-R1-MXFP4-Preview DP test with MTP"
mkdir -p cron/cron_log/$HARDWARE_TYPE/$LOG_DATE
bash scripts/perf_nightly.sh --model=deepseek --model-path="$DEEPSEEK_MODEL_PATH" --model-name="DeepSeek-R1-MXFP4-Preview" --mode=online --hardware="$HARDWARE_TYPE" --enable-dp-test --enable-mtp-test --teams-webhook-url="$TEAMS_WEBHOOK_URL" > cron/cron_log/$HARDWARE_TYPE/$LOG_DATE/deepseek_r1_mxfp4_dp_mtp.log 2>&1
bash cron/github_log_upload.sh
echo "[$(date)] Job 7 completed - check log for status"

# Job 8: Grok 2 online serving benchmark
echo ""
echo "[$(date)] Running Job 8: Grok 2 online serving benchmark"
mkdir -p cron/cron_log/$HARDWARE_TYPE/$LOG_DATE
bash scripts/perf_nightly.sh --model=grok2 --model-path="$GROK2_MODEL_PATH" --tokenizer-path="$GROK2_TOKENIZER_PATH" --mode=online --hardware="$HARDWARE_TYPE" --teams-webhook-url="$TEAMS_WEBHOOK_URL" > cron/cron_log/$HARDWARE_TYPE/$LOG_DATE/grok2_nightly_online.log 2>&1
bash cron/github_log_upload.sh
echo "[$(date)] Job 8 completed - check log for status"

# Job 9: Grok online benchmark
echo ""
echo "[$(date)] Running Job 9: Grok online benchmark with gating logic"
mkdir -p cron/cron_log/$HARDWARE_TYPE/$LOG_DATE
bash scripts/perf_nightly.sh --model=grok --model-path="$GROK_MODEL_PATH" --mode=online --hardware="$HARDWARE_TYPE" > cron/cron_log/$HARDWARE_TYPE/$LOG_DATE/grok_nightly.log 2>&1
bash cron/github_log_upload.sh
echo "[$(date)] Job 9 completed - check log for status"

# Job 10: DeepSeek online serving benchmark
echo ""
echo "[$(date)] Running Job 10: DeepSeek-R1-MXFP4-Preview online serving benchmark"
mkdir -p cron/cron_log/$HARDWARE_TYPE/$LOG_DATE
bash scripts/perf_nightly.sh --model=deepseek --model-path="$DEEPSEEK_MODEL_PATH" --model-name="DeepSeek-R1-MXFP4-Preview" --mode=online --hardware="$HARDWARE_TYPE" > cron/cron_log/$HARDWARE_TYPE/$LOG_DATE/deepseek_nightly_online.log 2>&1
bash cron/github_log_upload.sh
echo "[$(date)] Job 10 completed - check log for status"

echo ""
echo "=========================================="
echo "All failed tasks re-run completed at $(date)"
echo "=========================================="
echo ""
echo "Logs saved to: cron/cron_log/$HARDWARE_TYPE/$LOG_DATE/"
echo ""
echo "Check the following log files for results:"
echo "  - cron/cron_log/$HARDWARE_TYPE/$LOG_DATE/sanity_check_nightly.log"
echo "  - cron/cron_log/$HARDWARE_TYPE/$LOG_DATE/test_nightly_pd.log"
echo "  - cron/cron_log/$HARDWARE_TYPE/$LOG_DATE/deepseek_torch_compile.log"
echo "  - cron/cron_log/$HARDWARE_TYPE/$LOG_DATE/deepseek_dp_attention_torch_compile.log"
echo "  - cron/cron_log/$HARDWARE_TYPE/$LOG_DATE/deepseek_r1_mxfp4_mtp.log"
echo "  - cron/cron_log/$HARDWARE_TYPE/$LOG_DATE/deepseek_r1_mxfp4_dp_test.log"
echo "  - cron/cron_log/$HARDWARE_TYPE/$LOG_DATE/deepseek_r1_mxfp4_dp_mtp.log"
echo "  - cron/cron_log/$HARDWARE_TYPE/$LOG_DATE/grok2_nightly_online.log"
echo "  - cron/cron_log/$HARDWARE_TYPE/$LOG_DATE/grok_nightly.log"
echo "  - cron/cron_log/$HARDWARE_TYPE/$LOG_DATE/deepseek_nightly_online.log"
echo ""
