#!/bin/bash

# PD Nightly Test Runner with Docker Support
# This script runs PD disaggregation tests using Docker containers

set -euo pipefail

# Configuration
MODEL_PATH="${1:-/mnt/raid/models/huggingface/lmsys/gpt-oss-20b-bf16}"
MODEL_NAME="${2:-GPT-OSS-20B}"
# Identifier used in completion requests; fall back to model path if name not provided.
COMPLETION_MODEL="${MODEL_NAME:-${MODEL_PATH}}"
DOCKER_IMAGE="${DOCKER_IMAGE:-rocm/sgl-dev:v0.5.3.post1-rocm700-mi30x-20251014}"
TEST_TIMEOUT="${TEST_TIMEOUT:-300}"
TEST_DIR="/mnt/raid/michael/sglang-ci/test/pd"
# Dynamically detect host IP address (first non-loopback IP: ip route get 8.8.8.8 | awk '{print $7}' | awk 'NR==1 {print $1}')
HOST_IP="10.235.26.27"

# Extract hardware type from docker image tag (mi30x, mi35x, etc.)
DOCKER_TAG="${DOCKER_IMAGE##*:}"
if [[ "$DOCKER_TAG" =~ (mi[0-9]+x) ]]; then
  HARDWARE="${BASH_REMATCH[1]}"
else
  HARDWARE="mi30x"  # default
fi

# Log directory structure: test/pd/pd_log/{hardware}/{docker_tag}
LOG_BASE_DIR="/mnt/raid/michael/sglang-ci/test/pd/pd_log"
LOG_DIR="${LOG_BASE_DIR}/${HARDWARE}/${DOCKER_TAG}"

# Docker command with sudo
DOCKER_CMD=(sudo /usr/bin/docker)

# Main execution log file
MAIN_LOG="${LOG_DIR}/main_execution.log"

# Create log directory with proper permissions
if [ ! -d "${LOG_DIR}" ]; then
  if mkdir -p "${LOG_DIR}" 2>/dev/null; then
    echo "[pd-test] Log directory created: ${LOG_DIR}"
  else
    echo "[pd-test] Permission denied, trying with sudo..."
    sudo mkdir -p "${LOG_DIR}"
    sudo chown -R ${USER}:${USER} "${LOG_DIR}"
    sudo chmod -R 775 "${LOG_DIR}"
  fi
fi

# Setup logging - redirect all output to main log while still showing on terminal
exec > >(tee -a "${MAIN_LOG}") 2>&1
echo "==================================================="
echo "PD Test Execution Started: $(date)"
echo "==================================================="

echo "[pd-test] =========================================="
echo "[pd-test] SGLang PD Docker-based Test Runner"
echo "[pd-test] =========================================="
echo "[pd-test] Model: ${MODEL_PATH}"
echo "[pd-test] Model Name: ${MODEL_NAME}"
echo "[pd-test] Docker Image: ${DOCKER_IMAGE}"
echo "[pd-test] Hardware: ${HARDWARE}"
echo "[pd-test] Docker Tag: ${DOCKER_TAG}"
echo "[pd-test] Log Directory: ${LOG_DIR}"
echo "[pd-test] IP: ${HOST_IP}"
echo "[pd-test]"

# Check if model exists
if [ ! -d "${MODEL_PATH}" ]; then
  echo "[pd-test] ERROR: Model path does not exist: ${MODEL_PATH}"
  exit 1
fi

# Function to cleanup on exit
cleanup() {
  local EXIT_CODE=$?
  echo ""
  echo "[pd-test] =========================================="
  echo "[pd-test] Cleaning up Docker containers..."
  echo "[pd-test] =========================================="
  
  # Collect final logs before cleanup
  if "${DOCKER_CMD[@]}" ps -a | grep -q sglang-pd-router; then
    echo "[pd-test] Collecting final router logs..."
    "${DOCKER_CMD[@]}" logs sglang-pd-router > "${LOG_DIR}/load_balance.log" 2>&1 || true
  fi
  if "${DOCKER_CMD[@]}" ps -a | grep -q sglang-pd-prefill; then
    echo "[pd-test] Collecting final prefill logs..."
    "${DOCKER_CMD[@]}" logs sglang-pd-prefill > "${LOG_DIR}/prefill.log" 2>&1 || true
  fi
  if "${DOCKER_CMD[@]}" ps -a | grep -q sglang-pd-decode; then
    echo "[pd-test] Collecting final decode logs..."
    "${DOCKER_CMD[@]}" logs sglang-pd-decode > "${LOG_DIR}/decode.log" 2>&1 || true
  fi
  
  "${DOCKER_CMD[@]}" stop sglang-pd-router 2>/dev/null || true
  "${DOCKER_CMD[@]}" stop sglang-pd-prefill 2>/dev/null || true
  "${DOCKER_CMD[@]}" stop sglang-pd-decode 2>/dev/null || true
  "${DOCKER_CMD[@]}" rm sglang-pd-router 2>/dev/null || true
  "${DOCKER_CMD[@]}" rm sglang-pd-prefill 2>/dev/null || true
  "${DOCKER_CMD[@]}" rm sglang-pd-decode 2>/dev/null || true
  sleep 3
  echo "[pd-test] Cleanup complete."
  echo "==================================================="
  echo "PD Test Execution Ended: $(date) (Exit Code: $EXIT_CODE)"
  echo "==================================================="
}

