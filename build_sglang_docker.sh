#!/usr/bin/env bash
# Helper script to build SGLang Docker images using existing Dockerfile.rocm
#
# IMPORTANT: Please specify your SGLANG_DOCKERFILE_PATH using --dockerfile-path=<PATH>
# If this script doesn't work due to sglang changes, you can build directly

set -euo pipefail

SCRIPT_DIR=$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )

# Default values
SGLANG_DOCKERFILE_PATH="${SGLANG_DOCKERFILE_PATH:-/mnt/raid/michael/sgl-project/sglang/docker/Dockerfile.rocm}"
SGL_BRANCH="${SGL_BRANCH:-main}"
ROCM_VERSION="${ROCM_VERSION:-630}"
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
        --rocm-version=*)
            ROCM_VERSION="${1#*=}"
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
            echo "  --rocm-version=VERSION  ROCm version for tag (default: 630)"
            echo "  --build-type=TYPE       Build type: all or srt (default: all)"
            echo "  --no-pull               Skip git pull (default: pull latest)"
            echo "  --help                  Show this help message"
            echo ""
            echo "Examples:"
            echo "  $0 --branch=v0.4.7"
            echo "  $0 --branch=main"
            echo "  $0 --branch=pull/7559"
            echo "  $0 --dockerfile-path=/path/to/Dockerfile.rocm --branch=commit_hash"
            exit 0
            ;;
        *)
            echo "Unknown option: $1"
            exit 1
            ;;
    esac
done

# Setup logging and redirect all output
LOG_DIR="$SCRIPT_DIR/build_docker_log"
mkdir -p "$LOG_DIR"
# Sanitize branch name for filename
SANITIZED_SGL_BRANCH=$(echo "$SGL_BRANCH" | tr '/' '_')
LOG_FILE="$LOG_DIR/${SANITIZED_SGL_BRANCH}_$(TZ='America/Los_Angeles' date +'%Y%m%d_%H%M%S').log"
echo ""
echo "Logging all output to $LOG_FILE"

{
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
        if [[ "$SGL_BRANCH" =~ ^pull/([0-9]+)(/merge)?$ ]]; then
            PR_NUMBER="${BASH_REMATCH[1]}"
            echo "Handling pull request #$PR_NUMBER using GitHub API..."

            # Go to a neutral branch to avoid fetch conflicts
            git checkout main
            git pull origin main

            # Get repo owner/name from remote URL. Handles both https and ssh formats.
            GIT_REMOTE_URL=$(git remote get-url origin)
            REPO_OWNER_SLASH_NAME=$(echo "$GIT_REMOTE_URL" | sed -E 's/.*github.com[:/]//; s/\.git$//')

            if [ -z "$REPO_OWNER_SLASH_NAME" ]; then
                echo "Error: Could not determine GitHub repository from remote 'origin'."
                exit 1
            fi

            echo "Detected repository: $REPO_OWNER_SLASH_NAME"

            API_URL="https://api.github.com/repos/$REPO_OWNER_SLASH_NAME/pulls/$PR_NUMBER"
            API_RESPONSE_FILE=$(mktemp)

            # Use -f to fail silently on HTTP errors, which we check for.
            HTTP_STATUS=$(curl -s -L -w "%{http_code}" -o "$API_RESPONSE_FILE" "$API_URL")

            if [ "$HTTP_STATUS" -ne 200 ]; then
                echo "Error: Failed to get PR info from GitHub API (Status: $HTTP_STATUS). URL: $API_URL"
                cat "$API_RESPONSE_FILE"
                rm "$API_RESPONSE_FILE"
                exit 1
            fi

            # Check for python, prefer python3
            PYTHON_CMD=""
            if command -v python3 &> /dev/null; then
                PYTHON_CMD=python3
            elif command -v python &> /dev/null; then
                PYTHON_CMD=python
            else
                echo "Error: python is required to parse the GitHub API response."
                rm "$API_RESPONSE_FILE"
                exit 1
            fi

            IS_MERGED=$($PYTHON_CMD -c "import json, sys; data = json.load(sys.stdin); print(data.get('merged', False))" < "$API_RESPONSE_FILE")

            if [ "$IS_MERGED" = "True" ]; then
                MERGE_COMMIT_SHA=$($PYTHON_CMD -c "import json, sys; print(json.load(sys.stdin).get('merge_commit_sha'))" < "$API_RESPONSE_FILE")
                echo "PR #$PR_NUMBER is merged. Checking out merge commit: $MERGE_COMMIT_SHA"
                git checkout "$MERGE_COMMIT_SHA"
                ACTUAL_BRANCH="$MERGE_COMMIT_SHA"
            else
                HEAD_SHA=$($PYTHON_CMD -c "import json, sys; print(json.load(sys.stdin)['head']['sha'])" < "$API_RESPONSE_FILE")
                echo "PR #$PR_NUMBER is open. Fetching and checking out commit: $HEAD_SHA"
                git fetch origin "pull/$PR_NUMBER/head"
                git checkout "$HEAD_SHA"
                ACTUAL_BRANCH="$HEAD_SHA"
            fi
            rm "$API_RESPONSE_FILE"
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

    # When building a PR, use the specific commit hash for the build argument
    BUILD_SGL_BRANCH_ARG="$SGL_BRANCH"
    if [[ "$SGL_BRANCH" =~ ^pull/([0-9]+)(/merge)?$ ]]; then
        echo "Using commit hash $FULL_COMMIT_HASH for build arg SGL_BRANCH"
        BUILD_SGL_BRANCH_ARG="$FULL_COMMIT_HASH"
    fi

    # Extract PR number if building from a PR
    PR_NUMBER=""
    if [[ "$SGL_BRANCH" =~ ^pull/([0-9]+)(/merge)?$ ]]; then
        PR_NUMBER="${BASH_REMATCH[1]}"
    fi

    # Build Docker image with tag format
    if [ "$SGL_BRANCH" = "main" ]; then
        IMAGE_TAG="main-${COMMIT_HASH}-rocm${ROCM_VERSION}"
    elif [[ "$SGL_BRANCH" =~ ^pull/([0-9]+)(/merge)?$ ]]; then
        # For pull requests, use pr-NUMBER format
        IMAGE_TAG="pr-${PR_NUMBER}-${COMMIT_HASH}-rocm${ROCM_VERSION}"
    else
        # For version tags like v0.4.7, use the simpler format
        # For commit hashes, include the hash in the tag
        if [[ "$SGL_BRANCH" =~ ^v[0-9]+\.[0-9]+\.[0-9]+.*$ ]]; then
            IMAGE_TAG="${SGL_BRANCH}-rocm${ROCM_VERSION}"
        else
            IMAGE_TAG="${SGL_BRANCH}-${COMMIT_HASH}-rocm${ROCM_VERSION}"
        fi
    fi

    echo ""
    echo "Building Docker image: $IMAGE_TAG"
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
        --build-arg SGL_BRANCH="$BUILD_SGL_BRANCH_ARG" \
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

} 2>&1 | tee "$LOG_FILE"
