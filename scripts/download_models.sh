#!/usr/bin/env bash
# ------------------------------------------------------------------------------
# download_models.sh
#   Script to download required models for SGLang sanity check testing.
#
# USAGE:
#   bash download_models.sh [--model MODEL_NAME] [--all] [--hardware TYPE] [--dry-run]
#
# Examples:
#   bash download_models.sh --all                         # Download all missing models
#   bash download_models.sh --hardware=mi30x              # Download models for mi30x
#   bash download_models.sh --hardware=mi35x --dry-run    # Show mi35x models
#   bash download_models.sh --model=GPT-OSS-120B-LMSYS    # Download specific model
#   bash download_models.sh --dry-run                     # Show what would be downloaded
# ------------------------------------------------------------------------------

set -euo pipefail

# Set timezone to PST/PDT
export TZ='America/Los_Angeles'

# Configuration
# Path to store models - can be overridden by setting BASE_DIR environment variable
BASE_DIR="${BASE_DIR:-/data}"
HUGGINGFACE_HUB_CACHE="${BASE_DIR}/.cache"

# Model definitions
declare -A MODELS=(
    ["GPT-OSS-120B-LMSYS"]="lmsys/gpt-oss-120b-bf16"
    ["GPT-OSS-120B-OPENAI"]="openai/gpt-oss-120b"
    ["GPT-OSS-20B-LMSYS"]="lmsys/gpt-oss-20b-bf16"
    ["GPT-OSS-20B-OPENAI"]="openai/gpt-oss-20b"
    ["QWEN-30B"]="Qwen/Qwen3-30B-A3B-Thinking-2507"
    ["GROK1-TOKENIZER"]="Xenova/grok-1-tokenizer"
    ["GROK1-INT4"]="amd/grok-1-W4A8KV8"
    ["GROK1-FP8"]="lmzheng/grok-1"
    ["GROK2-TOKENIZER"]="alvarobartt/grok-2-tokenizer"
    ["GROK2"]="xai-org/grok-2"
    ["DEEPSEEK-V3"]="deepseek-ai/DeepSeek-V3-0324"
    ["DEEPSEEK-R1-MXFP4"]="amd/DeepSeek-R1-MXFP4-Preview"
    ["LLAMA4-MAVERICK-17B"]="meta-llama/Llama-4-Maverick-17B-128E-Instruct-FP8"
    # ["DEEPSEEK-V3.1"]="deepseek-ai/DeepSeek-V3.1"  # Commented out - only one DeepSeek version needed
    # ["DEEPSEEK-R1"]="deepseek-ai/DeepSeek-R1-0528"  # Commented out - only one DeepSeek version needed
)

# Local paths where models should be stored
declare -A MODEL_PATHS=(
    ["GPT-OSS-120B-LMSYS"]="${BASE_DIR}/lmsys/gpt-oss-120b-bf16"
    ["GPT-OSS-120B-OPENAI"]="${BASE_DIR}/openai/gpt-oss-120b"
    ["GPT-OSS-20B-LMSYS"]="${BASE_DIR}/lmsys/gpt-oss-20b-bf16"
    ["GPT-OSS-20B-OPENAI"]="${BASE_DIR}/openai/gpt-oss-20b"
    ["QWEN-30B"]="${BASE_DIR}/Qwen/Qwen3-30B-A3B-Thinking-2507"
    ["GROK1-TOKENIZER"]="${BASE_DIR}/Xenova--grok-1-tokenizer"
    ["GROK1-INT4"]="${BASE_DIR}/amd--grok-1-W4A8KV8"
    ["GROK1-FP8"]="${BASE_DIR}/lmzheng-grok-1"
    ["GROK2-TOKENIZER"]="${BASE_DIR}/alvarobartt--grok-2-tokenizer"
    ["GROK2"]="${BASE_DIR}/grok-2"
    ["DEEPSEEK-V3"]="${BASE_DIR}/deepseek-ai/DeepSeek-V3-0324"
    ["DEEPSEEK-R1-MXFP4"]="${BASE_DIR}/amd/DeepSeek-R1-MXFP4-Preview"
    ["LLAMA4-MAVERICK-17B"]="${BASE_DIR}/meta-llama/Llama-4-Maverick-17B-128E-Instruct-FP8"
    # ["DEEPSEEK-V3.1"]="${BASE_DIR}/deepseek-ai/DeepSeek-V3.1"  # Commented out - only one DeepSeek version needed
    # ["DEEPSEEK-R1"]="${BASE_DIR}/deepseek-ai/DeepSeek-R1-0528"  # Commented out - only one DeepSeek version needed
)

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Logging functions
log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

