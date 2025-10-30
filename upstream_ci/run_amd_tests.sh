#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# run_amd_tests.sh - SGL AMD Test Runner for Individual Tests
#
# DESCRIPTION:
#   Run individual SGL tests on AMD hardware with custom Docker images.
#   Supports running multiple test files one by one with proper logging.
#
# USAGE:
#   run_amd_tests.sh --docker-name <name> --test <test1.py> [--test <test2.py> ...] [OPTIONS]
#
# OPTIONS:
#   --docker-name=NAME          Local Docker image name to use for container (e.g., michael_0916)
#   --docker-image=IMAGE        Docker image to pull from registry (optional, e.g., rocm/sgl-dev:latest)
#   --test=TESTFILE             Test file to run (can be specified multiple times)
#   --sglang-path=PATH          Path to sglang repository [default: /mnt/raid/michael/hubertlu-tw/sglang]
#   --num-gpus=N                Number of GPUs to use [default: 8]
#   --timeout=SECONDS           Timeout per test in seconds [default: 1800]
#   --hf-token=TOKEN            HuggingFace token for model access [default: HF_TOKEN_REMOVED]
#   --help, -h                  Show help message
#
# EXAMPLES:
#   run_amd_tests.sh --docker-name michael_0916 --test test_harmony_parser.py
#   run_amd_tests.sh --docker-name michael_0916 --test test_harmony_parser.py --test test_start_profile.py
#   run_amd_tests.sh --docker-name michael_0916 --docker-image rocm/sgl-dev:latest --test test_triton_attention_kernels.py
#   run_amd_tests.sh --docker-name michael_0916 --sglang-path /custom/path/to/sglang --test test_harmony_parser.py
# ---------------------------------------------------------------------------

set -euo pipefail

###############################################################################
# Configuration Variables
###############################################################################

# Base paths and directories
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE_CI_DIR="/mnt/raid/michael/sglang-ci"
DEFAULT_SGLANG_REPO_DIR="/mnt/raid/michael/hubertlu-tw/sglang"
WORK_DIR="/sgl-workspace"

# Docker configuration
CONTAINER_SHM_SIZE="32g"
DOCKER_CMD=(sudo /usr/bin/docker)

# Default test configuration
DEFAULT_NUM_GPUS=8
DEFAULT_TIMEOUT=1800
DEFAULT_HF_TOKEN="HF_TOKEN_REMOVED"
# TEST_DIR will be dynamically set based on sglang path: <sglang_path>/test/srt

# Get current date in format YYYYMMDD
CURRENT_DATE=$(date '+%Y%m%d')

###############################################################################
# Logging Functions
###############################################################################

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"
}

log_error() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] ERROR: $*" >&2
}

log_warn() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] WARN: $*" >&2
}

###############################################################################
# Help Function
###############################################################################

show_help() {
    cat << EOF
Usage: $0 --docker-name <name> --test <test1.py> [--test <test2.py> ...] [OPTIONS]

Run individual SGL tests on AMD hardware with custom Docker images.

Required Options:
  --docker-name=NAME          Local Docker image name to use for container (e.g., michael_0916)
  --test=TESTFILE             Test file to run (can be specified multiple times)

Optional:
  --docker-image=IMAGE        Docker image to pull from registry (e.g., rocm/sgl-dev:latest)
  --sglang-path=PATH          Path to sglang repository [default: $DEFAULT_SGLANG_REPO_DIR]
  --num-gpus=N                Number of GPUs to use [default: $DEFAULT_NUM_GPUS]
  --timeout=SECONDS           Timeout per test in seconds [default: $DEFAULT_TIMEOUT]
  --hf-token=TOKEN            HuggingFace token for model access [default: $DEFAULT_HF_TOKEN]
  --help, -h                  Show this help message

Examples:
  $0 --docker-name michael_0916 --test test_harmony_parser.py
  $0 --docker-name michael_0916 --test test_harmony_parser.py --test test_start_profile.py
  $0 --docker-name michael_0916 --docker-image rocm/sgl-dev:latest --test test_triton_attention_kernels.py
  $0 --docker-name michael_0916 --sglang-path /custom/path/to/sglang --test test_harmony_parser.py

Log Files:
  Logs are saved to: $BASE_CI_DIR/upstream_ci/upstream_test_log/<docker_image_tag>/{num_gpus}-gpu-amd-{date}_{test_name}.log
  Example: /mnt/raid/michael/sglang-ci/upstream_ci/upstream_test_log/v0.5.4.post1-rocm630-mi30x-20251030/8-gpu-amd-20251030_test_harmony_parser.log

EOF
}

