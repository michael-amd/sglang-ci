#!/usr/bin/env python3
"""
SGLang CI Dashboard

A comprehensive web dashboard for viewing CI results, plots, and trends
across mi30x and mi35x hardware platforms.

USAGE:
    python app.py
    python app.py --port 8080
    python app.py --host 0.0.0.0 --port 8080

ENVIRONMENT VARIABLES:
    DASHBOARD_PORT: Port to run dashboard on (default: 5000)
    DASHBOARD_HOST: Host to bind to (default: 127.0.0.1)
    SGL_BENCHMARK_CI_DIR: Base directory for CI logs (default: /mnt/raid/michael/sglang-ci)
"""

import argparse
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

from flask import Flask, jsonify, render_template, request, send_from_directory

# Add parent directory to path to import data_collector
sys.path.insert(0, str(Path(__file__).parent.parent))

from dashboard.data_collector import DashboardDataCollector
from dashboard.github_data_collector import GitHubDataCollector

app = Flask(__name__)

# Configuration
BASE_DIR = os.environ.get("SGL_BENCHMARK_CI_DIR", "/mnt/raid/michael/sglang-ci")
GITHUB_REPO = os.environ.get("GITHUB_REPO", "ROCm/sglang-ci")
USE_GITHUB = os.environ.get("USE_GITHUB", "true").lower() in ["true", "1", "yes"]


@app.route("/")
def index():
    """Main dashboard page"""
    # Get today's date
    today = datetime.now().strftime("%Y%m%d")
    return render_template("index.html", date=today, github_repo=GITHUB_REPO)


@app.route("/hardware/<hardware>")
def hardware_view(hardware):
    """Hardware-specific view"""
    if hardware not in ["mi30x", "mi35x"]:
        return "Invalid hardware type", 404

    today = datetime.now().strftime("%Y%m%d")
    return render_template(
        "hardware.html", hardware=hardware, date=today, github_repo=GITHUB_REPO
    )


@app.route("/trends")
def trends():
    """Historical trends page"""
    return render_template("trends.html", github_repo=GITHUB_REPO)


@app.route("/plots/<hardware>")
def plots_view(hardware):
    """Plots viewer page"""
    if hardware not in ["mi30x", "mi35x"]:
        return "Invalid hardware type", 404

    return render_template("plots.html", hardware=hardware, github_repo=GITHUB_REPO)


# REST API Endpoints


