#!/usr/bin/env python3
"""
Dashboard Data Collector

Collects and aggregates CI data from logs for the dashboard.
Reuses logic from send_daily_summary_alert.py but extends it for dashboard needs.
"""

import glob
import os
import re

# Import the DailySummaryReporter to reuse its parsing logic
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

sys.path.insert(0, str(Path(__file__).parent.parent))
from team_alert.send_daily_summary_alert import DailySummaryReporter


class DashboardDataCollector:
    """Collect and aggregate CI data for dashboard display"""

    def __init__(
        self, hardware: str = "mi30x", base_dir: str = "/mnt/raid/michael/sglang-ci"
    ):
        """
        Initialize data collector

        Args:
            hardware: Hardware type (mi30x, mi35x)
            base_dir: Base directory for CI logs
        """
        self.hardware = hardware
        self.base_dir = base_dir
        self.github_repo = os.environ.get("GITHUB_REPO", "ROCm/sglang-ci")

        # Initialize the underlying reporter for parsing
        self.reporter = DailySummaryReporter(
            webhook_url=None,  # No Teams webhook needed
            hardware=hardware,
            base_dir=base_dir,
        )

    def collect_task_results(self, date_str: str) -> Dict:
        """
        Collect results from all nightly tasks for a given date

        Args:
            date_str: Date string in YYYYMMDD format

        Returns:
            Dictionary with all task results
        """
        return self.reporter.collect_task_results(date_str)

    def parse_sanity_check_log(self, date_str: str) -> Optional[Dict]:
        """
        Parse sanity check timing_summary log for model accuracies

        Args:
            date_str: Date string in YYYYMMDD format

        Returns:
            Dictionary with sanity check results or None
        """
        return self.reporter.parse_sanity_check_log(date_str)

    def calculate_summary_stats(
        self, task_results: Dict, sanity_results: Optional[Dict]
    ) -> Dict:
        """
        Calculate summary statistics from task results

        Args:
            task_results: Dictionary of task results
            sanity_results: Dictionary of sanity check results

        Returns:
            Dictionary with summary statistics
        """
        # Parse sanity results for per-model counts
        sanity_model_count = 0
        sanity_passed = 0
        sanity_failed = 0

        if sanity_results:
            model_results = sanity_results["model_results"]
            sanity_model_count = len(model_results)
            sanity_passed = sum(
                1 for r in model_results.values() if r["status"] == "pass"
            )
            sanity_failed = sum(
                1 for r in model_results.values() if r["status"] == "fail"
            )

        # Count task statuses (excluding "Sanity Check" from task_results)
        total_tasks = (
            len([k for k in task_results.keys() if k != "Sanity Check"])
            + sanity_model_count
        )
        passed_tasks = (
            sum(
                1
                for k, r in task_results.items()
                if k != "Sanity Check" and r["status"] == "pass"
            )
            + sanity_passed
        )
        failed_tasks = (
            sum(
                1
                for k, r in task_results.items()
                if k != "Sanity Check" and r["status"] == "fail"
            )
            + sanity_failed
        )
        unknown_tasks = sum(
            1
            for k, r in task_results.items()
            if k != "Sanity Check" and r["status"] == "unknown"
        )
        not_run = sum(
            1
            for k, r in task_results.items()
            if k != "Sanity Check" and not r["exists"]
        )

        # Overall status
        if failed_tasks > 0:
            overall_status = "failed"
        elif unknown_tasks > 0 or not_run > 0:
            overall_status = "partial"
        elif passed_tasks > 0:
            overall_status = "passed"
        else:
            overall_status = "unknown"

        return {
            "total_tasks": total_tasks,
            "passed_tasks": passed_tasks,
            "failed_tasks": failed_tasks,
            "unknown_tasks": unknown_tasks,
            "not_run": not_run,
            "tasks_run": total_tasks - not_run,
            "overall_status": overall_status,
            "sanity_passed": sanity_passed,
            "sanity_failed": sanity_failed,
            "sanity_total": sanity_model_count,
        }

    def get_available_dates(self, max_days: int = 90) -> List[str]:
        """
        Get list of available dates with CI logs

        Args:
            max_days: Maximum number of days to look back

        Returns:
            List of date strings in YYYYMMDD format, sorted newest first
        """
        cron_log_dir = os.path.join(self.base_dir, "cron", "cron_log", self.hardware)

        if not os.path.exists(cron_log_dir):
            return []

        # Get all date directories
        date_dirs = []
        for entry in os.listdir(cron_log_dir):
            full_path = os.path.join(cron_log_dir, entry)
            if os.path.isdir(full_path) and re.match(r"\d{8}", entry):
                date_dirs.append(entry)

        # Sort by date (newest first) and limit
        date_dirs.sort(reverse=True)
        return date_dirs[:max_days]

    def get_historical_trends(self, days: int = 30) -> Dict:
        """
        Get historical trend data for the specified number of days

        Args:
            days: Number of days to include

        Returns:
            Dictionary with trend data
        """
        dates = self.get_available_dates(max_days=days)

        trends = {
            "dates": [],
            "overall_status": [],
            "passed_tasks": [],
            "failed_tasks": [],
            "total_tasks": [],
            "pass_rate": [],
            "benchmarks": {},  # Task-specific trends
        }

        # Task names to track individually
        benchmark_tasks = [
            "Grok Online Benchmark",
            "Grok 2 Online Benchmark",
            "DeepSeek Online Benchmark",
        ]

        # Initialize benchmark trends
        for task in benchmark_tasks:
            trends["benchmarks"][task] = {
                "status": [],
                "gsm8k_accuracy": [],
                "runtime_minutes": [],
            }

        for date_str in reversed(dates):  # Process oldest to newest
            try:
                task_results = self.collect_task_results(date_str)
                sanity_results = self.parse_sanity_check_log(date_str)
                stats = self.calculate_summary_stats(task_results, sanity_results)

                # Format date for display
                date_obj = datetime.strptime(date_str, "%Y%m%d")
                display_date = date_obj.strftime("%Y-%m-%d")

                trends["dates"].append(display_date)
                trends["overall_status"].append(stats["overall_status"])
                trends["passed_tasks"].append(stats["passed_tasks"])
                trends["failed_tasks"].append(stats["failed_tasks"])
                trends["total_tasks"].append(stats["total_tasks"])

                # Calculate pass rate
                if stats["tasks_run"] > 0:
                    pass_rate = (stats["passed_tasks"] / stats["tasks_run"]) * 100
                else:
                    pass_rate = 0
                trends["pass_rate"].append(round(pass_rate, 1))

                # Track individual benchmark trends
                for task in benchmark_tasks:
                    if task in task_results:
                        result = task_results[task]
                        trends["benchmarks"][task]["status"].append(result["status"])

                        # Extract GSM8K accuracy if available
                        if result.get("gsm8k_accuracy") is not None:
                            accuracy_pct = result["gsm8k_accuracy"] * 100
                            trends["benchmarks"][task]["gsm8k_accuracy"].append(
                                round(accuracy_pct, 1)
                            )
                        else:
                            trends["benchmarks"][task]["gsm8k_accuracy"].append(None)

                        # Extract runtime in minutes
                        if result.get("runtime"):
                            runtime_str = result["runtime"]
                            # Parse "Xh Ym" or "Ym" format
                            hours = 0
                            minutes = 0
                            if "h" in runtime_str:
                                parts = runtime_str.split("h")
                                hours = int(parts[0].strip())
                                if len(parts) > 1 and "m" in parts[1]:
                                    minutes = int(parts[1].strip().replace("m", ""))
                            elif "m" in runtime_str:
                                minutes = int(runtime_str.replace("m", "").strip())

                            total_minutes = hours * 60 + minutes
                            trends["benchmarks"][task]["runtime_minutes"].append(
                                total_minutes
                            )
                        else:
                            trends["benchmarks"][task]["runtime_minutes"].append(None)
                    else:
                        # Task not found for this date
                        trends["benchmarks"][task]["status"].append("unknown")
                        trends["benchmarks"][task]["gsm8k_accuracy"].append(None)
                        trends["benchmarks"][task]["runtime_minutes"].append(None)

            except Exception as e:
                print(f"Warning: Could not process data for {date_str}: {e}")
                continue

        return trends

    def get_available_plots(self, date_str: str) -> Dict:
        """
        Get list of available plots for a specific date

        Args:
            date_str: Date string in YYYYMMDD format

        Returns:
            Dictionary mapping benchmark names to plot URLs
        """
        plots = {}

        # Define benchmark to model directory mapping
        if self.hardware == "mi35x":
            benchmark_model_map = {
                "Grok Online Benchmark": ("GROK1", ["standard"]),
                "Grok 2 Online Benchmark": ("GROK2", ["standard"]),
                "DeepSeek Online Benchmark": (
                    "DeepSeek-R1-MXFP4-Preview",
                    ["all", "standard"],
                ),
            }
        else:  # mi30x
            benchmark_model_map = {
                "Grok Online Benchmark": ("GROK1", ["standard"]),
                "Grok 2 Online Benchmark": ("GROK2", ["standard"]),
                "DeepSeek Online Benchmark": ("DeepSeek-V3-0324", ["standard"]),
            }

        for benchmark_name, (model_dir, suffixes) in benchmark_model_map.items():
            plots[benchmark_name] = []

            for suffix in suffixes:
                plot_filename = f"{date_str}_{model_dir}_online_{suffix}.png"

                # Check if plot exists locally in plots_server
                local_plot_path = os.path.join(
                    self.base_dir, "plots_server", model_dir, "online", plot_filename
                )

                # Use GitHub proxy endpoint (handles both local and GitHub plots)
                plot_url = f"https://github.com/{self.github_repo}/blob/log/plot/{self.hardware}/{model_dir}/online/{plot_filename}"
                raw_url = (
                    f"/github-plots/{self.hardware}/{model_dir}/online/{plot_filename}"
                )

                plots[benchmark_name].append(
                    {
                        "suffix": suffix,
                        "url": plot_url,
                        "raw_url": raw_url,
                        "local_available": os.path.exists(local_plot_path),
                    }
                )

        return plots

    def get_log_file_path(self, date_str: str, log_filename: str) -> Optional[str]:
        """
        Get the full path to a log file

        Args:
            date_str: Date string in YYYYMMDD format
            log_filename: Name of the log file

        Returns:
            Full path to log file or None if not found
        """
        log_path = os.path.join(
            self.base_dir, "cron", "cron_log", self.hardware, date_str, log_filename
        )

        if os.path.exists(log_path):
            return log_path

        return None

    def get_dates_with_plots(self, max_days: int = 90) -> List[str]:
        """
        Get list of dates that have plots available from local filesystem

        Args:
            max_days: Maximum number of days to check

        Returns:
            List of date strings in YYYYMMDD format with available plots
        """
        dates_with_plots = set()

        # Check local plots_server directory
        plots_base = os.path.join(self.base_dir, "plots_server")

        if not os.path.exists(plots_base):
            return []

        # Check for each model directory
        models = ["GROK1", "GROK2", "DeepSeek-V3-0324", "DeepSeek-R1-MXFP4-Preview"]

        for model_dir in models:
            model_path = os.path.join(plots_base, model_dir, "online")
            if not os.path.exists(model_path):
                continue

            # List all PNG files
            try:
                for filename in os.listdir(model_path):
                    if filename.endswith(".png"):
                        # Extract date from filename: YYYYMMDD_MODEL_online_*.png
                        date_match = re.match(r"(\d{8})_.*\.png", filename)
                        if date_match:
                            date_str = date_match.group(1)
                            dates_with_plots.add(date_str)
            except Exception:
                continue

        # Sort dates (newest first) and limit to max_days
        sorted_dates = sorted(list(dates_with_plots), reverse=True)[:max_days]

        return sorted_dates