trap cleanup EXIT INT TERM

# Cleanup any existing containers
echo "[pd-test] Checking for existing containers..."
cleanup

# Step 1: Start Load Balancer/Router in Docker
echo "[pd-test] =========================================="
echo "[pd-test] Step 1: Starting Load Balancer/Router..."
echo "[pd-test] =========================================="

"${DOCKER_CMD[@]}" run -d --name sglang-pd-router \
  --network=host \
  -v /mnt/raid:/mnt/raid \
  -v /data2:/data2 \
  "${DOCKER_IMAGE}" \
  python3 -m sglang_router.launch_router \
    --pd-disaggregation \
    --prefill "http://${HOST_IP}:30025" \
    --decode "http://${HOST_IP}:30026" \
    --host 0.0.0.0 \
    --port 30028 \
  > "${LOG_DIR}/load_balance.log" 2>&1

echo "[pd-test] Router container started"
echo "[pd-test] Waiting 10 seconds for router to initialize..."
sleep 10

# Check if router container is still running
if ! "${DOCKER_CMD[@]}" ps | grep -q sglang-pd-router; then
  echo "[pd-test] ERROR: Router container stopped"
  "${DOCKER_CMD[@]}" logs sglang-pd-router > "${LOG_DIR}/load_balance.log" 2>&1
  echo "[pd-test] Router container exit code:"
  "${DOCKER_CMD[@]}" inspect sglang-pd-router --format='{{.State.ExitCode}}' 2>/dev/null || echo "N/A"
  echo "[pd-test] Last 50 lines of router log:"
  tail -50 "${LOG_DIR}/load_balance.log"
  exit 1
fi
echo "[pd-test] ✓ Router started successfully"
echo ""

# Step 2: Start Prefill Server in Docker
echo "[pd-test] =========================================="
echo "[pd-test] Step 2: Starting Prefill Server (GPUs 0-3)..."
echo "[pd-test] =========================================="

"${DOCKER_CMD[@]}" run -d --name sglang-pd-prefill \
  --network=host --ipc=host --cap-add=SYS_PTRACE \
  --device=/dev/kfd --device=/dev/dri --security-opt seccomp=unconfined \
  --group-add video --privileged \
  -v /mnt/raid:/mnt/raid \
  -v /data2:/data2 \
  -e HIP_VISIBLE_DEVICES=0,1,2,3 \
  -e LD_LIBRARY_PATH=/opt/rocm/lib:/usr/local/lib \
  "${DOCKER_IMAGE}" \
  python3 -m sglang.launch_server \
    --model-path "${MODEL_PATH}" \
    --disaggregation-mode prefill \
    --port 30025 \
    --disaggregation-ib-device lo \
    --host "${HOST_IP}" \
    --tp 4 \
    --trust-remote-code \
    --enable-two-batch-overlap \
    --attention-backend triton \
  > "${LOG_DIR}/prefill.log" 2>&1

echo "[pd-test] Prefill Server container started"
echo "[pd-test] Waiting 60 seconds for prefill server to initialize..."
sleep 60

# Check if prefill container is still running
if ! "${DOCKER_CMD[@]}" ps | grep -q sglang-pd-prefill; then
  echo "[pd-test] ERROR: Prefill Server container stopped"
  "${DOCKER_CMD[@]}" logs sglang-pd-prefill > "${LOG_DIR}/prefill.log" 2>&1
  echo "[pd-test] Prefill container exit code:"
  "${DOCKER_CMD[@]}" inspect sglang-pd-prefill --format='{{.State.ExitCode}}' 2>/dev/null || echo "N/A"
  echo "[pd-test] Last 50 lines of prefill log:"
  tail -50 "${LOG_DIR}/prefill.log"
  echo "[pd-test] Checking for common errors:"
  grep -i "error\|exception\|failed\|traceback" "${LOG_DIR}/prefill.log" | tail -20 || echo "No error keywords found"
  exit 1
