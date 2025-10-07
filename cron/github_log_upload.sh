#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# github_log_upload.sh - Push logs to the sglang-ci-data repository
#
# This helper script copies the generated logs into a working clone of the
# dedicated data repository and pushes a commit so the logs are permanently preserved.
#
# Supports both cron logs and sanity check logs.
#
# Usage (inside another script / cron entry):
#     bash cron/github_log_upload.sh [DATE] [HARDWARE_TYPE] [LOG_TYPE] [FOLDER]
#
# Examples:
#     # Cron logs
#     bash cron/github_log_upload.sh                          # Upload today's cron logs for $HARDWARE_TYPE
#     bash cron/github_log_upload.sh 20251005                 # Upload 2025-10-05 cron logs for $HARDWARE_TYPE
#     bash cron/github_log_upload.sh 20251005 mi30x           # Upload 2025-10-05 cron logs for mi30x
#     bash cron/github_log_upload.sh 20251005 mi30x cron      # Same as above (explicit)
#
#     # Sanity logs (4th arg is docker image tag/folder name)
#     bash cron/github_log_upload.sh "" mi30x sanity v0.5.3rc0-rocm630-mi30x-20251005
#
#     # Direct path upload (auto-detects type when 4th arg is absolute path)
#     bash cron/github_log_upload.sh "" "" "" /mnt/raid/michael/sglang-ci/test/sanity_check_log/mi30x/v0.5.3rc0
#
# Arguments:
#   DATE          – Optional. Date in YYYYMMDD format (defaults to today). Ignored if FOLDER is provided.
#   HARDWARE_TYPE – Optional. Machine descriptor (defaults to $HARDWARE_TYPE env var or 'unknown')
#   LOG_TYPE      – Optional. Type of logs: 'cron' or 'sanity' (defaults to 'cron')
#   FOLDER        – Optional. Folder name (relative) or full path (absolute). If relative, builds path based on LOG_TYPE.
#
# Requirements:
#   • GITHUB_TOKEN    – Personal access token with `repo` scope that can push
#   • HARDWARE_TYPE   – The machine descriptor (mi30x / mi35x …).
#
# The script is *idempotent*: running it multiple times will only
# update the repository with new / changed files.  It also performs a re-base
# pull to minimise the chance of push rejects when multiple cron jobs run in
# parallel.
# ---------------------------------------------------------------------------

set -euo pipefail

###########################################################################
# 1. Basic environment
###########################################################################

# Repository that stores the published logs – do **not** include the token
readonly REMOTE_REPO_URL_BASE="https://github.com/michael-amd/sglang-ci-data.git"

# Resolve required env variables (most are exported in crontab header)
readonly GITHUB_TOKEN="${GITHUB_TOKEN:-}"  # optional – clone over https if empty
readonly SGL_CI_DIR="${SGL_BENCHMARK_CI_DIR:-$(pwd)}"  # repository root

# Parse arguments
readonly ARG_DATE="${1:-}"
readonly ARG_HARDWARE="${2:-}"
readonly ARG_LOG_TYPE="${3:-cron}"  # Default to 'cron' if not specified
readonly ARG_FOLDER="${4:-}"        # Folder name or full path (optional)

