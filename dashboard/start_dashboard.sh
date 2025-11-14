#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# start_dashboard.sh
#   Start the SGLang CI Dashboard
#
# USAGE:
#   bash start_dashboard.sh                    # Start in development mode
#   bash start_dashboard.sh --port 8080        # Custom port
#   bash start_dashboard.sh --production       # Start with Gunicorn
#   bash start_dashboard.sh --background       # Start in background
#   bash start_dashboard.sh --production --background --port 8080
# ---------------------------------------------------------------------------

set -euo pipefail

###############################################################################
# Configuration Variables
###############################################################################

# Default configuration
DEFAULT_HOST="${DASHBOARD_HOST:-127.0.0.1}"
DEFAULT_PORT="${DASHBOARD_PORT:-5000}"
DEFAULT_BASE_DIR="${SGL_BENCHMARK_CI_DIR:-/mnt/raid/michael/sglang-ci}"

# Get script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

# PID file for background mode
PID_FILE="${SCRIPT_DIR}/dashboard.pid"
LOG_FILE="${SCRIPT_DIR}/dashboard.log"

###############################################################################
# Parse command line arguments
###############################################################################

HOST="$DEFAULT_HOST"
PORT="$DEFAULT_PORT"
BASE_DIR="$DEFAULT_BASE_DIR"
PRODUCTION=false
BACKGROUND=false
DEBUG=false
WORKERS=4
SHOW_HELP=false

while [[ $# -gt 0 ]]; do
    case $1 in
        --host)
            HOST="$2"
            shift 2
            ;;
        --port)
            PORT="$2"
            shift 2
            ;;
        --base-dir)
            BASE_DIR="$2"
            shift 2
            ;;
        --production)
            PRODUCTION=true
            shift
            ;;
        --background)
            BACKGROUND=true
            shift
            ;;
        --debug)
            DEBUG=true
            shift
            ;;
        --workers)
            WORKERS="$2"
            shift 2
            ;;
        --help|-h)
            SHOW_HELP=true
            shift
            ;;
        *)
            echo "Unknown option: $1"
            echo "Use --help for usage information"
            exit 1
            ;;
    esac
done

if [[ "$SHOW_HELP" == true ]]; then
    cat << EOF
Usage: $0 [OPTIONS]

Start the SGLang CI Dashboard

Options:
  --host HOST           Host to bind to (default: $DEFAULT_HOST)
  --port PORT           Port to run on (default: $DEFAULT_PORT)
  --base-dir DIR        Base directory for CI logs (default: $DEFAULT_BASE_DIR)
  --production          Run with Gunicorn (production mode)
  --background          Run in background
  --debug               Run in debug mode (development only)
  --workers N           Number of Gunicorn workers (default: 4, production only)
  --help, -h            Show this help message

Environment Variables:
  DASHBOARD_HOST        Default host
  DASHBOARD_PORT        Default port
  SGL_BENCHMARK_CI_DIR  Base directory for CI logs

Examples:
  $0                                    # Start in development mode
  $0 --port 8080                        # Custom port
  $0 --production                       # Production mode with Gunicorn
  $0 --production --background          # Production mode in background
  $0 --host 0.0.0.0 --port 8080        # Bind to all interfaces
  $0 --debug                            # Development with debug mode

EOF
    exit 0
fi

###############################################################################
# Validation
###############################################################################

# Check if port is already in use
if lsof -Pi :"$PORT" -sTCP:LISTEN -t >/dev/null 2>&1; then
    echo "❌ Error: Port $PORT is already in use!"
    echo ""
    echo "Options:"
    echo "1. Use a different port: $0 --port 8080"
    echo "2. Find what's using port $PORT: lsof -i :$PORT"
    echo "3. Kill the process: kill \$(lsof -t -i:$PORT)"
    exit 1
fi

# Check if base directory exists
if [ ! -d "$BASE_DIR" ]; then
    echo "❌ Error: Base directory does not exist: $BASE_DIR"
    exit 1
fi

# Check if Python is available
if ! command -v python3 &> /dev/null; then
    echo "❌ Error: python3 is not installed"
    exit 1
fi

