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

import requests
from flask import Flask, jsonify, render_template, request, send_from_directory
from flask_caching import Cache

# Add parent directory to path to import data_collector
sys.path.insert(0, str(Path(__file__).parent.parent))

from dashboard.data_collector import DashboardDataCollector
from dashboard.github_data_collector import GitHubDataCollector

app = Flask(__name__)

# Configure caching
cache = Cache(
    app,
    config={"CACHE_TYPE": "simple", "CACHE_DEFAULT_TIMEOUT": 600},  # 10 minutes default
)

# Configuration
BASE_DIR = os.environ.get("SGL_BENCHMARK_CI_DIR", "/mnt/raid/michael/sglang-ci")
GITHUB_REPO = os.environ.get("GITHUB_REPO", "ROCm/sglang-ci")
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN")
USE_GITHUB = os.environ.get("USE_GITHUB", "true").lower() in ["true", "1", "yes"]

# Determine current hardware from hostname
CURRENT_HARDWARE = None
try:
    import socket

    hostname = socket.gethostname().lower()
    if "30" in hostname or "300" in hostname:
        CURRENT_HARDWARE = "mi30x"
    elif "35" in hostname or "355" in hostname or "350" in hostname:
        CURRENT_HARDWARE = "mi35x"
except Exception:
    pass


def get_data_collector(hardware: str):
    """
    Get appropriate data collector for hardware

    For mi30x on mi30x server: Use local files first (instant, no GitHub calls)
    For mi35x or remote access: Use GitHub first (works behind firewall)
    """
    use_local_first = hardware == CURRENT_HARDWARE and hardware == "mi30x"

    if use_local_first:
        # Local-first mode: Fast, no GitHub calls
        return DashboardDataCollector(hardware=hardware, base_dir=BASE_DIR)
    elif USE_GITHUB:
        # GitHub-first mode: Works behind firewall, has local fallback
        return GitHubDataCollector(
            hardware=hardware,
            base_dir=BASE_DIR,
            github_repo=GITHUB_REPO,
            use_local_fallback=True,
            github_token=GITHUB_TOKEN,
        )
    else:
        # Local only mode
        return DashboardDataCollector(hardware=hardware, base_dir=BASE_DIR)


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


@app.route("/upstream-ci")
def upstream_ci_view():
    """Upstream CI test coverage page"""
    return render_template("upstream_ci.html", github_repo=GITHUB_REPO)


# REST API Endpoints