###############################################################################
# GPU Functions
###############################################################################

check_gpu_idle() {
    if ! command -v rocm-smi &> /dev/null; then
        log_warn "rocm-smi not found. Skipping GPU idle check."
        return 0
    fi

    # Check if GPUs are idle (usage < 15%)
    if rocm-smi | awk '
        NR > 2 && NF >= 2 {
            gpu_usage=gensub(/%/, "", "g", $(NF-1));
            vram_usage=gensub(/%/, "", "g", $NF);
            if (gpu_usage > 15 || vram_usage > 15) {
                exit 1;
            }
        }'; then
        return 0  # idle
    else
        return 1  # busy
    fi
}

ensure_gpu_idle() {
    if ! check_gpu_idle; then
        log "GPU is busy. Stopping running Docker containers..."
        if "${DOCKER_CMD[@]}" info >/dev/null 2>&1; then
            running_ids="$("${DOCKER_CMD[@]}" ps -q 2>/dev/null || true)"
            if [[ -n "$running_ids" ]]; then
                log "Stopping containers: $(echo "$running_ids" | tr '\n' ' ')"
                "${DOCKER_CMD[@]}" stop $running_ids >/dev/null 2>&1 || true
            fi
        fi
        log "Waiting 15s for GPU to become idle..."
        sleep 15
    fi

    if check_gpu_idle; then
        log "GPU is idle. Proceeding..."
    else
        log_warn "GPU may still be busy, but proceeding..."
    fi
}

###############################################################################
# Docker Functions
###############################################################################

create_cuda_visible_devices() {
    local num_gpus=$1
    local devices=""
    for ((i=0; i<num_gpus; i++)); do
        if [ $i -eq 0 ]; then
            devices="$i"
        else
            devices="$devices,$i"
        fi
    done
    echo "$devices"
}

setup_container() {
    local docker_name="$1"
    local container_name="$2"
    local sglang_repo_dir="$3"

    # Container test directory is always the same since we mount to /sgl-workspace/sglang
    local container_test_dir="/sgl-workspace/sglang/test/srt"

    log "Setting up container: $container_name with image: $docker_name"
    log "Mounting sglang from: $sglang_repo_dir"

    # Check if container already exists and is running
    if "${DOCKER_CMD[@]}" ps --format '{{.Names}}' | grep -q "^${container_name}$"; then
        log "Reusing existing running container: $container_name"

        # Verify container is still accessible
        if "${DOCKER_CMD[@]}" exec "$container_name" test -d "$container_test_dir" 2>/dev/null; then
            log "Container verified and ready to use"
            return 0
        else
            log_warn "Existing container not accessible, will recreate"
            "${DOCKER_CMD[@]}" stop "$container_name" >/dev/null 2>&1 || true
            "${DOCKER_CMD[@]}" rm "$container_name" >/dev/null 2>&1 || true
        fi
    elif "${DOCKER_CMD[@]}" ps -a --format '{{.Names}}' | grep -q "^${container_name}$"; then
        # Container exists but is stopped, remove it and create new one
        log "Removing stopped container: $container_name"
        "${DOCKER_CMD[@]}" rm "$container_name" >/dev/null 2>&1 || true
    fi

    # Create new container
    log "Creating container: $container_name"
    "${DOCKER_CMD[@]}" run -d --name "$container_name" \
        --shm-size "$CONTAINER_SHM_SIZE" --ipc=host --cap-add=SYS_PTRACE --network=host \
        --device=/dev/kfd --device=/dev/dri --security-opt seccomp=unconfined \
        -v "$sglang_repo_dir:$WORK_DIR/sglang" \
        -v "$BASE_CI_DIR:$BASE_CI_DIR" \
        --group-add video --privileged \
        -w "$WORK_DIR" "$docker_name" tail -f /dev/null

    # Verify container is running and test directory is accessible
    if ! "${DOCKER_CMD[@]}" exec "$container_name" test -d "$container_test_dir" 2>/dev/null; then
        log_error "Test directory not accessible in container: $container_test_dir"
        return 1
    fi

    log "Container setup complete: $container_name"
    return 0
}

