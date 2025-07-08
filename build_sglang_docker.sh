#!/usr/bin/env bash
# Helper script to pull SGLang Docker images with PR support
#
# This script can either:
# 1. Pull pre-built images from DockerHub for releases/main branch (default)
# 2. Pull base image and update with PR code for specific PRs
#
# Usage Examples:
#
# Pull pre-built image for release (default workflow):
#   ./pull_sglang_docker.sh --branch=v0.4.9
#
# Pull base image and update with PR code (recommended for PRs):
#   ./pull_sglang_docker.sh --pr=7865
#
# Pull base image and update with PR code using specific base branch:
#   ./pull_sglang_docker.sh --pr=7865 --branch=v0.4.9
#
# Show help:
#   ./pull_sglang_docker.sh --help
#
# Limitations:
# - PR feature only updates SGLang code, not dependencies
# - If PR changes triton or aiter versions, manual rebuild is required
# - sgl_kernel will be automatically rebuilt for PR workflow

set -euo pipefail

SCRIPT_DIR=$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )

# Default values
GITHUB_DOCKERFILE_URL="${GITHUB_DOCKERFILE_URL:-https://raw.githubusercontent.com/sgl-project/sglang/main/docker/Dockerfile.rocm}"
SGL_BRANCH="${SGL_BRANCH:-main}"
PR_NUMBER=""
GITHUB_TOKEN="${GITHUB_TOKEN:-}"
CONTAINER_NAME="sglang-pr-build"

# Function to check if python3 and requests are available
check_python_requirements() {
    if ! command -v python3 &> /dev/null; then
        echo "Error: python3 is required for PR functionality"
        exit 1
    fi

    if ! python3 -c "import requests" &> /dev/null 2>&1; then
        echo "Installing requests library..."
        pip3 install requests || {
            echo "Error: Failed to install requests library"
            echo "Please install manually: pip3 install requests"
            exit 1
        }
    fi
}

# Function to get PR details using GitHub API
get_pr_details() {
    local pr_number="$1"

    echo "Fetching PR #$pr_number details from GitHub API..." >&2

    # Use python to get PR details
    python3 - <<EOF
import requests
import sys
import json

def get_pr_info(pr_number, token=None):
    url = f"https://api.github.com/repos/sgl-project/sglang/pulls/{pr_number}"
    headers = {'Accept': 'application/vnd.github.v3+json'}

    if token and token.strip():
        headers['Authorization'] = f'token {token}'

    try:
        response = requests.get(url, headers=headers, timeout=30)
    except Exception as e:
        print(f"ERROR: Network error: {e}", file=sys.stderr)
        sys.exit(1)

    if response.status_code == 404:
        print(f"ERROR: PR #{pr_number} not found", file=sys.stderr)
        sys.exit(1)
    elif response.status_code == 403:
        print("ERROR: GitHub API rate limit exceeded or authentication required", file=sys.stderr)
        print(f"Response: {response.text}", file=sys.stderr)
        sys.exit(1)
    elif response.status_code != 200:
        print(f"ERROR: GitHub API error: {response.status_code}", file=sys.stderr)
        print(f"Response: {response.text}", file=sys.stderr)
        sys.exit(1)

    try:
        pr_data = response.json()
    except json.JSONDecodeError as e:
        print(f"ERROR: Invalid JSON in response: {e}", file=sys.stderr)
        print(f"Response text: {response.text}", file=sys.stderr)
        sys.exit(1)

    # Extract key information with error handling
    try:
        info = {
            'title': pr_data.get('title', ''),
            'state': pr_data.get('state', ''),
            'source_repo': pr_data['head']['repo']['clone_url'],
            'source_branch': pr_data['head']['ref'],
            'source_sha': pr_data['head']['sha'],
            'target_branch': pr_data['base']['ref']
        }
    except KeyError as e:
        print(f"ERROR: Missing required field in PR data: {e}", file=sys.stderr)
        print(f"Available keys: {list(pr_data.keys())}", file=sys.stderr)
        sys.exit(1)

    return info

try:
    pr_info = get_pr_info($pr_number, "$GITHUB_TOKEN" if "$GITHUB_TOKEN" else None)

    # Output as JSON for easier parsing, ensure it goes to stdout
    print(json.dumps(pr_info, ensure_ascii=False))

except Exception as e:
    print(f"ERROR: Failed to fetch PR details: {e}", file=sys.stderr)
    sys.exit(1)
EOF
}

# Function to extract image tag from Dockerfile usage
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

