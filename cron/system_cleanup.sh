#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# system_cleanup.sh - Automatic Docker and GPU core cleanup to prevent disk space issues
#
# This script removes:
# 1. Docker images starting with "sgl-dev" or "rocm/sgl-dev" older than 7 days
# 2. All stopped Docker containers
# 3. Dangling images and unused build cache
# 4. ALL GPU core dump files (gpucore.*) by default
#
# Usage:
#     bash cron/system_cleanup.sh
#
# Optional environment variables:
#   CLEANUP_AGE_DAYS: Number of days to keep Docker images (default: 7)
#   GPUCORE_AGE_DAYS: Number of days to keep GPU core dumps (default: 0 = all)
#   GPUCORE_SEARCH_DIR: Directory to search for gpucore files (default: script parent dir)
#   DRY_RUN: Set to "true" to simulate without actually deleting (default: false)
#   CRITICAL_SPACE_GB: Free space threshold for aggressive cleanup (default: 200)
# ---------------------------------------------------------------------------

set -euo pipefail

readonly CLEANUP_AGE_DAYS="${CLEANUP_AGE_DAYS:-7}"
readonly DRY_RUN="${DRY_RUN:-false}"
readonly LOG_PREFIX="[system-cleanup]"
readonly CRITICAL_SPACE_GB="${CRITICAL_SPACE_GB:-200}"
readonly TEAMS_WEBHOOK_URL="${TEAMS_WEBHOOK_URL:-}"
readonly GPUCORE_AGE_DAYS="${GPUCORE_AGE_DAYS:-0}"  # Clean ALL GPU core dumps by default (0 = all files)
readonly GPUCORE_SEARCH_DIR="${GPUCORE_SEARCH_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"

###########################################################################
# Helper Functions
###########################################################################

log_info() {
    echo "$LOG_PREFIX $1"
}

log_success() {
    echo "$LOG_PREFIX ✅ $1"
}

log_warning() {
    echo "$LOG_PREFIX ⚠️  $1"
}

log_error() {
    echo "$LOG_PREFIX ❌ $1"
}

get_disk_usage() {
    df -h / | tail -1 | awk '{print $5 " (" $4 " free)"}'
}

get_free_space_gb() {
    # Returns free space in GB
    df -BG / | tail -1 | awk '{print $4}' | sed 's/G//'
}

send_teams_alert() {
    local TITLE="$1"
    local MESSAGE="$2"
    local COLOR="${3:-warning}"

    if [ -z "$TEAMS_WEBHOOK_URL" ]; then
        log_warning "Teams webhook URL not set, skipping alert"
        return 0
    fi

    local THEME_COLOR
    case "$COLOR" in
        critical) THEME_COLOR="FF0000" ;;  # Red
        warning) THEME_COLOR="FFA500" ;;   # Orange
        success) THEME_COLOR="00FF00" ;;   # Green
        *) THEME_COLOR="0078D4" ;;         # Blue
    esac

    local PAYLOAD=$(cat <<EOF
{
    "@type": "MessageCard",
    "@context": "https://schema.org/extensions",
    "themeColor": "${THEME_COLOR}",
    "title": "${TITLE}",
    "text": "${MESSAGE}",
    "sections": [{
        "facts": [
            {"name": "Server", "value": "$(hostname)"},
            {"name": "Time", "value": "$(date '+%Y-%m-%d %H:%M:%S %Z')"},
            {"name": "Free Space", "value": "$(get_free_space_gb) GB"}
        ]
    }]
}
EOF
    )

    curl -s -X POST "$TEAMS_WEBHOOK_URL" \
        -H "Content-Type: application/json" \
        -d "$PAYLOAD" > /dev/null 2>&1 || log_warning "Failed to send Teams alert"
}

###########################################################################
# Main Cleanup Logic
###########################################################################

log_info "=========================================="
log_info "Docker & GPU Core Cleanup Script Started"
log_info "Date: $(date '+%Y-%m-%d %H:%M:%S %Z')"
log_info "Cleanup Docker images older than: ${CLEANUP_AGE_DAYS} days"
if [ "$GPUCORE_AGE_DAYS" -eq 0 ]; then
    log_info "Cleanup GPU core dumps: ALL files"