log_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Function to check if a model exists locally
check_model_exists() {
    local model_name="$1"
    local model_path="${MODEL_PATHS[$model_name]}"

    if [[ -d "$model_path" ]] && [[ -n "$(ls -A "$model_path" 2>/dev/null)" ]]; then
        return 0  # Model exists and is not empty
    else
        return 1  # Model doesn't exist or is empty
    fi
}

# Function to download a model using huggingface-hub
download_model() {
    local model_name="$1"
    local repo_id="${MODELS[$model_name]}"
    local local_path="${MODEL_PATHS[$model_name]}"
    local dry_run="${2:-false}"

    log_info "Processing model: $model_name"
    log_info "Repository: $repo_id"
    log_info "Local path: $local_path"

    if [[ "$dry_run" == "true" ]]; then
        if check_model_exists "$model_name"; then
            log_info "[DRY RUN] Model exists, would verify/resume download of $repo_id to $local_path"
        else
            log_info "[DRY RUN] Would download $repo_id to $local_path"
        fi
        return 0
    fi

    if check_model_exists "$model_name"; then
        log_info "Model folder exists at $local_path - verifying/resuming download to ensure completeness"
    fi

    # Create directory
    mkdir -p "$(dirname "$local_path")"

    # Special handling for certain models
    case "$model_name" in
        "GROK1-INT4")
            log_info "Downloading GROK1-INT4 (W4A8KV8 quantized) from amd/grok-1-W4A8KV8"
            log_info "Note: This model uses Xenova/grok-1-tokenizer as tokenizer"
            ;;
        "GROK1-FP8")
            log_info "Downloading GROK1-FP8 from lmzheng/grok-1"
            log_info "Note: This model uses Xenova/grok-1-tokenizer as tokenizer"
            ;;
        "GROK1-TOKENIZER")
            log_info "Downloading GROK1 tokenizer from Xenova/grok-1-tokenizer"
            ;;
        "GROK2")
            log_info "Downloading GROK2 from xai-org/grok-2"
            log_warning "Note: This model may require HuggingFace authentication for access"
            ;;
        "GROK2-TOKENIZER")
            log_info "Downloading GROK2 tokenizer from alvarobartt/grok-2-tokenizer"
            ;;
    esac

    log_info "Starting download of $model_name..."

    # Use huggingface-hub to download
    if command -v huggingface-cli &> /dev/null; then
        log_info "Using huggingface-cli to download $repo_id"
        if huggingface-cli download "$repo_id" --local-dir "$local_path" --local-dir-use-symlinks False; then
            log_success "Successfully downloaded $model_name"
        else
            log_error "Failed to download $model_name using huggingface-cli"
            return 1
        fi
    elif python3 -c "import huggingface_hub" 2>/dev/null; then
        log_info "Using Python huggingface_hub to download $repo_id"
        python3 -c "
from huggingface_hub import snapshot_download
import os
snapshot_download(
    repo_id='$repo_id',
    local_dir='$local_path',
    local_dir_use_symlinks=False
)
print('Download completed successfully')
" && log_success "Successfully downloaded $model_name" || {
            log_error "Failed to download $model_name using Python huggingface_hub"
            return 1
        }
    else
        log_error "Neither huggingface-cli nor Python huggingface_hub is available"
        log_info "Please install huggingface_hub: pip install huggingface_hub"
        return 1
    fi

    # Verify download
    if check_model_exists "$model_name"; then
        log_success "Verified: $model_name downloaded successfully"
    else
        log_error "Download verification failed for $model_name"
        return 1
    fi
}