cleanup_container() {
    local container_name="$1"
    log "Cleaning up container: $container_name"
    "${DOCKER_CMD[@]}" stop "$container_name" >/dev/null 2>&1 || true
    "${DOCKER_CMD[@]}" rm "$container_name" >/dev/null 2>&1 || true
}

###############################################################################
# Test Running Functions
###############################################################################

run_single_test() {
    local test_file="$1"
    local docker_name="$2"
    local num_gpus="$3"
    local timeout_seconds="$4"
    local sglang_repo_dir="$5"
    local host_test_dir="$6"
    local hf_token="$7"
    local docker_image_tag="$8"

    # Extract test name from file (remove .py extension)
    local test_name="${test_file%.py}"

    # Generate container name - reusable across tests for the same docker image
    local container_name="sgl-dev_${docker_image_tag}_amd_test"

    # Generate log file path in upstream_ci/upstream_test_log directory
    local log_dir="${BASE_CI_DIR}/upstream_ci/upstream_test_log/${docker_image_tag}"
    local log_file="${log_dir}/${num_gpus}-gpu-amd-${CURRENT_DATE}_${test_name}.log"

    # Container test directory is always the same since we mount to /sgl-workspace/sglang
    local container_test_dir="/sgl-workspace/sglang/test/srt"

    # Create CUDA_VISIBLE_DEVICES string
    local cuda_devices=$(create_cuda_visible_devices "$num_gpus")

    log "=========================================="
    log "Starting test: $test_file"
    log "Container: $container_name"
    log "Docker name: $docker_name"
    log "Host test dir: $host_test_dir"
    log "Container test dir: $container_test_dir"
    log "GPUs: $num_gpus ($cuda_devices)"
    log "Timeout: ${timeout_seconds}s"
    log "Log: $log_file"
    log "=========================================="

    # Ensure GPU is idle
    ensure_gpu_idle

    # Setup container
    if ! setup_container "$docker_name" "$container_name" "$sglang_repo_dir"; then
        log_error "Failed to setup container for test: $test_file"
        return 1
    fi

    # Create log directory
    mkdir -p "$(dirname "$log_file")"

    # Start logging
    {
        echo "=========================================="
        echo "SGL AMD Test Log"
        echo "=========================================="
        echo "Test file: $test_file"
        echo "Docker name: $docker_name"
        echo "Container: $container_name"
        echo "GPUs used: $num_gpus ($cuda_devices)"
        echo "Start time: $(date '+%Y-%m-%d %H:%M:%S %Z')"
        echo "Timeout: ${timeout_seconds}s"
        echo "Test directory: $container_test_dir"
        echo "=========================================="
        echo ""
    } > "$log_file"

    # Set up environment variables for the test
    local env_vars=(
        "INSIDE_CONTAINER=1"
        "CUDA_VISIBLE_DEVICES=$cuda_devices"
        "HF_TOKEN=$hf_token"
        "SGLANG_AMD_CI=1"
        "SGLANG_IS_IN_CI=1"
        "SGLANG_USE_AITER=1"
        "PYTHONPATH=/sgl-workspace/sglang/python"
    )

    # Build docker exec command with environment variables
    local docker_exec_cmd=("${DOCKER_CMD[@]}" exec)
    for env_var in "${env_vars[@]}"; do
        docker_exec_cmd+=(-e "$env_var")
    done
    docker_exec_cmd+=("$container_name" bash -c)

    # Run the test with timeout
    local test_exit_code=0
    local test_command="cd '$container_test_dir' && timeout ${timeout_seconds}s python3 -m unittest discover -s . -p '$test_file' -v"

    log "Running test command: $test_command"

    # Execute the test
    "${docker_exec_cmd[@]}" "$test_command" >> "$log_file" 2>&1 || test_exit_code=$?

    # Add footer to log
    {
        echo ""
        echo "=========================================="
        echo "End time: $(date '+%Y-%m-%d %H:%M:%S %Z')"
        echo "Exit code: $test_exit_code"
        if [ $test_exit_code -eq 0 ]; then
            echo "Result: PASSED"
        elif [ $test_exit_code -eq 124 ]; then
            echo "Result: TIMEOUT (${timeout_seconds}s)"
        else
            echo "Result: FAILED"
        fi
        echo "=========================================="
    } >> "$log_file"

    # Keep container running for reuse across tests
    log "Container kept running for reuse: $container_name"

    # Report results
    if [ $test_exit_code -eq 0 ]; then
        log "✅ PASSED: $test_file (docker: $docker_name)"
    elif [ $test_exit_code -eq 124 ]; then
        log "⏱️  TIMEOUT: $test_file (docker: $docker_name, ${timeout_seconds}s)"
    else
        log "❌ FAILED: $test_file (docker: $docker_name, exit code: $test_exit_code)"
    fi

    log "Log saved to: $log_file"
    log "=========================================="

    return $test_exit_code
}

