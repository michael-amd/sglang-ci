#!/usr/bin/env bash
# Helper script to build SGLang Docker images using existing Dockerfile.rocm

set -euo pipefail

# Default values
SGLANG_DOCKERFILE_PATH="${SGLANG_DOCKERFILE_PATH:-/mnt/raid/michael/sgl-project/sglang/docker/Dockerfile.rocm}"
SGL_BRANCH="${SGL_BRANCH:-main}"
BASE_IMAGE="${BASE_IMAGE:-rocm/sgl-dev:vllm20250114}"
BUILD_TYPE="${BUILD_TYPE:-all}"
PULL_LATEST="${PULL_LATEST:-true}"

# Parse command line arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --dockerfile-path=*)
            SGLANG_DOCKERFILE_PATH="${1#*=}"
            shift
            ;;
        --branch=*)
            SGL_BRANCH="${1#*=}"
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
        --no-pull)
            PULL_LATEST="false"
            shift
            ;;
        --help)
            echo "Usage: $0 [options]"
            echo "Options:"
            echo "  --dockerfile-path=PATH  Path to Dockerfile.rocm (default: /mnt/raid/michael/sgl-project/sglang/docker/Dockerfile.rocm)"
            echo "  --branch=BRANCH         Git branch/tag/commit/PR (default: main)"
            echo "  --base-image=IMAGE      Base Docker image (default: rocm/sgl-dev:vllm20250114)"
            echo "  --build-type=TYPE       Build type: all or srt (default: all)"
            echo "  --no-pull               Skip git pull (default: pull latest)"
            echo "  --help                  Show this help message"
            echo ""
            echo "Examples:"
            echo "  $0 --branch=v0.4.7"
            echo "  $0 --branch=main --base-image=rocm/sgl-dev:vllm20250114"
            echo "  $0 --branch=pull/1234/merge"
            echo "  $0 --dockerfile-path=/path/to/Dockerfile.rocm --branch=commit_hash"
            exit 0
            ;;
        *)
            echo "Unknown option: $1"
            exit 1
            ;;
    esac
done

# Validate dockerfile path
if [ ! -f "$SGLANG_DOCKERFILE_PATH" ]; then
    echo "Error: Dockerfile not found at $SGLANG_DOCKERFILE_PATH"
    exit 1
fi

# Get the directory containing the dockerfile
DOCKERFILE_DIR=$(dirname "$SGLANG_DOCKERFILE_PATH")
DOCKERFILE_NAME=$(basename "$SGLANG_DOCKERFILE_PATH")

# Navigate to the sglang project root (assuming dockerfile is in docker/ subdirectory)
SGLANG_PROJECT_ROOT=$(dirname "$DOCKERFILE_DIR")

echo "Using dockerfile: $SGLANG_DOCKERFILE_PATH"
echo "SGLang project root: $SGLANG_PROJECT_ROOT"

# Check if we're in a git repository
if [ ! -d "$SGLANG_PROJECT_ROOT/.git" ]; then
    echo "Error: $SGLANG_PROJECT_ROOT is not a git repository"
    exit 1
fi

cd "$SGLANG_PROJECT_ROOT"

# Pull latest code if requested
if [ "$PULL_LATEST" = "true" ]; then
    echo "Pulling latest code..."
    git fetch origin
fi

# Handle branch checkout and PR refs
ACTUAL_BRANCH="$SGL_BRANCH"
ORIGINAL_BRANCH=$(git rev-parse --abbrev-ref HEAD)

echo "Current branch: $ORIGINAL_BRANCH"
echo "Target branch/ref: $SGL_BRANCH"

if [ "$SGL_BRANCH" != "main" ]; then
    echo "Checking out branch: $SGL_BRANCH"

    # Handle pull request refs specially
    if [[ "$SGL_BRANCH" =~ ^pull/([0-9]+)/merge$ ]]; then
        PR_NUMBER="${BASH_REMATCH[1]}"
        echo "Fetching pull request #$PR_NUMBER..."
        git fetch origin "pull/$PR_NUMBER/head:pr-$PR_NUMBER" || {
            echo "Error: Could not fetch PR #$PR_NUMBER"
            exit 1
        }
        # Try to fetch the merge ref if available
        git fetch origin "pull/$PR_NUMBER/merge:pr-$PR_NUMBER-merge" 2>/dev/null || {
            echo "Could not fetch merge ref, using PR head instead"
            git checkout "pr-$PR_NUMBER"
            ACTUAL_BRANCH="pr-$PR_NUMBER"
        }
        if git rev-parse "pr-$PR_NUMBER-merge" >/dev/null 2>&1; then
            git checkout "pr-$PR_NUMBER-merge"
            ACTUAL_BRANCH="pr-$PR_NUMBER-merge"
        fi
    else
        # Handle regular branches, tags, or commit hashes
        if git rev-parse --verify "$SGL_BRANCH" >/dev/null 2>&1; then
            git checkout "$SGL_BRANCH"
        elif git rev-parse --verify "origin/$SGL_BRANCH" >/dev/null 2>&1; then
            git checkout -b "$SGL_BRANCH" "origin/$SGL_BRANCH"
        else
            echo "Error: Branch/tag/commit '$SGL_BRANCH' not found"
            exit 1
        fi
    fi