# Check if requirements are installed
cd "$SCRIPT_DIR"
if ! python3 -c "import flask" 2>/dev/null; then
    echo "⚠️  Warning: Flask not found. Installing dependencies..."
    pip install -r requirements.txt
fi

# Check if production mode requires Gunicorn
if [[ "$PRODUCTION" == true ]]; then
    if ! python3 -c "import gunicorn" 2>/dev/null; then
        echo "⚠️  Warning: Gunicorn not found. Installing..."
        pip install gunicorn
    fi
fi

###############################################################################
# Initialize Database
###############################################################################

echo "💾 Initializing dashboard database..."

# Check if database exists, if not try to sync from GitHub
DB_FILE="${BASE_DIR}/database/ci_dashboard.db"

if [[ ! -f "$DB_FILE" ]]; then
    echo "   Database not found locally. Attempting to sync from GitHub..."

    if command -v python3 &> /dev/null; then
        cd "$BASE_DIR/database"
        if python3 sync_database.py pull 2>/dev/null; then
            echo "   ✅ Database synced from GitHub"
        else
            echo "   ⚠️  Could not sync database from GitHub. Will use fresh database."
            echo "   Database will be populated on first data ingestion."
        fi
    fi
else
    echo "   ✅ Database found at $DB_FILE"

    # Optionally sync with GitHub to get latest updates
    if [[ "${SYNC_DB_ON_START:-false}" == "true" ]]; then
        echo "   Syncing with GitHub..."
        cd "$BASE_DIR/database"
        if python3 sync_database.py pull --backup 2>/dev/null; then
            echo "   ✅ Database synced with GitHub"
        else
            echo "   ⚠️  Could not sync with GitHub. Using local database."
        fi
    fi
fi

echo ""

###############################################################################
# Start Dashboard
###############################################################################

echo "🚀 Starting SGLang CI Dashboard"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "📁 Base directory: $BASE_DIR"
echo "🌐 Server: http://$HOST:$PORT"
echo "🔧 Mode: $([ "$PRODUCTION" == true ] && echo "Production (Gunicorn)" || echo "Development (Flask)")"
echo "📋 Background: $([ "$BACKGROUND" == true ] && echo "Yes" || echo "No")"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

# Export environment variables
export DASHBOARD_HOST="$HOST"
export DASHBOARD_PORT="$PORT"
export SGL_BENCHMARK_CI_DIR="$BASE_DIR"

# Build command
if [[ "$PRODUCTION" == true ]]; then
    # Production mode with Gunicorn
    CMD="gunicorn -w $WORKERS -b $HOST:$PORT"

    # Add timeout for long-running requests
    CMD="$CMD --timeout 120"

    # Add access log
    CMD="$CMD --access-logfile -"

    # Add error log
    CMD="$CMD --error-logfile -"

    # Add application
    CMD="$CMD app:app"
else
    # Development mode with Flask
    DEBUG_FLAG=""
    if [[ "$DEBUG" == true ]]; then
        DEBUG_FLAG="--debug"
    fi

    CMD="python3 app.py --host $HOST --port $PORT $DEBUG_FLAG"
fi

# Run in background or foreground
if [[ "$BACKGROUND" == true ]]; then
    echo "Starting dashboard in background..."
    echo "PID file: $PID_FILE"
    echo "Log file: $LOG_FILE"
    echo ""

    # Start in background and save PID
    nohup $CMD > "$LOG_FILE" 2>&1 &
    PID=$!
    echo $PID > "$PID_FILE"

    # Wait a moment and check if process is still running
    sleep 2
    if kill -0 $PID 2>/dev/null; then
        echo "✅ Dashboard started successfully (PID: $PID)"
        echo ""
        echo "To view logs:"
        echo "  tail -f $LOG_FILE"
        echo ""
        echo "To stop dashboard:"
        echo "  bash $SCRIPT_DIR/stop_dashboard.sh"
        echo "  or kill $PID"
    else
        echo "❌ Dashboard failed to start"
        echo "Check logs: cat $LOG_FILE"
        exit 1
    fi
else
    echo "Starting dashboard in foreground..."
    echo "Press Ctrl+C to stop"
    echo ""

    # Run in foreground
    $CMD
fi