###############################################################################
# Main Script
###############################################################################

main() {
    local docker_name=""
    local docker_image=""
    local test_files=()
    local num_gpus=$DEFAULT_NUM_GPUS
    local timeout_seconds=$DEFAULT_TIMEOUT
    local sglang_repo_dir="$DEFAULT_SGLANG_REPO_DIR"
    local hf_token="$DEFAULT_HF_TOKEN"

    # Parse command line arguments
    while [[ $# -gt 0 ]]; do
        case $1 in
            --docker-name=*)
                docker_name="${1#*=}"
                shift
                ;;
            --docker-image=*)
                docker_image="${1#*=}"
                shift
                ;;
            --test=*)
                test_files+=("${1#*=}")
                shift
                ;;
            --sglang-path=*)
                sglang_repo_dir="${1#*=}"
                shift
                ;;
            --num-gpus=*)
                num_gpus="${1#*=}"
                shift
                ;;
            --timeout=*)
                timeout_seconds="${1#*=}"
                shift
                ;;
            --hf-token=*)
                hf_token="${1#*=}"
                shift
                ;;
            --help|-h)
                show_help
                exit 0
                ;;
            *)
                log_error "Unknown option: $1"
                echo "Use --help for usage information"
                exit 1
                ;;
        esac
    done

    # Validate required arguments
    if [[ -z "$docker_name" ]]; then
        log_error "Docker name is required. Use --docker-name=<name>"
        echo "Use --help for usage information"
        exit 1
    fi

    if [[ ${#test_files[@]} -eq 0 ]]; then
        log_error "At least one test file is required. Use --test=<test_file.py>"
        echo "Use --help for usage information"
        exit 1
    fi

    # Validate sglang path
    # Set TEST_DIR based on the sglang repository path
    local TEST_DIR="${sglang_repo_dir}/test/srt"

    # Validate sglang path and test directory
    if [[ ! -d "$sglang_repo_dir" ]]; then
        log_error "Sglang repository directory does not exist: $sglang_repo_dir"
        exit 1
    fi

    if [[ ! -d "$TEST_DIR" ]]; then
        log_error "Test directory not found: $TEST_DIR"
        log_error "Please ensure the sglang path points to a valid sglang repository"
        exit 1
    fi

    if [[ ! -f "$TEST_DIR/run_suite.py" ]]; then
        log_error "run_suite.py not found in: $TEST_DIR"
        log_error "Please ensure the sglang path points to a valid sglang repository"
        exit 1
    fi

    # Validate num_gpus
    if ! [[ "$num_gpus" =~ ^[0-9]+$ ]] || [ "$num_gpus" -lt 1 ] || [ "$num_gpus" -gt 8 ]; then
        log_error "Invalid --num-gpus value. Must be a number between 1 and 8."
        exit 1
    fi

    # Validate timeout
    if ! [[ "$timeout_seconds" =~ ^[0-9]+$ ]] || [ "$timeout_seconds" -lt 1 ]; then
        log_error "Invalid --timeout value. Must be a positive number."
        exit 1
    fi

    # Validate Docker access
    if [[ ! -x "/usr/bin/docker" ]]; then
        log_error "Docker executable not found at /usr/bin/docker"
        exit 1
    fi

    if ! "${DOCKER_CMD[@]}" info >/dev/null 2>&1; then
        log_error "Cannot access Docker daemon. Please check Docker setup."
        exit 1
    fi

    # Extract docker image tag from docker_image (e.g., "rocm/sgl-dev:v0.5.4.post1-rocm630-mi30x-20251030" -> "v0.5.4.post1-rocm630-mi30x-20251030")
    local docker_image_tag
    if [[ -n "$docker_image" ]]; then
        docker_image_tag="${docker_image##*:}"
    else
        # Use docker_name as fallback
        docker_image_tag="$docker_name"
    fi

    log "=========================================="
    log "SGL AMD Test Runner Started"
    log "=========================================="
    log "Docker container name: $docker_name"
    if [[ -n "$docker_image" ]]; then
        log "Docker image to pull: $docker_image"
        log "Docker image tag: $docker_image_tag"
    else
        log "Docker image to pull: None (using local image)"
    fi
    log "Sglang repository: $sglang_repo_dir"
    log "Test directory: $TEST_DIR"
    log "Number of GPUs: $num_gpus"
    log "Timeout per test: ${timeout_seconds}s"
    log "HF Token: ${hf_token:0:10}...${hf_token: -4}"
    log "Tests to run: ${#test_files[@]}"
    for test_file in "${test_files[@]}"; do
        log "  - $test_file"
    done
    log "Log directory: ${BASE_CI_DIR}/upstream_ci/upstream_test_log/${docker_image_tag}/"
    log "Current date: $CURRENT_DATE"
    log "=========================================="

    # Try to pull the Docker image if specified
    if [[ -n "$docker_image" ]]; then
        log "Pulling Docker image: $docker_image"
        if ! "${DOCKER_CMD[@]}" pull "$docker_image" 2>&1; then
            log_warn "Failed to pull image: $docker_image. Continuing with local image..."
        else
            log "Successfully pulled: $docker_image"
        fi
    else
        log "No Docker image specified for pulling. Using local image: $docker_name"
    fi

    # Generate container name for reuse - same for all tests
    local container_name="sgl-dev_${docker_image_tag}_amd_test"
    log "Container name for this session: $container_name"

    # Run tests one by one
    local failed_tests=()
    local passed_tests=()
    local timeout_tests=()

    for test_file in "${test_files[@]}"; do
        local result
        if run_single_test "$test_file" "$docker_name" "$num_gpus" "$timeout_seconds" "$sglang_repo_dir" "$TEST_DIR" "$hf_token" "$docker_image_tag"; then
            passed_tests+=("$test_file")
        else
            result=$?
            if [ $result -eq 124 ]; then
                timeout_tests+=("$test_file")
            else
                failed_tests+=("$test_file")
            fi
        fi

        # Brief pause between tests
        if [ ${#test_files[@]} -gt 1 ]; then
            log "Waiting 5s before next test..."
            sleep 5
        fi
    done

    # Final summary
    log ""
    log "=========================================="
    log "FINAL SUMMARY"
    log "=========================================="
    log "Total tests: ${#test_files[@]}"
    log "Passed: ${#passed_tests[@]}"
    log "Failed: ${#failed_tests[@]}"
    log "Timeout: ${#timeout_tests[@]}"

    if [ ${#passed_tests[@]} -gt 0 ]; then
        log ""
        log "✅ PASSED TESTS:"
        for test in "${passed_tests[@]}"; do
            log "  - $test"
        done
    fi

    if [ ${#failed_tests[@]} -gt 0 ]; then
        log ""
        log "❌ FAILED TESTS:"
        for test in "${failed_tests[@]}"; do
            log "  - $test"
        done
    fi

    if [ ${#timeout_tests[@]} -gt 0 ]; then
        log ""
        log "⏱️  TIMEOUT TESTS:"
        for test in "${timeout_tests[@]}"; do
            log "  - $test"
        done
    fi

    log "=========================================="
    log ""
    log "NOTE: Container '$container_name' is left running for reuse."
    log "To stop and remove it manually, run:"
    log "  sudo docker stop $container_name && sudo docker rm $container_name"
    log ""

    # Exit with error if any tests failed
    if [ ${#failed_tests[@]} -gt 0 ] || [ ${#timeout_tests[@]} -gt 0 ]; then
        exit 1
    else
        exit 0
    fi
}

# Run main function with all arguments
main "$@"