# Determine source log directory
if [[ -n "$ARG_FOLDER" ]]; then
  # Check if ARG_FOLDER is an absolute path or just a folder name
  if [[ "$ARG_FOLDER" == /* ]]; then
    # It's a full path - use directly
    readonly SRC_LOG_DIR="$ARG_FOLDER"

    # Auto-detect hardware type and log type from path
    if [[ "$SRC_LOG_DIR" == *"/cron_log/"* ]]; then
      readonly LOG_TYPE="cron"
      # Extract hardware and date from path: .../cron_log/<hw>/<date>
      HARDWARE_TYPE=$(echo "$SRC_LOG_DIR" | grep -oP 'cron_log/\K[^/]+' || echo "${ARG_HARDWARE:-${HARDWARE_TYPE:-unknown}}")
      DATE_FOLDER=$(basename "$SRC_LOG_DIR")
    elif [[ "$SRC_LOG_DIR" == *"/sanity_check_log/"* ]]; then
      readonly LOG_TYPE="sanity"
      # Extract hardware from path: .../sanity_check_log/<hw>/<docker_name>
      HARDWARE_TYPE=$(echo "$SRC_LOG_DIR" | grep -oP 'sanity_check_log/\K[^/]+' || echo "${ARG_HARDWARE:-${HARDWARE_TYPE:-unknown}}")
      DATE_FOLDER=$(basename "$SRC_LOG_DIR")
    else
      readonly LOG_TYPE="$ARG_LOG_TYPE"
      HARDWARE_TYPE="${ARG_HARDWARE:-${HARDWARE_TYPE:-unknown}}"
      DATE_FOLDER=$(basename "$SRC_LOG_DIR")
    fi
  else
    # It's just a folder name - build full path based on log type
    readonly LOG_TYPE="$ARG_LOG_TYPE"
    HARDWARE_TYPE="${ARG_HARDWARE:-${HARDWARE_TYPE:-unknown}}"
    DATE_FOLDER="$ARG_FOLDER"

    if [[ "$LOG_TYPE" == "sanity" ]]; then
      readonly SRC_LOG_DIR="${SGL_CI_DIR}/test/sanity_check_log/${HARDWARE_TYPE}/${ARG_FOLDER}"
    else
      readonly SRC_LOG_DIR="${SGL_CI_DIR}/cron/cron_log/${HARDWARE_TYPE}/${ARG_FOLDER}"
    fi
  fi
else
  # Build path from arguments
  readonly LOG_TYPE="$ARG_LOG_TYPE"
  readonly DATE="${ARG_DATE:-$(date +%Y%m%d)}"
  HARDWARE_TYPE="${ARG_HARDWARE:-${HARDWARE_TYPE:-unknown}}"

  if [[ "$LOG_TYPE" == "sanity" ]]; then
    # For sanity logs, DATE is actually the docker image tag
    readonly SRC_LOG_DIR="${SGL_CI_DIR}/test/sanity_check_log/${HARDWARE_TYPE}/${DATE}"
    DATE_FOLDER="$DATE"
  else
    # For cron logs, DATE is YYYYMMDD format
    readonly SRC_LOG_DIR="${SGL_CI_DIR}/cron/cron_log/${HARDWARE_TYPE}/${DATE}"
    DATE_FOLDER="$DATE"
  fi
fi

readonly HARDWARE_TYPE
readonly DATE_FOLDER

# Skip gracefully if there is nothing to upload yet
if [[ ! -d "$SRC_LOG_DIR" ]]; then
  echo "[github_log_upload] No logs found in $SRC_LOG_DIR – skipping upload."
  exit 0
fi

# Working clone location (outside CI repo for shared access)
readonly WORK_CLONE_DIR="/mnt/raid/michael/sglang-ci-data"

###########################################################################
# 2. Ensure we have a clone of the data repository
###########################################################################

if [[ ! -d "$WORK_CLONE_DIR/.git" ]]; then
  echo "[github_log_upload] Cloning data repository…"
  # When a token is available inject it into the clone URL so pushes succeed.
  if [[ -n "$GITHUB_TOKEN" ]]; then
    git clone "https://${GITHUB_TOKEN}@github.com/michael-amd/sglang-ci-data.git" "$WORK_CLONE_DIR"
  else
    git clone "$REMOTE_REPO_URL_BASE" "$WORK_CLONE_DIR"
  fi
else
  echo "[github_log_upload] Repository already cloned – re-using $WORK_CLONE_DIR"
fi

cd "$WORK_CLONE_DIR"

# Always work against the main branch
git fetch origin main
git checkout -q main

# Rebase to avoid merge commits when multiple machines push concurrently
git pull --rebase --quiet || true

###########################################################################
# 3. Copy / stage logs
###########################################################################

# Determine destination path based on log type
if [[ "$LOG_TYPE" == "sanity" ]]; then
  readonly DEST_PATH="test/sanity_check_log/${HARDWARE_TYPE}/${DATE_FOLDER}"
else
  readonly DEST_PATH="cron_log/${HARDWARE_TYPE}/${DATE_FOLDER}"
fi

mkdir -p "$DEST_PATH"

echo "[github_log_upload] Uploading logs from: $SRC_LOG_DIR"
echo "[github_log_upload] Destination in repo: $DEST_PATH"

# rsync keeps timestamps and only updates changed files; falls back to cp when
# rsync is unavailable.
if command -v rsync &>/dev/null; then
  rsync -a --delete "$SRC_LOG_DIR/" "$DEST_PATH/"
else
  cp -a "$SRC_LOG_DIR/." "$DEST_PATH/"
fi

git add "$DEST_PATH"

# Abort early if nothing changed (git diff --cached –quiet == no staged diff)
if git diff --cached --quiet; then
  echo "[github_log_upload] No new log changes – nothing to commit."
  exit 0
fi

###########################################################################
# 4. Commit & push
###########################################################################

# Generate commit message based on log type
if [[ "$LOG_TYPE" == "sanity" ]]; then
  COMMIT_MSG="Backup sanity logs for ${HARDWARE_TYPE} – ${DATE_FOLDER} ($(date +%Y%m%d_%H%M))"
else
  COMMIT_MSG="Backup cron logs for ${HARDWARE_TYPE} – ${DATE_FOLDER}"
fi

git -c user.name="ci-bot" -c user.email="ci-bot@example.com" commit -m "$COMMIT_MSG" --quiet

echo "[github_log_upload] Pushing commit to remote…"

if [[ -n "$GITHUB_TOKEN" ]]; then
  # Use token-authenticated remote for push only (clone may have been unauthenticated)
  git push "https://${GITHUB_TOKEN}@github.com/michael-amd/sglang-ci-data.git" main --quiet
else
  git push origin main --quiet
fi

echo "[github_log_upload] ✅  Logs uploaded successfully: $DEST_PATH"
