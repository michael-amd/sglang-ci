#!/usr/bin/env bash
# Helper script to build SGLang Docker images from source

set -euo pipefail

# Default values
SGLANG_REPO="${SGLANG_REPO:-https://github.com/sgl-project/sglang.git}"
SGL_BRANCH="${SGL_BRANCH:-main}"
BASE_IMAGE="${BASE_IMAGE:-rocm/sgl-dev:vllm20250114}"
BUILD_TYPE="${BUILD_TYPE:-all}"

# Parse command line arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --branch=*)
            SGL_BRANCH="${1#*=}"
            shift
            ;;
        --repo=*)
            SGLANG_REPO="${1#*=}"
            shift
            ;;
        --base-image=*)
            BASE_IMAGE="${1#*=}"
            shift
            ;;
        --build-type=*)
            BUILD_TYPE="${1#*=}"
            shift
            ;;
        --help)
            echo "Usage: $0 [options]"
            echo "Options:"
            echo "  --branch=BRANCH         Git branch/tag/commit (default: main)"
            echo "  --repo=URL              SGLang repository URL (default: https://github.com/sgl-project/sglang.git)"
            echo "  --base-image=IMAGE      Base Docker image (default: rocm/sgl-dev:vllm20250114)"
            echo "  --build-type=TYPE       Build type: all or srt (default: all)"
            echo "  --help                  Show this help message"
            echo ""
            echo "Examples:"
            echo "  $0 --branch=v0.4.7"
            echo "  $0 --branch=main --base-image=rocm/sgl-dev:vllm20250114"
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
echo "Cloning SGLang repository from $SGLANG_REPO..."
cd "$BUILD_DIR"
git clone "$SGLANG_REPO" sglang
cd sglang

# Checkout branch if specified
if [ "$SGL_BRANCH" != "main" ]; then
    echo "Checking out branch: $SGL_BRANCH"
    git checkout "$SGL_BRANCH"
fi

# Get short commit hash for tagging
COMMIT_HASH=$(git rev-parse --short HEAD)

# Determine ROCm version from base image name
if [[ "$BASE_IMAGE" =~ rocm([0-9]+) ]]; then
    ROCM_VERSION="${BASH_REMATCH[1]}"
else
    ROCM_VERSION="630"  # default
fi

# Build Docker image with tag format matching Dockerfile.rocm example
if [ "$SGL_BRANCH" = "main" ]; then
    IMAGE_TAG="${SGL_BRANCH}-${COMMIT_HASH}-rocm${ROCM_VERSION}"
else
    # For version tags like v0.4.7, use the simpler format
    IMAGE_TAG="${SGL_BRANCH}-rocm${ROCM_VERSION}"
fi

echo "Building Docker image: $IMAGE_TAG"
echo "Using base image: $BASE_IMAGE"
echo "Build type: $BUILD_TYPE"

docker build \
    -t "$IMAGE_TAG" \
    -f docker/Dockerfile.rocm \
    --build-arg BASE_IMAGE="$BASE_IMAGE" \
    --build-arg SGL_BRANCH="$SGL_BRANCH" \
    --build-arg SGL_REPO="$SGLANG_REPO" \
    --build-arg BUILD_TYPE="$BUILD_TYPE" \
    .

# Clean up
cd /
rm -rf "$BUILD_DIR"

echo ""
echo "Successfully built Docker image: $IMAGE_TAG"
echo ""
echo "To use with benchmark scripts:"
echo "  bash grok_perf_offline_csv.sh --docker_image=$IMAGE_TAG"
echo "  bash grok_perf_online_csv.sh --docker_image=$IMAGE_TAG"
