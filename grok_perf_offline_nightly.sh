#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# grok_perf_offline_nightly.sh
#   • Pull today’s (PST) rocm/sgl-dev:$YYYYMMDD nightly image, or fall back
#     to yesterday’s.
#   • Ensure a long-running container named sgl-dev_$TAG.
#   • Launch grok_perf_offline_csv.sh inside that container, passing
#     --docker_image so it can pick the correct backend automatically.
# ---------------------------------------------------------------------------
set -euo pipefail

IMAGE_REPO="rocm/sgl-dev"

echo "[offline-nightly] === Starting nightly Grok-1 offline benchmark ==="

# -------------------------------------------------------------
# 1. Determine the image tag (today PST, else yesterday)
# -------------------------------------------------------------
date_pst() { TZ=America/Los_Angeles date -d "-$1 day" +%Y%m%d; }

SELECTED_TAG=""
for offset in 0 1; do
  tag=$(date_pst "$offset")
  echo "[offline-nightly] Trying to pull ${IMAGE_REPO}:${tag} ..."
  if docker pull "${IMAGE_REPO}:${tag}" >/dev/null 2>&1; then
    echo "[offline-nightly] Found nightly image: ${IMAGE_REPO}:${tag}"
    SELECTED_TAG="$tag"
    break
  else
    echo "[offline-nightly] Image for ${tag} not found."
  fi
done
[[ -n "$SELECTED_TAG" ]] || { echo "[offline-nightly] ERROR: No nightly image found"; exit 1; }

DOCKER_IMAGE="${IMAGE_REPO}:${SELECTED_TAG}"
CONTAINER_NAME="sgl-dev_${SELECTED_TAG}"

# -------------------------------------------------------------
# 2. Start or resume the container
# -------------------------------------------------------------
if docker ps -a --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"; then
  echo "[offline-nightly] Reusing existing container ${CONTAINER_NAME}"
  docker start "${CONTAINER_NAME}" >/dev/null || true
else
  echo "[offline-nightly] Creating container ${CONTAINER_NAME}"
  docker run -d --name "${CONTAINER_NAME}" \
    --shm-size 32g --ipc=host --cap-add=SYS_PTRACE --network=host \
    --device=/dev/kfd --device=/dev/dri --security-opt seccomp=unconfined \
    -v /mnt/raid/:/mnt/raid/ --group-add video --privileged \
    -w /sgl-workspace "${DOCKER_IMAGE}" tail -f /dev/null
fi

# -------------------------------------------------------------
# 3. Invoke the offline benchmark script
# -------------------------------------------------------------
echo "[offline-nightly] Launching grok_perf_offline_csv.sh inside ${CONTAINER_NAME}"
docker exec \
  -e INSIDE_CONTAINER=1 \
  -e LATEST_TAG="${SELECTED_TAG}" \
  "${CONTAINER_NAME}" \
  bash /mnt/raid/michael/sgl_benchmark_ci/grok_perf_offline_csv.sh \
       --docker_image="${DOCKER_IMAGE}"

echo "[offline-nightly] === Benchmark job dispatched; check container logs for progress ==="
