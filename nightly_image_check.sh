#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# nightly_image_check.sh - SGL Nightly Docker Image Availability Checker
#
# DESCRIPTION:
#   Checks for availability of nightly Docker images for both mi30x and mi35x
#   hardware types using Docker Hub API. Reports missing images and provides
#   links to GitHub workflow for troubleshooting build issues.
#
# IMAGE FORMAT:
#   • mi30x: v0.5.0rc2-rocm630-mi30x-YYYYMMDD (ROCM version auto-detected)
#   • mi35x: v0.5.0rc2-rocm700-mi35x-YYYYMMDD (ROCM version auto-detected)
#   • Automatically excludes SRT variants (ends with -srt)
#   • ROCM versions are dynamically discovered from available Docker images
#
# USAGE:
#   nightly_image_check.sh [OPTIONS]
#
# OPTIONS:
#   --date=YYYYMMDD      Check for specific date [default: today]
#   --days=N             Check for last N days [default: 1]
#   --teams-webhook=URL  Teams webhook URL for alerts [env: TEAMS_WEBHOOK_URL]
#   --help, -h           Show help message
#
# EXAMPLES:
#   nightly_image_check.sh                    # Check today's images
#   nightly_image_check.sh --date=20250806    # Check specific date
#   nightly_image_check.sh --days=3           # Check last 3 days
#   nightly_image_check.sh --teams-webhook="https://..." # Check images with Teams alerts
# ---------------------------------------------------------------------------

set -euo pipefail

###############################################################################
# Configuration Variables
###############################################################################

# Docker configuration
IMAGE_REPO="${IMAGE_REPO:-rocm/sgl-dev}"

# Hardware types to check
HARDWARE_TYPES=("mi30x" "mi35x")

# ROCM versions will be dynamically discovered from Docker Hub

# Timezone for date calculations (San Francisco time)
TIME_ZONE="${TIME_ZONE:-America/Los_Angeles}"

# GitHub workflow link for troubleshooting
GITHUB_WORKFLOW_URL="https://github.com/sgl-project/sglang/actions/workflows/release-docker-amd-nightly.yml"

# Teams notification configuration
TEAMS_WEBHOOK_URL="${TEAMS_WEBHOOK_URL:-}"
TEAMS_ALERT_SCRIPT="${TEAMS_ALERT_SCRIPT:-$(dirname "$0")/team_alert/send_docker_image_alert.py}"

###############################################################################
# CLI Parameter Processing
###############################################################################
CUSTOM_DATE=""
CHECK_DAYS=1

for arg in "$@"; do
  case $arg in
    --date=*)
      CUSTOM_DATE="${arg#*=}"
      ;;
    --days=*)
      CHECK_DAYS="${arg#*=}"
      # Validate that it's a positive integer
      if ! [[ "$CHECK_DAYS" =~ ^[1-9][0-9]*$ ]]; then
        echo "[check] ERROR: --days must be a positive integer (got: $CHECK_DAYS)"
        exit 1
      fi
      ;;
    --teams-webhook=*)
      TEAMS_WEBHOOK_URL="${arg#*=}"
      ;;
    --help|-h)
      echo "Usage: $0 [OPTIONS]"
      echo ""
      echo "Check availability of SGL nightly Docker images for mi30x and mi35x hardware"
      echo ""
      echo "Options:"
      echo "  --date=YYYYMMDD          Check for specific date [default: today]"
      echo "  --days=N                 Check for last N days [default: 1]"
      echo "  --teams-webhook=URL      Teams webhook URL for alerts [env: TEAMS_WEBHOOK_URL]"
      echo "  --help, -h               Show this help message"
      echo ""
      echo "Examples:"
      echo "  $0                            # Check today's images"
      echo "  $0 --date=20250806            # Check specific date"
      echo "  $0 --days=3                   # Check last 3 days"
      echo "  $0 --teams-webhook=\"https://...\" # Check images with Teams alerts"
      echo ""
      echo "Hardware Types Checked:"
      echo "  mi30x: Images like v0.5.0rc2-rocm630-mi30x-YYYYMMDD (ROCM version auto-detected)"
      echo "  mi35x: Images like v0.5.0rc2-rocm700-mi35x-YYYYMMDD (ROCM version auto-detected)"
      echo ""
      echo "Teams Notifications:"
      echo "  Automatically sends Teams alerts when --teams-webhook is provided"
      echo "  Set TEAMS_WEBHOOK_URL environment variable with your Teams webhook"
      echo ""
      echo "Troubleshooting:"
      echo "  If images are missing, check GitHub workflow for build errors:"
      echo "  ${GITHUB_WORKFLOW_URL}"
      exit 0
      ;;
    *)
      echo "Unknown argument: $arg"
      echo "Use --help for usage information"
      exit 1
      ;;
  esac
