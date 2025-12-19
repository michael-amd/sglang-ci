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

# Rebase to avoid merge commits, with automatic conflict resolution
CONFLICT_RESOLVED=false
if ! git pull --rebase --quiet 2>&1; then
  echo "[github_database_upload] Rebase conflict detected – auto-resolving…"

  # Check if we're in a rebase state
  if [[ -d ".git/rebase-merge" ]] || [[ -d ".git/rebase-apply" ]]; then
    # Accept remote version of database (newer data)
    if [[ -f "database/ci_dashboard.db" ]]; then
      git checkout --theirs database/ci_dashboard.db 2>/dev/null || true
      git add database/ci_dashboard.db
    fi

    # Continue rebase with the original commit message
    if ! git -c core.editor=true rebase --continue 2>&1; then
      echo "[github_database_upload] Rebase failed, aborting and using current state"
      git rebase --abort 2>/dev/null || true
    else
      echo "[github_database_upload] ✅ Conflict auto-resolved (accepted remote database)"
      CONFLICT_RESOLVED=true
    fi
  fi
fi

###########################################################################
# 3a. If conflict was resolved, re-ingest local data to recover any lost data
###########################################################################

if [[ "$CONFLICT_RESOLVED" == "true" ]]; then
  echo "[github_database_upload] Re-ingesting local data after conflict resolution..."

  # Detect hardware type from environment or hostname
  HARDWARE="${HARDWARE_TYPE:-}"
  if [[ -z "$HARDWARE" ]]; then
    HOSTNAME=$(hostname)
    if [[ "$HOSTNAME" == *"t10-23"* ]]; then
      HARDWARE="mi30x"
    elif [[ "$HOSTNAME" == *"t12-38"* ]]; then
      HARDWARE="mi35x"
    else
      # Default based on common machine patterns
      HARDWARE="mi30x"
    fi
  fi

  # Re-ingest last 7 days of local data to recover any lost entries
  # (With symlink setup, this writes directly to the repo database)
  echo "[github_database_upload] Backfilling last 7 days for $HARDWARE..."
  cd "$SGL_CI_DIR"
  if python3 database/ingest_data.py --backfill 7 --hardware "$HARDWARE" --quiet 2>&1; then
    echo "[github_database_upload] ✅ Backfill complete - local data recovered"
  else
    echo "[github_database_upload] ⚠️  Backfill had issues (non-fatal)"
  fi
  cd "$WORK_CLONE_DIR"
fi

###########################################################################
# 4. Stage database file (handles both symlink and regular file cases)
###########################################################################

readonly DEST_PATH="database"
readonly DEST_FILE="$DEST_PATH/ci_dashboard.db"

mkdir -p "$DEST_PATH"

echo "[github_database_upload] Database source: $DB_FILE"
echo "[github_database_upload] Destination in repo: $DEST_FILE"

# Check if source is a symlink pointing to the repo database
if [[ -L "$DB_FILE" ]]; then
  LINK_TARGET=$(readlink -f "$DB_FILE")
  REPO_DB=$(readlink -f "$DEST_FILE")

  if [[ "$LINK_TARGET" == "$REPO_DB" ]]; then
    echo "[github_database_upload] Source is symlink to repo database – no copy needed"
  else
    # Symlink points elsewhere, copy the resolved file
    cp "$(readlink -f "$DB_FILE")" "$DEST_FILE"
  fi
else
  # Regular file – copy to repo
  cp "$DB_FILE" "$DEST_FILE"
fi

# Stage the file
git add "$DEST_FILE"

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