@app.route("/api/summary/<hardware>/<date>")
def api_summary(hardware, date):
    """Get daily summary for specific hardware and date"""
    if hardware not in ["mi30x", "mi35x"]:
        return jsonify({"error": "Invalid hardware type"}), 400

    try:
        # Use GitHub data collector if enabled, otherwise use local
        if USE_GITHUB:
            collector = GitHubDataCollector(
                hardware=hardware,
                base_dir=BASE_DIR,
                github_repo=GITHUB_REPO,
                use_local_fallback=True,
            )
        else:
            collector = DashboardDataCollector(hardware=hardware, base_dir=BASE_DIR)

        task_results = collector.collect_task_results(date)
        sanity_results = collector.parse_sanity_check_log(date)

        # Calculate summary statistics
        stats = collector.calculate_summary_stats(task_results, sanity_results)

        return jsonify(
            {
                "hardware": hardware,
                "date": date,
                "task_results": task_results,
                "sanity_results": sanity_results,
                "stats": stats,
            }
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/trends/<hardware>")
def api_trends(hardware):
    """Get historical trends for specific hardware"""
    if hardware not in ["mi30x", "mi35x"]:
        return jsonify({"error": "Invalid hardware type"}), 400

    # Get days parameter (default: 30 days)
    days = request.args.get("days", 30, type=int)
    days = min(days, 90)  # Cap at 90 days

    try:
        # Use GitHub data collector if enabled, otherwise use local
        if USE_GITHUB:
            collector = GitHubDataCollector(
                hardware=hardware,
                base_dir=BASE_DIR,
                github_repo=GITHUB_REPO,
                use_local_fallback=True,
            )
        else:
            collector = DashboardDataCollector(hardware=hardware, base_dir=BASE_DIR)

        trends_data = collector.get_historical_trends(days=days)

        return jsonify({"hardware": hardware, "days": days, "trends": trends_data})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/dates/<hardware>")
def api_available_dates(hardware):
    """Get available dates for specific hardware"""
    if hardware not in ["mi30x", "mi35x"]:
        return jsonify({"error": "Invalid hardware type"}), 400

    try:
        # Use GitHub data collector if enabled, otherwise use local
        if USE_GITHUB:
            collector = GitHubDataCollector(
                hardware=hardware,
                base_dir=BASE_DIR,
                github_repo=GITHUB_REPO,
                use_local_fallback=True,
            )
        else:
            collector = DashboardDataCollector(hardware=hardware, base_dir=BASE_DIR)

        dates = collector.get_available_dates()

        return jsonify({"hardware": hardware, "dates": dates})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/plots/<hardware>/<date>")
def api_plots(hardware, date):
    """Get available plots for specific hardware and date"""
    if hardware not in ["mi30x", "mi35x"]:
        return jsonify({"error": "Invalid hardware type"}), 400

    try:
        # Use GitHub data collector if enabled, otherwise use local
        if USE_GITHUB:
            collector = GitHubDataCollector(
                hardware=hardware,
                base_dir=BASE_DIR,
                github_repo=GITHUB_REPO,
                use_local_fallback=True,
            )
        else:
            collector = DashboardDataCollector(hardware=hardware, base_dir=BASE_DIR)

        plots = collector.get_available_plots(date)

        return jsonify({"hardware": hardware, "date": date, "plots": plots})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/compare")
def api_compare():
    """Compare results between mi30x and mi35x for a specific date"""
    date = request.args.get("date")
    if not date:
        return jsonify({"error": "Date parameter required"}), 400

    try:
        results = {}
        for hardware in ["mi30x", "mi35x"]:
            # Use GitHub data collector if enabled, otherwise use local
            if USE_GITHUB:
                collector = GitHubDataCollector(
                    hardware=hardware,
                    base_dir=BASE_DIR,
                    github_repo=GITHUB_REPO,
                    use_local_fallback=True,
                )
            else:
                collector = DashboardDataCollector(hardware=hardware, base_dir=BASE_DIR)

            task_results = collector.collect_task_results(date)
            sanity_results = collector.parse_sanity_check_log(date)
            stats = collector.calculate_summary_stats(task_results, sanity_results)

            results[hardware] = {
                "task_results": task_results,
                "sanity_results": sanity_results,
                "stats": stats,
            }

        return jsonify({"date": date, "results": results})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# Static file serving for logs and plots


@app.route("/logs/<hardware>/<date>/<filename>")
def serve_log(hardware, date, filename):
    """Serve log files"""
    log_dir = os.path.join(BASE_DIR, "cron", "cron_log", hardware, date)
    try:
        return send_from_directory(log_dir, filename)
    except FileNotFoundError:
        return "Log file not found", 404


@app.route("/health")
def health():
    """Health check endpoint"""
    return jsonify(
        {
            "status": "healthy",
            "timestamp": datetime.now().isoformat(),
            "base_dir": BASE_DIR,
        }
    )


# Error handlers


@app.errorhandler(404)
def not_found(e):
    """Handle 404 errors"""
    if request.path.startswith("/api/"):
        return jsonify({"error": "Not found"}), 404
    return render_template("404.html"), 404


@app.errorhandler(500)
def server_error(e):
    """Handle 500 errors"""
    if request.path.startswith("/api/"):
        return jsonify({"error": "Internal server error"}), 500
    return render_template("500.html"), 500


def main():
    """Main function"""
    global BASE_DIR, USE_GITHUB

    parser = argparse.ArgumentParser(
        description="SGLang CI Dashboard",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    parser.add_argument(
        "--host",
        type=str,
        default=os.environ.get("DASHBOARD_HOST", "127.0.0.1"),
        help="Host to bind to",
    )

    parser.add_argument(
        "--port",
        type=int,
        default=os.environ.get("DASHBOARD_PORT", 5000),
        help="Port to run on",
    )

    parser.add_argument(
        "--base-dir",
        type=str,
        default=BASE_DIR,
        help="Base directory for CI logs",
    )

    parser.add_argument("--debug", action="store_true", help="Run in debug mode")

    parser.add_argument(
        "--use-github",
        action="store_true",
        default=USE_GITHUB,
        help="Fetch data from GitHub instead of local filesystem (default: enabled)",
    )

    parser.add_argument(
        "--use-local",
        action="store_true",
        help="Force use of local filesystem (disables GitHub mode)",
    )

    args = parser.parse_args()

    # Update BASE_DIR if provided
    BASE_DIR = args.base_dir

    # Update USE_GITHUB based on args
    if args.use_local:
        USE_GITHUB = False
    elif args.use_github:
        USE_GITHUB = True

    print(f"🚀 Starting SGLang CI Dashboard")
    print(f"📁 Base directory: {BASE_DIR}")
    print(f"🌐 Server: http://{args.host}:{args.port}")
    print(f"🔗 GitHub Repo: {GITHUB_REPO}")
    print(
        f"📡 Data Source: {'GitHub (with local fallback)' if USE_GITHUB else 'Local filesystem only'}"
    )
    print()

    app.run(host=args.host, port=args.port, debug=args.debug)


if __name__ == "__main__":
    main()