done

###############################################################################
# Utility Functions
###############################################################################

# Generate date in PST timezone
date_pst() {
  if [[ -n "$CUSTOM_DATE" ]]; then
    echo "$CUSTOM_DATE"
  else
    TZ="$TIME_ZONE" date -d "-$1 day" +%Y%m%d
  fi
}

# Find Docker image for a specific date and hardware type using Docker Hub API
find_image_for_date_and_hardware() {
  local repo="$1" target_date="$2" hardware_type="$3"

  # First, discover available ROCM versions for this hardware and date
  local available_rocm_versions
  if ! available_rocm_versions=$(discover_rocm_versions "$repo" "$target_date" "$hardware_type"); then
    return 1  # No ROCM versions found for this hardware/date combination
  fi

  # Try each discovered ROCM version until we find a valid image
  while IFS= read -r rocm_version; do
    [[ -z "$rocm_version" ]] && continue

    local next_url="https://hub.docker.com/v2/repositories/${repo}/tags/?page_size=100"
    local use_jq=$(command -v jq &> /dev/null && echo "true" || echo "false")
    local search_pattern="-${rocm_version}-${hardware_type}-${target_date}"

    while [[ -n "$next_url" && "$next_url" != "null" ]]; do
      local response=$(curl -s --max-time 15 "$next_url")

      [[ -z "$response" || "$response" == *"not found"* || "$response" == *"error"* ]] && break

      # Extract and filter tags based on available tools
      local found_tag=""
      if [[ "$use_jq" == "true" ]]; then
        found_tag=$(echo "$response" | jq -r '.results[].name' | grep -- "${search_pattern}" | grep -v -- "-srt$" | head -1)
        next_url=$(echo "$response" | jq -r '.next // empty')
      else
        found_tag=$(echo "$response" | grep -o '"name":"[^"]*"' | cut -d'"' -f4 | grep -- "${search_pattern}" | grep -v -- "-srt$" | head -1)
        next_url=$(echo "$response" | grep -o '"next":"[^"]*"' | cut -d'"' -f4)
        # If next_url is "null", set to empty string
        if [[ "$next_url" == "null" ]]; then
          next_url=""
        fi
      fi

      if [[ -n "$found_tag" ]]; then
        echo "$found_tag"
        return 0
      fi
      [[ -z "$next_url" || "$next_url" == "null" ]] && break
    done
  done <<< "$available_rocm_versions"

  return 1
}

# Verify image can be pulled from Docker Hub
verify_image_pullable() {
  local full_image="$1"

  # Try to get image manifest without pulling
  if docker manifest inspect "$full_image" >/dev/null 2>&1; then
    return 0
  else
    return 1
  fi
}

