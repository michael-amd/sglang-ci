#!/usr/bin/env python3
"""
Dashboard Data Collector

PRIMARY data collection module - parses individual test logs and determines status.
This is the source of truth for all CI data before it goes into the database.
"""

import glob
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional


class DashboardDataCollector:
    """PRIMARY data collector - parses all test logs and determines status"""

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

    def find_timing_summary_log(
        self, model_dir, mode_suffix: str, date_str: str
    ) -> Optional[str]:
        """
        Find timing_summary log file for benchmark tasks

        Args:
            model_dir: Model directory name(s) - can be string or list of strings
            mode_suffix: Mode suffix (e.g., "online", "online_dp_attention")
            date_str: Date string (YYYYMMDD)

        Returns:
            Path to timing_summary log file or None
        """
        model_dirs = [model_dir] if isinstance(model_dir, str) else model_dir

        for model_dir_name in model_dirs:
            online_dir = os.path.join(self.base_dir, "online", model_dir_name)

            if not os.path.exists(online_dir):
                continue

            patterns = [
                f"{online_dir}/*{date_str}*{mode_suffix}*/timing_summary_*.log",
                f"{online_dir}/*{date_str}*{mode_suffix}/timing_summary_*.log",
            ]

            matching_files = []
            for pattern in patterns:
                files = glob.glob(pattern)
                for file_path in files:
                    dir_name = os.path.basename(os.path.dirname(file_path))
                    if dir_name.endswith(f"_{mode_suffix}") or dir_name.endswith(
                        mode_suffix
                    ):
                        suffix_pos = dir_name.rfind(mode_suffix)
                        if suffix_pos != -1 and suffix_pos + len(mode_suffix) == len(
                            dir_name
                        ):
                            matching_files.append(file_path)

            if matching_files:
                matching_files.sort(key=os.path.getmtime, reverse=True)
                return matching_files[0]

        return None

    def parse_timing_summary_log(self, log_path: str) -> Dict:
        """
        Parse timing_summary log for benchmark results

        Args:
            log_path: Path to timing_summary log file

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

        try:
            with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()

            # Check if log is incomplete
            lines = content.strip().split("\n")
            if (
                len(lines) < 20
                and "GSM8K" not in content
                and "Total execution time" not in content
            ):
                result["status"] = "fail"
                result["error"] = "Test failed or did not complete"
                return result

            # Extract GSM8K accuracy
            gsm8k_match = re.search(r"GSM8K accuracy:\s*([\d.]+)", content)
            if gsm8k_match:
                result["gsm8k_accuracy"] = float(gsm8k_match.group(1))

            # Check for errors
            error_count_match = re.search(r"RuntimeError count:\s*(\d+)", content)
            if error_count_match and int(error_count_match.group(1)) > 0:
                result["status"] = "fail"
                result["error"] = f"RuntimeError count: {error_count_match.group(1)}"
            elif "Server error status: FAIL" in content:
                result["status"] = "fail"
                result["error"] = "Server errors detected"
            elif result["gsm8k_accuracy"] is not None:
                result["status"] = "pass"
            elif "Status: SKIPPED (prerequisites not met)" in content:
                result["exists"] = False
                return result
            elif "OVERALL SCRIPT SUMMARY" in content:
                result["status"] = "pass"
            else:
                has_completion_marker = (
                    "Total execution time:" in content
                    or ("End time:" in content and "Total duration:" in content)
                    or re.search(r"Server error status:\s*(PASS|FAIL)", content)
                )

                if "Script started at:" in content and not has_completion_marker:
                    result["status"] = "fail"
                    result["error"] = "Test did not complete"
                else:
                    result["status"] = "unknown"

            # Extract runtime
            runtime_match = re.search(
                r"Total execution time:\s*(\d+)\s*seconds\s*\((\d+)\s*minutes\)",
                content,
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

    def parse_cron_log_file(self, log_path: str) -> Dict:
        """
        Parse a cron log file for non-benchmark tasks

        Args:
            log_path: Path to the log file

        Returns:
            Dictionary with status info
        """
        result = {
            "exists": False,
            "status": "unknown",
            "runtime": None,
            "error": None,
        }

        if not os.path.exists(log_path):
            return result

        result["exists"] = True

        try:
            with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()

            # Check for bash errors
            if "bash:" in content and "No such file or directory" in content:
                result["status"] = "fail"
                result["error"] = "Script not found"
                return result

            # Check for Docker image unavailability (should be treated as "not run")
            docker_image_error_patterns = [
                (
                    r"ERROR:\s*Could not find and (pull|obtain) any valid.*images?",
                    "Docker image not available",
                ),
                (
                    r"No image found for today.*yesterday either",
                    "Docker image not found for today or yesterday",
                ),
                (
                    r"Primary version.*not found.*fallback.*not found",
                    "Docker image not available (tried primary and fallback)",
                ),
                (r"ERROR:.*Image.*not found", "Docker image not found"),
                (
                    r"Failed to pull.*Image might be a local build",
                    "Failed to pull Docker image",
                ),
            ]

            for pattern, error_msg in docker_image_error_patterns:
                if re.search(pattern, content, re.IGNORECASE | re.DOTALL):
                    result["status"] = "not run"
                    result["exists"] = False
                    # Extract more specific error message if available
                    error_match = re.search(r"ERROR:\s*(.+)", content)
                    if error_match:
                        error_text = error_match.group(1).strip()
                        error_text = error_text.split("\n")[0]
                        if len(error_text) > 150:
                            error_text = error_text[:150] + "..."
                        result["error"] = error_text
                    else:
                        result["error"] = error_msg
                    return result

            # Special handling for docker_image_check.log
            if "docker_image_check" in os.path.basename(log_path):
                if "Missing images:" in content:
                    missing_match = re.search(r"Missing images:\s*(\d+)", content)
                    if missing_match and int(missing_match.group(1)) > 0:
                        result["status"] = "fail"
                        result["error"] = (
                            f"{missing_match.group(1)} Docker image(s) not available"
                        )
                        return result
                if "✓ All expected images are available!" in content:
                    result["status"] = "pass"
                    return result

            # Check for common success/failure patterns
            if "Status: SKIPPED (prerequisites not met)" in content:
                result["exists"] = False
                return result
            elif "test FAILED" in content or "Result: FAILED" in content:
                result["status"] = "fail"
            elif "FAIL" in content and "[test]" in content:
                result["status"] = "fail"
            elif "Result: PASSED" in content or "Models passed:" in content:
                result["status"] = "pass"
            elif "Overall:" in content and "models passed (100" in content:
                result["status"] = "pass"
            elif (
                "OVERALL SCRIPT SUMMARY" in content
                and "Total execution time:" in content
            ):
                result["status"] = "pass"
            elif "✅ CSV generated from existing logs successfully" in content:
                result["status"] = "pass"
            elif "[test] Test completed for image:" in content:
                result["status"] = "pass"
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

            # Extract error details for failed tasks
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
        Parse sanity check timing_summary log for model accuracies

        Args:
            date_str: Date string in YYYYMMDD format

        Returns:
            Dictionary with sanity check results or None
        """
        sanity_base = os.path.join(
            self.base_dir, "test", "sanity_check_log", self.hardware
        )

        if not os.path.exists(sanity_base):
            return None

        pattern = f"*{date_str}"
        matching_dirs = glob.glob(os.path.join(sanity_base, pattern))

        if not matching_dirs:
            return None

        matching_dirs.sort(key=os.path.getmtime, reverse=True)
        log_dir = matching_dirs[0]

        timing_logs = glob.glob(
            os.path.join(log_dir, f"timing_summary_{date_str}_*.log")
        )
        if not timing_logs:
            timing_logs = glob.glob(os.path.join(log_dir, "timing_summary_*.log"))

        if not timing_logs:
            return None

        timing_logs.sort(key=os.path.getmtime, reverse=True)
        log_file = timing_logs[0]

        model_results = {}

        try:
            with open(log_file, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()

            model_sections = re.findall(
                r"===\s+(\S+)\s+on\s+(\S+)\s+===(.*?)(?====|$)", content, re.DOTALL
            )

            for model_name, _platform, section_content in model_sections:
                result_match = re.search(
                    r"Final result:\s*(PASS \[OK\]|FAIL \[X\])", section_content
                )

                status = "unknown"
                if result_match:
                    status = "pass" if "PASS" in result_match.group(1) else "fail"

                avg_accuracy = None
                avg_acc_match = re.search(
                    r"Average accuracy:\s*([\d.]+)", section_content
                )
                if avg_acc_match:
                    avg_accuracy = float(avg_acc_match.group(1))
                else:
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
            print(f"   Warning: Could not parse sanity check log {log_file}: {e}")
            return None

        if not model_results:
            return None

        return {"model_results": model_results, "log_file": log_file}

    def collect_task_results(self, date_str: str) -> Dict:
        """
        PRIMARY method: Collect results from all nightly tasks by parsing individual test logs

        Args:
            date_str: Date string in YYYYMMDD format

        Returns:
            Dictionary with all task results
        """
        results = {}

        # Check if Docker images were unavailable for this date
        log_dir = os.path.join(
            self.base_dir, "cron", "cron_log", self.hardware, date_str
        )
        docker_check_log_path = os.path.join(log_dir, "docker_image_check.log")
        docker_images_unavailable = False
        docker_error_message = "Docker image not available"

        if os.path.exists(docker_check_log_path):
            try:
                with open(
                    docker_check_log_path, "r", encoding="utf-8", errors="ignore"
                ) as f:
                    docker_check_content = f.read()
                missing_match = re.search(
                    r"Missing images:\s*(\d+)", docker_check_content
                )
                if missing_match and int(missing_match.group(1)) > 0:
                    docker_images_unavailable = True
                    docker_error_message = (
                        f"{missing_match.group(1)} Docker image(s) not available"
                    )
            except Exception:
                pass

        # Performance Benchmarks
        benchmarks = {
            "Grok 2 Online Benchmark": (
                ["GROK2"],
                "online",
                "grok2_nightly_online.log",
            ),
            "Grok Online Benchmark": (["GROK1"], "online", "grok_nightly.log"),
            "DeepSeek Online Benchmark": (
                ["DeepSeek-R1-MXFP4-Preview", "DeepSeek-V3-0324"],
                "online",
                "deepseek_nightly_online.log",
            ),
        }

        for task_name, (model_dir, mode_suffix, cron_log) in benchmarks.items():
            timing_log = self.find_timing_summary_log(model_dir, mode_suffix, date_str)
            if timing_log:
                results[task_name] = self.parse_timing_summary_log(timing_log)
            else:
                cron_log_path = os.path.join(log_dir, cron_log)
                results[task_name] = self.parse_cron_log_file(cron_log_path)
                if not results[task_name]["exists"] and docker_images_unavailable:
                    results[task_name]["status"] = "not run"
                    results[task_name]["error"] = docker_error_message

        # Integration Tests
        integration_tests = {
            "DeepSeek DP Attention Test": (
                ["DeepSeek-R1-MXFP4-Preview", "DeepSeek-V3-0324"],
                "online_dp_attention",
                "deepseek_dp_attention.log",
            ),
            "DeepSeek Torch Compile Test": (
                ["DeepSeek-R1-MXFP4-Preview", "DeepSeek-V3-0324"],
                "online_torch_compile",
                "deepseek_torch_compile.log",
            ),
            "DeepSeek DP+Torch Compile": (
                ["DeepSeek-R1-MXFP4-Preview", "DeepSeek-V3-0324"],
                "online_dp_attention_torch_compile",
                "deepseek_dp_attention_torch_compile.log",
            ),
            "DeepSeek MTP Test": (
                ["DeepSeek-R1-MXFP4-Preview", "DeepSeek-V3-0324"],
                "online_mtp",
                "deepseek_r1_mxfp4_mtp.log",
            ),
            "DeepSeek DP+MTP Test": (
                ["DeepSeek-R1-MXFP4-Preview", "DeepSeek-V3-0324"],
                "online_dp_mtp",
                "deepseek_r1_mxfp4_dp_mtp.log",
            ),
        }

        for task_name, (model_dir, mode_suffix, cron_log) in integration_tests.items():
            timing_log = self.find_timing_summary_log(model_dir, mode_suffix, date_str)
            if timing_log:
                results[task_name] = self.parse_timing_summary_log(timing_log)
            else:
                cron_log_path = os.path.join(log_dir, cron_log)
                results[task_name] = self.parse_cron_log_file(cron_log_path)
                if not results[task_name]["exists"] and docker_images_unavailable:
                    results[task_name]["status"] = "not run"
                    results[task_name]["error"] = docker_error_message

        # Validation & Checks
        validation_tasks = {
            "Unit Tests": "test_nightly.log",
            "PD Disaggregation Tests": "test_nightly_pd.log",
            "Sanity Check": "sanity_check_nightly.log",
            "Docker Image Check": "docker_image_check.log",
        }

        for task_name, log_file in validation_tasks.items():
            log_path = os.path.join(log_dir, log_file)
            results[task_name] = self.parse_cron_log_file(log_path)

        return results

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

    def get_test_history(self, days: int = 30) -> Dict:
        """
        Get individual test pass/fail history for all tests

        Args:
            days: Number of days to include

        Returns:
            Dictionary with per-test history
        """
        dates = self.get_available_dates(max_days=days)

        # Dictionary to store test history
        # Format: {test_name: {"dates": [...], "status": [...], "details": [...]}}
        test_history = {}

        # All possible tests
        all_tests = [
            "Grok Online Benchmark",
            "Grok 2 Online Benchmark",
            "DeepSeek Online Benchmark",
            "DeepSeek DP Attention Test",
            "DeepSeek Torch Compile Test",
            "DeepSeek DP+Torch Compile",
            "Unit Tests",
            "PD Disaggregation Tests",
            "Docker Image Check",
        ]

        # Also include sanity check models as individual tests
        sanity_models = []

        for date_str in reversed(dates):  # Process oldest to newest
            try:
                task_results = self.collect_task_results(date_str)
                sanity_results = self.parse_sanity_check_log(date_str)

                # Format date for display
                date_obj = datetime.strptime(date_str, "%Y%m%d")
                display_date = date_obj.strftime("%Y-%m-%d")

                # Process regular tasks
                for test_name in all_tests:
                    if test_name not in test_history:
                        test_history[test_name] = {
                            "dates": [],
                            "status": [],
                            "details": [],
                        }

                    if test_name in task_results:
                        result = task_results[test_name]
                        test_history[test_name]["dates"].append(display_date)
                        test_history[test_name]["status"].append(result["status"])

                        # Add details (runtime, error, accuracy)
                        details = {}
                        if result.get("runtime"):
                            details["runtime"] = result["runtime"]
                        if result.get("gsm8k_accuracy") is not None:
                            details["gsm8k_accuracy"] = round(
                                result["gsm8k_accuracy"] * 100, 1
                            )
                        if result.get("error"):
                            details["error"] = result["error"]
                        test_history[test_name]["details"].append(details)
                    else:
                        # Test didn't run this date
                        test_history[test_name]["dates"].append(display_date)
                        test_history[test_name]["status"].append("not_run")
                        test_history[test_name]["details"].append({})

                # Process sanity check models
                if sanity_results:
                    model_results = sanity_results["model_results"]
                    for model_name, model_result in model_results.items():
                        test_name = f"Sanity: {model_name}"

                        # Track unique sanity models
                        if model_name not in sanity_models:
                            sanity_models.append(model_name)

                        if test_name not in test_history:
                            test_history[test_name] = {
                                "dates": [],
                                "status": [],
                                "details": [],
                            }

                        test_history[test_name]["dates"].append(display_date)
                        test_history[test_name]["status"].append(model_result["status"])

                        details = {}
                        if model_result.get("accuracy") is not None:
                            details["accuracy"] = round(model_result["accuracy"], 2)
                        test_history[test_name]["details"].append(details)

                    # Add empty entries for sanity models that didn't run
                    for model_name in sanity_models:
                        test_name = f"Sanity: {model_name}"
                        if test_name in test_history:
                            # Check if we already added data for this date
                            if (
                                not test_history[test_name]["dates"]
                                or test_history[test_name]["dates"][-1] != display_date
                            ):
                                test_history[test_name]["dates"].append(display_date)
                                test_history[test_name]["status"].append("not_run")
                                test_history[test_name]["details"].append({})

            except Exception as e:
                print(f"Warning: Could not process test history for {date_str}: {e}")
                continue

        return test_history