# Function to pull pre-built Docker image
pull_base_image() {
    echo ""
    echo "============================================================"
    echo "STEP 1: PULLING PRE-BUILT DOCKER IMAGE"
    echo "============================================================"

    # For specific branches/versions, modify the URL
    local dockerfile_url="$GITHUB_DOCKERFILE_URL"
    if [ "$SGL_BRANCH" != "main" ]; then
        if [[ "$SGL_BRANCH" =~ ^v[0-9]+\.[0-9]+\.[0-9]+.*$ ]]; then
            # For version tags, use the tag in the URL
            dockerfile_url="https://raw.githubusercontent.com/sgl-project/sglang/$SGL_BRANCH/docker/Dockerfile.rocm"
            echo "Using branch-specific Dockerfile URL: $dockerfile_url"
        fi
    fi

    # Extract the image tag from the Dockerfile usage example
    local image_tag=$(extract_image_tag_from_usage "$dockerfile_url")

    if [ $? -ne 0 ]; then
        echo "Failed to extract image tag. Using default..."
        if [ "$SGL_BRANCH" = "main" ]; then
            image_tag="latest"
        else
            image_tag="$SGL_BRANCH"
        fi
    fi

    BASE_IMAGE="lmsysorg/sglang:$image_tag"
    echo "Target Docker image: $BASE_IMAGE"

    # Check if image exists on DockerHub
    echo "Checking if image exists on DockerHub..."
    if docker manifest inspect "$BASE_IMAGE" >/dev/null 2>&1; then
        echo "✓ Image exists on DockerHub"
    else
        echo "⚠ Warning: Image may not exist on DockerHub, but will attempt to pull anyway"
    fi

    # Pull the Docker image
    echo "Pulling Docker image: $BASE_IMAGE"
    if docker pull "$BASE_IMAGE"; then
        echo "✓ Successfully pulled Docker image: $BASE_IMAGE"
    else
        echo "✗ Failed to pull Docker image: $BASE_IMAGE"
        exit 1
    fi
}