# Discover available ROCM versions for a specific hardware type and date
discover_rocm_versions() {
  local repo="$1" target_date="$2" hardware_type="$3"
  local next_url="https://hub.docker.com/v2/repositories/${repo}/tags/?page_size=100"
  local use_jq=$(command -v jq &> /dev/null && echo "true" || echo "false")
  local search_pattern="-${hardware_type}-${target_date}"
  local rocm_versions=()

  while [[ -n "$next_url" && "$next_url" != "null" ]]; do
    local response=$(curl -s --max-time 15 "$next_url")

    [[ -z "$response" || "$response" == *"not found"* || "$response" == *"error"* ]] && break

    # Extract tags and find ROCM versions
    local tags=""
    if [[ "$use_jq" == "true" ]]; then
      tags=$(echo "$response" | jq -r '.results[].name' | grep -- "${search_pattern}" | grep -v -- "-srt$")
      next_url=$(echo "$response" | jq -r '.next // empty')
    else
      tags=$(echo "$response" | grep -o '"name":"[^"]*"' | cut -d'"' -f4 | grep -- "${search_pattern}" | grep -v -- "-srt$")
      next_url=$(echo "$response" | grep -o '"next":"[^"]*"' | cut -d'"' -f4)
      # If next_url is "null", set to empty string
      if [[ "$next_url" == "null" ]]; then
        next_url=""
      fi
    fi

    # Extract ROCM versions from matching tags
    while IFS= read -r tag; do
      [[ -z "$tag" ]] && continue
      # Extract rocm version using pattern like: v0.5.0rc2-rocm630-mi30x-20250820
      if [[ "$tag" =~ -rocm([0-9]+)- ]]; then
        local rocm_version="rocm${BASH_REMATCH[1]}"
        # Add to array if not already present
        if [[ ! " ${rocm_versions[*]} " =~ " ${rocm_version} " ]]; then
          rocm_versions+=("$rocm_version")
        fi
      fi
    done <<< "$tags"

    [[ -z "$next_url" || "$next_url" == "null" ]] && break
  done

  # Return the found ROCM versions (latest first)
  if [[ ${#rocm_versions[@]} -gt 0 ]]; then
    printf '%s\n' "${rocm_versions[@]}" | sort -rV
    return 0
  else
    return 1
  fi
}

# Send Teams notification about Docker image status
send_teams_notification() {
  local status="$1"          # success, warning, error
  local message="$2"         # Main status message
  local checked_count="$3"   # Total images checked
  local found_count="$4"     # Images found
  local date_suffix="$5"     # Date checked (YYYYMMDD)
  shift 5
  # Split remaining arguments - available images come before details
  local available_images=()
  local details=()
  local processing_available=true

  for arg in "$@"; do
    if [[ "$arg" == "--details-start" ]]; then
      processing_available=false
      continue
    fi

    if [[ "$processing_available" == "true" ]]; then
      available_images+=("$arg")
    else
      details+=("$arg")
    fi
  done

  # Check if Teams notification is configured
  if [[ -z "$TEAMS_WEBHOOK_URL" ]]; then
    return 0  # No webhook URL configured, skip Teams notification
  fi

  if [[ ! -f "$TEAMS_ALERT_SCRIPT" ]]; then
    echo "[check] Warning: Teams alert script not found at $TEAMS_ALERT_SCRIPT"
    return 1
  fi

  # Check if Python 3 is available
  if ! command -v python3 &> /dev/null; then
    echo "[check] Warning: python3 not available for Teams notifications"
    return 1
  fi

  echo ""
  echo "[check] =========================================="
  echo "[check] Sending Teams Notification"
  echo "[check] =========================================="

  # Build command arguments
  local cmd_args=(
    "$TEAMS_ALERT_SCRIPT"
    "--status=$status"
    "--message=$message"
    "--checked-count=$checked_count"
    "--found-count=$found_count"
    "--webhook-url=$TEAMS_WEBHOOK_URL"
  )

  # Add date if provided
  if [[ -n "$date_suffix" ]]; then
    cmd_args+=("--date-checked=$date_suffix")
  fi

  # Add available images if any
  if [[ ${#available_images[@]} -gt 0 ]]; then
    cmd_args+=("--available-images" "${available_images[@]}")
  fi

  # Add details if any
  if [[ ${#details[@]} -gt 0 ]]; then
    cmd_args+=("--details" "${details[@]}")
  fi

  # Execute the Teams notification script
  if python3 "${cmd_args[@]}"; then
    echo "[check] Teams notification sent successfully"
    return 0
  else
    echo "[check] Failed to send Teams notification"
    return 1
  fi
}

###############################################################################
# Main Logic
###############################################################################

echo "[check] =========================================="
echo "[check] SGL Nightly Docker Image Availability Check"
echo "[check] =========================================="
echo "[check] Repository: $IMAGE_REPO"
echo "[check] Hardware types: ${HARDWARE_TYPES[*]}"

# Check curl availability
if ! command -v curl &> /dev/null; then
  echo "[check] ERROR: curl is required but not found."
  exit 1
fi

# Track overall results
TOTAL_IMAGES_CHECKED=0
TOTAL_IMAGES_FOUND=0
MISSING_IMAGES=()
AVAILABLE_IMAGES=()

# Check images for the specified number of days
for offset in $(seq 0 $((CHECK_DAYS - 1))); do
  if [[ -n "$CUSTOM_DATE" && "$offset" -gt 0 ]]; then
    # If custom date is specified, only check that one date
    break
  fi

  date_suffix=$(date_pst "$offset")

  if [[ "$CHECK_DAYS" -gt 1 ]]; then
    echo ""
    echo "[check] ------------------------------------------"
    echo "[check] Checking images for date: $date_suffix"
    echo "[check] ------------------------------------------"
  else
    echo "[check] Checking images for date: $date_suffix"
    echo ""
  fi

  # Check each hardware type
  for hardware in "${HARDWARE_TYPES[@]}"; do
    TOTAL_IMAGES_CHECKED=$((TOTAL_IMAGES_CHECKED + 1))

    echo -n "[check] Searching for $hardware image (discovering ROCM version)... "

    # Find the image tag
    if candidate_tag=$(find_image_for_date_and_hardware "$IMAGE_REPO" "$date_suffix" "$hardware"); then
      full_image="${IMAGE_REPO}:${candidate_tag}"

      # Verify the image is actually pullable
      echo -n "found, verifying... "
      if verify_image_pullable "$full_image"; then
        echo "✓ AVAILABLE"
        echo "[check]   Image: $full_image"
        TOTAL_IMAGES_FOUND=$((TOTAL_IMAGES_FOUND + 1))
        # Extract ROCM version from the found tag for better Teams notification
        if [[ "$candidate_tag" =~ -rocm([0-9]+)- ]]; then
          discovered_rocm="rocm${BASH_REMATCH[1]}"
          AVAILABLE_IMAGES+=("$hardware ($discovered_rocm): $candidate_tag")
        else
          AVAILABLE_IMAGES+=("$hardware: $candidate_tag")
        fi
      else
        echo "✗ FOUND BUT NOT PULLABLE"
        echo "[check]   Image: $full_image (manifest check failed)"
        MISSING_IMAGES+=("$hardware ($date_suffix): $full_image - not pullable")
      fi
    else
      echo "✗ NOT FOUND"
      MISSING_IMAGES+=("$hardware ($date_suffix): no image found for any available ROCM version")
    fi
  done
done

echo ""
echo "[check] =========================================="
echo "[check] Summary"
echo "[check] =========================================="
echo "[check] Total images checked: $TOTAL_IMAGES_CHECKED"
echo "[check] Available images: $TOTAL_IMAGES_FOUND"
echo "[check] Missing images: ${#MISSING_IMAGES[@]}"

# Determine date suffix for Teams notification (use the first date checked)
TEAMS_DATE_SUFFIX=""
if [[ -n "$CUSTOM_DATE" ]]; then
  TEAMS_DATE_SUFFIX="$CUSTOM_DATE"
else
  TEAMS_DATE_SUFFIX=$(date_pst 0)
fi

if [[ ${#MISSING_IMAGES[@]} -eq 0 ]]; then
  echo ""
  echo "[check] ✓ All expected images are available!"

  # Send Teams notification for success
  send_teams_notification "success" "All expected Docker images are available" \
    "$TOTAL_IMAGES_CHECKED" "$TOTAL_IMAGES_FOUND" "$TEAMS_DATE_SUFFIX" \
    "${AVAILABLE_IMAGES[@]}" "--details-start"

  exit 0
else
  echo ""
  echo "[check] ✗ Missing or problematic images:"
  for missing in "${MISSING_IMAGES[@]}"; do
    echo "[check]   - $missing"
  done

  echo ""
  echo "[check] =========================================="
  echo "[check] Troubleshooting"
  echo "[check] =========================================="
  echo "[check] Some images are missing or not accessible."
  echo "[check] This could indicate build failures in the nightly pipeline."
  echo ""
  echo "[check] To investigate build errors, check the GitHub workflow:"
  echo "[check] $GITHUB_WORKFLOW_URL"
  echo ""
  echo "[check] Look for recent workflow runs around the missing dates."
  echo "[check] Check for:"
  echo "[check]   - Build failures in the workflow logs"
  echo "[check]   - Docker registry push errors"
  echo "[check]   - Resource or timeout issues"
  echo "[check]   - AMD-specific build problems"

  # Send Teams notification for missing images
  missing_count=${#MISSING_IMAGES[@]}
  if [[ $missing_count -eq 1 ]]; then
    status_message="1 Docker image is missing or not accessible"
  else
    status_message="$missing_count Docker images are missing or not accessible"
  fi

  # Determine status level
  if [[ $TOTAL_IMAGES_FOUND -eq 0 ]]; then
    teams_status="error"
  else
    teams_status="warning"
  fi

  send_teams_notification "$teams_status" "$status_message" \
    "$TOTAL_IMAGES_CHECKED" "$TOTAL_IMAGES_FOUND" "$TEAMS_DATE_SUFFIX" \
    "${AVAILABLE_IMAGES[@]}" "--details-start" "${MISSING_IMAGES[@]}"

  exit 1
fi