else
    log_info "Cleanup GPU core dumps older than: ${GPUCORE_AGE_DAYS} days"
fi
log_info "GPU core search directory: ${GPUCORE_SEARCH_DIR}"
log_info "Critical space threshold: ${CRITICAL_SPACE_GB} GB"
log_info "Dry run mode: ${DRY_RUN}"
log_info "=========================================="

# Show initial disk usage
INITIAL_DISK=$(get_disk_usage)
INITIAL_FREE_GB=$(get_free_space_gb)
log_info "Initial disk usage: $INITIAL_DISK"
log_info "Initial free space: ${INITIAL_FREE_GB} GB"

###########################################################################
# Check if we're in critical disk space situation
###########################################################################

AGGRESSIVE_CLEANUP=false

if [ "$INITIAL_FREE_GB" -lt "$CRITICAL_SPACE_GB" ]; then
    log_error "CRITICAL: Free space (${INITIAL_FREE_GB} GB) is below threshold (${CRITICAL_SPACE_GB} GB)!"
    log_warning "Switching to AGGRESSIVE cleanup mode"
    AGGRESSIVE_CLEANUP=true

    # Send alert
    send_teams_alert \
        "🚨 Critical Disk Space Alert - $(hostname)" \
        "Free space is critically low at ${INITIAL_FREE_GB} GB (threshold: ${CRITICAL_SPACE_GB} GB). Running aggressive Docker cleanup." \
        "critical"
else
    log_info "Disk space is healthy (${INITIAL_FREE_GB} GB available)"
fi

###########################################################################
# 1. Remove stopped Docker containers
###########################################################################

log_info "Step 1: Removing stopped Docker containers..."

STOPPED_CONTAINERS=$(docker ps -a -q -f status=exited 2>/dev/null || true)

if [ -n "$STOPPED_CONTAINERS" ]; then
    CONTAINER_COUNT=$(echo "$STOPPED_CONTAINERS" | wc -l)
    log_info "Found $CONTAINER_COUNT stopped container(s)"

    if [ "$DRY_RUN" = "true" ]; then
        log_info "[DRY RUN] Would remove containers: $STOPPED_CONTAINERS"
    else
        docker container prune -f > /dev/null 2>&1 || log_warning "Failed to remove some containers"
        log_success "Removed stopped containers"
    fi
else
    log_info "No stopped containers found"
fi

###########################################################################
# 2. Remove old sgl-dev Docker images (older than CLEANUP_AGE_DAYS)
###########################################################################

# Use more aggressive cleanup age if in critical mode
EFFECTIVE_CLEANUP_DAYS=$CLEANUP_AGE_DAYS
if [ "$AGGRESSIVE_CLEANUP" = "true" ]; then
    EFFECTIVE_CLEANUP_DAYS=3
    log_warning "Using aggressive cleanup: removing images older than ${EFFECTIVE_CLEANUP_DAYS} days"
fi

log_info "Step 2: Removing old sgl-dev images (older than ${EFFECTIVE_CLEANUP_DAYS} days)..."

# Calculate cutoff date (EFFECTIVE_CLEANUP_DAYS ago)
CUTOFF_HOURS=$((EFFECTIVE_CLEANUP_DAYS * 24))

# Find and remove old sgl-dev images
OLD_IMAGES=$(docker images --filter "reference=*/sgl-dev:*" --format "{{.Repository}}:{{.Tag}}" | grep -E "sgl-dev.*[0-9]{8}" || true)