# Function to update sglang code in container with PR
update_with_pr() {
    local pr_number="$1"

    echo ""
    echo "============================================================"
    echo "STEP 2: ENTERING CONTAINER AND UPDATING WITH PR #$pr_number"
    echo "============================================================"

    # Get PR details
    local pr_info=$(get_pr_details "$pr_number")
    if [ $? -ne 0 ]; then
        echo "Failed to get PR details"
        exit 1
    fi

    # Parse JSON response with error handling
    if [ -z "$pr_info" ]; then
        echo "Error: Empty response from GitHub API"
        exit 1
    fi

    echo "DEBUG: PR info response: $pr_info"

    local pr_title=$(echo "$pr_info" | python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
    print(data['title'])
except (json.JSONDecodeError, KeyError) as e:
    print('', file=sys.stderr)
    sys.exit(1)
")

    local pr_state=$(echo "$pr_info" | python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
    print(data['state'])
except (json.JSONDecodeError, KeyError) as e:
    print('', file=sys.stderr)
    sys.exit(1)
")

    local source_repo=$(echo "$pr_info" | python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
    print(data['source_repo'])
except (json.JSONDecodeError, KeyError) as e:
    print('', file=sys.stderr)
    sys.exit(1)
")

    local source_branch=$(echo "$pr_info" | python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
    print(data['source_branch'])
except (json.JSONDecodeError, KeyError) as e:
    print('', file=sys.stderr)
    sys.exit(1)
")

    local source_sha=$(echo "$pr_info" | python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
    print(data['source_sha'])
except (json.JSONDecodeError, KeyError) as e:
    print('', file=sys.stderr)
    sys.exit(1)
")

    local target_branch=$(echo "$pr_info" | python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
    print(data['target_branch'])
except (json.JSONDecodeError, KeyError) as e:
    print('', file=sys.stderr)
    sys.exit(1)
")

    echo "PR Information:"
    echo "  Title: $pr_title"
    echo "  State: $pr_state"
    echo "  Source Branch: $source_branch"
    echo "  Source SHA: ${source_sha:0:8}"
    echo "  Target Branch: $target_branch"
    echo ""

    if [ "$pr_state" != "open" ]; then
        echo "⚠ Warning: PR #$pr_number is $pr_state (not open)"
        read -p "Continue anyway? (y/N): " -n 1 -r
        echo
        if [[ ! $REPLY =~ ^[Yy]$ ]]; then
            echo "Aborted by user"
            exit 1
        fi
    fi

    # Remove existing container if it exists
    echo "Cleaning up any existing container..."
    docker rm -f "$CONTAINER_NAME" 2>/dev/null || true

    echo ""
    echo "============================================================"
    echo "STEP 3: ENTERING DOCKER CONTAINER (sgl-workspace folder)"
    echo "============================================================"

    # Start container in background
    echo "Starting container from image: $BASE_IMAGE"
    docker run -d --name "$CONTAINER_NAME" "$BASE_IMAGE" sleep infinity

    if [ $? -ne 0 ]; then
        echo "✗ Failed to start container"
        exit 1
    fi

    echo "✓ Container started: $CONTAINER_NAME"
    echo ""

    echo "============================================================"
    echo "STEP 4: PULLING LATEST CODE FROM PR REPO AND BRANCH"
    echo "============================================================"

    # Navigate to sgl-workspace and clone PR repo
    echo "Navigating to /sgl-workspace and cloning PR repository..."

    docker exec "$CONTAINER_NAME" bash -c "
        set -e
        cd /sgl-workspace
        echo 'Current directory: \$(pwd)'
        echo 'Contents:'
        ls -la

        echo ''
        echo 'Cloning PR repository...'
        echo 'Repo: $source_repo'
        echo 'Branch: $source_branch'

        # Remove existing sglang if it exists
        rm -rf sglang-pr

        # Clone the PR branch
        git clone --depth 1 -b '$source_branch' '$source_repo' sglang-pr

        echo '✓ Successfully cloned PR repository'
        cd sglang-pr
        echo 'Cloned repository contents:'
        ls -la

        echo ''
        echo 'Git information:'
        git log --oneline -5

        echo ''
        echo 'Current commit SHA:'
        git rev-parse HEAD
    "

    if [ $? -ne 0 ]; then
        echo "✗ Failed to clone PR repository in container"
        docker rm -f "$CONTAINER_NAME"
        exit 1
    fi

    echo ""
    echo "============================================================"
    echo "STEP 5: ENTERING CLONED REPO AND SGLANG DIRECTORY"
    echo "============================================================"

    # Enter the sglang directory and show information
    docker exec "$CONTAINER_NAME" bash -c "
        set -e
        cd /sgl-workspace/sglang-pr
        echo 'Current directory: \$(pwd)'
        echo 'SGLang directory contents:'
        ls -la

        # Check if python directory exists (where sglang code typically is)
        if [ -d 'python' ]; then
            echo ''
            echo 'Python directory contents:'
            ls -la python/

            if [ -d 'python/sglang' ]; then
                echo ''
                echo 'SGLang package contents:'
                ls -la python/sglang/
            fi
        fi

        # Show current sglang installation
        echo ''
        echo 'Current SGLang installation:'
        python -c 'import sglang; print(f\"SGLang version: {sglang.__version__}\"); print(f\"SGLang path: {sglang.__file__}\")'
    "

    if [ $? -eq 0 ]; then
        echo "✓ Successfully entered sglang directory"
    else
        echo "⚠ Warning: Could not fully inspect sglang directory, but container is ready"
    fi

    # Create a tagged image from the updated container for easier use
    echo "Creating tagged image from updated container..."
    local pr_image_tag="sglang-pr${pr_number}"
    docker commit "$CONTAINER_NAME" "$pr_image_tag"

    if [ $? -eq 0 ]; then
        echo "✓ Created image: $pr_image_tag"

        # Create additional descriptive tag
        local commit_short="${source_sha:0:8}"
        local descriptive_tag="sglang-pr${pr_number}-${commit_short}"
        docker tag "$pr_image_tag" "$descriptive_tag"
        echo "✓ Tagged image as: $descriptive_tag"
    else
        echo "⚠ Warning: Failed to create tagged image, but container is still available"
        pr_image_tag="$CONTAINER_NAME"
    fi

    echo ""
    echo "============================================================"
    echo "BUILD COMPLETE - CONTAINER READY"
    echo "============================================================"
    echo "Container name: $CONTAINER_NAME"
    echo "Base image: $BASE_IMAGE"
    echo "PR: #$pr_number ($pr_title)"
    echo "PR code location: /sgl-workspace/sglang-pr"
    echo "Commit: ${source_sha:0:8}"
    echo "Image tag: $pr_image_tag"
    echo ""
    echo "Image ready for use with benchmark scripts:"
    echo "  bash grok_perf_offline_csv.sh --docker_image=$pr_image_tag"
    echo "  bash grok_perf_online_csv.sh --docker_image=$pr_image_tag"
    echo ""
    echo "To run the container with PR code:"
    echo "  docker run -it --rm $pr_image_tag bash -c 'cd /sgl-workspace/sglang-pr && bash'"
    echo ""
    echo "To enter the existing container:"
    echo "  docker exec -it $CONTAINER_NAME bash"
    echo ""
    echo "To work with the PR code in existing container:"
    echo "  docker exec -it $CONTAINER_NAME bash -c 'cd /sgl-workspace/sglang-pr && bash'"
    echo ""
    echo "To stop and remove the container:"
    echo "  docker rm -f $CONTAINER_NAME"
    echo ""
    echo "Note: The container has both the original SGLang (/sgl-workspace/sglang) and"
    echo "      the PR code (/sgl-workspace/sglang-pr) available."
}

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
        --pr=*)
            PR_NUMBER="${1#*=}"
            shift
            ;;
        --github-token=*)
            GITHUB_TOKEN="${1#*=}"
            shift
            ;;
        --container-name=*)
            CONTAINER_NAME="${1#*=}"
            shift
            ;;
        --help)
            echo "Usage: $0 [options]"
            echo ""
            echo "Options:"
            echo "  --github-dockerfile-url=URL  URL to Dockerfile.rocm on GitHub"
            echo "  --branch=BRANCH              Git branch/tag for reference (default: main)"
            echo "  --pr=NUMBER                  Pull base image and update with PR code"
            echo "  --github-token=TOKEN         GitHub personal access token"
            echo "  --container-name=NAME        Container name for PR workflow (default: sglang-pr-build)"
            echo "  --help                       Show this help message"
            echo ""
            echo "Examples:"
            echo "  # Pull pre-built image for release (default workflow)"
            echo "  $0 --branch=v0.4.9"
            echo ""
            echo "  # Pull base image and update with PR code (recommended for PRs)"
            echo "  $0 --pr=7865"
            echo ""
            echo "  # Pull base image and update with PR code using specific base branch"
            echo "  $0 --pr=7865 --branch=v0.4.9"
            echo ""
            echo "  # Use custom GitHub token"
            echo "  $0 --pr=7865 --github-token=ghp_xxxx"
            echo ""
            echo "Environment Variables:"
            echo "  GITHUB_TOKEN    GitHub personal access token"
            exit 0
            ;;
        *)
            echo "Unknown option: $1"
            echo "Use --help for usage information"
            exit 1
            ;;
    esac
done

# Setup logging
LOG_DIR="$SCRIPT_DIR/build_docker_log"
mkdir -p "$LOG_DIR"

# Determine log file name based on build type
if [ -n "$PR_NUMBER" ]; then
    LOG_FILE="$LOG_DIR/pr${PR_NUMBER}_$(TZ='America/Los_Angeles' date +'%Y%m%d_%H%M%S').log"
else
    SANITIZED_SGL_BRANCH=$(echo "$SGL_BRANCH" | tr '/' '_')
    LOG_FILE="$LOG_DIR/${SANITIZED_SGL_BRANCH}_$(TZ='America/Los_Angeles' date +'%Y%m%d_%H%M%S').log"
fi

echo ""
echo "Logging all output to $LOG_FILE"

{
    echo "SGLang Docker Script"
    echo "===================="
    echo "Timestamp: $(date)"
    echo "Script directory: $SCRIPT_DIR"
    echo "Branch: $SGL_BRANCH"
    if [ -n "$PR_NUMBER" ]; then
        echo "PR Number: $PR_NUMBER"
    fi
    echo ""

    # Check if we should use PR workflow
    if [ -n "$PR_NUMBER" ]; then
        echo "Mode: Pull base image and update with PR #$PR_NUMBER"
        check_python_requirements

        # Step 1: Pull pre-built image
        pull_base_image

        # Step 2-5: Update with PR code
        update_with_pr "$PR_NUMBER"
    else
        echo "Mode: Pull pre-built image (branch: $SGL_BRANCH)"

        echo "Fetching latest SGLang Docker image tag from GitHub..."
        echo "Dockerfile URL: $GITHUB_DOCKERFILE_URL"

        # For specific branches/versions, modify the URL
        if [ "$SGL_BRANCH" != "main" ]; then
            if [[ "$SGL_BRANCH" =~ ^v[0-9]+\.[0-9]+\.[0-9]+.*$ ]]; then
                # For version tags, use the tag in the URL
                DOCKERFILE_URL="https://raw.githubusercontent.com/sgl-project/sglang/$SGL_BRANCH/docker/Dockerfile.rocm"
                echo "Using branch-specific Dockerfile URL: $DOCKERFILE_URL"
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
            echo ""
            echo "You may want to try:"
            echo "1. Check available tags at: https://hub.docker.com/r/lmsysorg/sglang/tags"
            echo "2. Try a different branch/version"
            echo "3. Use --pr option to pull base image and update with PR code"
            exit 1
        fi
    fi

} 2>&1 | tee "$LOG_FILE"
