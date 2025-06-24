#!/usr/bin/env bash
# ------------------------------------------------------------------------------
# compare_pr_performance.sh
#
# Compare performance between a PR and the SGLang main branch
#
# USAGE:
#   bash compare_pr_performance.sh --pr=1234
#   bash compare_pr_performance.sh --pr=1234 --repo=sgl-project/sglang
#   bash compare_pr_performance.sh --pr=1234 --models=grok,deepseek
#   bash compare_pr_performance.sh --pr=1234 --benchmark-types=offline,online
#   bash compare_pr_performance.sh --pr=1234 --base-image=rocm/sgl-dev:vllm20250114
# ------------------------------------------------------------------------------

set -euo pipefail

# Set timezone to PST/PDT
export TZ='America/Los_Angeles'

# Record script start time for total duration calculation
SCRIPT_START_TIME=$(date +%s)

###############################################################################
# Parse CLI options
###############################################################################
PR_NUMBER=""
REPO="sgl-project/sglang"
MODELS="grok,deepseek"  # comma-separated list
BENCHMARK_TYPES="offline,online"  # comma-separated list
BASE_IMAGE="rocm/sgl-dev:vllm20250114"
WORK_DIR="/mnt/raid/michael/sgl_benchmark_ci"
OUTPUT_DIR=""  # If empty, will use work_dir/comparison_results
SKIP_BUILD="false"
MAIN_IMAGE=""  # Optional: use existing main branch image
PR_IMAGE=""    # Optional: use existing PR image

for arg in "$@"; do
  case $arg in
    --pr=*)
      PR_NUMBER="${arg#*=}"
      shift
      ;;
    --repo=*)
      REPO="${arg#*=}"
      shift
      ;;
    --models=*)
      MODELS="${arg#*=}"
      shift
      ;;
    --benchmark-types=*)
      BENCHMARK_TYPES="${arg#*=}"
      shift
      ;;
    --base-image=*)
      BASE_IMAGE="${arg#*=}"
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
    --skip-build)
      SKIP_BUILD="true"
      shift
      ;;
    --main-image=*)
      MAIN_IMAGE="${arg#*=}"
      shift
      ;;
    --pr-image=*)
      PR_IMAGE="${arg#*=}"
      shift
      ;;
    --help)
      echo "Usage: $0 [OPTIONS]"
      echo "Options:"
      echo "  --pr=NUMBER             PR number to compare (required)"
      echo "  --repo=REPO             GitHub repository (default: sgl-project/sglang)"
      echo "  --models=LIST           Comma-separated list of models to test: grok,deepseek (default: grok,deepseek)"
      echo "  --benchmark-types=LIST  Comma-separated list of benchmark types: offline,online (default: offline,online)"
      echo "  --base-image=IMAGE      Base Docker image for building (default: rocm/sgl-dev:vllm20250114)"
      echo "  --work-dir=PATH         Working directory (default: /mnt/raid/michael/sgl_benchmark_ci)"
      echo "  --output-dir=PATH       Output directory (default: work_dir/comparison_results)"
      echo "  --skip-build            Skip building Docker images (use existing ones)"
      echo "  --main-image=IMAGE      Use existing main branch image instead of building"
      echo "  --pr-image=IMAGE        Use existing PR image instead of building"
      echo "  --help                  Show this help message"
      exit 0
      ;;
  esac
done

# Validate required arguments
if [[ -z "$PR_NUMBER" ]]; then
  echo "Error: --pr is required"
  exit 1
fi

# Set default output directory
OUTPUT_DIR="${OUTPUT_DIR:-$WORK_DIR/comparison_results}"

# Create timestamp for this comparison run
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
COMPARISON_DIR="${OUTPUT_DIR}/pr${PR_NUMBER}_${TIMESTAMP}"
mkdir -p "$COMPARISON_DIR"

# Log file for the comparison process
LOG_FILE="${COMPARISON_DIR}/comparison.log"
echo "Comparison started at: $(date '+%Y-%m-%d %H:%M:%S %Z')" | tee "$LOG_FILE"
echo "PR: #${PR_NUMBER} from ${REPO}" | tee -a "$LOG_FILE"
echo "Models: ${MODELS}" | tee -a "$LOG_FILE"
echo "Benchmark types: ${BENCHMARK_TYPES}" | tee -a "$LOG_FILE"
echo "" | tee -a "$LOG_FILE"

