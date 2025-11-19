#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# stop_dashboard.sh
#   Stop the SGLang CI Dashboard (if running in background)
#
# USAGE:
#   bash stop_dashboard.sh
# ---------------------------------------------------------------------------

set -euo pipefail

# Get script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# PID file location
PID_FILE="${SCRIPT_DIR}/dashboard.pid"

###############################################################################
# Stop Dashboard
###############################################################################

echo "🛑 Stopping SGLang CI Dashboard..."
echo ""

if [ ! -f "$PID_FILE" ]; then
    echo "⚠️  No PID file found at: $PID_FILE"
    echo "Dashboard may not be running in background mode."
    echo ""
    echo "To check for running dashboard processes:"
    echo "  ps aux | grep 'app.py\\|gunicorn.*app:app'"
    exit 1
fi

PID=$(cat "$PID_FILE")

if ! kill -0 "$PID" 2>/dev/null; then
    echo "⚠️  Process with PID $PID is not running"
    rm -f "$PID_FILE"
    exit 1
fi

# Try graceful shutdown first (SIGTERM)
echo "Sending SIGTERM to process $PID..."
kill "$PID"

# Wait for process to stop (up to 10 seconds)
for i in {1..10}; do
    if ! kill -0 "$PID" 2>/dev/null; then
        echo "✅ Dashboard stopped successfully"
        rm -f "$PID_FILE"
        exit 0
    fi
    sleep 1
done

# If still running, force kill (SIGKILL)
echo "Process still running, sending SIGKILL..."
kill -9 "$PID" 2>/dev/null || true

# Wait a moment
sleep 1

if ! kill -0 "$PID" 2>/dev/null; then
    echo "✅ Dashboard stopped (forced)"
    rm -f "$PID_FILE"
    exit 0
else
    echo "❌ Failed to stop dashboard process"
    echo "You may need to manually kill it: kill -9 $PID"
    exit 1
fi
