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
import sqlite3
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

# Import database collector from database module
sys.path.insert(0, str(Path(__file__).parent.parent))
from database.db_data_collector import DatabaseDataCollector

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
USE_DATABASE = os.environ.get("USE_DATABASE", "true").lower() in ["true", "1", "yes"]

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
    # Ignore hostname detection errors; CURRENT_HARDWARE will remain None (default)
    pass


def get_data_collector(hardware: str):
    """
    Get appropriate data collector for hardware

    Priority order:
    1. Database (fastest, most efficient)
    2. GitHub (works behind firewall)
    3. Local filesystem (fallback)
    """
    # Use database if enabled (fastest option)
    if USE_DATABASE:
        return DatabaseDataCollector(hardware=hardware, base_dir=BASE_DIR)

    # Use GitHub or local based on location
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


def get_database_path() -> str:
    """Return the path to the dashboard database file"""
    return os.path.join(BASE_DIR, "database", "ci_dashboard.db")


def get_db_connection():
    """Create a SQLite connection with row factory enabled"""
    db_path = get_database_path()
    if not os.path.exists(db_path):
        return None

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


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
        # Use unified data collector (database-first with fallback)
        collector = get_data_collector(hardware)

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


@app.route("/api/dates/<hardware>")
@cache.cached(timeout=600)  # Cache for 10 minutes
def api_available_dates(hardware):
    """Get available dates for specific hardware (based on cron log directory existence)"""
    if hardware not in ["mi30x", "mi35x"]:
        return jsonify({"error": "Invalid hardware type"}), 400

    try:
        # Use unified data collector (database-first with fallback)
        collector = get_data_collector(hardware)
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


@app.route("/database")
def database_explorer():
    """Database explorer page"""
    default_hw = CURRENT_HARDWARE or "mi30x"
    return render_template(
        "database.html", github_repo=GITHUB_REPO, default_hardware=default_hw
    )


