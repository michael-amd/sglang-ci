#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# sync_and_ingest_database.sh - Sync database from GitHub, ingest new data, push back
#
# This ensures both mi30x and mi35x machines stay synchronized.
# Run after each test completes.
#
# Usage:
#     bash cron/sync_and_ingest_database.sh [date] [hardware]
#
# Example:
#     bash cron/sync_and_ingest_database.sh 20251114 mi30x
#     bash cron/sync_and_ingest_database.sh  # Uses today and $HARDWARE_TYPE
# ---------------------------------------------------------------------------

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly SGL_CI_DIR="$(dirname "$SCRIPT_DIR")"

readonly DATE_ARG="${1:-$(date +%Y%m%d)}"
readonly HARDWARE_ARG="${2:-${HARDWARE_TYPE:-mi30x}}"
readonly PYTHON="${PYTHON:-python3}"

###########################################################################
# 1. Pull latest database from GitHub (get updates from other machine)
###########################################################################

echo "[sync_and_ingest] Syncing database from GitHub..."

cd "$SGL_CI_DIR"

if $PYTHON database/sync_database.py pull 2>&1 | grep -E "(✅|⚠️|Database)" || true; then
  echo "[sync_and_ingest] ✅ Database synced from GitHub"
else
  echo "[sync_and_ingest] ⚠️  Sync had issues (non-fatal, will use local database)"
fi

###########################################################################
# 2. Ingest new data into database
###########################################################################

echo "[sync_and_ingest] Ingesting test data..."

if $PYTHON database/ingest_data.py --date "$DATE_ARG" --hardware "$HARDWARE_ARG" --quiet 2>&1; then
  echo "[sync_and_ingest] ✅ Data ingested"
else
  echo "[sync_and_ingest] ⚠️  Ingestion had issues"
fi

###########################################################################
# 3. Push updated database back to GitHub (share with other machines)
###########################################################################

echo "[sync_and_ingest] Uploading database to GitHub..."

if bash "${SCRIPT_DIR}/github_database_upload.sh" 2>&1 | grep -E "(✅|⚠️)" || true; then
  echo "[sync_and_ingest] ✅ Database uploaded"
else
  echo "[sync_and_ingest] ⚠️  Upload had issues"
fi

echo "[sync_and_ingest] ✅ Complete - database synchronized"