if [ -n "$OLD_IMAGES" ]; then
    REMOVED_COUNT=0
    SKIPPED_COUNT=0

    while IFS= read -r IMAGE; do
        # Extract date from image tag (format: ...-YYYYMMDD)
        IMAGE_DATE=$(echo "$IMAGE" | grep -oE "[0-9]{8}" | tail -1)

        if [ -n "$IMAGE_DATE" ]; then
            # Convert to seconds since epoch
            IMAGE_EPOCH=$(date -d "$IMAGE_DATE" +%s 2>/dev/null || echo "0")
            CUTOFF_EPOCH=$(date -d "${EFFECTIVE_CLEANUP_DAYS} days ago" +%s)

            if [ "$IMAGE_EPOCH" -lt "$CUTOFF_EPOCH" ] && [ "$IMAGE_EPOCH" -gt "0" ]; then
                if [ "$DRY_RUN" = "true" ]; then
                    log_info "[DRY RUN] Would remove: $IMAGE (date: $IMAGE_DATE)"
                    REMOVED_COUNT=$((REMOVED_COUNT + 1))
                else
                    if docker rmi "$IMAGE" > /dev/null 2>&1; then
                        log_success "Removed: $IMAGE"
                        REMOVED_COUNT=$((REMOVED_COUNT + 1))
                    else
                        log_warning "Failed to remove (may be in use): $IMAGE"
                        SKIPPED_COUNT=$((SKIPPED_COUNT + 1))
                    fi
                fi
            else
                SKIPPED_COUNT=$((SKIPPED_COUNT + 1))
            fi
        fi
    done <<< "$OLD_IMAGES"

    log_info "Processed old sgl-dev images: $REMOVED_COUNT removed, $SKIPPED_COUNT kept/skipped"
else
    log_info "No old sgl-dev images found"
fi

###########################################################################
# 3. Remove dangling images and unused data
###########################################################################

log_info "Step 3: Removing dangling images and unused data..."

if [ "$DRY_RUN" = "true" ]; then
    log_info "[DRY RUN] Would prune dangling images and unused data"
else
    # Remove dangling images (untagged)
    docker image prune -f > /dev/null 2>&1 || log_warning "Failed to prune some images"

    # Remove unused volumes (be careful with this - only uncomment if needed)
    # docker volume prune -f > /dev/null 2>&1 || log_warning "Failed to prune volumes"

    log_success "Cleaned up dangling images"
fi

###########################################################################
# 4. Remove old GPU core dump files
###########################################################################

log_info "Step 4: Removing old GPU core dump files (gpucore.*)..."

# Use more aggressive cleanup for GPU cores in critical mode
EFFECTIVE_GPUCORE_DAYS=$GPUCORE_AGE_DAYS
if [ "$AGGRESSIVE_CLEANUP" = "true" ]; then
    EFFECTIVE_GPUCORE_DAYS=0
    log_warning "Using aggressive cleanup: removing ALL GPU core dumps"
fi

# Find GPU core dump files based on age threshold
if [ "$EFFECTIVE_GPUCORE_DAYS" -eq 0 ]; then
    # Clean ALL gpucore files when age is 0
    GPUCORE_FILES=$(find "$GPUCORE_SEARCH_DIR" -maxdepth 1 -type f -name "gpucore.*" 2>/dev/null || true)
else
    # Clean only files older than specified days
    GPUCORE_FILES=$(find "$GPUCORE_SEARCH_DIR" -maxdepth 1 -type f -name "gpucore.*" -mtime +${EFFECTIVE_GPUCORE_DAYS} 2>/dev/null || true)
fi

if [ -n "$GPUCORE_FILES" ]; then
    GPUCORE_COUNT=$(echo "$GPUCORE_FILES" | wc -l)
    GPUCORE_SIZE=0

    # Calculate total size
    while IFS= read -r GPUCORE_FILE; do
        if [ -f "$GPUCORE_FILE" ]; then
            FILE_SIZE=$(stat -c%s "$GPUCORE_FILE" 2>/dev/null || echo "0")
            GPUCORE_SIZE=$((GPUCORE_SIZE + FILE_SIZE))
        fi
    done <<< "$GPUCORE_FILES"

    GPUCORE_SIZE_GB=$((GPUCORE_SIZE / 1024 / 1024 / 1024))
    if [ "$EFFECTIVE_GPUCORE_DAYS" -eq 0 ]; then
        log_info "Found $GPUCORE_COUNT GPU core dump file(s) (total: ~${GPUCORE_SIZE_GB} GB)"
    else
        log_info "Found $GPUCORE_COUNT GPU core dump file(s) older than ${EFFECTIVE_GPUCORE_DAYS} day(s) (total: ~${GPUCORE_SIZE_GB} GB)"
    fi

    if [ "$DRY_RUN" = "true" ]; then
        while IFS= read -r GPUCORE_FILE; do
            log_info "[DRY RUN] Would remove: $(basename "$GPUCORE_FILE")"
        done <<< "$GPUCORE_FILES"
    else
        REMOVED_GPUCORE_COUNT=0
        while IFS= read -r GPUCORE_FILE; do
            if rm -f "$GPUCORE_FILE" 2>/dev/null; then
                log_success "Removed: $(basename "$GPUCORE_FILE")"
                REMOVED_GPUCORE_COUNT=$((REMOVED_GPUCORE_COUNT + 1))
            else
                log_warning "Failed to remove: $(basename "$GPUCORE_FILE")"
            fi
        done <<< "$GPUCORE_FILES"
        log_success "Removed $REMOVED_GPUCORE_COUNT GPU core dump file(s), freed ~${GPUCORE_SIZE_GB} GB"
    fi