else
    # For main branch, ensure we're up to date if pulling
    if [ "$PULL_LATEST" = "true" ]; then
        git checkout main
        git pull origin main
    fi
fi

# Get the actual commit hash that we're building
COMMIT_HASH=$(git rev-parse --short HEAD)
FULL_COMMIT_HASH=$(git rev-parse HEAD)

echo "Building from commit: $FULL_COMMIT_HASH"

# Extract PR number if building from a PR
PR_NUMBER=""
if [[ "$SGL_BRANCH" =~ ^pull/([0-9]+)/merge$ ]]; then
    PR_NUMBER="${BASH_REMATCH[1]}"
fi

# Determine ROCm version from base image name
if [[ "$BASE_IMAGE" =~ rocm([0-9]+) ]]; then
    ROCM_VERSION="${BASH_REMATCH[1]}"
else
    ROCM_VERSION="630"  # default
fi

# Extract image name from Dockerfile usage comment and append '-build'
USAGE_EXAMPLE=$(grep -A 1 "# Usage" "$SGLANG_DOCKERFILE_PATH" | tail -1)
BASE_TAG_FROM_DOCKERFILE=$(echo "$USAGE_EXAMPLE" | sed -n 's/.*-t \([^ ]*\).*/\1/p')

if [ -z "$BASE_TAG_FROM_DOCKERFILE" ]; then
    echo "Error: Could not extract image name from Dockerfile."
    echo "Expected a line like: #   docker build ... -t <image_name> ..."
    exit 1
fi

IMAGE_TAG="${BASE_TAG_FROM_DOCKERFILE}-build"

echo ""
echo "Building Docker image: $IMAGE_TAG"
echo "Using base image: $BASE_IMAGE"
echo "Build type: $BUILD_TYPE"
echo "Using dockerfile: $DOCKERFILE_NAME"

# Extract usage command from dockerfile header (for reference)
USAGE_EXAMPLE=$(grep -A 1 "# Usage" "$SGLANG_DOCKERFILE_PATH" | tail -1 | sed 's/^#\s*//')
echo "Dockerfile usage example: $USAGE_EXAMPLE"

# Navigate to dockerfile directory for build context
cd "$DOCKERFILE_DIR"

# Create a temporary modified dockerfile
TEMP_DOCKERFILE="${DOCKERFILE_NAME}.temp"
echo "Creating temporary modified dockerfile: $TEMP_DOCKERFILE"

# Copy the original dockerfile and modify line 62 to fix aiter installation
cp "$DOCKERFILE_NAME" "$TEMP_DOCKERFILE"

# Replace the problematic PREBUILD_KERNELS line with pip install
sed -i 's/.*PREBUILD_KERNELS=1 GPU_ARCHS=gfx942 python3 setup.py develop.*/    \&\& pip install ./' "$TEMP_DOCKERFILE"

echo "Modified aiter installation line to use 'pip install .' instead of PREBUILD_KERNELS"

# Build using the modified Dockerfile
docker build \
    --build-arg BASE_IMAGE="$BASE_IMAGE" \
    --build-arg SGL_BRANCH="$SGL_BRANCH" \
    --build-arg BUILD_TYPE="$BUILD_TYPE" \
    -t "$IMAGE_TAG" \
    -f "$TEMP_DOCKERFILE" \
    .

# Clean up the temporary dockerfile
rm -f "$TEMP_DOCKERFILE"
echo "Cleaned up temporary dockerfile"

# Return to original branch if we switched
if [ "$ACTUAL_BRANCH" != "$ORIGINAL_BRANCH" ] && [ "$ORIGINAL_BRANCH" != "HEAD" ]; then
    echo "Returning to original branch: $ORIGINAL_BRANCH"
    cd "$SGLANG_PROJECT_ROOT"
    git checkout "$ORIGINAL_BRANCH"
fi

echo ""
echo "Successfully built Docker image: $IMAGE_TAG"
echo ""
echo "To use with benchmark scripts:"
echo "  bash grok_perf_offline_csv.sh --docker_image=$IMAGE_TAG"
echo "  bash grok_perf_online_csv.sh --docker_image=$IMAGE_TAG"
echo ""
echo "To run the container:"
echo "  docker run -it --rm $IMAGE_TAG"
