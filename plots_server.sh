#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# plots_server.sh
#   Launches the custom HTTP server to serve benchmark plots.
#   Serves plots from the centralized plots_server directory.
#
# USAGE:
#   bash plots_server.sh              # Serve all plots (default)
#   bash plots_server.sh 8080         # Serve on port 8080
#   bash plots_server.sh --port=8001  # Alternative syntax
#   bash plots_server.sh --plots-dir=/custom/path --port=9000
# ---------------------------------------------------------------------------

set -euo pipefail

###############################################################################
# Configuration Variables - Override via environment variables if needed
###############################################################################

# Default configuration - can be overridden via environment variables
DEFAULT_PORT="${HTTP_SERVER_PORT:-8000}"
DEFAULT_PLOTS_DIR="${PLOTS_SERVER_DIR:-/mnt/raid/michael/sgl_benchmark_ci/plots_server}"
DEFAULT_BENCHMARK_CI_DIR="${BENCHMARK_CI_DIR:-/mnt/raid/michael/sgl_benchmark_ci}"
DEFAULT_MODEL_DIRS="${PLOTS_MODEL_DIRS:-"GROK1 DeepSeek-V3-0324"}"  # Space-separated list

# Server configuration
HTTP_SERVER_SCRIPT="${HTTP_SERVER_SCRIPT:-${DEFAULT_BENCHMARK_CI_DIR}/custom_http_server.py}"

# Alternative ports to suggest if default is busy
ALTERNATIVE_PORTS="${ALTERNATIVE_PORTS:-8001 8080 8888 9000}"

###############################################################################
# Parse command line arguments
###############################################################################

PORT=""
PLOTS_DIR=""
SHOW_HELP=false

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --port=*)
            PORT="${1#*=}"
            shift
            ;;
        --plots-dir=*)
            PLOTS_DIR="${1#*=}"
            shift
            ;;
        --help|-h)
            SHOW_HELP=true
            shift
            ;;
        --*)
            echo "Unknown option: $1"
            echo "Use --help for usage information"
            exit 1
            ;;
        [0-9]*)
            # Backward compatibility: treat numeric argument as port
            PORT="$1"
            shift
            ;;
        *)
            echo "Unknown argument: $1"
            echo "Use --help for usage information"
            exit 1
            ;;
    esac
done

if [[ "$SHOW_HELP" == true ]]; then
    echo "Usage: $0 [OPTIONS] [PORT]"
    echo ""
    echo "Launch HTTP server to serve benchmark plots"
    echo ""
    echo "Options:"
    echo "  --port=PORT         Port number (default: $DEFAULT_PORT, can be set via HTTP_SERVER_PORT env var)"
    echo "  --plots-dir=DIR     Plots directory (default: $DEFAULT_PLOTS_DIR, can be set via PLOTS_SERVER_DIR env var)"
    echo "  --help, -h          Show this help message"
    echo ""
    echo "Environment Variables:"
    echo "  HTTP_SERVER_PORT    Default port number"
    echo "  PLOTS_SERVER_DIR    Default plots directory"
    echo "  BENCHMARK_CI_DIR    Base benchmark CI directory"
    echo "  PLOTS_MODEL_DIRS    Model directories to create (space-separated)"
    echo "  HTTP_SERVER_SCRIPT  Path to HTTP server script"
    echo "  ALTERNATIVE_PORTS   Alternative ports to suggest"
    echo ""
    echo "Examples:"
    echo "  $0                   # Use defaults"
    echo "  $0 8080             # Use port 8080"
    echo "  $0 --port=8001      # Use port 8001"
    echo "  $0 --plots-dir=/custom/plots --port=9000"
    exit 0
fi

# Set defaults if not provided
PORT="${PORT:-$DEFAULT_PORT}"
PLOTS_DIR="${PLOTS_DIR:-$DEFAULT_PLOTS_DIR}"

###############################################################################
# Validation and setup
###############################################################################

# Validate port is a number
if ! [[ "$PORT" =~ ^[0-9]+$ ]]; then
    echo "Error: Port must be a number"
    echo "Usage: $0 [OPTIONS] [PORT]"
    echo "Use --help for more information"
    exit 1
fi

# Check if port is already in use
if lsof -Pi :"$PORT" -sTCP:LISTEN -t >/dev/null 2>&1; then
    echo "Error: Port $PORT is already in use!"
    echo ""
    echo "You can:"
    echo "1. Use a different port: $0 --port=8080"
    echo "2. Find what's using port $PORT: lsof -i :$PORT"
    echo "3. Kill the process using port $PORT: kill \$(lsof -t -i:$PORT)"
    echo ""
    # Suggest some alternative ports
    read -ra ALT_PORTS <<< "$ALTERNATIVE_PORTS"
    for alt_port in "${ALT_PORTS[@]}"; do
        if ! lsof -Pi :"$alt_port" -sTCP:LISTEN -t >/dev/null 2>&1; then
            echo "Port $alt_port appears to be available"
            break
        fi
    done
    exit 1
fi

# Check if plots directory exists and create if needed
if [ ! -d "$PLOTS_DIR" ]; then
    echo "Creating plots directory: $PLOTS_DIR"
    mkdir -p "$PLOTS_DIR"

    # Create model subdirectories
    read -ra MODEL_DIRS <<< "$DEFAULT_MODEL_DIRS"
    for model in "${MODEL_DIRS[@]}"; do
        mkdir -p "$PLOTS_DIR/$model/offline"
        mkdir -p "$PLOTS_DIR/$model/online"
        echo "Created directories for model: $model"
    done
fi

# Check if HTTP server script exists
if [ ! -f "$HTTP_SERVER_SCRIPT" ]; then
    echo "Error: HTTP server script not found: $HTTP_SERVER_SCRIPT"
    echo "Please check the HTTP_SERVER_SCRIPT environment variable or BENCHMARK_CI_DIR"
    exit 1
fi

###############################################################################
# Start the server
###############################################################################

# Change to the plots directory and start the server
cd "$PLOTS_DIR"
echo "Serving plots from: $PLOTS_DIR"
echo "Using HTTP server script: $HTTP_SERVER_SCRIPT"
echo ""
echo "Navigate to:"
echo "  - http://$(hostname -I | awk '{print $1}'):$PORT/ to browse all plots"

# Show model-specific URLs
read -ra MODEL_DIRS <<< "$DEFAULT_MODEL_DIRS"
for model in "${MODEL_DIRS[@]}"; do
    if [ -d "$model" ]; then
        echo "  - http://$(hostname -I | awk '{print $1}'):$PORT/$model/offline/ for $model offline plots"
        echo "  - http://$(hostname -I | awk '{print $1}'):$PORT/$model/online/ for $model online plots"
    fi
done

echo ""
echo "Starting HTTP server on port $PORT..."
echo "Press Ctrl+C to stop the server"
python3 "$HTTP_SERVER_SCRIPT" "$PORT"
