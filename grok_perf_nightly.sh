#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# grok_perf_nightly.sh
#   • Pull rocm/sgl-dev:$YYYYMMDD (today PST; fallback yesterday).
#   • Ensure container  sgl-dev_$TAG  is up with proper mounts.
#   • Invoke chosen benchmark script (offline or online) inside it,
#     forwarding --docker_image so the script knows which backend to use.
#
# USAGE:
#   bash grok_perf_nightly.sh                 # default offline
#   bash grok_perf_nightly.sh --mode=online   # run online benchmark
# ---------------------------------------------------------------------------
set -euo pipefail

###############################################################################
# 0. Parse CLI flags
###############################################################################
MODE="offline"        # default
for arg in "$@"; do
  case $arg in
    --mode=*)
      MODE="${arg#*=}"
      shift ;;
  esac
done
[[ "$MODE" =~ ^(offline|online)$ ]] || {
  echo "[nightly] ERROR: --mode must be 'offline' or 'online'"; exit 1; }

IMAGE_REPO="rocm/sgl-dev"
echo "[nightly] === Starting nightly Grok-1 ${MODE} benchmark ==="

###############################################################################
# 1. Pick image tag (PST date)
###############################################################################
date_pst() { TZ=America/Los_Angeles date -d "-$1 day" +%Y%m%d; }

SELECTED_TAG=""
for offset in 0 1; do
  tag=$(date_pst "$offset")
  echo "[nightly] Trying ${IMAGE_REPO}:${tag} ..."
  if docker pull "${IMAGE_REPO}:${tag}" >/dev/null 2>&1; then
    SELECTED_TAG="$tag"; break
  fi
done
[[ -n "$SELECTED_TAG" ]] || { echo "[nightly] No nightly image found"; exit 1; }

DOCKER_IMAGE="${IMAGE_REPO}:${SELECTED_TAG}"
CONTAINER_NAME="sgl-dev_${SELECTED_TAG}"

###############################################################################
# 2. Ensure container is running
###############################################################################
if docker ps -a --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"; then
  echo "[nightly] Reusing container ${CONTAINER_NAME}"
  docker start "${CONTAINER_NAME}" >/dev/null || true
else
  echo "[nightly] Creating container ${CONTAINER_NAME}"
  docker run -d --name "${CONTAINER_NAME}" \
    --shm-size 32g --ipc=host --cap-add=SYS_PTRACE --network=host \
    --device=/dev/kfd --device=/dev/dri --security-opt seccomp=unconfined \
    -v /mnt/raid/:/mnt/raid/ --group-add video --privileged \
    -w /sgl-workspace "${DOCKER_IMAGE}" tail -f /dev/null
fi

###############################################################################
# 3. Determine which benchmark script to run
###############################################################################
if [ "$MODE" == "offline" ]; then
  SCRIPT="/mnt/raid/michael/sgl_benchmark_ci/grok_perf_offline_csv.sh"
else
  SCRIPT="/mnt/raid/michael/sgl_benchmark_ci/grok_perf_online_csv.sh"
fi
echo "[nightly] Launching $(basename "$SCRIPT") inside ${CONTAINER_NAME}"

docker exec \
  -e INSIDE_CONTAINER=1 \
  -e LATEST_TAG="${SELECTED_TAG}" \
  "${CONTAINER_NAME}" \
  bash "$SCRIPT" --docker_image="${DOCKER_IMAGE}"

echo "[nightly] === ${MODE^} benchmark dispatched; check logs in ${CONTAINER_NAME} ==="
