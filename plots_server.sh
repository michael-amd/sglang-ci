#!/bin/bash
# Script to start the custom Python HTTP server for plots

# The directory where the plots are located
PLOTS_DIR="/mnt/raid/michael/sgl_benchmark_ci/offline/GROK1/plots"

# Path to the custom Python server script
PYTHON_SERVER_SCRIPT="/mnt/raid/michael/sgl_benchmark_ci/custom_http_server.py"

# Port to run the server on
PORT=8000

# Change to the plots directory
cd "$PLOTS_DIR"

if [ ! -d "$PLOTS_DIR" ]; then
    echo "Error: Plots directory not found at $PLOTS_DIR" >&2
    exit 1
fi

if [ -f "$PYTHON_SERVER_SCRIPT" ]; then
    echo "Starting server in $PLOTS_DIR on port $PORT..."
    # Execute the Python server script, passing the port as an argument
    python3 "$PYTHON_SERVER_SCRIPT" "$PORT"
else
    echo "Error: Python server script not found at $PYTHON_SERVER_SCRIPT" >&2
    exit 1
fi 