#!/usr/bin/env bash
# Helper script to build SGLang Docker images from source

set -euo pipefail

# Default values
ROCM_VERSION="${ROCM_VERSION:-6.3.0}"
PYTHON_VERSION="${PYTHON_VERSION:-3.10}"
SGLANG_REPO="${SGLANG_REPO:-https://github.com/sgl-project/sglang.git}"
BRANCH="${BRANCH:-main}"
IMAGE_PREFIX="${IMAGE_PREFIX:-my-sglang}"

# Parse command line arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --rocm-version=*)
            ROCM_VERSION="${1#*=}"
            shift
            ;;
        --python-version=*)
            PYTHON_VERSION="${1#*=}"
            shift
            ;;
        --branch=*)
            BRANCH="${1#*=}"
            shift
            ;;
        --repo=*)
            SGLANG_REPO="${1#*=}"
            shift
            ;;
        --image-prefix=*)
            IMAGE_PREFIX="${1#*=}"
            shift
            ;;
        --help)
            echo "Usage: $0 [options]"
            echo "Options:"
            echo "  --rocm-version=VERSION   ROCm version (default: 6.3.0)"
            echo "  --python-version=VERSION Python version (default: 3.10)"
            echo "  --branch=BRANCH         Git branch/tag/commit (default: main)"
            echo "  --repo=URL              SGLang repository URL (default: https://github.com/sgl-project/sglang.git)"
            echo "  --image-prefix=PREFIX   Docker image name prefix (default: my-sglang)"
            echo "  --help                  Show this help message"
            exit 0
            ;;
        *)
            echo "Unknown option: $1"
            exit 1
            ;;
    esac
done

# Create temporary directory for build
BUILD_DIR=$(mktemp -d)
echo "Building in temporary directory: $BUILD_DIR"

# Clone repository
echo "Cloning SGLang repository from $SGLANG_REPO (branch: $BRANCH)..."
cd "$BUILD_DIR"
git clone --depth 1 --branch "$BRANCH" "$SGLANG_REPO" sglang || {
    # If branch doesn't exist, try as commit hash
    git clone "$SGLANG_REPO" sglang
    cd sglang
    git checkout "$BRANCH"
}
cd sglang

# Get short commit hash for tagging
COMMIT_HASH=$(git rev-parse --short HEAD)
BRANCH_NAME=$(git rev-parse --abbrev-ref HEAD | sed 's/[^a-zA-Z0-9-]/-/g')

# Build Docker image
IMAGE_TAG="${IMAGE_PREFIX}:${BRANCH_NAME}-${COMMIT_HASH}-rocm${ROCM_VERSION//./}"
echo "Building Docker image: $IMAGE_TAG"

docker build \
    -t "$IMAGE_TAG" \
    -f docker/Dockerfile.rocm \
    --build-arg ROCM_VERSION="$ROCM_VERSION" \
    --build-arg PYTHON_VERSION="$PYTHON_VERSION" \
    .

# Also tag as latest for this branch
docker tag "$IMAGE_TAG" "${IMAGE_PREFIX}:${BRANCH_NAME}-latest"

# Clean up
cd /
rm -rf "$BUILD_DIR"

echo ""
echo "Successfully built Docker image: $IMAGE_TAG"
echo "Also tagged as: ${IMAGE_PREFIX}:${BRANCH_NAME}-latest"
echo ""
echo "To use with benchmark scripts:"
echo "  bash grok_perf_offline_csv.sh --docker_image=$IMAGE_TAG"
echo "  bash grok_perf_online_csv.sh --docker_image=$IMAGE_TAG" 