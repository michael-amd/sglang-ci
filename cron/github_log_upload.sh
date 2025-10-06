#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# github_log_upload.sh - Push daily cron logs to the sglang-ci-data repository
#
# This helper script copies the generated cron logs for the *current* calendar
# day into a working clone of the dedicated data repository and pushes a commit
# so the logs are permanently preserved.
#
# Usage (inside another script / cron entry):
#     bash cron/github_log_upload.sh [DATE] [HARDWARE_TYPE]
#
# Examples:
#     bash cron/github_log_upload.sh                    # Upload today's logs for $HARDWARE_TYPE
#     bash cron/github_log_upload.sh 20251005           # Upload 2025-10-05 logs for $HARDWARE_TYPE
#     bash cron/github_log_upload.sh 20251005 mi30x     # Upload 2025-10-05 logs for mi30x
#     bash cron/github_log_upload.sh 20251005 mi35x     # Upload 2025-10-05 logs for mi35x
#
# Arguments:
#   DATE          – Optional. Date in YYYYMMDD format (defaults to today)
#   HARDWARE_TYPE – Optional. Machine descriptor (defaults to $HARDWARE_TYPE env var or 'unknown')
#
# Requirements:
#   • GITHUB_TOKEN    – Personal access token with `repo` scope that can push
#   • HARDWARE_TYPE   – The machine descriptor (mi30x / mi35x …).  Falls back
#                       to `unknown` if unset (but should always be set in the
#                       crontab header).
#
# The script is *idempotent*: running it multiple times the same day will only
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

# Parse arguments for date and hardware type
readonly TODAY="${1:-$(date +%Y%m%d)}"
readonly HARDWARE_TYPE="${2:-${HARDWARE_TYPE:-unknown}}"

# Location where the logs are produced by the cron jobs
readonly SRC_LOG_DIR="${SGL_CI_DIR}/cron/cron_log/${HARDWARE_TYPE}/${TODAY}"

# Skip gracefully if there is nothing to upload yet
if [[ ! -d "$SRC_LOG_DIR" ]]; then
  echo "[github_log_upload] No logs found in $SRC_LOG_DIR – skipping upload."
  exit 0
fi

# Working clone location (nested inside repo to avoid permission issues)
readonly WORK_CLONE_DIR="${SGL_CI_DIR}/.cron_log_repo"

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
# 3. Copy / stage today’s logs
###########################################################################

readonly DEST_PATH="cron_log/${HARDWARE_TYPE}/${TODAY}"
mkdir -p "$DEST_PATH"

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

COMMIT_MSG="Backup cron logs for ${HARDWARE_TYPE} – ${TODAY}"

git -c user.name="ci-bot" -c user.email="ci-bot@example.com" commit -m "$COMMIT_MSG" --quiet

echo "[github_log_upload] Pushing commit to remote…"

if [[ -n "$GITHUB_TOKEN" ]]; then
  # Use token-authenticated remote for push only (clone may have been unauthenticated)
  git push "https://${GITHUB_TOKEN}@github.com/michael-amd/sglang-ci-data.git" main --quiet
else
  git push origin main --quiet
fi

echo "[github_log_upload] ✅  Logs for $TODAY uploaded successfully."
