"""
Database-Powered Data Collector

Uses the database as the primary data source with fallback to filesystem.
Provides much faster data access compared to parsing logs each time.
"""

import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

sys.path.insert(0, str(Path(__file__).parent.parent))

from dashboard.data_collector import DashboardDataCollector
from database.database import DashboardDatabase


class DatabaseDataCollector:
    """Collect CI data from database with filesystem fallback"""

    def __init__(
        self,
        hardware: str = "mi30x",
        base_dir: str = "/mnt/raid/michael/sglang-ci",
        db_path: Optional[str] = None,
    ):
        """
        Initialize database data collector

        Args:
            hardware: Hardware type (mi30x, mi35x)
            base_dir: Base directory for CI logs (for fallback)
            db_path: Path to database file (optional)
        """
        self.hardware = hardware
        self.base_dir = base_dir
        self.github_repo = os.environ.get("GITHUB_REPO", "ROCm/sglang-ci")

        # Initialize database
        self.db = DashboardDatabase(db_path)

        # Filesystem fallback collector
        self.fallback_collector = DashboardDataCollector(
            hardware=hardware, base_dir=base_dir
        )

    def collect_task_results(self, date_str: str) -> Dict:
        """
        Collect results from all nightly tasks for a given date

        Args:
            date_str: Date string in YYYYMMDD format

        Returns:
            Dictionary with all task results
        """
        # Try database first
        test_run = self.db.get_test_run(date_str, self.hardware)

        if test_run:
            # Build results from database
            results = {}

            # Get benchmark results
            benchmark_results = self.db.get_benchmark_results(test_run["id"])

            for br in benchmark_results:
                results[br["benchmark_name"]] = {
                    "exists": True,
                    "status": br["status"],
                    "runtime": self._format_runtime(br["runtime_minutes"]),
                    "error": br["error_message"],
                    "gsm8k_accuracy": br["gsm8k_accuracy"],
                }

            # Add validation tasks (these are tracked separately in filesystem)
            # We'll need to check logs for these
            validation_tasks = {
                "Unit Tests": "test_nightly.log",
                "PD Disaggregation Tests": "test_nightly_pd.log",
                "Sanity Check": "sanity_check_nightly.log",
                "Docker Image Check": "docker_image_check.log",
            }

            for task_name, log_file in validation_tasks.items():
                # Check if we have this in log_files table
                log_files = self.db.get_log_files(test_run["id"])
                log_exists = any(lf["log_name"] == log_file for lf in log_files)

                if log_exists:
                    # Try to get status from logs if available
                    # For now, mark as unknown and rely on fallback
                    results[task_name] = {
                        "exists": log_exists,
                        "status": "unknown",
                        "runtime": None,
                        "error": None,
                    }

            return results

        # Fallback to filesystem
        return self.fallback_collector.collect_task_results(date_str)

    def parse_sanity_check_log(self, date_str: str) -> Optional[Dict]:
        """
        Parse sanity check results from database

        Args:
            date_str: Date string in YYYYMMDD format

        Returns:
            Dictionary with sanity check results or None
        """
        # Try database first
        test_run = self.db.get_test_run(date_str, self.hardware)

        if test_run:
            sanity_results = self.db.get_sanity_check_results(test_run["id"])

            if sanity_results:
                model_results = {}
                for sr in sanity_results:
                    model_results[sr["model_name"]] = {
                        "status": sr["status"],
                        "accuracy": sr["accuracy"],
                    }

                return {
                    "model_results": model_results,
                    "log_file": f"test/sanity_check_log/{self.hardware}/{date_str}",
                }

        # Fallback to filesystem
        return self.fallback_collector.parse_sanity_check_log(date_str)

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
        return self.fallback_collector.calculate_summary_stats(
            task_results, sanity_results
        )

    def get_available_dates(self, max_days: int = 90) -> List[str]:
        """
        Get list of available dates with CI logs

        Args:
            max_days: Maximum number of days to look back

        Returns:
            List of date strings in YYYYMMDD format, sorted newest first
        """
        # Try database first
        dates = self.db.get_available_dates(self.hardware, max_days)

        if dates:
            return dates

        # Fallback to filesystem
        return self.fallback_collector.get_available_dates(max_days)

    def get_historical_trends(self, days: int = 30) -> Dict:
        """
        Get historical trend data for the specified number of days

        Args:
            days: Number of days to include

        Returns:
            Dictionary with trend data
        """
        # Try database first
        trends = self.db.get_historical_trends(self.hardware, days)

        if trends and trends.get("dates"):
            return trends

        # Fallback to filesystem
        return self.fallback_collector.get_historical_trends(days)

    def get_available_plots(self, date_str: str) -> Dict:
        """
        Get list of available plots for a specific date

        Args:
            date_str: Date string in YYYYMMDD format

        Returns:
            Dictionary mapping benchmark names to plot URLs
        """
        # Try database first
        test_run = self.db.get_test_run(date_str, self.hardware)

        if test_run:
            plot_files = self.db.get_plot_files(test_run["id"])

            if plot_files:
                plots = {}

                # Group by benchmark name
                for pf in plot_files:
                    benchmark_name = pf["benchmark_name"]

                    if benchmark_name not in plots:
                        plots[benchmark_name] = []

                    # Generate URLs
                    plot_url = f"https://github.com/{self.github_repo}/blob/log/plot/{self.hardware}/{pf['local_path']}"
                    raw_url = f"/github-plots/{self.hardware}/{pf['local_path']}"

                    plots[benchmark_name].append(
                        {
                            "suffix": pf["plot_suffix"],
                            "url": pf["github_url"] or plot_url,
                            "raw_url": raw_url,
                            "local_available": pf["local_path"] is not None,
                        }
                    )

                return plots

        # Fallback to filesystem
        return self.fallback_collector.get_available_plots(date_str)

    def get_dates_with_plots(self, max_days: int = 90) -> List[str]:
        """
        Get list of dates that have plots available

        Args:
            max_days: Maximum number of days to check

        Returns:
            List of date strings in YYYYMMDD format with available plots
        """
        # Use database if available
        dates_with_data = self.db.get_available_dates(self.hardware, max_days)

        if dates_with_data:
            # Filter to only dates with plots
            dates_with_plots = []
            for date_str in dates_with_data:
                test_run = self.db.get_test_run(date_str, self.hardware)
                if test_run:
                    plot_files = self.db.get_plot_files(test_run["id"])
                    if plot_files:
                        dates_with_plots.append(date_str)

            if dates_with_plots:
                return dates_with_plots

        # Fallback to filesystem
        return self.fallback_collector.get_dates_with_plots(max_days)

    def get_test_history(self, days: int = 30) -> Dict:
        """
        Get individual test pass/fail history for all tests

        Args:
            days: Number of days to include

        Returns:
            Dictionary with per-test history
        """
        # For now, use fallback since we need detailed test history
        # TODO: Build this from database for better performance
        return self.fallback_collector.get_test_history(days)

    def _format_runtime(self, runtime_minutes: Optional[int]) -> Optional[str]:
        """
        Format runtime minutes to human-readable string

        Args:
            runtime_minutes: Runtime in minutes

        Returns:
            Formatted runtime string (e.g., "2h 30m", "45m")
        """
        if runtime_minutes is None:
            return None

        hours = runtime_minutes // 60
        minutes = runtime_minutes % 60

        if hours > 0:
            return f"{hours}h {minutes}m"
        else:
            return f"{minutes}m"