else
    log_info "No old GPU core dump files found"
fi

###########################################################################
# 5. Remove other old images if in aggressive mode or disk still critically low
###########################################################################

CURRENT_FREE_GB=$(get_free_space_gb)
CURRENT_DISK_PERCENT=$(df / | tail -1 | awk '{print int($5)}')

if [ "$AGGRESSIVE_CLEANUP" = "true" ] || [ "$CURRENT_DISK_PERCENT" -gt 85 ]; then
    log_warning "Additional cleanup needed - Free: ${CURRENT_FREE_GB} GB, Usage: ${CURRENT_DISK_PERCENT}%"
    log_info "Removing all unused Docker images older than ${EFFECTIVE_CLEANUP_DAYS} days..."

    if [ "$DRY_RUN" = "true" ]; then
        log_info "[DRY RUN] Would prune all unused images older than ${EFFECTIVE_CLEANUP_DAYS} days"
    else
        EFFECTIVE_CUTOFF_HOURS=$((EFFECTIVE_CLEANUP_DAYS * 24))
        PRUNE_OUTPUT=$(docker image prune -a --filter "until=${EFFECTIVE_CUTOFF_HOURS}h" -f 2>&1 || true)

        if echo "$PRUNE_OUTPUT" | grep -q "Total reclaimed space"; then
            RECLAIMED=$(echo "$PRUNE_OUTPUT" | grep "Total reclaimed space" | tail -1)
            log_success "Pruned additional images: $RECLAIMED"
        else
            log_info "No additional images to prune"
        fi
    fi

    # Check if cleanup was effective
    AFTER_CLEANUP_FREE_GB=$(get_free_space_gb)
    if [ "$AFTER_CLEANUP_FREE_GB" -lt "$CRITICAL_SPACE_GB" ]; then
        log_error "WARNING: Space still critically low after cleanup (${AFTER_CLEANUP_FREE_GB} GB free)"

        # Send critical alert
        send_teams_alert \
            "🔴 URGENT: Disk Space Still Critical - $(hostname)" \
            "After aggressive cleanup, only ${AFTER_CLEANUP_FREE_GB} GB free. Manual intervention may be required!" \
            "critical"
    fi
fi

###########################################################################
# Summary
###########################################################################

FINAL_DISK=$(get_disk_usage)
FINAL_FREE_GB=$(get_free_space_gb)
SPACE_FREED=$((FINAL_FREE_GB - INITIAL_FREE_GB))

log_info "=========================================="
log_success "Docker Cleanup Complete"
log_info "Initial disk usage: $INITIAL_DISK (${INITIAL_FREE_GB} GB free)"
log_info "Final disk usage: $FINAL_DISK (${FINAL_FREE_GB} GB free)"
log_info "Space freed: ${SPACE_FREED} GB"
log_info "Date: $(date '+%Y-%m-%d %H:%M:%S %Z')"
log_info "=========================================="

# Show Docker disk usage summary
log_info "Docker disk usage summary:"
docker system df 2>/dev/null || log_warning "Failed to get Docker disk usage"

# Send success alert if we recovered from critical state
if [ "$AGGRESSIVE_CLEANUP" = "true" ] && [ "$FINAL_FREE_GB" -ge "$CRITICAL_SPACE_GB" ]; then
    send_teams_alert \
        "✅ Disk Space Recovered - $(hostname)" \
        "Aggressive cleanup freed ${SPACE_FREED} GB. Current free space: ${FINAL_FREE_GB} GB" \
        "success"
elif [ "$AGGRESSIVE_CLEANUP" = "true" ]; then
    log_warning "Cleanup completed but space is still below threshold"
fi

exit 0