fi
echo "[pd-test] ✓ Prefill Server started successfully"
echo ""

# Step 3: Start Decode Server in Docker
echo "[pd-test] =========================================="
echo "[pd-test] Step 3: Starting Decode Server (GPUs 4-7)..."
echo "[pd-test] =========================================="

"${DOCKER_CMD[@]}" run -d --name sglang-pd-decode \
  --network=host --ipc=host --cap-add=SYS_PTRACE \
  --device=/dev/kfd --device=/dev/dri --security-opt seccomp=unconfined \
  --group-add video --privileged \
  -v /mnt/raid:/mnt/raid \
  -v /data2:/data2 \
  -e HIP_VISIBLE_DEVICES=4,5,6,7 \
  -e LD_LIBRARY_PATH=/opt/rocm/lib:/usr/local/lib \
  "${DOCKER_IMAGE}" \
  python3 -m sglang.launch_server \
    --model-path "${MODEL_PATH}" \
    --disaggregation-mode decode \
    --port 30026 \
    --disaggregation-ib-device lo \
    --host "${HOST_IP}" \
    --tp 4 \
    --trust-remote-code \
    --attention-backend triton \
  > "${LOG_DIR}/decode.log" 2>&1

echo "[pd-test] Decode Server container started"
echo "[pd-test] Waiting 60 seconds for decode server to initialize..."
sleep 60

# Check if decode container is still running
if ! "${DOCKER_CMD[@]}" ps | grep -q sglang-pd-decode; then
  echo "[pd-test] ERROR: Decode Server container stopped"
  "${DOCKER_CMD[@]}" logs sglang-pd-decode > "${LOG_DIR}/decode.log" 2>&1
  echo "[pd-test] Decode container exit code:"
  "${DOCKER_CMD[@]}" inspect sglang-pd-decode --format='{{.State.ExitCode}}' 2>/dev/null || echo "N/A"
  echo "[pd-test] Last 50 lines of decode log:"
  tail -50 "${LOG_DIR}/decode.log"
  echo "[pd-test] Checking for common errors:"
  grep -i "error\|exception\|failed\|traceback" "${LOG_DIR}/decode.log" | tail -20 || echo "No error keywords found"
  exit 1
fi
echo "[pd-test] ✓ Decode Server started successfully"
echo ""

# Step 4: Wait for all services to be healthy
echo "[pd-test] =========================================="
echo "[pd-test] Step 4: Waiting for services to be healthy..."
echo "[pd-test] =========================================="
echo "[pd-test] Waiting additional 30 seconds..."
sleep 30

# Check health endpoints for all services
echo "[pd-test] Checking health endpoints for all services..."
HEALTH_CHECK_PASSED=false
HEALTH_CHECK_MAX_ATTEMPTS=60  # Increased to account for CUDA graph capture
HEALTH_CHECK_SLEEP=5
PREFILL_READY=false
DECODE_READY=false
ROUTER_READY=false