###############################################################################
# Build Docker images
###############################################################################
build_images() {
    if [[ "$SKIP_BUILD" == "true" ]]; then
        echo "Skipping Docker image builds (--skip-build specified)" | tee -a "$LOG_FILE"
        return
    fi

    local build_start=$(date +%s)

    # Build main branch image if not provided
    if [[ -z "$MAIN_IMAGE" ]]; then
        echo "Building SGLang main branch Docker image..." | tee -a "$LOG_FILE"
        bash "${WORK_DIR}/build_sglang_docker.sh" \
            --branch=main \
            --repo="https://github.com/${REPO}.git" \
            --base-image="${BASE_IMAGE}" \
            2>&1 | tee -a "$LOG_FILE"

        # Extract the built image name from the output
        MAIN_IMAGE=$(grep "Successfully built Docker image:" "$LOG_FILE" | tail -1 | awk '{print $NF}')
        if [[ -z "$MAIN_IMAGE" ]]; then
            echo "Error: Failed to build main branch image" | tee -a "$LOG_FILE"
            exit 1
        fi
    fi

    # Build PR image if not provided
    if [[ -z "$PR_IMAGE" ]]; then
        echo "Building SGLang PR #${PR_NUMBER} Docker image..." | tee -a "$LOG_FILE"
        # For PRs, we need to use the PR's merge ref
        bash "${WORK_DIR}/build_sglang_docker.sh" \
            --branch="pull/${PR_NUMBER}/merge" \
            --repo="https://github.com/${REPO}.git" \
            --base-image="${BASE_IMAGE}" \
            2>&1 | tee -a "$LOG_FILE"

        # Extract the built image name from the output
        PR_IMAGE=$(grep "Successfully built Docker image:" "$LOG_FILE" | tail -1 | awk '{print $NF}')
        if [[ -z "$PR_IMAGE" ]]; then
            echo "Error: Failed to build PR image" | tee -a "$LOG_FILE"
            exit 1
        fi
    fi

    local build_end=$(date +%s)
    local build_duration=$((build_end - build_start))
    echo "Docker image build completed in ${build_duration} seconds" | tee -a "$LOG_FILE"
    echo "Main branch image: ${MAIN_IMAGE}" | tee -a "$LOG_FILE"
    echo "PR image: ${PR_IMAGE}" | tee -a "$LOG_FILE"
    echo "" | tee -a "$LOG_FILE"
}

###############################################################################
# Run benchmarks
###############################################################################
run_benchmarks() {
    local image=$1
    local label=$2  # "main" or "pr"
    local results_dir="${COMPARISON_DIR}/${label}"
    mkdir -p "$results_dir"

    echo "Running benchmarks for ${label} (${image})..." | tee -a "$LOG_FILE"
    local bench_start=$(date +%s)

    # Convert comma-separated lists to arrays
    IFS=',' read -ra MODEL_ARRAY <<< "$MODELS"
    IFS=',' read -ra TYPE_ARRAY <<< "$BENCHMARK_TYPES"

    for model in "${MODEL_ARRAY[@]}"; do
        for type in "${TYPE_ARRAY[@]}"; do
            echo "  Running ${model} ${type} benchmark..." | tee -a "$LOG_FILE"

            case "${model}-${type}" in
                grok-offline)
                    bash "${WORK_DIR}/grok_perf_offline_csv.sh" \
                        --docker_image="${image}" \
                        --output-dir="${results_dir}" \
                        2>&1 | tee -a "${results_dir}/grok_offline.log"
                    ;;
                grok-online)
                    bash "${WORK_DIR}/grok_perf_online_csv.sh" \
                        --docker_image="${image}" \
                        --output-dir="${results_dir}" \
                        --skip-gsm8k=true \
                        2>&1 | tee -a "${results_dir}/grok_online.log"
                    ;;
                deepseek-offline)
                    bash "${WORK_DIR}/deepseek_perf_offline_csv.sh" \
                        --docker_image="${image}" \
                        --output-dir="${results_dir}" \
                        2>&1 | tee -a "${results_dir}/deepseek_offline.log"
                    ;;
                deepseek-online)
                    echo "    DeepSeek online benchmark not yet implemented" | tee -a "$LOG_FILE"
                    ;;
                *)
                    echo "    Unknown benchmark combination: ${model}-${type}" | tee -a "$LOG_FILE"
                    ;;
            esac
        done
    done

    local bench_end=$(date +%s)
    local bench_duration=$((bench_end - bench_start))
    echo "Benchmarks for ${label} completed in ${bench_duration} seconds" | tee -a "$LOG_FILE"
    echo "" | tee -a "$LOG_FILE"
}

