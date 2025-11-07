#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# tmux_dashboard.sh
#   Manage SGLang CI Dashboard in tmux session
#
# USAGE:
#   bash tmux_dashboard.sh start       # Start dashboard in tmux
#   bash tmux_dashboard.sh stop        # Stop dashboard
#   bash tmux_dashboard.sh restart     # Restart dashboard
#   bash tmux_dashboard.sh status      # Check status
#   bash tmux_dashboard.sh attach      # Attach to tmux session
#   bash tmux_dashboard.sh logs        # View logs
# ---------------------------------------------------------------------------

set -euo pipefail

SESSION_NAME="sglang-dashboard"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Get server IP
SERVER_IP=$(hostname -I | awk '{print $1}')

# Configuration
HOST="${DASHBOARD_HOST:-0.0.0.0}"
PORT="${DASHBOARD_PORT:-5000}"

start_dashboard() {
    # Check if session already exists
    if tmux has-session -t "$SESSION_NAME" 2>/dev/null; then
        echo "⚠️  Dashboard session already exists"
        echo "   Use 'bash $0 stop' first, or 'bash $0 restart'"
        exit 1
    fi

    echo "🚀 Starting SGLang CI Dashboard in tmux..."

    # Create tmux session and start dashboard
    cd "$SCRIPT_DIR"
    tmux new-session -d -s "$SESSION_NAME" \
        "python3 app.py --host $HOST --port $PORT; read -p 'Press Enter to close...'"

    # Wait for startup
    sleep 3

    # Check if it's running
    if tmux has-session -t "$SESSION_NAME" 2>/dev/null; then
        echo "✅ Dashboard started successfully!"
        echo ""
        echo "Access URLs:"
        echo "  Local:    http://localhost:$PORT"
        echo "  Internal: http://$SERVER_IP:$PORT"
        echo ""
        echo "Management:"
        echo "  Status:  bash $0 status"
        echo "  Logs:    bash $0 logs"
        echo "  Attach:  bash $0 attach"
        echo "  Stop:    bash $0 stop"
    else
        echo "❌ Failed to start dashboard"
        exit 1
    fi
}

stop_dashboard() {
    if ! tmux has-session -t "$SESSION_NAME" 2>/dev/null; then
        echo "⚠️  Dashboard session not found"
        exit 1
    fi

    echo "🛑 Stopping dashboard..."
    tmux kill-session -t "$SESSION_NAME"
    echo "✅ Dashboard stopped"
}

restart_dashboard() {
    echo "🔄 Restarting dashboard..."
    if tmux has-session -t "$SESSION_NAME" 2>/dev/null; then
        stop_dashboard
        sleep 1
    fi
    start_dashboard
}

status_dashboard() {
    echo "📊 Dashboard Status:"
    echo ""

    # Check tmux session
    if tmux has-session -t "$SESSION_NAME" 2>/dev/null; then
        echo "✅ Tmux session: RUNNING"
        tmux list-sessions | grep "$SESSION_NAME"
    else
        echo "❌ Tmux session: NOT RUNNING"
        exit 1
    fi

    echo ""

    # Check HTTP endpoint
    if curl -s http://localhost:$PORT/health >/dev/null 2>&1; then
        echo "✅ HTTP endpoint: RESPONDING"
        echo ""
        echo "Health check:"
        curl -s http://localhost:$PORT/health | python3 -m json.tool
        echo ""
        echo "Access URLs:"
        echo "  Local:    http://localhost:$PORT"
        echo "  Internal: http://$SERVER_IP:$PORT"
    else
        echo "❌ HTTP endpoint: NOT RESPONDING"
        echo "   The session exists but dashboard may have crashed"
        echo "   Use: bash $0 logs"
        exit 1
    fi
}

attach_dashboard() {
    if ! tmux has-session -t "$SESSION_NAME" 2>/dev/null; then
        echo "❌ Dashboard session not found"
        exit 1
    fi

    echo "📎 Attaching to dashboard session..."
    echo "   (Press Ctrl+B, then D to detach)"
    sleep 1
    tmux attach-session -t "$SESSION_NAME"
}

logs_dashboard() {
    if ! tmux has-session -t "$SESSION_NAME" 2>/dev/null; then
        echo "❌ Dashboard session not found"
        exit 1
    fi

    echo "📜 Dashboard logs (last 50 lines):"
    echo "   (Press Ctrl+C to exit)"
    echo ""
    tmux capture-pane -t "$SESSION_NAME" -p -S -50
}

case "${1:-}" in
    start)
        start_dashboard
        ;;
    stop)
        stop_dashboard
        ;;
    restart)
        restart_dashboard
        ;;
    status)
        status_dashboard
        ;;
    attach)
        attach_dashboard
        ;;
    logs)
        logs_dashboard
        ;;
    *)
        echo "Usage: $0 {start|stop|restart|status|attach|logs}"
        echo ""
        echo "Commands:"
        echo "  start    - Start dashboard in tmux"
        echo "  stop     - Stop dashboard"
        echo "  restart  - Restart dashboard"
        echo "  status   - Check if dashboard is running"
        echo "  attach   - Attach to tmux session"
        echo "  logs     - View dashboard logs"
        exit 1
        ;;
esac