for i in $(seq 1 ${HEALTH_CHECK_MAX_ATTEMPTS}); do
  echo "[pd-test] Attempt $i/${HEALTH_CHECK_MAX_ATTEMPTS}..."

  # Check prefill server health
  if [ "$PREFILL_READY" = false ]; then
    if curl -s -f "http://${HOST_IP}:30025/health" > /dev/null 2>&1; then
      echo "[pd-test]   ✓ Prefill server is healthy"
      PREFILL_READY=true
    else
      echo "[pd-test]   ⧗ Prefill server not ready yet..."
    fi
  fi

  # Check decode server health
  if [ "$DECODE_READY" = false ]; then
    if curl -s -f "http://${HOST_IP}:30026/health" > /dev/null 2>&1; then
      echo "[pd-test]   ✓ Decode server is healthy"
      DECODE_READY=true
    else
      echo "[pd-test]   ⧗ Decode server not ready yet..."
    fi
  fi

  # Check router health (only after backends are ready)
  if [ "$ROUTER_READY" = false ] && [ "$PREFILL_READY" = true ] && [ "$DECODE_READY" = true ]; then
    if curl -s -f "http://${HOST_IP}:30028/health" > /dev/null 2>&1; then
      echo "[pd-test]   ✓ Router is healthy"
      ROUTER_READY=true
    else
      echo "[pd-test]   ⧗ Router not ready yet..."
    fi
  fi

  # All services ready - perform functional test
  if [ "$PREFILL_READY" = true ] && [ "$DECODE_READY" = true ] && [ "$ROUTER_READY" = true ]; then
    echo "[pd-test] All services report healthy. Performing functional test..."

    # Try a simple completion request to verify the full pipeline works
    FUNCTIONAL_TEST=$(curl -s -X POST "http://${HOST_IP}:30028/v1/completions" \
      -H 'Content-Type: application/json' \
      -d "{\"model\": \"${COMPLETION_MODEL}\", \"prompt\": \"Test\", \"max_tokens\": 5, \"temperature\": 0.0}" 2>&1)

    # Check if we got a valid response (not a 422 error)
    if echo "$FUNCTIONAL_TEST" | grep -q '"choices"'; then
      echo "[pd-test] ✓ Functional test passed - all services fully operational!"
      HEALTH_CHECK_PASSED=true
      break
    elif echo "$FUNCTIONAL_TEST" | grep -q "422"; then
      echo "[pd-test]   ⧗ Functional test failed with 422 - backends still initializing (likely CUDA graph capture)..."
    else
      echo "[pd-test]   ⧗ Functional test failed - retrying..."
    fi
  fi

  if [ $i -eq ${HEALTH_CHECK_MAX_ATTEMPTS} ]; then
    echo "[pd-test] WARNING: Services may not be fully healthy yet"
    echo "[pd-test] Prefill ready: $PREFILL_READY, Decode ready: $DECODE_READY, Router ready: $ROUTER_READY"
    echo "[pd-test] Router log tail:"
    "${DOCKER_CMD[@]}" logs --tail 30 sglang-pd-router
    echo "[pd-test] Decode log tail:"
    "${DOCKER_CMD[@]}" logs --tail 30 sglang-pd-decode
  fi

  sleep ${HEALTH_CHECK_SLEEP}
done
echo ""

if [ "$HEALTH_CHECK_PASSED" = false ]; then
  echo "[pd-test] ERROR: Health check failed after ${HEALTH_CHECK_MAX_ATTEMPTS} attempts"
  echo "[pd-test] Collecting logs from Docker containers for debugging..."
  "${DOCKER_CMD[@]}" logs sglang-pd-router > "${LOG_DIR}/load_balance.log" 2>&1
  "${DOCKER_CMD[@]}" logs sglang-pd-prefill > "${LOG_DIR}/prefill.log" 2>&1
  "${DOCKER_CMD[@]}" logs sglang-pd-decode > "${LOG_DIR}/decode.log" 2>&1
  
  echo "[pd-test] =========================================="
  echo "[pd-test] Debug Information"
  echo "[pd-test] =========================================="
  echo "[pd-test] Container status:"
  "${DOCKER_CMD[@]}" ps -a | grep sglang-pd || echo "No containers found"
  echo ""
  echo "[pd-test] Router log errors:"
  grep -i "error\|exception\|failed" "${LOG_DIR}/load_balance.log" | tail -10 || echo "No errors found in router log"
  echo ""
  echo "[pd-test] Prefill log errors:"
  grep -i "error\|exception\|failed" "${LOG_DIR}/prefill.log" | tail -10 || echo "No errors found in prefill log"
  echo ""
  echo "[pd-test] Decode log errors:"
  grep -i "error\|exception\|failed" "${LOG_DIR}/decode.log" | tail -10 || echo "No errors found in decode log"
  echo ""
  echo "[pd-test] Logs saved to: ${LOG_DIR}"
  echo "[pd-test] Main execution log: ${MAIN_LOG}"
  echo "[pd-test] Check prefill.log and decode.log for startup errors"
  exit 1
fi

# Step 5: Run tests
echo "[pd-test] =========================================="
echo "[pd-test] Step 5: Running Tests..."
echo "[pd-test] =========================================="

TEST_EXIT_CODE=0

# Test 1: Health check
echo "[pd-test] Test 1: Health Check"
if curl -s "http://${HOST_IP}:30028/health" | tee "${LOG_DIR}/test_health.json"; then
  echo "[pd-test] ✓ Health check passed"