###############################################################################
# Compare results
###############################################################################
compare_results() {
    echo "Generating comparison report..." | tee -a "$LOG_FILE"

    # Create comparison report
    REPORT_FILE="${COMPARISON_DIR}/comparison_report.md"

    cat > "$REPORT_FILE" <<EOF
# Performance Comparison Report

**PR:** #${PR_NUMBER} from ${REPO}
**Date:** $(date '+%Y-%m-%d %H:%M:%S %Z')
**Main Branch Image:** ${MAIN_IMAGE}
**PR Image:** ${PR_IMAGE}

## Summary

This report compares the performance impact of PR #${PR_NUMBER} against the main branch.

EOF

    # Process each benchmark result
    IFS=',' read -ra MODEL_ARRAY <<< "$MODELS"
    IFS=',' read -ra TYPE_ARRAY <<< "$BENCHMARK_TYPES"

    for model in "${MODEL_ARRAY[@]}"; do
        for type in "${TYPE_ARRAY[@]}"; do
            echo "" >> "$REPORT_FILE"
            echo "### ${model^} ${type^} Benchmark" >> "$REPORT_FILE"
            echo "" >> "$REPORT_FILE"

            # Find the CSV files
            local main_csv=$(find "${COMPARISON_DIR}/main" -name "*${model^^}*_${type}.csv" 2>/dev/null | head -1)
            local pr_csv=$(find "${COMPARISON_DIR}/pr" -name "*${model^^}*_${type}.csv" 2>/dev/null | head -1)

            if [[ -f "$main_csv" && -f "$pr_csv" ]]; then
                # Run Python script to generate comparison
                python3 "${WORK_DIR}/compare_csv_results.py" \
                    --main-csv "$main_csv" \
                    --pr-csv "$pr_csv" \
                    --output-md "$REPORT_FILE" \
                    --append \
                    2>&1 | tee -a "$LOG_FILE" || {
                        echo "CSV files found but comparison failed. Showing file locations:" >> "$REPORT_FILE"
                        echo "- Main: $main_csv" >> "$REPORT_FILE"
                        echo "- PR: $pr_csv" >> "$REPORT_FILE"
                    }
            else
                echo "Results not found for ${model} ${type} benchmark." >> "$REPORT_FILE"
                [[ -f "$main_csv" ]] || echo "- Main branch CSV not found" >> "$REPORT_FILE"
                [[ -f "$pr_csv" ]] || echo "- PR CSV not found" >> "$REPORT_FILE"
            fi
        done
    done

    echo "" >> "$REPORT_FILE"
    echo "## Raw Results" >> "$REPORT_FILE"
    echo "" >> "$REPORT_FILE"
    echo "All benchmark results are stored in: \`${COMPARISON_DIR}\`" >> "$REPORT_FILE"

    echo "" | tee -a "$LOG_FILE"
    echo "Comparison report saved to: ${REPORT_FILE}" | tee -a "$LOG_FILE"
}

###############################################################################
# Main execution
###############################################################################
main() {
    # Build Docker images
    build_images

    # Run benchmarks on main branch
    run_benchmarks "$MAIN_IMAGE" "main"

    # Run benchmarks on PR
    run_benchmarks "$PR_IMAGE" "pr"

    # Compare results
    compare_results

    # Final summary
    local script_end=$(date +%s)
    # Get script start time from the beginning of the script
    local total_duration=$((script_end - SCRIPT_START_TIME))

    echo "" | tee -a "$LOG_FILE"
    echo "========================================" | tee -a "$LOG_FILE"
    echo "COMPARISON COMPLETE" | tee -a "$LOG_FILE"
    echo "========================================" | tee -a "$LOG_FILE"
    echo "Total execution time: ${total_duration} seconds" | tee -a "$LOG_FILE"
    echo "Results directory: ${COMPARISON_DIR}" | tee -a "$LOG_FILE"
    echo "Comparison report: ${REPORT_FILE}" | tee -a "$LOG_FILE"
    echo "========================================" | tee -a "$LOG_FILE"
}

# Run main function
main
