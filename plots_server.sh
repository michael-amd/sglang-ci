#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# plots_server.sh
#   Launches the custom HTTP server to serve GROK1 benchmark plots.
#   Serves plots from the centralized plots_server directory.
#
# USAGE:
#   bash plots_server.sh              # Serve all plots (default)
#   bash plots_server.sh 8080         # Serve on port 8080
# ---------------------------------------------------------------------------

set -euo pipefail

# Default values
PORT="${1:-8000}"
PLOTS_DIR="/mnt/raid/michael/sgl_benchmark_ci/plots_server"

# Validate port is a number
if ! [[ "$PORT" =~ ^[0-9]+$ ]]; then
    echo "Error: Port must be a number"
    echo "Usage: $0 [port]"
    exit 1
fi

# Check if port is already in use
if lsof -Pi :"$PORT" -sTCP:LISTEN -t >/dev/null 2>&1; then
    echo "Error: Port $PORT is already in use!"
    echo ""
    echo "You can:"
    echo "1. Use a different port: bash $0 8080"
    echo "2. Find what's using port $PORT: lsof -i :$PORT"
    echo "3. Kill the process using port $PORT: kill \$(lsof -t -i:$PORT)"
    echo ""
    # Suggest some alternative ports
    for alt_port in 8001 8080 8888 9000; do
        if ! lsof -Pi :"$alt_port" -sTCP:LISTEN -t >/dev/null 2>&1; then
            echo "Port $alt_port appears to be available"
            break
        fi
    done
    exit 1
fi

# Check if plots directory exists
if [ ! -d "$PLOTS_DIR" ]; then
    echo "Creating plots directory: $PLOTS_DIR"
    mkdir -p "$PLOTS_DIR/GROK1/offline"
    mkdir -p "$PLOTS_DIR/GROK1/online"
fi

# Change to the plots directory and start the server
cd "$PLOTS_DIR"
echo "Serving plots from: $PLOTS_DIR"
echo "Navigate to:"
echo "  - http://$(hostname -I | awk '{print $1}'):$PORT/ to browse all plots"
echo "  - http://$(hostname -I | awk '{print $1}'):$PORT/GROK1/offline/ for offline plots"
echo "  - http://$(hostname -I | awk '{print $1}'):$PORT/GROK1/online/ for online plots"
echo ""
echo "Starting HTTP server on port $PORT..."
echo "Press Ctrl+C to stop the server"
python3 "/mnt/raid/michael/sgl_benchmark_ci/custom_http_server.py" "$PORT"