# Function to get models for specific hardware
get_models_for_hardware() {
    local hw="$1"
    local models=()

    # Shared models for all hardware
    local shared_models=("QWEN-30B" "GROK1-TOKENIZER" "GROK1-INT4" "GROK1-FP8" "GROK2" "GROK2-TOKENIZER" "DEEPSEEK-V3" "DEEPSEEK-R1-MXFP4" "LLAMA4-MAVERICK-17B")

    if [[ "$hw" == "mi30x" ]]; then
        models+=("GPT-OSS-120B-LMSYS" "GPT-OSS-20B-LMSYS")
    elif [[ "$hw" == "mi35x" ]]; then
        models+=("GPT-OSS-120B-OPENAI" "GPT-OSS-20B-OPENAI")
    fi

    # Add shared models
    models+=("${shared_models[@]}")

    echo "${models[@]}"
}

# Function to show model status
show_status() {
    local hw_filter="$1"
    log_info "Model Status Report"

    if [[ -n "$hw_filter" ]]; then
        echo "==================== (Filtered for $hw_filter)"
        local models_to_check=($(get_models_for_hardware "$hw_filter"))
    else
        echo "===================="
        local models_to_check=("${!MODELS[@]}")
    fi

    for model_name in "${models_to_check[@]}"; do
        if check_model_exists "$model_name"; then
            echo -e "${GREEN}✓${NC} $model_name - Available at ${MODEL_PATHS[$model_name]}"
        else
            echo -e "${RED}✗${NC} $model_name - Missing (would download from ${MODELS[$model_name]})"
        fi
    done
    echo ""
}

# Function to estimate disk space requirements
estimate_space() {
    log_info "Estimated Disk Space Requirements:"
    echo "=================================="
    echo "GPT-OSS-120B-LMSYS:  ~111GB (bf16)"
    echo "GPT-OSS-120B-OPENAI: ~111GB"
    echo "GPT-OSS-20B-LMSYS:   ~22GB (bf16)"
    echo "GPT-OSS-20B-OPENAI:  ~39GB"
    echo "QWEN-30B:            ~57GB"
    echo "GROK1-TOKENIZER:     ~9MB"
    echo "GROK1-INT4:          ~223GB (W4A8KV8 quantized)"
    echo "GROK1-FP8:           ~685GB (FP8 quantized)"
    echo "GROK2:               ~503GB"
    echo "GROK2-TOKENIZER:     ~18MB"
    echo "DeepSeek-V3:         ~642GB"
    echo "DeepSeek-R1-MXFP4:   ~455GB (MXFP4 quantized, 72 shards)"
    echo "LLAMA4-MAVERICK-17B: ~389GB (FP8 quantized)"
    echo "DeepSeek-V3.1:       ~640GB"
    echo "DeepSeek-R1:         ~640GB"
    echo ""
    echo "Total estimated: ~4TB+ (depending on which models are downloaded)"
    echo ""
}

# Parse command line arguments
DOWNLOAD_ALL=false
SPECIFIC_MODEL=""
HARDWARE=""
DRY_RUN=false
SHOW_STATUS=false
SHOW_HELP=false

for arg in "$@"; do
    case $arg in
        --all)
            DOWNLOAD_ALL=true
            ;;
        --model=*)
            SPECIFIC_MODEL="${arg#*=}"
            ;;
        --hardware=*)
            HARDWARE="${arg#*=}"
            if [[ "$HARDWARE" != "mi30x" && "$HARDWARE" != "mi35x" ]]; then
                log_error "Invalid hardware: $HARDWARE. Must be 'mi30x' or 'mi35x'"
                exit 1
            fi
            ;;
        --dry-run)
            DRY_RUN=true
            ;;
        --status)
            SHOW_STATUS=true
            ;;
        --help)
            SHOW_HELP=true
            ;;
        *)
            log_error "Unknown argument: $arg"
            SHOW_HELP=true
            ;;
    esac
done

