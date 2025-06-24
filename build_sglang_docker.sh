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

# Handle PR refs and checkout
ACTUAL_BRANCH="$SGL_BRANCH"
if [ "$SGL_BRANCH" != "main" ]; then
    echo "Checking out branch: $SGL_BRANCH"

    # Handle pull request refs specially
    if [[ "$SGL_BRANCH" =~ ^pull/([0-9]+)/merge$ ]]; then
        PR_NUMBER="${BASH_REMATCH[1]}"
        echo "Fetching pull request #$PR_NUMBER..."
        git fetch origin "pull/$PR_NUMBER/head:pr-$PR_NUMBER"
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
        git checkout "$SGL_BRANCH"
    fi
fi

# Get the actual commit hash that we're building
COMMIT_HASH=$(git rev-parse --short HEAD)
FULL_COMMIT_HASH=$(git rev-parse HEAD)

echo "Building from commit: $FULL_COMMIT_HASH"

# Determine ROCm version from base image name
if [[ "$BASE_IMAGE" =~ rocm([0-9]+) ]]; then
    ROCM_VERSION="${BASH_REMATCH[1]}"
else
    ROCM_VERSION="630"  # default
fi

# Build Docker image with tag format
if [ "$SGL_BRANCH" = "main" ]; then
    IMAGE_TAG="${SGL_BRANCH}-${COMMIT_HASH}-rocm${ROCM_VERSION}"
elif [[ "$SGL_BRANCH" =~ ^pull/([0-9]+)/merge$ ]]; then
    # For pull requests, use pr-NUMBER format
    PR_NUMBER="${BASH_REMATCH[1]}"
    IMAGE_TAG="pr-${PR_NUMBER}-${COMMIT_HASH}-rocm${ROCM_VERSION}"
else
    # For version tags like v0.4.7, use the simpler format
    IMAGE_TAG="${SGL_BRANCH}-rocm${ROCM_VERSION}"
fi

echo "Building Docker image: $IMAGE_TAG"
echo "Using base image: $BASE_IMAGE"
echo "Build type: $BUILD_TYPE"

# Create a temporary Dockerfile that clones at the specific commit
cat > Dockerfile.tmp <<EOF
# Usage (to build SGLang ROCm docker image):
# docker build -t sglang-pr -f Dockerfile.tmp .

FROM ${BASE_IMAGE} AS base
USER root

WORKDIR /sgl-workspace
ARG BUILD_TYPE=${BUILD_TYPE}
ARG SGL_REPO=${SGLANG_REPO}
ARG SGL_COMMIT=${FULL_COMMIT_HASH}

# Environment variables from original Dockerfile
ENV SGL_DEFAULT="main"

# Triton and Aiter repositories (from original Dockerfile)
ARG TRITON_REPO="https://github.com/ROCm/triton.git"
ARG TRITON_COMMIT="improve_fa_decode_3.0.0"
ARG AITER_REPO="https://github.com/ROCm/aiter.git"
ARG AITER_COMMIT="v0.1.3"

# Clone and build SGLang at specific commit
RUN git clone \${SGL_REPO} sglang && \\
    cd sglang && \\
    git checkout \${SGL_COMMIT} && \\
    cd sgl-kernel && \\
    rm -f pyproject.toml && \\
    mv pyproject_rocm.toml pyproject.toml && \\
    python setup_rocm.py install && \\
    cd .. && \\
    if [ "\${BUILD_TYPE}" = "srt" ]; then \\
        python -m pip --no-cache-dir install -e "python[srt_hip]"; \\
    else \\
        python -m pip --no-cache-dir install -e "python[all_hip]"; \\
    fi

RUN cp -r /sgl-workspace/sglang /sglang
RUN python -m pip cache purge

# Install additional dependencies from original Dockerfile
RUN pip install IPython \\
    && pip install orjson \\
    && pip install python-multipart \\
    && pip install torchao \\
    && pip install pybind11

# Install Triton
RUN pip install ninja cmake wheel pybind11 && \\
    cd /opt && \\
    git clone \${TRITON_REPO} && \\
    cd triton && \\
    git checkout \${TRITON_COMMIT} && \\
    cd python && \\
    pip install . && \\
    cd /opt && \\
    rm -rf triton

# Install Aiter
RUN cd /opt && \\
    git clone \${AITER_REPO} && \\
    cd aiter && \\
    git checkout \${AITER_COMMIT} && \\
    pip install .

# Environment configurations from original
RUN find /sgl-workspace/sglang/python/sglang/srt/layers/quantization/configs/ \\
         /sgl-workspace/sglang/python/sglang/srt/layers/moe/fused_moe_triton/configs/ \\
         -type f -name '*MI300X*' | xargs -I {} sh -c 'vf_config=\$(echo "\$1" | sed "s/MI300X/MI300X_VF/"); cp "\$1" "\$vf_config"' -- {}

ENV HIP_FORCE_DEV_KERNARG=1
ENV HSA_NO_SCRATCH_RECLAIM=1
ENV SGLANG_SET_CPU_AFFINITY=1
ENV SGLANG_ALLOW_OVERWRITE_LONGER_CONTEXT_LEN=1
ENV NCCL_MIN_NCHANNELS=112
ENV SGLANG_MOE_PADDING=1
ENV VLLM_FP8_PADDING=1
ENV VLLM_FP8_ACT_PADDING=1
ENV VLLM_FP8_WEIGHT_PADDING=1
ENV VLLM_FP8_REDUCE_CONV=1
ENV TORCHINDUCTOR_MAX_AUTOTUNE=1
ENV TORCHINDUCTOR_MAX_AUTOTUNE_POINTWISE=1

WORKDIR /sglang

CMD ["/bin/bash"]
EOF

# Build using our temporary Dockerfile
docker build \
    -t "$IMAGE_TAG" \
    -f Dockerfile.tmp \
    --build-arg SGL_REPO="$SGLANG_REPO" \
    --build-arg SGL_COMMIT="$FULL_COMMIT_HASH" \
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
