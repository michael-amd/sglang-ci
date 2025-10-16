#!/bin/bash

# PD Nightly Test Runner with Docker Support
# This script runs PD disaggregation tests using Docker containers

set -euo pipefail

# Configuration
MODEL_PATH="${1:-/mnt/raid/models/huggingface/lmsys/gpt-oss-20b-bf16}"
MODEL_NAME="${2:-GPT-OSS-20B}"
DOCKER_IMAGE="${DOCKER_IMAGE:-rocm/sgl-dev:v0.5.3.post1-rocm700-mi30x-20251014}"
TEST_TIMEOUT="${TEST_TIMEOUT:-300}"
TEST_DIR="/mnt/raid/michael/sglang-ci/test/pd"
HOST_IP="10.194.129.138"

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
  echo ""
  echo "[pd-test] =========================================="
  echo "[pd-test] Cleaning up Docker containers..."
  echo "[pd-test] =========================================="
  "${DOCKER_CMD[@]}" stop sglang-pd-router 2>/dev/null || true
  "${DOCKER_CMD[@]}" stop sglang-pd-prefill 2>/dev/null || true
  "${DOCKER_CMD[@]}" stop sglang-pd-decode 2>/dev/null || true
  "${DOCKER_CMD[@]}" rm sglang-pd-router 2>/dev/null || true
  "${DOCKER_CMD[@]}" rm sglang-pd-prefill 2>/dev/null || true
  "${DOCKER_CMD[@]}" rm sglang-pd-decode 2>/dev/null || true
  sleep 3
  echo "[pd-test] Cleanup complete."
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
  tail -50 "${LOG_DIR}/prefill.log"
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
    --mem-fraction-static 0.75 \
    --attention-backend triton \
  > "${LOG_DIR}/decode.log" 2>&1

echo "[pd-test] Decode Server container started"
echo "[pd-test] Waiting 60 seconds for decode server to initialize..."
sleep 60

# Check if decode container is still running
if ! "${DOCKER_CMD[@]}" ps | grep -q sglang-pd-decode; then
  echo "[pd-test] ERROR: Decode Server container stopped"
  "${DOCKER_CMD[@]}" logs sglang-pd-decode > "${LOG_DIR}/decode.log" 2>&1
  tail -50 "${LOG_DIR}/decode.log"
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

# Check health endpoints
echo "[pd-test] Checking health endpoints..."
HEALTH_CHECK_PASSED=false
for i in {1..10}; do
  echo "[pd-test] Attempt $i/10..."

  if curl -s -f "http://${HOST_IP}:30028/health" > /dev/null 2>&1; then
    echo "[pd-test] ✓ All services are healthy!"
    HEALTH_CHECK_PASSED=true
    break
  fi

  if [ $i -eq 10 ]; then
    echo "[pd-test] WARNING: Services may not be fully healthy yet"
    echo "[pd-test] Router log tail:"
    "${DOCKER_CMD[@]}" logs --tail 20 sglang-pd-router
  fi

  sleep 5
done
echo ""

if [ "$HEALTH_CHECK_PASSED" = false ]; then
  echo "[pd-test] ERROR: Health check failed after 10 attempts"
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
    \"model\": \"${MODEL_NAME}\",
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
    \"model\": \"${MODEL_NAME}\",
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

# Test 5: Concurrent requests
echo "[pd-test] Test 5: Concurrent Requests (Load Balancing)"
CONCURRENT_FAILED=0
for i in {1..5}; do
  (
    if curl -s -X POST "http://${HOST_IP}:30028/v1/completions" \
      -H 'Content-Type: application/json' \
      -d "{
        \"model\": \"${MODEL_NAME}\",
        \"prompt\": \"Count to $i:\",
        \"max_tokens\": 30,
        \"temperature\": 0.5
      }" > "${LOG_DIR}/test_concurrent_${i}.json"; then
      echo "[pd-test] Request $i completed successfully"
    else
      echo "[pd-test] Request $i failed"
      exit 1
    fi
  ) &
done
wait || CONCURRENT_FAILED=1

if [ $CONCURRENT_FAILED -eq 0 ]; then
  echo "[pd-test] ✓ All concurrent requests completed"
else
  echo "[pd-test] ✗ Some concurrent requests failed"
  TEST_EXIT_CODE=1
fi
echo ""

# Test 6: GSM8K Accuracy Test
echo "[pd-test] Test 6: GSM8K Accuracy Test"
echo "[pd-test] Running GSM8K benchmark (2000 questions, parallel 2000)..."
GSM8K_START=$(date +%s)

"${DOCKER_CMD[@]}" exec sglang-pd-router \
  python3 /sgl-workspace/sglang/benchmark/gsm8k/bench_sglang.py \
    --num-questions 2000 \
    --parallel 2000 \
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

# Create summary
echo "[pd-test] =========================================="
echo "[pd-test] Creating Test Summary"
echo "[pd-test] =========================================="
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

Log Files:
- Load Balancer: ${LOG_DIR}/load_balance.log
- Prefill Server: ${LOG_DIR}/prefill.log
- Decode Server: ${LOG_DIR}/decode.log
- Test Results: ${LOG_DIR}/test_*.json

Test completed in nightly mode (Docker-based).
EOF

cat "${LOG_DIR}/test_summary.txt"
echo ""

# Generate timing summary
echo "[pd-test] =========================================="
echo "[pd-test] Generating timing summary..."
echo "[pd-test] =========================================="
TIMING_SUMMARY_SCRIPT="${TEST_DIR}/generate_timing_summary.py"
if [ -f "${TIMING_SUMMARY_SCRIPT}" ]; then
  sudo python3 "${TIMING_SUMMARY_SCRIPT}" --log-dir "${LOG_DIR}" 2>/dev/null || \
    python3 "${TIMING_SUMMARY_SCRIPT}" --log-dir "${LOG_DIR}"
  if [ -f "${LOG_DIR}/timing_summary.txt" ]; then
    echo "[pd-test] ✓ Timing summary generated: ${LOG_DIR}/timing_summary.txt"
  else
    echo "[pd-test] WARNING: Failed to generate timing summary"
  fi
else
  echo "[pd-test] WARNING: Timing summary script not found: ${TIMING_SUMMARY_SCRIPT}"
fi
echo ""

echo "[pd-test] =========================================="
echo "[pd-test] Tests complete! Logs saved to: ${LOG_DIR}"
echo "[pd-test] =========================================="

# Cleanup will be called by trap
exit ${TEST_EXIT_CODE}
