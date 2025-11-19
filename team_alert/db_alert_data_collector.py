"""
Database-Aware Alert Data Collector

Extends DailySummaryReporter to use database as primary data source.
Falls back to filesystem parsing if database unavailable.

This ensures team alerts get the same data as the dashboard - single source of truth.
"""

import os
import sys
from pathlib import Path
from typing import Dict, Optional

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from database.database import DashboardDatabase
from team_alert.send_daily_summary_alert import DailySummaryReporter


class DatabaseAlertDataCollector(DailySummaryReporter):
    """
    Team alert data collector that uses database as primary source

    Inherits from DailySummaryReporter for backward compatibility
    but adds database querying capabilities.
    """

    def __init__(
        self,
        webhook_url: Optional[str] = None,
        hardware: str = "mi30x",
        base_dir: str = "/mnt/raid/michael/sglang-ci",
        use_database: bool = True,
    ):
        """
        Initialize database-aware alert data collector

        Args:
            webhook_url: Microsoft Teams webhook URL (optional)
            hardware: Hardware type (mi30x, mi35x)
            base_dir: Base directory for CI logs
            use_database: Whether to use database (default: True)
        """
        super().__init__(webhook_url, hardware, base_dir)

        self.use_database = use_database
        self.db = None

        if use_database:
            try:
                self.db = DashboardDatabase()
            except Exception as e:
                print(f"⚠️  Could not initialize database: {e}")
                print("   Falling back to filesystem parsing")
                self.use_database = False

    def collect_task_results(self, date_str: str) -> Dict:
        """
        Collect results from all nightly tasks for a given date

        Args:
            date_str: Date string in YYYYMMDD format

        Returns:
            Dictionary with all task results
        """
        # Try database first
        if self.use_database and self.db:
            try:
                test_run = self.db.get_test_run(date_str, self.hardware)

                if test_run:
                    results = {}

                    # Get ALL benchmark results from database (includes validation tests, benchmarks, integration tests)
                    benchmark_results = self.db.get_benchmark_results(test_run["id"])

                    for br in benchmark_results:
                        # Check if task actually ran or was marked as "not run"
                        exists = br["status"] != "not run"

                        results[br["benchmark_name"]] = {
                            "exists": exists,
                            "status": br["status"],
                            "runtime": self._format_runtime(br["runtime_minutes"]),
                            "error": br["error_message"],
                            "gsm8k_accuracy": br["gsm8k_accuracy"],
                        }

                    # Get log file links from database
                    log_files = self.db.get_log_files(test_run["id"])

                    # Add log links to results
                    for result_name in results:
                        # Find matching log file
                        for log_file in log_files:
                            if (
                                result_name.lower().replace(" ", "_")
                                in log_file["log_name"].lower()
                            ):
                                results[result_name]["log_url"] = log_file["github_url"]
                                results[result_name]["log_path"] = log_file[
                                    "local_path"
                                ]
                                break

                    return results

            except Exception as e:
                print(f"⚠️  Database query failed: {e}")
                print("   Falling back to filesystem parsing")

        # Fallback to parent class filesystem parsing
        return super().collect_task_results(date_str)

    def parse_sanity_check_log(self, date_str: str) -> Optional[Dict]:
        """
        Parse sanity check results from database or filesystem

        Args:
            date_str: Date string in YYYYMMDD format

        Returns:
            Dictionary with sanity check results or None
        """
        # Try database first
        if self.use_database and self.db:
            try:
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

            except Exception as e:
                print(f"⚠️  Database query failed: {e}")
                print("   Falling back to filesystem parsing")

        # Fallback to parent class filesystem parsing
        return super().parse_sanity_check_log(date_str)

    def get_plot_links(self, date_str: str) -> Dict:
        """
        Get plot file links for a specific date

        Args:
            date_str: Date string in YYYYMMDD format

        Returns:
            Dictionary mapping benchmark names to plot URLs
        """
        plots = {}

        # Try database first
        if self.use_database and self.db:
            try:
                test_run = self.db.get_test_run(date_str, self.hardware)

                if test_run:
                    plot_files = self.db.get_plot_files(test_run["id"])

                    for pf in plot_files:
                        benchmark_name = pf["benchmark_name"]

                        if benchmark_name not in plots:
                            plots[benchmark_name] = []

                        plots[benchmark_name].append(
                            {
                                "suffix": pf["plot_suffix"],
                                "github_url": pf["github_url"],
                                "local_path": pf["local_path"],
                            }
                        )

                    if plots:
                        return plots

            except Exception as e:
                print(f"⚠️  Could not get plot links from database: {e}")

        # Return empty dict (plots are optional)
        return plots

    def extract_docker_image(self, date_str: str) -> Optional[str]:
        """
        Extract the Docker image used for tests/benchmarks from database

        Args:
            date_str: Date string in YYYYMMDD format

        Returns:
            Docker image name or None
        """
        # Try database first
        if self.use_database and self.db:
            try:
                test_run = self.db.get_test_run(date_str, self.hardware)

                if test_run and test_run.get("docker_image"):
                    return test_run["docker_image"]
            except Exception as e:
                print(f"⚠️  Could not get docker image from database: {e}")
                print("   Falling back to filesystem parsing")

        # Fallback to parent class filesystem parsing
        return super().extract_docker_image(date_str)

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

    def parse_cron_log_file(self, log_file_path: str) -> Dict:
        """
        Parse a cron log file for status information

        Args:
            log_file_path: Path to log file

        Returns:
            Dictionary with status info
        """
        result = {
            "exists": True,
            "status": "unknown",
            "runtime": None,
            "error": None,
        }

        if not os.path.exists(log_file_path):
            result["exists"] = False
            return result

        try:
            with open(log_file_path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()

            # Check for common status patterns
            if "Result: PASSED" in content or "Models passed:" in content:
                result["status"] = "pass"
            elif "Overall:" in content and "models passed (100" in content:
                result["status"] = "pass"
            elif "Result: FAILED" in content or "FAIL" in content:
                result["status"] = "fail"
            elif "✅" in content:
                result["status"] = "pass"

            # Extract runtime
            import re

            runtime_match = re.search(
                r"Total execution time:\s*(\d+)\s*seconds\s*\((\d+\.?\d*)\s*minutes\)",
                content,
            )
            if runtime_match:
                minutes = int(float(runtime_match.group(2)))
                hours = minutes // 60
                remaining_minutes = minutes % 60
                if hours > 0:
                    result["runtime"] = f"{hours}h {remaining_minutes}m"
                else:
                    result["runtime"] = f"{minutes}m"

        except Exception as e:
            result["error"] = f"Failed to parse: {str(e)}"

        return result