@app.route("/api/database/overview")
def api_database_overview():
    """Get filtered database overview data"""
    try:
        hardware = request.args.get("hardware", "mi30x")
        if hardware not in ["mi30x", "mi35x"]:
            hardware = "mi30x"

        selected_test = request.args.get("test", "all")
        selected_machine = request.args.get("machine", "all")  # Filter by machine name
        range_days = request.args.get("range", "7")
        try:
            range_days = int(range_days)
        except ValueError:
            range_days = 7
        range_days = max(1, min(range_days, 90))

        conn = get_db_connection()
        if conn is None:
            return jsonify({"error": "Database file not found"}), 404

        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT DISTINCT run_date
            FROM test_runs
            WHERE hardware = ?
            ORDER BY run_date DESC
            LIMIT 90
            """,
            (hardware,),
        )
        available_dates = [row["run_date"] for row in cursor.fetchall()]

        if not available_dates:
            conn.close()
            return jsonify({"error": "No data found for hardware"}), 404

        selected_date = request.args.get("date")
        if not selected_date or selected_date not in available_dates:
            selected_date = available_dates[0]

        try:
            selected_dt = datetime.strptime(selected_date, "%Y%m%d")
        except ValueError:
            selected_dt = datetime.strptime(available_dates[0], "%Y%m%d")
            selected_date = available_dates[0]

        start_dt = selected_dt - timedelta(days=range_days - 1)
        start_date = start_dt.strftime("%Y%m%d")

        cursor.execute(
            "SELECT DISTINCT benchmark_name FROM benchmark_results ORDER BY benchmark_name;"
        )
        benchmark_names = [row["benchmark_name"] for row in cursor.fetchall()]
        test_options = ["all"] + benchmark_names
        if selected_test not in test_options:
            selected_test = "all"

        # Get available machine names for this hardware
        cursor.execute(
            """
            SELECT DISTINCT machine_name FROM test_runs
            WHERE hardware = ? AND machine_name IS NOT NULL
            ORDER BY machine_name
            """,
            (hardware,),
        )
        machine_names = [row["machine_name"] for row in cursor.fetchall()]
        machine_options = ["all"] + machine_names
        if selected_machine not in machine_options:
            selected_machine = "all"

        # Get all test runs including multiple machines per date
        # Order by run_date DESC, then by machine_name to group by machine
        # Filter by machine if specified
        if selected_machine != "all":
            cursor.execute(
                """
                SELECT id, run_date, overall_status, passed_tasks, failed_tasks,
                       total_tasks, docker_image, not_run, run_datetime_pt, machine_name,
                       github_log_url, github_cron_log_url, github_detail_log_url, plot_github_url
                FROM test_runs
                WHERE hardware = ? AND run_date BETWEEN ? AND ? AND machine_name = ?
                ORDER BY run_date DESC, machine_name
                """,
                (hardware, start_date, selected_date, selected_machine),
            )
        else:
            cursor.execute(
                """
                SELECT id, run_date, overall_status, passed_tasks, failed_tasks,
                       total_tasks, docker_image, not_run, run_datetime_pt, machine_name,
                       github_log_url, github_cron_log_url, github_detail_log_url, plot_github_url
                FROM test_runs
                WHERE hardware = ? AND run_date BETWEEN ? AND ?
                ORDER BY run_date DESC, machine_name
                """,
                (hardware, start_date, selected_date),
            )
        daily_runs = []
        for row in cursor.fetchall():
            total_tasks = row["total_tasks"] or 0
            passed_tasks = row["passed_tasks"] or 0
            failed_tasks = row["failed_tasks"] or 0
            not_run = row["not_run"] or 0
            pass_rate = (
                round((passed_tasks / total_tasks) * 100, 1) if total_tasks else 0
            )
            daily_runs.append(
                {
                    "run_id": row["id"],
                    "run_date": row["run_date"],
                    "overall_status": row["overall_status"],
                    "passed_tasks": passed_tasks,
                    "failed_tasks": failed_tasks,
                    "total_tasks": total_tasks,
                    "not_run": not_run,
                    "docker_image": row["docker_image"],
                    "run_datetime_pt": row["run_datetime_pt"],
                    "machine_name": row["machine_name"],
                    "github_log_url": row["github_log_url"],
                    "github_cron_log_url": row["github_cron_log_url"],
                    "github_detail_log_url": row["github_detail_log_url"],
                    "plot_github_url": row["plot_github_url"],
                    "pass_rate": pass_rate,
                }
            )

        summary = {
            "total_runs": len(daily_runs),
            "passed_runs": sum(
                1 for r in daily_runs if r["overall_status"] == "passed"
            ),
            "failed_runs": sum(
                1 for r in daily_runs if r["overall_status"] == "failed"
            ),
            "partial_runs": sum(
                1 for r in daily_runs if r["overall_status"] == "partial"
            ),
            "average_pass_rate": (
                round(sum(r["pass_rate"] for r in daily_runs) / len(daily_runs), 1)
                if daily_runs
                else 0
            ),
            "latest_pass_rate": daily_runs[0]["pass_rate"] if daily_runs else 0,
            "range_days": range_days,
        }

        cursor.execute(
            """
            SELECT *
            FROM test_runs
            WHERE hardware = ? AND run_date = ?
            LIMIT 1
            """,
            (hardware, selected_date),
        )
        selected_row = cursor.fetchone()
        if not selected_row:
            conn.close()
            return jsonify({"error": "No data found for selected date"}), 404

        selected_run = {
            "run_id": selected_row["id"],
            "run_date": selected_row["run_date"],
            "hardware": selected_row["hardware"],
            "overall_status": selected_row["overall_status"],
            "docker_image": selected_row["docker_image"],
            "machine_name": selected_row["machine_name"],
            "total_tasks": selected_row["total_tasks"] or 0,
            "passed_tasks": selected_row["passed_tasks"] or 0,
            "failed_tasks": selected_row["failed_tasks"] or 0,
            "unknown_tasks": selected_row["unknown_tasks"] or 0,
            "not_run": selected_row["not_run"] or 0,
        }
        total_tasks = selected_run["total_tasks"]
        selected_run["pass_rate"] = (
            round((selected_run["passed_tasks"] / total_tasks) * 100, 1)
            if total_tasks
            else 0
        )

        cursor.execute(
            """
            SELECT log_type, log_name, local_path, github_url
            FROM log_files
            WHERE test_run_id = ?
            ORDER BY log_type, log_name
            """,
            (selected_row["id"],),
        )
        selected_run["log_files"] = [
            {
                "log_type": row["log_type"],
                "log_name": row["log_name"],
                "local_path": row["local_path"],
                "github_url": row["github_url"],
            }
            for row in cursor.fetchall()
        ]

        cursor.execute(
            """
            SELECT model_name, status, accuracy
            FROM sanity_check_results
            WHERE test_run_id = ?
            ORDER BY model_name
            """,
            (selected_row["id"],),
        )
        selected_run["sanity_results"] = [
            {
                "model_name": row["model_name"],
                "status": row["status"],
                "gsm8k_accuracy": (
                    round(row["accuracy"] * 100, 1)
                    if row["accuracy"] is not None
                    else None
                ),
                "gsm8k_threshold": None,  # Not stored in database
            }
            for row in cursor.fetchall()
        ]

        cursor.execute(
            """
            SELECT benchmark_name, status, gsm8k_accuracy, runtime_minutes, error_message
            FROM benchmark_results
            WHERE test_run_id = ?
            ORDER BY benchmark_name
            """,
            (selected_row["id"],),
        )
        selected_run["benchmark_results"] = [
            {
                "benchmark_name": row["benchmark_name"],
                "status": row["status"],
                "gsm8k_accuracy": (
                    round(row["gsm8k_accuracy"] * 100, 1)
                    if row["gsm8k_accuracy"] is not None
                    else None
                ),
                "gsm8k_threshold": None,  # Not stored in database
                "runtime_minutes": row["runtime_minutes"],
                "error_message": row["error_message"],
            }
            for row in cursor.fetchall()
        ]

        params = [hardware, start_date, selected_date]
        benchmark_query = """
            SELECT tr.run_date, br.benchmark_name, br.status, br.gsm8k_accuracy,
                   br.runtime_minutes, br.error_message, br.github_detail_log_url
            FROM benchmark_results br
            JOIN test_runs tr ON tr.id = br.test_run_id
            WHERE tr.hardware = ? AND tr.run_date BETWEEN ? AND ?
            """
        if selected_test != "all":
            benchmark_query += " AND br.benchmark_name = ?"
            params.append(selected_test)
        benchmark_query += " ORDER BY tr.run_date DESC, br.benchmark_name"

        cursor.execute(benchmark_query, tuple(params))
        benchmark_rows = cursor.fetchall()

        # Get plot files for generating per-benchmark plot URLs
        plot_lookup = {}  # {(run_date, benchmark_name): plot_url}
        for run in daily_runs:
            cursor.execute(
                """
                SELECT pf.benchmark_name, pf.github_url
                FROM plot_files pf
                WHERE pf.test_run_id = ?
                """,
                (run["run_id"],),
            )
            for plot_row in cursor.fetchall():
                key = (run["run_date"], plot_row["benchmark_name"])
                plot_lookup[key] = plot_row["github_url"]

        benchmarks_range = []
        for row in benchmark_rows:
            plot_key = (row["run_date"], row["benchmark_name"])
            benchmarks_range.append(
                {
                    "run_date": row["run_date"],
                    "benchmark_name": row["benchmark_name"],
                    "status": row["status"],
                    "gsm8k_accuracy": (
                        round(row["gsm8k_accuracy"] * 100, 1)
                        if row["gsm8k_accuracy"] is not None
                        else None
                    ),
                    "gsm8k_threshold": None,  # Not stored in database
                    "runtime_minutes": row["runtime_minutes"],
                    "error_message": row["error_message"],
                    "plot_url": plot_lookup.get(plot_key),  # Per-benchmark plot URL
                    "detail_log_url": row[
                        "github_detail_log_url"
                    ],  # Per-test detail log
                }
            )

        benchmark_summary = {
            "total": len(benchmarks_range),
            "passed": sum(1 for b in benchmarks_range if b["status"] == "pass"),
            "failed": sum(1 for b in benchmarks_range if b["status"] == "fail"),
        }
        benchmark_summary["pass_rate"] = (
            round((benchmark_summary["passed"] / benchmark_summary["total"]) * 100, 1)
            if benchmark_summary["total"]
            else 0
        )

        summary["benchmarks_total"] = benchmark_summary["total"]
        summary["benchmarks_passed"] = benchmark_summary["passed"]
        summary["benchmarks_failed"] = benchmark_summary["failed"]

        response = {
            "hardware": hardware,
            "selected_date": selected_date,
            "available_dates": available_dates,
            "date_range": {
                "start": start_date,
                "end": selected_date,
                "days": range_days,
            },
            "test_names": test_options,
            "selected_test": selected_test,
            "machine_names": machine_options,
            "selected_machine": selected_machine,
            "summary": summary,
            "daily_runs": daily_runs,
            "selected_run": selected_run,
            "benchmarks_range": benchmarks_range,
            "benchmark_summary": benchmark_summary,
        }

        conn.close()
        return jsonify(response)

    except Exception as e:
        import traceback

        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@app.route("/api/database/query", methods=["POST"])
def api_database_query():
    """Execute SQL query on database (SELECT only)"""
    try:
        import time

        data = request.get_json() or {}
        query = data.get("query", "").strip()

        if not query:
            return jsonify({"error": "No query provided"}), 400

        # Security: Only allow SELECT queries
        if not query.upper().startswith("SELECT"):
            return jsonify({"error": "Only SELECT queries are allowed"}), 403

        # Additional security: Block dangerous keywords
        dangerous_keywords = [
            "DROP",
            "DELETE",
            "UPDATE",
            "INSERT",
            "ALTER",
            "CREATE",
            "TRUNCATE",
            "REPLACE",
        ]
        query_upper = query.upper()
        for keyword in dangerous_keywords:
            if keyword in query_upper:
                return jsonify({"error": f"Keyword '{keyword}' is not allowed"}), 403

        conn = get_db_connection()
        if conn is None:
            return jsonify({"error": "Database file not found"}), 404

        start_time = time.time()
        cursor = conn.cursor()
        cursor.execute(query)
        rows = cursor.fetchall()
        columns = (
            [description[0] for description in cursor.description]
            if cursor.description
            else []
        )
        conn.close()

        rows_serializable = [list(row) for row in rows]
        execution_time = int((time.time() - start_time) * 1000)

        return jsonify(
            {
                "columns": columns,
                "rows": rows_serializable,
                "row_count": len(rows_serializable),
                "execution_time": execution_time,
            }
        )

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/database/schema")
def api_database_schema():
    """Get database schema"""
    try:
        conn = get_db_connection()
        if conn is None:
            return jsonify({"error": "Database file not found"}), 404

        cursor = conn.cursor()

        # Get all tables
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name;"
        )
        tables = cursor.fetchall()

        schema = {}
        for row in tables:
            table_name = row[0]
            cursor.execute(f"PRAGMA table_info({table_name});")
            columns = cursor.fetchall()
            schema[table_name] = [
                f"{col[1]} ({col[2]}){' PRIMARY KEY' if col[5] else ''}{' NOT NULL' if col[3] else ''}"
                for col in columns
            ]

        conn.close()

        return jsonify({"tables": schema})

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/database/refresh", methods=["POST"])
def api_database_refresh():
    """Refresh database from GitHub and clear cache"""
    try:
        import subprocess

        # Pull latest database from GitHub
        db_sync_script = os.path.join(BASE_DIR, "database", "sync_database.py")

        if not os.path.exists(db_sync_script):
            return (
                jsonify(
                    {
                        "success": False,
                        "error": f"Sync script not found at {db_sync_script}. Check BASE_DIR configuration.",
                    }
                ),
                500,
            )

        result = subprocess.run(
            ["python3", db_sync_script, "pull"],
            capture_output=True,
            text=True,
            timeout=60,
        )

        if result.returncode != 0:
            return (
                jsonify(
                    {
                        "success": False,
                        "error": f"Database sync failed: {result.stderr}",
                    }
                ),
                500,
            )

        # Clear all Flask caches
        cache.clear()

        # Get database file info
        db_path = get_database_path()
        if os.path.exists(db_path):
            file_size = os.path.getsize(db_path)
            file_mtime = os.path.getmtime(db_path)
            from datetime import datetime

            last_modified = datetime.fromtimestamp(file_mtime).strftime(
                "%Y-%m-%d %H:%M:%S"
            )

            return jsonify(
                {
                    "success": True,
                    "message": "Database refreshed successfully",
                    "database": {
                        "path": db_path,
                        "size_kb": round(file_size / 1024, 1),
                        "last_modified": last_modified,
                    },
                }
            )
        else:
            return (
                jsonify(
                    {"success": False, "error": "Database file not found after sync"}
                ),
                404,
            )

    except subprocess.TimeoutExpired:
        return jsonify({"success": False, "error": "Database sync timed out"}), 504
    except Exception as e:
        import traceback

        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/cache/clear", methods=["POST"])
def api_cache_clear():
    """Clear all Flask caches"""
    try:
        cache.clear()
        return jsonify({"success": True, "message": "Cache cleared successfully"})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/database/stats")
def api_database_stats():
    """Get database statistics"""
    try:
        conn = get_db_connection()
        if conn is None:
            return jsonify({"error": "Database file not found"}), 404

        cursor = conn.cursor()

        stats = {}
        tables = [
            "test_runs",
            "benchmark_results",
            "sanity_check_results",
            "log_files",
            "plot_files",
        ]

        for table in tables:
            cursor.execute(f"SELECT COUNT(*) FROM {table};")
            count = cursor.fetchone()[0]
            stats[table] = count

        cursor.execute("SELECT COUNT(DISTINCT run_date) FROM test_runs;")
        stats["unique_dates"] = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(DISTINCT hardware) FROM test_runs;")
        stats["hardware_types"] = cursor.fetchone()[0]

        db_path = get_database_path()
        stats["database_size_kb"] = int(os.path.getsize(db_path) / 1024)

        conn.close()

        return jsonify({"stats": stats})

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/database/sync", methods=["POST"])
def api_database_sync():
    """Sync database from GitHub to get latest updates from other machines"""
    try:
        import subprocess

        sync_script = os.path.join(BASE_DIR, "database", "sync_database.py")

        # Run sync in background (non-blocking)
        result = subprocess.run(
            ["python3", sync_script, "pull"],
            cwd=BASE_DIR,
            capture_output=True,
            text=True,
            timeout=30,
        )

        if result.returncode == 0:
            return jsonify(
                {"status": "success", "message": "Database synced from GitHub"}
            )
        else:
            return (
                jsonify(
                    {
                        "status": "warning",
                        "message": "Sync attempted but may have issues",
                    }
                ),
                200,
            )

    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


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

    if USE_DATABASE:
        print(f"📡 Data Source: Database (with filesystem fallback)")
    elif USE_GITHUB:
        print(f"📡 Data Source: GitHub (with local fallback)")
    else:
        print(f"📡 Data Source: Local filesystem only")
    print()

    app.run(host=args.host, port=args.port, debug=args.debug)


if __name__ == "__main__":
    main()