@app.route("/api/summary/<hardware>/<date>")
@cache.cached(timeout=300)  # Cache for 5 minutes
def api_summary(hardware, date):
    """Get daily summary for specific hardware and date"""
    if hardware not in ["mi30x", "mi35x"]:
        return jsonify({"error": "Invalid hardware type"}), 400

    try:
        # Use GitHub data collector if enabled, otherwise use local
        if USE_GITHUB:
            # Get token from environment (may have been set after module import)
            github_token = os.environ.get("GITHUB_TOKEN") or GITHUB_TOKEN
            collector = GitHubDataCollector(
                hardware=hardware,
                base_dir=BASE_DIR,
                github_repo=GITHUB_REPO,
                use_local_fallback=True,
                github_token=github_token,
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
@cache.cached(timeout=600, query_string=True)  # Cache for 10 minutes
def api_trends(hardware):
    """Get historical trends for specific hardware"""
    if hardware not in ["mi30x", "mi35x"]:
        return jsonify({"error": "Invalid hardware type"}), 400

    # Get days parameter (default: 7 days to improve performance)
    days = request.args.get("days", 7, type=int)
    days = min(days, 90)  # Cap at 90 days

    try:
        collector = get_data_collector(hardware)
        trends_data = collector.get_historical_trends(days=days)

        return jsonify({"hardware": hardware, "days": days, "trends": trends_data})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/dates/<hardware>")
@cache.cached(timeout=600)  # Cache for 10 minutes
def api_available_dates(hardware):
    """Get available dates for specific hardware (based on cron log directory existence)"""
    if hardware not in ["mi30x", "mi35x"]:
        return jsonify({"error": "Invalid hardware type"}), 400

    try:
        # Use GitHub data collector if enabled, otherwise use local
        if USE_GITHUB:
            # Get token from environment (may have been set after module import)
            github_token = os.environ.get("GITHUB_TOKEN") or GITHUB_TOKEN
            collector = GitHubDataCollector(
                hardware=hardware,
                base_dir=BASE_DIR,
                github_repo=GITHUB_REPO,
                use_local_fallback=True,
                github_token=github_token,
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
        collector = get_data_collector(hardware)
        plots = collector.get_available_plots(date)

        return jsonify({"hardware": hardware, "date": date, "plots": plots})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/available-plot-dates/<hardware>")
@cache.cached(timeout=3600)  # Cache for 1 hour (plots don't change often)
def api_available_plot_dates(hardware):
    """Get list of dates that have plots available"""
    if hardware not in ["mi30x", "mi35x"]:
        return jsonify({"error": "Invalid hardware type"}), 400

    try:
        collector = get_data_collector(hardware)
        # Get dates with available plots
        available_dates = collector.get_dates_with_plots()

        return jsonify({"hardware": hardware, "dates": available_dates})
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
            collector = get_data_collector(hardware)
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


@app.route("/api/upstream-ci/available-dates")
@cache.cached(timeout=600)  # Cache for 10 minutes
def api_upstream_ci_dates():
    """Get available dates for upstream CI reports"""
    try:
        ci_report_dir = os.path.join(BASE_DIR, "upstream_ci", "ci_report")

        if not os.path.exists(ci_report_dir):
            return jsonify({"dates": []})

        # Find all CSV files
        import glob

        csv_files = glob.glob(os.path.join(ci_report_dir, "sglang_ci_report_*.csv"))

        # Extract dates from filenames (sglang_ci_report_YYYYMMDD.csv)
        dates = set()
        for csv_file in csv_files:
            filename = os.path.basename(csv_file)
            # Extract date part: sglang_ci_report_YYYYMMDD.csv -> YYYYMMDD
            if filename.startswith("sglang_ci_report_") and filename.endswith(".csv"):
                date_part = filename[17:-4]  # Extract YYYYMMDD
                if len(date_part) == 8 and date_part.isdigit():
                    dates.add(date_part)

        # Sort dates in descending order (newest first)
        sorted_dates = sorted(list(dates), reverse=True)

        return jsonify({"dates": sorted_dates[:90]})  # Return last 90 days
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/upstream-ci/report/<date>")
@cache.cached(timeout=600)  # Cache for 10 minutes
def api_upstream_ci_report(date):
    """Get upstream CI report for a specific date"""
    try:
        ci_report_dir = os.path.join(BASE_DIR, "upstream_ci", "ci_report")

        if not os.path.exists(ci_report_dir):
            return jsonify({"error": "CI report directory not found"}), 404

        # Find CSV file for this date
        import csv as csv_module

        # Look for file with pattern: sglang_ci_report_YYYYMMDD.csv
        csv_file = os.path.join(ci_report_dir, f"sglang_ci_report_{date}.csv")

        if not os.path.exists(csv_file):
            return jsonify({"error": "No report found for this date"}), 404

        # Parse CSV file
        data = []
        with open(csv_file, "r", encoding="utf-8") as f:
            reader = csv_module.DictReader(f)
            for row in reader:
                data.append(row)

        # Extract metadata from filename
        filename = os.path.basename(csv_file)

        return jsonify({"date": date, "data": data, "filename": filename})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/upstream-ci/trends")
@cache.cached(timeout=600, query_string=True)  # Cache for 10 minutes
def api_upstream_ci_trends():
    """Get historical trends for upstream CI coverage"""
    try:
        days = request.args.get("days", 30, type=int)
        days = min(days, 90)  # Cap at 90 days

        ci_report_dir = os.path.join(BASE_DIR, "upstream_ci", "ci_report")

        if not os.path.exists(ci_report_dir):
            return jsonify({"trends": []})

        import csv as csv_module
        import glob

        csv_files = glob.glob(os.path.join(ci_report_dir, "sglang_ci_report_*.csv"))

        # Extract dates and sort
        date_file_map = {}
        for csv_file in csv_files:
            filename = os.path.basename(csv_file)
            # Extract date part: sglang_ci_report_YYYYMMDD.csv -> YYYYMMDD
            if filename.startswith("sglang_ci_report_") and filename.endswith(".csv"):
                date_part = filename[17:-4]  # Extract YYYYMMDD
                if len(date_part) == 8 and date_part.isdigit():
                    date_file_map[date_part] = csv_file

        sorted_dates = sorted(date_file_map.keys(), reverse=True)[:days]

        # Parse each file and extract total coverage
        trends_data = []
        for date in reversed(sorted_dates):  # Reverse to show oldest to newest
            csv_file = date_file_map[date]

            try:
                with open(csv_file, "r", encoding="utf-8") as f:
                    reader = csv_module.DictReader(f)
                    rows = list(reader)

                    # Find Total row
                    total_row = None
                    for row in rows:
                        if row.get("Test Category") == "Total":
                            total_row = row
                            break

                    if total_row:
                        trends_data.append(
                            {
                                "date": date,
                                "date_formatted": f"{date[0:4]}-{date[4:6]}-{date[6:8]}",
                                "amd_tests": int(total_row["AMD # of Tests"]),
                                "nvidia_tests": int(total_row["Nvidia # of Tests"]),
                                "coverage": float(
                                    total_row["AMD Coverage (%)"].rstrip("%")
                                ),
                            }
                        )
            except Exception as e:
                print(f"Error parsing {csv_file}: {e}")
                continue

        return jsonify({"trends": trends_data, "days": days})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/test-history/<hardware>")
@cache.cached(timeout=600, query_string=True)  # Cache for 10 minutes
def api_test_history(hardware):
    """Get individual test pass/fail history for specific hardware"""
    if hardware not in ["mi30x", "mi35x"]:
        return jsonify({"error": "Invalid hardware type"}), 400

    # Get days parameter (default: 30 days)
    days = request.args.get("days", 30, type=int)
    days = min(days, 90)  # Cap at 90 days

    try:
        collector = get_data_collector(hardware)
        test_history = collector.get_test_history(days=days)

        return jsonify(
            {"hardware": hardware, "days": days, "test_history": test_history}
        )
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


@app.route("/github-plots/<hardware>/<model>/<mode>/<filename>")
def serve_github_plot(hardware, model, mode, filename):
    """Proxy plot files (tries local first for mi30x, then GitHub)"""
    if hardware not in ["mi30x", "mi35x"]:
        return "Invalid hardware type", 400

    try:
        # For mi30x on mi30x server: check local first (instant)
        if hardware == "mi30x" and CURRENT_HARDWARE == "mi30x":
            local_path = os.path.join(BASE_DIR, "plots_server", model, mode, filename)
            if os.path.exists(local_path):
                # Serve from local filesystem (instant)
                return send_from_directory(
                    os.path.join(BASE_DIR, "plots_server", model, mode),
                    filename,
                    mimetype="image/png",
                )

        # Fetch from GitHub (for mi35x or if local not available)
        github_token = os.environ.get("GITHUB_TOKEN") or GITHUB_TOKEN
        plot_url = f"https://raw.githubusercontent.com/{GITHUB_REPO}/log/plot/{hardware}/{model}/{mode}/{filename}"

        headers = {}
        if github_token:
            headers["Authorization"] = f"token {github_token}"

        response = requests.get(plot_url, headers=headers, timeout=30)

        if response.status_code == 200:
            from flask import Response

            return Response(
                response.content,
                status=200,
                mimetype="image/png",
                headers={"Cache-Control": "public, max-age=3600"},
            )
        else:
            return f"Plot not found (local and GitHub)", 404

    except Exception as e:
        import traceback

        error_detail = traceback.format_exc()
        print(f"Error in serve_github_plot: {error_detail}")
        return f"Error fetching plot: {str(e)}", 500


@app.route("/health")
def health():
    """Health check endpoint"""
    return jsonify(
        {
            "status": "healthy",
            "timestamp": datetime.now().isoformat(),
            "base_dir": BASE_DIR,
            "github_token_set": bool(os.environ.get("GITHUB_TOKEN")),
            "use_github": USE_GITHUB,
            "current_hardware": CURRENT_HARDWARE,
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