# Show help
if [[ "$SHOW_HELP" == "true" ]]; then
    echo "Usage: $0 [OPTIONS]"
    echo ""
    echo "Options:"
    echo "  --all              Download all missing models"
    echo "  --model=MODEL      Download specific model"
    echo "  --hardware=TYPE    Download models for specific hardware (mi30x or mi35x)"
    echo "  --dry-run          Show what would be downloaded without actually downloading"
    echo "  --status           Show current model status"
    echo "  --help             Show this help message"
    echo ""
    echo "Available models:"
    for model_name in "${!MODELS[@]}"; do
        echo "  $model_name (${MODELS[$model_name]})"
    done
    echo ""
    echo "Hardware-specific models:"
    echo "  mi30x: GPT-OSS-120B-LMSYS, GPT-OSS-20B-LMSYS (+ shared models)"
    echo "  mi35x: GPT-OSS-120B-OPENAI, GPT-OSS-20B-OPENAI (+ shared models)"
    echo "  Shared: QWEN-30B, GROK1-TOKENIZER, GROK1-INT4, GROK1-FP8,"
    echo "          GROK2, GROK2-TOKENIZER, DEEPSEEK-V3, DEEPSEEK-R1-MXFP4, LLAMA4-MAVERICK-17B"
    echo ""
    echo "Examples:"
    echo "  $0 --status                    # Show current status (all models)"
    echo "  $0 --status --hardware=mi30x   # Show status for mi30x models only"
    echo "  $0 --all                       # Download all missing models"
    echo "  $0 --hardware=mi30x            # Download models for mi30x hardware"
    echo "  $0 --hardware=mi35x --dry-run  # Show what would be downloaded for mi35x"
    echo "  $0 --model=GPT-OSS-120B-LMSYS  # Download specific model"
    echo "  $0 --dry-run --all             # Show what would be downloaded"
    exit 0
fi

# Show status if requested
if [[ "$SHOW_STATUS" == "true" ]]; then
    show_status "$HARDWARE"
    estimate_space
    exit 0
fi

# Main execution
log_info "SGLang Model Download Script"
log_info "Starting at: $(date '+%Y-%m-%d %H:%M:%S %Z')"
log_info "Base directory: $BASE_DIR"

# Create base directory
mkdir -p "$BASE_DIR"

# Check prerequisites
if ! command -v python3 &> /dev/null; then
    log_error "Python3 is required but not found"
    exit 1
fi

# Check for huggingface_hub
if ! command -v huggingface-cli &> /dev/null && ! python3 -c "import huggingface_hub" 2>/dev/null; then
    log_warning "huggingface_hub is not installed"
    log_info "Installing huggingface_hub..."
    if ! pip3 install huggingface_hub; then
        log_error "Failed to install huggingface_hub"
        exit 1
    fi
fi

# Show initial status
show_status "$HARDWARE"

# Download models
if [[ "$DOWNLOAD_ALL" == "true" ]] || [[ -n "$HARDWARE" ]]; then
    if [[ -n "$HARDWARE" ]]; then
        log_info "Downloading models for $HARDWARE hardware..."
        models_to_download=($(get_models_for_hardware "$HARDWARE"))
    else
        log_info "Downloading all missing models..."
        models_to_download=("${!MODELS[@]}")
    fi

    failed_downloads=()

    for model_name in "${models_to_download[@]}"; do
        # Skip if model doesn't exist in MODELS array (shouldn't happen, but safe check)
        if [[ ! -v "MODELS[$model_name]" ]]; then
            log_warning "Model $model_name not found in MODELS array, skipping"
            continue
        fi

        # Always attempt download to verify/resume incomplete downloads
        if ! download_model "$model_name" "$DRY_RUN"; then
            failed_downloads+=("$model_name")
        fi
        echo ""  # Add spacing between models
    done

    if [[ ${#failed_downloads[@]} -gt 0 ]]; then
        log_error "Failed to download: ${failed_downloads[*]}"
        exit 1
    else
        log_success "All models processed successfully"
    fi

elif [[ -n "$SPECIFIC_MODEL" ]]; then
    if [[ -v "MODELS[$SPECIFIC_MODEL]" ]]; then
        download_model "$SPECIFIC_MODEL" "$DRY_RUN"
    else
        log_error "Unknown model: $SPECIFIC_MODEL"
        log_info "Available models: ${!MODELS[*]}"
        exit 1
    fi
else
    log_warning "No action specified. Use --all, --hardware=TYPE, --model=MODEL_NAME, or --status"
    log_info "Use --help for more information"
    exit 1
fi

# Final status
if [[ "$DRY_RUN" == "false" ]]; then
    echo ""
    log_info "Final Status:"
    show_status "$HARDWARE"
fi

log_info "Script completed at: $(date '+%Y-%m-%d %H:%M:%S %Z')"