else
  echo "[pd-test] ✗ Health check failed"
  TEST_EXIT_CODE=1
fi
echo ""
echo ""

# Test 2: Model info
echo "[pd-test] Test 2: Model Info"
if curl -s "http://${HOST_IP}:30028/v1/models" | tee "${LOG_DIR}/test_models.json"; then
  echo "[pd-test] ✓ Model info passed"
else
  echo "[pd-test] ✗ Model info failed"
  TEST_EXIT_CODE=1
fi
echo ""
echo ""

# Test 3: Simple completion
echo "[pd-test] Test 3: Simple Completion"
if curl -s -X POST "http://${HOST_IP}:30028/v1/completions" \
  -H 'Content-Type: application/json' \
  -d "{
    \"model\": \"${COMPLETION_MODEL}\",
    \"prompt\": \"Hello, how are you?\",
    \"max_tokens\": 50,
    \"temperature\": 0.7
  }" | tee "${LOG_DIR}/test_completion_1.json"; then
  echo "[pd-test] ✓ Simple completion passed"
else
  echo "[pd-test] ✗ Simple completion failed"
  TEST_EXIT_CODE=1
fi
echo ""
echo ""

# Test 4: Code generation
echo "[pd-test] Test 4: Code Generation"
if curl -s -X POST "http://${HOST_IP}:30028/v1/completions" \
  -H 'Content-Type: application/json' \
  -d "{
    \"model\": \"${COMPLETION_MODEL}\",
    \"prompt\": \"Write a Python function to calculate fibonacci numbers:\\n\\ndef fibonacci(n):\",
    \"max_tokens\": 100,
    \"temperature\": 0.3
  }" | tee "${LOG_DIR}/test_completion_2.json"; then
  echo "[pd-test] ✓ Code generation passed"
else
  echo "[pd-test] ✗ Code generation failed"
  TEST_EXIT_CODE=1
fi
echo ""
echo ""

# # Test 5: Concurrent requests
# echo "[pd-test] Test 5: Concurrent Requests (Load Balancing)"
# CONCURRENT_FAILED=0
# for i in {1..5}; do
#   (
#     if curl -s -X POST "http://${HOST_IP}:30028/v1/completions" \
#       -H 'Content-Type: application/json' \
#       -d "{
#         \"model\": \"${MODEL_NAME}\",
#         \"prompt\": \"Count to $i:\",
#         \"max_tokens\": 30,
#         \"temperature\": 0.5
#       }" > "${LOG_DIR}/test_concurrent_${i}.json"; then
#       echo "[pd-test] Request $i completed successfully"
#     else
#       echo "[pd-test] Request $i failed"
#       exit 1
#     fi
#   ) &
# done
# wait || CONCURRENT_FAILED=1

# if [ $CONCURRENT_FAILED -eq 0 ]; then
#   echo "[pd-test] ✓ All concurrent requests completed"
# else
#   echo "[pd-test] ✗ Some concurrent requests failed"
#   TEST_EXIT_CODE=1
# fi
# echo ""

# Test 6: GSM8K Accuracy Test
echo "[pd-test] Test 6: GSM8K Accuracy Test"
echo "[pd-test] Running GSM8K benchmark (200 questions, parallel 32, 5-shot)..."
echo "[pd-test] Using official implementation (bench_gsm8k_pd.py based on sglang/benchmark/gsm8k/bench_sglang.py)"
echo "[pd-test] Note: Using parallel=32 instead of 128 to avoid PD timeout issues with long prompts"
GSM8K_START=$(date +%s)

# Use official implementation based on sglang/benchmark/gsm8k/bench_sglang.py
# Reduced parallelism from 128 to 32 to prevent PD disaggregation timeouts
# PD has more limited concurrent request capacity than monolithic serving
"${DOCKER_CMD[@]}" exec sglang-pd-router \
  python3 /mnt/raid/michael/sglang-ci/test/pd/bench_gsm8k_pd.py \
    --num-questions 200 \
    --parallel 32 \
    --num-shots 5 \
    --max-new-tokens 512 \
    --model "${COMPLETION_MODEL}" \
    --host "http://${HOST_IP}" \
    --port 30028 \
  > "${LOG_DIR}/test_gsm8k.log" 2>&1

GSM8K_EXIT_CODE=$?
GSM8K_END=$(date +%s)
GSM8K_DURATION=$((GSM8K_END - GSM8K_START))

