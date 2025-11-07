#!/usr/bin/env python3
"""
GitHub Data Collector

Collects and aggregates CI data from GitHub instead of local filesystem.
This allows the dashboard to work even when the server is behind a firewall.
"""

import os
import re

# Import the local data collector for fallback
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import requests

sys.path.insert(0, str(Path(__file__).parent.parent))
from dashboard.data_collector import DashboardDataCollector as LocalDataCollector


class GitHubDataCollector:
    """Collect and aggregate CI data from GitHub log branch"""

    def __init__(
        self,
        hardware: str = "mi30x",
        base_dir: str = "/mnt/raid/michael/sglang-ci",
        github_repo: str = "ROCm/sglang-ci",
        use_local_fallback: bool = True,
        github_token: Optional[str] = None,
    ):
        """
        Initialize GitHub data collector

        Args:
            hardware: Hardware type (mi30x, mi35x)
            base_dir: Base directory for CI logs (used for local fallback)
            github_repo: GitHub repository (owner/repo format)
            use_local_fallback: Whether to fall back to local filesystem if GitHub fails
            github_token: GitHub personal access token for private repos
        """
        self.hardware = hardware
        self.base_dir = base_dir
        self.github_repo = github_repo
        self.use_local_fallback = use_local_fallback
        self.github_token = github_token or os.environ.get("GITHUB_TOKEN")

        # GitHub raw content URL
        self.github_raw_base = f"https://raw.githubusercontent.com/{github_repo}/log"

        # GitHub API URL
        self.github_api_base = f"https://api.github.com/repos/{github_repo}/contents"

        # Session for connection pooling
        self.session = requests.Session()

        # Add authentication header if token is available
        if self.github_token:
            self.session.headers.update(
                {
                    "Authorization": f"token {self.github_token}",
                    "Accept": "application/vnd.github.v3+json",
                }
            )

        # Local fallback collector
        if use_local_fallback:
            self.local_collector = LocalDataCollector(
                hardware=hardware, base_dir=base_dir
            )
        else:
            self.local_collector = None

    def _fetch_github_raw(self, path: str) -> Optional[str]:
        """
        Fetch raw file content from GitHub

        Args:
            path: Path relative to repository root

        Returns:
            File content as string or None if not found
        """
        url = f"{self.github_raw_base}/{path}"
        try:
            response = self.session.get(url, timeout=10)
            if response.status_code == 200:
                return response.text
            return None
        except Exception as e:
            print(f"Warning: Could not fetch {url}: {e}")
            return None

    def _fetch_github_directory(self, path: str) -> Optional[List[Dict]]:
        """
        List directory contents from GitHub API

        Args:
            path: Path relative to repository root

        Returns:
            List of file/directory entries or None if not found
        """
        url = f"{self.github_api_base}/{path}?ref=log"
        try:
            response = self.session.get(url, timeout=10)
            if response.status_code == 200:
                return response.json()
            return None
        except Exception as e:
            print(f"Warning: Could not fetch directory {url}: {e}")
            return None

    def find_timing_summary_log(
        self, model_dir, mode_suffix: str, date_str: str
    ) -> Optional[str]:
        """
        Find timing_summary log file for benchmark tasks from GitHub

        Args:
            model_dir: Model directory name(s)
            mode_suffix: Mode suffix (e.g., "online", "online_dp_attention")
            date_str: Date string (YYYYMMDD)

        Returns:
            Content of timing_summary log file or None
        """
        # Support both single string and list of directories
        model_dirs = [model_dir] if isinstance(model_dir, str) else model_dir

        for model_dir_name in model_dirs:
            # Try to find the timing summary log in GitHub
            online_path = f"online/{model_dir_name}"

            # List directory contents
            entries = self._fetch_github_directory(online_path)
            if not entries:
                continue

            # Look for directories matching the date and mode
            for entry in entries:
                if entry["type"] != "dir":
                    continue

                dir_name = entry["name"]

                # Check if directory matches date and mode suffix
                if date_str in dir_name and mode_suffix in dir_name:
                    # Check if it ends with the exact mode suffix
                    if dir_name.endswith(f"_{mode_suffix}") or dir_name.endswith(
                        mode_suffix
                    ):
                        # Try to fetch timing_summary log
                        log_dir_path = f"{online_path}/{dir_name}"
                        log_entries = self._fetch_github_directory(log_dir_path)

                        if log_entries:
                            # Find timing_summary*.log file
                            for log_entry in log_entries:
                                if log_entry["name"].startswith(
                                    "timing_summary"
                                ) and log_entry["name"].endswith(".log"):
                                    log_path = f"{log_dir_path}/{log_entry['name']}"
                                    content = self._fetch_github_raw(log_path)
                                    if content:
                                        return content

        # Fallback to local if enabled
        if self.use_local_fallback and self.local_collector:
            log_path = self.local_collector.reporter.find_timing_summary_log(
                model_dir, mode_suffix, date_str
            )
            if log_path:
                try:
                    with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
                        return f.read()
                except Exception:
                    pass

        return None

    def parse_timing_summary_log(self, log_content: str) -> Dict:
        """
        Parse timing_summary log content for benchmark results

        Args:
            log_content: Content of timing_summary log file

        Returns:
            Dictionary with status info
        """
        result = {
            "exists": True,
            "status": "unknown",
            "runtime": None,
            "error": None,
            "gsm8k_accuracy": None,
        }

        if not log_content:
            result["exists"] = False
            return result

        try:
            # Check if log is incomplete
            lines = log_content.strip().split("\n")
            if (
                len(lines) < 20
                and "GSM8K" not in log_content
                and "Total execution time" not in log_content
            ):
                result["status"] = "fail"
                result["error"] = "Test failed or did not complete"
                return result

            # Extract GSM8K accuracy
            gsm8k_match = re.search(r"GSM8K accuracy:\s*([\d.]+)", log_content)
            if gsm8k_match:
                result["gsm8k_accuracy"] = float(gsm8k_match.group(1))

            # Check for errors
            error_count_match = re.search(r"RuntimeError count:\s*(\d+)", log_content)
            if error_count_match and int(error_count_match.group(1)) > 0:
                result["status"] = "fail"
                result["error"] = f"RuntimeError count: {error_count_match.group(1)}"
            elif "Server error status: FAIL" in log_content:
                result["status"] = "fail"
                result["error"] = "Server errors detected"
            elif result["gsm8k_accuracy"] is not None:
                result["status"] = "pass"
            else:
                # Check if it's an incomplete run
                has_completion_marker = (
                    "Total execution time:" in log_content
                    or ("End time:" in log_content and "Total duration:" in log_content)
                    or re.search(r"Server error status:\s*(PASS|FAIL)", log_content)
                )

                if "Script started at:" in log_content and not has_completion_marker:
                    result["status"] = "fail"
                    result["error"] = "Test did not complete"
                else:
                    result["status"] = "unknown"

            # Extract runtime
            runtime_match = re.search(
                r"Total execution time:\s*(\d+)\s*seconds\s*\((\d+)\s*minutes\)",
                log_content,
            )
            if runtime_match:
                minutes = int(runtime_match.group(2))
                hours = minutes // 60
                remaining_minutes = minutes % 60
                if hours > 0:
                    result["runtime"] = f"{hours}h {remaining_minutes}m"
                else:
                    result["runtime"] = f"{minutes}m"

        except Exception as e:
            result["error"] = f"Failed to parse: {str(e)}"
            result["status"] = "fail"

        return result

    def parse_cron_log_file(self, date_str: str, log_filename: str) -> Dict:
        """
        Parse a cron log file from GitHub

        Args:
            date_str: Date string (YYYYMMDD)
            log_filename: Name of the log file

        Returns:
            Dictionary with status info
        """
        result = {
            "exists": False,
            "status": "unknown",
            "runtime": None,
            "error": None,
        }

        # Fetch log from GitHub
        log_path = f"cron_log/{self.hardware}/{date_str}/{log_filename}"
        content = self._fetch_github_raw(log_path)

        if not content:
            # Try local fallback
            if self.use_local_fallback and self.local_collector:
                local_path = os.path.join(
                    self.base_dir,
                    "cron",
                    "cron_log",
                    self.hardware,
                    date_str,
                    log_filename,
                )
                if os.path.exists(local_path):
                    try:
                        with open(
                            local_path, "r", encoding="utf-8", errors="ignore"
                        ) as f:
                            content = f.read()
                    except Exception:
                        pass

        if not content:
            return result

        result["exists"] = True

        try:
            # Check for bash errors
            if "bash:" in content and "No such file or directory" in content:
                result["status"] = "fail"
                result["error"] = "Script not found"
                return result

            # Check for common success/failure patterns
            if "Result: PASSED" in content or "Models passed:" in content:
                result["status"] = "pass"
            elif "Overall:" in content and "models passed (100" in content:
                result["status"] = "pass"
            elif "Result: FAILED" in content or "FAIL" in content:
                result["status"] = "fail"
            elif "Total execution time:" in content or "✅" in content:
                result["status"] = "pass"
            else:
                result["status"] = "unknown"

            # Extract runtime
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

            # Extract error details
            if result["status"] == "fail":
                error_patterns = [
                    r"Error:\s*(.+)",
                    r"FAILED\s*\((.+?)\)",
                    r"RuntimeError:\s*(.+)",
                    r"bash:\s*(.+)",
                ]
                for pattern in error_patterns:
                    error_match = re.search(pattern, content, re.IGNORECASE)
                    if error_match:
                        error_text = error_match.group(1).strip()
                        if len(error_text) > 100:
                            error_text = error_text[:100] + "..."
                        result["error"] = error_text
                        break

        except Exception as e:
            result["error"] = f"Failed to parse log: {str(e)}"

        return result

    def parse_sanity_check_log(self, date_str: str) -> Optional[Dict]:
        """
        Parse sanity check timing_summary log from GitHub

        Args:
            date_str: Date string in YYYYMMDD format

        Returns:
            Dictionary with sanity check results or None
        """
        # Try to find sanity check logs in GitHub
        sanity_base_path = f"test/sanity_check_log/{self.hardware}"

        # List directories
        entries = self._fetch_github_directory(sanity_base_path)
        if not entries:
            # Fallback to local
            if self.use_local_fallback and self.local_collector:
                return self.local_collector.parse_sanity_check_log(date_str)
            return None

        # Find directories matching the date
        matching_dirs = []
        for entry in entries:
            if entry["type"] == "dir" and date_str in entry["name"]:
                matching_dirs.append(entry["name"])

        if not matching_dirs:
            # Fallback to local
            if self.use_local_fallback and self.local_collector:
                return self.local_collector.parse_sanity_check_log(date_str)
            return None

        # Use the first matching directory (should be most recent)
        matching_dirs.sort(reverse=True)
        log_dir = matching_dirs[0]

        # Find timing_summary log
        log_dir_path = f"{sanity_base_path}/{log_dir}"
        log_entries = self._fetch_github_directory(log_dir_path)

        if not log_entries:
            return None

        # Find timing_summary*.log file
        log_content = None
        for log_entry in log_entries:
            if log_entry["name"].startswith("timing_summary") and log_entry[
                "name"
            ].endswith(".log"):
                if (
                    date_str in log_entry["name"] or True
                ):  # Accept any timing_summary log
                    log_path = f"{log_dir_path}/{log_entry['name']}"
                    log_content = self._fetch_github_raw(log_path)
                    if log_content:
                        break

        if not log_content:
            # Fallback to local
            if self.use_local_fallback and self.local_collector:
                return self.local_collector.parse_sanity_check_log(date_str)
            return None

        # Parse the timing_summary log (reuse local collector's logic)
        model_results = {}

        try:
            # Extract model sections
            model_sections = re.findall(
                r"===\s+(\S+)\s+on\s+(\S+)\s+===(.*?)(?====|$)", log_content, re.DOTALL
            )

            for model_name, _platform, section_content in model_sections:
                # Extract final result
                result_match = re.search(
                    r"Final result:\s*(PASS \[OK\]|FAIL \[X\])", section_content
                )

                status = "unknown"
                if result_match:
                    status = "pass" if "PASS" in result_match.group(1) else "fail"

                # Extract average accuracy
                avg_accuracy = None
                avg_acc_match = re.search(
                    r"Average accuracy:\s*([\d.]+)", section_content
                )
                if avg_acc_match:
                    avg_accuracy = float(avg_acc_match.group(1))
                else:
                    # Try to extract from individual accuracies
                    accuracies_match = re.search(
                        r"Accuracies:\s*\[([\d.,\s]+)\]", section_content
                    )
                    if accuracies_match:
                        acc_str = accuracies_match.group(1)
                        accs = [
                            float(a.strip()) for a in acc_str.split(",") if a.strip()
                        ]
                        if accs:
                            avg_accuracy = sum(accs) / len(accs)

                if avg_accuracy is not None:
                    model_results[model_name] = {
                        "status": status,
                        "accuracy": avg_accuracy,
                    }

        except Exception as e:
            print(f"Warning: Could not parse sanity check log: {e}")
            return None

        if not model_results:
            return None

        return {
            "model_results": model_results,
            "log_file": f"{log_dir_path}/timing_summary",
        }

    def collect_task_results(self, date_str: str) -> Dict:
        """
        Collect results from all nightly tasks for a given date from GitHub

        Args:
            date_str: Date string in YYYYMMDD format

        Returns:
            Dictionary with all task results
        """
        results = {}

        # Performance Benchmarks
        benchmarks = {
            "Grok 2 Online Benchmark": (["GROK2"], "online"),
            "Grok Online Benchmark": (["GROK1"], "online"),
            "DeepSeek Online Benchmark": (
                ["DeepSeek-R1-MXFP4-Preview", "DeepSeek-V3-0324"],
                "online",
            ),
        }

        for task_name, (model_dir, mode_suffix) in benchmarks.items():
            timing_log_content = self.find_timing_summary_log(
                model_dir, mode_suffix, date_str
            )
            if timing_log_content:
                results[task_name] = self.parse_timing_summary_log(timing_log_content)
            else:
                results[task_name] = {
                    "exists": False,
                    "status": "unknown",
                    "runtime": None,
                    "error": None,
                }

        # Integration Tests
        integration_tests = {
            "DeepSeek DP Attention Test": (
                ["DeepSeek-R1-MXFP4-Preview", "DeepSeek-V3-0324"],
                "online_dp_attention",
            ),
            "DeepSeek Torch Compile Test": (
                ["DeepSeek-R1-MXFP4-Preview", "DeepSeek-V3-0324"],
                "online_torch_compile",
            ),
            "DeepSeek DP+Torch Compile": (
                ["DeepSeek-R1-MXFP4-Preview", "DeepSeek-V3-0324"],
                "online_dp_attention_torch_compile",
            ),
        }

        for task_name, (model_dir, mode_suffix) in integration_tests.items():
            timing_log_content = self.find_timing_summary_log(
                model_dir, mode_suffix, date_str
            )
            if timing_log_content:
                results[task_name] = self.parse_timing_summary_log(timing_log_content)
            else:
                results[task_name] = {
                    "exists": False,
                    "status": "unknown",
                    "runtime": None,
                    "error": None,
                }

        # Validation & Checks
        validation_tasks = {
            "Unit Tests": "test_nightly.log",
            "PD Disaggregation Tests": "test_nightly_pd.log",
            "Sanity Check": "sanity_check_nightly.log",
            "Docker Image Check": "docker_image_check.log",
        }

        for task_name, log_file in validation_tasks.items():
            results[task_name] = self.parse_cron_log_file(date_str, log_file)

        return results

    def get_available_dates(self, max_days: int = 90) -> List[str]:
        """
        Get list of available dates with CI logs from GitHub

        Args:
            max_days: Maximum number of days to look back

        Returns:
            List of date strings in YYYYMMDD format, sorted newest first
        """
        cron_log_path = f"cron_log/{self.hardware}"

        # List directory contents
        entries = self._fetch_github_directory(cron_log_path)

        if not entries:
            # Fallback to local
            if self.use_local_fallback and self.local_collector:
                return self.local_collector.get_available_dates(max_days)
            return []

        # Extract date directories
        date_dirs = []
        for entry in entries:
            if entry["type"] == "dir" and re.match(r"\d{8}", entry["name"]):
                date_dirs.append(entry["name"])

        # Sort by date (newest first) and limit
        date_dirs.sort(reverse=True)
        return date_dirs[:max_days]

    # Delegate remaining methods to local collector with GitHub-first approach
    def calculate_summary_stats(
        self, task_results: Dict, sanity_results: Optional[Dict]
    ) -> Dict:
        """Calculate summary statistics from task results"""
        if self.local_collector:
            return self.local_collector.calculate_summary_stats(
                task_results, sanity_results
            )
        # Simplified version if no local collector
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

    def get_historical_trends(self, days: int = 30) -> Dict:
        """Get historical trend data"""
        if self.local_collector:
            # Use GitHub data collector for individual date collection
            dates = self.get_available_dates(max_days=days)

            trends = {
                "dates": [],
                "overall_status": [],
                "passed_tasks": [],
                "failed_tasks": [],
                "total_tasks": [],
                "pass_rate": [],
                "benchmarks": {},
            }

            benchmark_tasks = [
                "Grok Online Benchmark",
                "Grok 2 Online Benchmark",
                "DeepSeek Online Benchmark",
            ]

            for task in benchmark_tasks:
                trends["benchmarks"][task] = {
                    "status": [],
                    "gsm8k_accuracy": [],
                    "runtime_minutes": [],
                }

            for date_str in reversed(dates):
                try:
                    task_results = self.collect_task_results(date_str)
                    sanity_results = self.parse_sanity_check_log(date_str)
                    stats = self.calculate_summary_stats(task_results, sanity_results)

                    date_obj = datetime.strptime(date_str, "%Y%m%d")
                    display_date = date_obj.strftime("%Y-%m-%d")

                    trends["dates"].append(display_date)
                    trends["overall_status"].append(stats["overall_status"])
                    trends["passed_tasks"].append(stats["passed_tasks"])
                    trends["failed_tasks"].append(stats["failed_tasks"])
                    trends["total_tasks"].append(stats["total_tasks"])

                    if stats["tasks_run"] > 0:
                        pass_rate = (stats["passed_tasks"] / stats["tasks_run"]) * 100
                    else:
                        pass_rate = 0
                    trends["pass_rate"].append(round(pass_rate, 1))

                    # Track individual benchmark trends
                    for task in benchmark_tasks:
                        if task in task_results:
                            result = task_results[task]
                            trends["benchmarks"][task]["status"].append(
                                result["status"]
                            )

                            if result.get("gsm8k_accuracy") is not None:
                                accuracy_pct = result["gsm8k_accuracy"] * 100
                                trends["benchmarks"][task]["gsm8k_accuracy"].append(
                                    round(accuracy_pct, 1)
                                )
                            else:
                                trends["benchmarks"][task]["gsm8k_accuracy"].append(
                                    None
                                )

                            if result.get("runtime"):
                                runtime_str = result["runtime"]
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
                                trends["benchmarks"][task]["runtime_minutes"].append(
                                    None
                                )
                        else:
                            trends["benchmarks"][task]["status"].append("unknown")
                            trends["benchmarks"][task]["gsm8k_accuracy"].append(None)
                            trends["benchmarks"][task]["runtime_minutes"].append(None)

                except Exception as e:
                    print(f"Warning: Could not process data for {date_str}: {e}")
                    continue

            return trends

        return {}

    def get_available_plots(self, date_str: str) -> Dict:
        """Get list of available plots for a specific date"""
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

                # Use proxy endpoint for plots (handles authentication server-side)
                # This keeps the GitHub token secure and not exposed to clients
                plot_url = f"https://github.com/{self.github_repo}/blob/log/plot/{self.hardware}/{model_dir}/online/{plot_filename}"
                raw_url = (
                    f"/github-plots/{self.hardware}/{model_dir}/online/{plot_filename}"
                )

                plots[benchmark_name].append(
                    {
                        "suffix": suffix,
                        "url": plot_url,
                        "raw_url": raw_url,
                        "from_github": True,
                    }
                )

        return plots

    def get_dates_with_plots(self, max_days: int = 90) -> List[str]:
        """
        Get list of dates that have plots available

        Args:
            max_days: Maximum number of days to check

        Returns:
            List of date strings in YYYYMMDD format with available plots
        """
        dates_with_plots = []

        # Get all available dates
        all_dates = self.get_available_dates(max_days)

        # For each date, check if at least one plot exists
        for date_str in all_dates:
            plots = self.get_available_plots(date_str)

            # Check if any benchmark has plots
            has_plots = False
            for benchmark_plots in plots.values():
                if benchmark_plots:
                    has_plots = True
                    break

            if has_plots:
                dates_with_plots.append(date_str)

        return dates_with_plots
