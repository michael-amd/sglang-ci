# Docker Image Fallback Mechanism

## Overview

When today's Docker image is not available, the `perf_nightly.sh` script automatically uses yesterday's image as a fallback - but **only for tasks that failed or didn't run yesterday**. Tasks that completed successfully yesterday are skipped since there's no need to re-run them with the same image.

## How It Works

### 1. Image Discovery

When `perf_nightly.sh` runs, it:
1. Searches for today's Docker image (e.g., `rocm/sgl-dev:v0.5.x-rocm700-mi30x-20251217`)
2. If not found, checks if yesterday's image should be used as fallback

### 2. Yesterday's Run Status Check

Before using yesterday's image, the script checks yesterday's cron log for the same task:

| Condition | Action |
|-----------|--------|
| Log doesn't exist | **Allow fallback** - task didn't run yesterday |
| Critical errors found (Memory fault, Lock conflict, etc.) | **Allow fallback** - task failed |
| Completed successfully | **Skip fallback** - no need to re-run |
| Completed but had benchmark failures | **Allow fallback** - task needs re-run |
| Did not complete | **Allow fallback** - task was interrupted |

### 3. Error Detection Patterns

The script detects failures by searching for these patterns in yesterday's log:

```
Memory access fault
Fatal Python error
Lock File Conflict
ERROR:.*Another instance
SGLang server encountered critical errors
server process.*terminated
BENCHMARK_FAILED
```

### 4. Completion Detection

Success is determined by finding these markers:

```
[nightly] All benchmarks completed
All tests completed  (for sanity checks)
```

## Task-Specific Log Files

| Task | Log File |
|------|----------|
| Sanity Check | `sanity_check_nightly.log` |
| Grok Online | `grok_nightly.log` |
| Grok2 Online | `grok2_nightly_online.log` |
| DeepSeek Online | `deepseek_nightly_online.log` |
| DeepSeek DP Attention | `deepseek_dp_attention.log` |
| DeepSeek Torch Compile | `deepseek_torch_compile.log` |
| DeepSeek DP+Torch | `deepseek_dp_attention_torch_compile.log` |

## Example Behavior

Given yesterday (20251216) had these results:

| Task | Yesterday's Status | Today's Action |
|------|-------------------|----------------|
| grok_nightly | ✅ Completed | SKIP (already ran) |
| grok2_nightly_online | ✅ Completed | SKIP (already ran) |
| deepseek_nightly_online | ❌ Memory fault | RUN with fallback |
| deepseek_dp_attention | ✅ Completed | SKIP (already ran) |
| deepseek_torch_compile | ❌ Lock conflict | RUN with fallback |
| sanity_check_nightly | ✅ Completed (85.7%) | SKIP (good pass rate) |

## Exit Behavior

When today's image is unavailable:

- **Yesterday succeeded** → Script exits with code 0 (graceful skip)
- **Yesterday failed** → Uses yesterday's image and runs the task
- **No image available** → Script exits with code 1 (error)

## Benefits

1. **Efficient**: Doesn't waste resources re-running successful tasks
2. **Automatic**: No manual intervention needed
3. **Smart**: Distinguishes between failures and successes
4. **Clean**: Graceful exit when nothing needs to be done

## Directory Structure

```
cron/cron_log/
└── {HARDWARE_TYPE}/
    └── {YYYYMMDD}/
        ├── grok_nightly.log
        ├── deepseek_nightly_online.log
        ├── sanity_check_nightly.log
        └── ...
```

## Limitations

1. **One day lookback**: Only checks yesterday's status
2. **Single fallback**: Uses yesterday's image, not older
3. **Task-specific**: Each task is evaluated independently

## Implementation

The logic is implemented in `scripts/perf_nightly.sh`:

- `check_yesterday_run_status()` - Determines if yesterday's run was successful
- Called when today's image is not found
- Returns 0 (allow fallback) or 1 (skip fallback)
