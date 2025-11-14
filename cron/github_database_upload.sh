#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# github_database_upload.sh - Push dashboard database to the sglang-ci-data repository
#
# This script uploads the dashboard database file to GitHub for sharing
# across machines and providing a backup.
#
# Usage:
#     bash cron/github_database_upload.sh
#
# Requirements:
#   • GITHUB_REPO     – Repository identifier in 'owner/repo' format (defaults to 'ROCm/sglang-ci')
#   • GITHUB_TOKEN    – Personal access token with `repo` scope that can push
#   • SGL_BENCHMARK_CI_DIR – Base directory (defaults to /mnt/raid/michael/sglang-ci)
#
# The script is idempotent and will only push if the database has changed.
# ---------------------------------------------------------------------------

set -euo pipefail

###########################################################################
# 1. Basic environment
###########################################################################

# Resolve required env variables
readonly GITHUB_REPO="${GITHUB_REPO:-ROCm/sglang-ci}"
readonly GITHUB_TOKEN="${GITHUB_TOKEN:-}"
readonly SGL_CI_DIR="${SGL_BENCHMARK_CI_DIR:-$(pwd)}"

# Database file location
readonly DB_FILE="${SGL_CI_DIR}/database/ci_dashboard.db"

# Skip if database doesn't exist
if [[ ! -f "$DB_FILE" ]]; then
  echo "[github_database_upload] Database not found at $DB_FILE – skipping upload."
  exit 0
fi

# Working clone location (shared with log upload)
readonly WORK_CLONE_DIR="/mnt/raid/michael/sglang-ci-data"

###########################################################################
# 2. Acquire lock for Git operations (prevent concurrent access)
###########################################################################

readonly LOCK_FILE="/tmp/github_log_upload.lock"

# Use flock to serialize Git operations
exec 200>"$LOCK_FILE"

if ! flock -x -w 300 200; then
  echo "[github_database_upload] ERROR: Failed to acquire lock after 300 seconds. Aborting."
  exit 1
fi

echo "[github_database_upload] Lock acquired – proceeding with database upload"

# Ensure lock is released on exit
trap 'flock -u 200' EXIT

###########################################################################
# 3. Ensure we have a clone of the data repository
###########################################################################

if [[ ! -d "$WORK_CLONE_DIR/.git" ]]; then
  echo "[github_database_upload] Cloning data repository (${GITHUB_REPO})…"

  if [[ -n "$GITHUB_TOKEN" ]]; then
    git clone "https://${GITHUB_TOKEN}@github.com/${GITHUB_REPO}.git" "$WORK_CLONE_DIR"
  else
    git clone "https://github.com/${GITHUB_REPO}.git" "$WORK_CLONE_DIR"
  fi
else
  echo "[github_database_upload] Repository already cloned – re-using $WORK_CLONE_DIR"
fi

cd "$WORK_CLONE_DIR"

# Always work against the log branch
git fetch origin log
git checkout -q log

# Rebase to avoid merge commits
git pull --rebase --quiet || true

###########################################################################
# 4. Copy database file
###########################################################################

readonly DEST_PATH="database"

mkdir -p "$DEST_PATH"

echo "[github_database_upload] Uploading database from: $DB_FILE"
echo "[github_database_upload] Destination in repo: $DEST_PATH/ci_dashboard.db"

# Copy database file
cp "$DB_FILE" "$DEST_PATH/ci_dashboard.db"

# Stage the file
git add "$DEST_PATH/ci_dashboard.db"

# Abort early if nothing changed
if git diff --cached --quiet; then
  echo "[github_database_upload] No database changes – nothing to commit."
  exit 0
fi

###########################################################################
# 5. Commit & push
###########################################################################

COMMIT_MSG="Update dashboard database ($(date +%Y%m%d_%H%M))"

git -c user.name="ci-bot" -c user.email="ci-bot@example.com" commit -m "$COMMIT_MSG" --quiet

echo "[github_database_upload] Pushing commit to remote (${GITHUB_REPO})…"

if [[ -n "$GITHUB_TOKEN" ]]; then
  git push "https://${GITHUB_TOKEN}@github.com/${GITHUB_REPO}.git" log --quiet
else
  git push origin log --quiet
fi

echo "[github_database_upload] ✅  Database uploaded successfully"
