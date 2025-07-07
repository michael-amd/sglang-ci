#!/usr/bin/env bash
# Helper script to pull SGLang Docker images from DockerHub using latest tag from GitHub
#
# This script fetches the latest image tag from the SGLang Dockerfile.rocm on GitHub
# and pulls the corresponding pre-built image from lmsysorg/sglang instead of building
##
# IMPORTANT LIMITATIONS:
# 1. This approach will NOT work for PRs that change aiter version, since pre-built images
#    are based on main/released versions, not specific PR changes
# 2. This is a WORKAROUND until aiter build becomes faster in the future
# 3. When we go back to building images from PRs, remember to rebuild sgl_kernel
#    in the docker image after the PR branch is cloned
#
# TODO: Consider hybrid approach - pull pre-built for released versions,
#       build from source for PRs with version changes

set -euo pipefail

SCRIPT_DIR=$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )

# Default values
GITHUB_DOCKERFILE_URL="${GITHUB_DOCKERFILE_URL:-https://raw.githubusercontent.com/sgl-project/sglang/main/docker/Dockerfile.rocm}"
SGL_BRANCH="${SGL_BRANCH:-main}"
PULL_LATEST="${PULL_LATEST:-true}"

# Parse command line arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --github-dockerfile-url=*)
            GITHUB_DOCKERFILE_URL="${1#*=}"
            shift
            ;;
        --branch=*)
            SGL_BRANCH="${1#*=}"
            shift
            ;;
        --no-pull)
            PULL_LATEST="false"
            shift
            ;;
        --help)
            echo "Usage: $0 [options]"
            echo "Options:"
            echo "  --github-dockerfile-url=URL  URL to Dockerfile.rocm on GitHub (default: https://raw.githubusercontent.com/sgl-project/sglang/main/docker/Dockerfile.rocm)"
            echo "  --branch=BRANCH              Git branch/tag for reference (default: main)"
            echo "  --no-pull                    Skip pulling latest image info (default: pull latest)"
            echo "  --help                       Show this help message"
            echo ""
            echo "Examples:"
            echo "  $0 --branch=v0.4.9"
            echo "  $0 --github-dockerfile-url=https://raw.githubusercontent.com/sgl-project/sglang/v0.4.9/docker/Dockerfile.rocm"
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
    echo "Fetching latest SGLang Docker image tag from GitHub..."
    echo "Dockerfile URL: $GITHUB_DOCKERFILE_URL"

    # Function to extract image tag from usage example
    extract_image_tag_from_usage() {
        local dockerfile_url="$1"

        # Fetch the dockerfile and extract the usage example
        local usage_line=$(curl -s "$dockerfile_url" | grep -E "docker build.*-t.*-f Dockerfile.rocm" | head -1)

        if [ -z "$usage_line" ]; then
            echo "Error: Could not find usage example in Dockerfile" >&2
            return 1
        fi

        echo "Found usage example: $usage_line" >&2

        # Extract the tag (part after -t and before next space or end)
        local image_tag=$(echo "$usage_line" | sed -n 's/.*-t \([^ ]*\).*/\1/p')

        if [ -z "$image_tag" ]; then
            echo "Error: Could not extract image tag from usage example" >&2
            return 1
        fi

        echo "$image_tag"
    }

    # For specific branches/versions, modify the URL
    if [ "$SGL_BRANCH" != "main" ]; then
        if [[ "$SGL_BRANCH" =~ ^v[0-9]+\.[0-9]+\.[0-9]+.*$ ]]; then
            # For version tags, use the tag in the URL
            DOCKERFILE_URL="https://raw.githubusercontent.com/sgl-project/sglang/$SGL_BRANCH/docker/Dockerfile.rocm"
            echo "Using branch-specific Dockerfile URL: $DOCKERFILE_URL"
        elif [[ "$SGL_BRANCH" =~ ^pull/([0-9]+)(/merge)?$ ]]; then
            # Warn about PR limitations
            echo "⚠ WARNING: Using PR branch '$SGL_BRANCH'"
            echo "⚠ This script pulls pre-built images and will NOT include PR-specific changes!"
            echo "⚠ If this PR changes aiter version, the pre-built image may not work correctly."
            echo "⚠ Consider building from source when aiter build becomes faster."
            echo ""
            DOCKERFILE_URL="$GITHUB_DOCKERFILE_URL"
        else
            echo "Using main branch Dockerfile URL for non-version branch: $SGL_BRANCH"
            DOCKERFILE_URL="$GITHUB_DOCKERFILE_URL"
        fi
    else
        DOCKERFILE_URL="$GITHUB_DOCKERFILE_URL"
    fi

    # Extract the image tag from the Dockerfile usage example
    IMAGE_TAG=$(extract_image_tag_from_usage "$DOCKERFILE_URL")

    if [ $? -ne 0 ]; then
        echo "Failed to extract image tag. Falling back to latest tag..."
        # Fallback: use latest tag for main branch or branch name for others
        if [ "$SGL_BRANCH" = "main" ]; then
            IMAGE_TAG="latest"
        else
            IMAGE_TAG="$SGL_BRANCH"
        fi
    fi

    DOCKER_IMAGE="lmsysorg/sglang:$IMAGE_TAG"

    echo ""
    echo "Target Docker image: $DOCKER_IMAGE"
    echo "Image tag extracted: $IMAGE_TAG"
    echo ""

    # Check if image exists on DockerHub
    echo "Checking if image exists on DockerHub..."
    if docker manifest inspect "$DOCKER_IMAGE" >/dev/null 2>&1; then
        echo "✓ Image exists on DockerHub"
    else
        echo "⚠ Warning: Image may not exist on DockerHub, but will attempt to pull anyway"
    fi

    # Pull the Docker image
    echo ""
    echo "Pulling Docker image: $DOCKER_IMAGE"
    echo "This may take several minutes depending on your internet connection..."

    if docker pull "$DOCKER_IMAGE"; then
        echo ""
        echo "✓ Successfully pulled Docker image: $DOCKER_IMAGE"

        # Tag the image with the extracted tag for local reference
        LOCAL_TAG="$IMAGE_TAG"
        docker tag "$DOCKER_IMAGE" "$LOCAL_TAG"
        echo "✓ Tagged image locally as: $LOCAL_TAG"

        echo ""
        echo "Image ready for use with benchmark scripts:"
        echo "  bash grok_perf_offline_csv.sh --docker_image=$LOCAL_TAG"
        echo "  bash grok_perf_online_csv.sh --docker_image=$LOCAL_TAG"
        echo ""
        echo "To run the container:"
        echo "  docker run -it --rm $LOCAL_TAG"
        echo ""
        echo "Original DockerHub image: $DOCKER_IMAGE"

    else
        echo ""
        echo "✗ Failed to pull Docker image: $DOCKER_IMAGE"
        echo ""
        echo "This could be due to:"
        echo "1. Image not yet available on DockerHub for this version"
        echo "2. Network connectivity issues"
        echo "3. Incorrect image tag format"
        echo "4. PR-specific changes not available in pre-built images"
        echo ""
        echo "You may need to:"
        echo "1. Check available tags at: https://hub.docker.com/r/lmsysorg/sglang/tags"
        echo "2. Try a different branch/version"
        echo "3. For PRs with aiter changes, consider building from source when faster"
        exit 1
    fi

} 2>&1 | tee "$LOG_FILE"

# FUTURE BUILD PROCESS NOTES:
# When aiter build becomes faster and we switch back to building from source:
# 1. Clone/checkout the specific PR branch
# 2. IMPORTANT: Rebuild sgl_kernel inside the Docker image after PR checkout
# 3. Consider hybrid approach: pull for releases, build for PRs with changes