if [ $GSM8K_EXIT_CODE -eq 0 ]; then
  # Extract accuracy from log
  GSM8K_ACCURACY=$(grep "Accuracy:" "${LOG_DIR}/test_gsm8k.log" | tail -1 | awk '{print $2}')
  if [ -n "$GSM8K_ACCURACY" ]; then
    echo "[pd-test] ✓ GSM8K test completed - Accuracy: ${GSM8K_ACCURACY} (Duration: ${GSM8K_DURATION}s)"
  else
    echo "[pd-test] ✓ GSM8K test completed (Duration: ${GSM8K_DURATION}s)"
    GSM8K_ACCURACY="N/A"
  fi
else
  echo "[pd-test] ✗ GSM8K test failed (exit code: ${GSM8K_EXIT_CODE}, Duration: ${GSM8K_DURATION}s)"
  TEST_EXIT_CODE=1
  GSM8K_ACCURACY="FAILED"
fi
echo ""

# Collect logs from Docker containers
echo "[pd-test] Collecting logs from Docker containers..."
"${DOCKER_CMD[@]}" logs sglang-pd-router > "${LOG_DIR}/load_balance.log" 2>&1
"${DOCKER_CMD[@]}" logs sglang-pd-prefill > "${LOG_DIR}/prefill.log" 2>&1
"${DOCKER_CMD[@]}" logs sglang-pd-decode > "${LOG_DIR}/decode.log" 2>&1

# Create summary with timing information
echo "[pd-test] =========================================="
echo "[pd-test] Creating Test Summary"
echo "[pd-test] =========================================="

# Calculate total test time
TEST_END_TIME=$(date +%s)
TOTAL_TEST_TIME=$((TEST_END_TIME - GSM8K_START))

cat > "${LOG_DIR}/test_summary.txt" << EOF
SGLang PD Disaggregation Test Summary
======================================

Docker Tag: ${DOCKER_TAG}
Hardware: ${HARDWARE}
Model: ${MODEL_NAME}
Model Path: ${MODEL_PATH}
Docker Image: ${DOCKER_IMAGE}

Configuration:
- IP Address: ${HOST_IP}
- Prefill Server: Port 30025, GPUs 0-3, TP=4
- Decode Server: Port 30026, GPUs 4-7, TP=4
- Load Balancer: Port 30028
- Network: Loopback (lo)
- Execution: Docker containers

Test Results:
- Health Check: $([ -f "${LOG_DIR}/test_health.json" ] && echo "PASS" || echo "FAIL")
- Model Info: $([ -f "${LOG_DIR}/test_models.json" ] && echo "PASS" || echo "FAIL")
- Simple Completion: $([ -f "${LOG_DIR}/test_completion_1.json" ] && echo "PASS" || echo "FAIL")
- Code Generation: $([ -f "${LOG_DIR}/test_completion_2.json" ] && echo "PASS" || echo "FAIL")
- Concurrent Requests: $([ -f "${LOG_DIR}/test_concurrent_5.json" ] && echo "PASS" || echo "FAIL")
- GSM8K Accuracy: ${GSM8K_ACCURACY:-N/A}

Timing Summary:
- GSM8K Test Duration: ${GSM8K_DURATION}s
- GSM8K Questions per Second: $(awk "BEGIN {printf \"%.2f\", 200/${GSM8K_DURATION}}")
- GSM8K Parallelism: 32 concurrent requests
- Total Test Time: ${TOTAL_TEST_TIME}s

Log Files:
- Load Balancer: ${LOG_DIR}/load_balance.log
- Prefill Server: ${LOG_DIR}/prefill.log
- Decode Server: ${LOG_DIR}/decode.log
- Test Results: ${LOG_DIR}/test_*.json
- GSM8K Results: ${LOG_DIR}/test_gsm8k.log

Test completed in nightly mode (Docker-based).
EOF

cat "${LOG_DIR}/test_summary.txt"
echo ""

echo "[pd-test] =========================================="
echo "[pd-test] Tests complete! Logs saved to: ${LOG_DIR}"
echo "[pd-test] Main execution log: ${MAIN_LOG}"
echo "[pd-test] =========================================="

# Print summary of test results
if [ ${TEST_EXIT_CODE} -eq 0 ]; then
  echo "[pd-test] ✓ All tests PASSED"
else
  echo "[pd-test] ✗ Some tests FAILED (exit code: ${TEST_EXIT_CODE})"
  echo "[pd-test] Check individual test logs for details"
fi

# Cleanup will be called by trap
exit ${TEST_EXIT_CODE}
