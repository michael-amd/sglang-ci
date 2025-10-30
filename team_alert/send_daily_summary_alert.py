#!/usr/bin/env python3
"""
Send Daily Summary Report to Microsoft Teams

This script aggregates results from all nightly tasks defined in cron/crontab_rules.txt
and sends a comprehensive summary report to Microsoft Teams (optional).
Alert messages are always saved to team_alert/alert_log directory.

USAGE:
    python send_daily_summary_alert.py --teams-webhook-url "https://teams.webhook.url"
    python send_daily_summary_alert.py --teams-webhook-url "https://teams.webhook.url" --date 20251021
    python send_daily_summary_alert.py --date 20251021  # Save to log only, no Teams alert
    python send_daily_summary_alert.py --test-mode

ENVIRONMENT VARIABLES:
    TEAMS_WEBHOOK_URL: Teams webhook URL (optional - if not provided, only logs are saved)
    HARDWARE_TYPE: Hardware type (mi30x, mi35x) - default: mi30x
    SGL_BENCHMARK_CI_DIR: Base directory for CI logs - default: /mnt/raid/michael/sglang-ci

REQUIREMENTS:
    - requests library
    - pytz library (optional, for timezone handling)
"""

import argparse
import glob
import json
import os
import re
import socket
import sys
from datetime import datetime
from importlib import util as _importlib_util
from pathlib import Path
from typing import Dict, Optional

import requests

try:
    import pytz

    PYTZ_AVAILABLE = True
except ImportError:
    PYTZ_AVAILABLE = False
    print("⚠️  Warning: pytz not available, using UTC time instead of Pacific time")


def _load_model_criteria() -> dict:
    """Load accuracy thresholds from sanity_check.py"""
    repo_root = Path(__file__).resolve().parent.parent
    sanity_path = repo_root / "test" / "sanity_check.py"

    if not sanity_path.exists():
        return {}

    spec = _importlib_util.spec_from_file_location("_sg_sanity_check", sanity_path)
    if spec is None or spec.loader is None:
        return {}

    module = _importlib_util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception:
        return {}

    DEFAULT_MODELS = getattr(module, "DEFAULT_MODELS", {})
    return {
        name: cfg.get("criteria", {}).get("accuracy")
        for name, cfg in DEFAULT_MODELS.items()
        if isinstance(cfg, dict)
    }


_MODEL_CRITERIA = _load_model_criteria()


class DailySummaryReporter:
    """Generate and send daily summary reports aggregating all nightly tasks"""

    def __init__(
        self,
        webhook_url: Optional[str] = None,
        hardware: str = "mi30x",
        base_dir: str = "/mnt/raid/michael/sglang-ci",
    ):
        """
        Initialize daily summary reporter

        Args:
            webhook_url: Microsoft Teams webhook URL (optional)
            hardware: Hardware type (mi30x, mi35x)
            base_dir: Base directory for CI logs
        """
        self.webhook_url = webhook_url
        self.hardware = hardware
        self.base_dir = base_dir
        self.github_repo = os.environ.get("GITHUB_REPO", "ROCm/sglang-ci")
        self.alert_log_dir = os.path.join(base_dir, "team_alert", "alert_log")

    def find_timing_summary_log(
        self, model_dir: str, mode_suffix: str, date_str: str
    ) -> Optional[str]:
        """
        Find timing_summary log file for benchmark tasks

        Args:
            model_dir: Model directory (e.g., "GROK2", "DeepSeek-V3-0324")
            mode_suffix: Mode suffix (e.g., "online", "online_dp_attention")
            date_str: Date string (YYYYMMDD)

        Returns:
            Path to timing_summary log file or None
        """
        online_dir = os.path.join(self.base_dir, "online", model_dir)

        if not os.path.exists(online_dir):
            return None

        # Look for directories matching the date and mode
        # Note: timing_summary logs may have different dates in filename (overnight runs)
        # so we look for any timing_summary*.log in directories matching the date
        patterns = [
            f"{online_dir}/*{date_str}*{mode_suffix}*/timing_summary_*.log",
            f"{online_dir}/*{date_str}*{mode_suffix}/timing_summary_*.log",
        ]

        matching_files = []
        for pattern in patterns:
            files = glob.glob(pattern)
            for file_path in files:
                # Extract directory name from file path
                dir_name = os.path.basename(os.path.dirname(file_path))
                # Only include files where directory name ends with the exact mode suffix
                # This prevents "online" from matching "online_dp_attention" etc.
                if dir_name.endswith(f"_{mode_suffix}") or dir_name.endswith(
                    mode_suffix
                ):
                    # Additional check: ensure it's not a longer mode string
                    # e.g., "online" shouldn't match if dir ends with "online_torch_compile"
                    suffix_pos = dir_name.rfind(mode_suffix)
                    if suffix_pos != -1 and suffix_pos + len(mode_suffix) == len(
                        dir_name
                    ):
                        matching_files.append(file_path)

        if matching_files:
            # Return most recent
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

            # Check if log is incomplete (truncated during run)
            # Incomplete logs won't have GSM8K results or final status
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
                # If we got accuracy, benchmark completed successfully
                result["status"] = "pass"
            else:
                # Check if it's an incomplete run
                # A run is considered complete if it has ANY of these markers:
                # - "Total execution time:" (old/ideal marker)
                # - "End time:" + "Total duration:" (client benchmark completion)
                # - "Server error status: PASS/FAIL" (indicates full run completed)
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

            # Check for common success/failure patterns
            if "Result: PASSED" in content or "Models passed:" in content:
                result["status"] = "pass"
            elif "Overall:" in content and "models passed (100" in content:
                # Sanity check success pattern
                result["status"] = "pass"
            elif "Result: FAILED" in content or "FAIL" in content:
                result["status"] = "fail"
            elif "Total execution time:" in content or "✅" in content:
                result["status"] = "pass"
            else:
                # If log exists but no clear status
                result["status"] = "unknown"

            # Extract runtime if available
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
        # Find the sanity check log directory
        sanity_base = os.path.join(
            self.base_dir, "test", "sanity_check_log", self.hardware
        )

        if not os.path.exists(sanity_base):
            return None

        # Look for directories matching the date
        pattern = f"*{date_str}"
        matching_dirs = glob.glob(os.path.join(sanity_base, pattern))

        if not matching_dirs:
            return None

        # Get the most recent directory
        matching_dirs.sort(key=os.path.getmtime, reverse=True)
        log_dir = matching_dirs[0]

        # Find timing_summary log
        timing_logs = glob.glob(
            os.path.join(log_dir, f"timing_summary_{date_str}_*.log")
        )
        if not timing_logs:
            timing_logs = glob.glob(os.path.join(log_dir, "timing_summary_*.log"))

        if not timing_logs:
            return None

        timing_logs.sort(key=os.path.getmtime, reverse=True)
        log_file = timing_logs[0]

        # Parse the timing_summary log
        model_results = {}

        try:
            with open(log_file, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()

            # Extract model sections
            model_sections = re.findall(
                r"===\s+(\S+)\s+on\s+(\S+)\s+===(.*?)(?====|$)", content, re.DOTALL
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
            print(f"   Warning: Could not parse sanity check log {log_file}: {e}")
            return None

        if not model_results:
            return None

        return {"model_results": model_results, "log_file": log_file}

    def collect_task_results(self, date_str: str) -> Dict:
        """
        Collect results from all nightly tasks for a given date

        Args:
            date_str: Date string in YYYYMMDD format

        Returns:
            Dictionary with all task results
        """
        results = {}

        # Performance Benchmarks - use timing_summary logs
        benchmarks = {
            "Grok 2 Online Benchmark": ("GROK2", "online"),
            "Grok Online Benchmark": ("GROK1", "online"),
            "DeepSeek Online Benchmark": ("DeepSeek-V3-0324", "online"),
        }

        for task_name, (model_dir, mode_suffix) in benchmarks.items():
            timing_log = self.find_timing_summary_log(model_dir, mode_suffix, date_str)
            if timing_log:
                results[task_name] = self.parse_timing_summary_log(timing_log)
            else:
                results[task_name] = {
                    "exists": False,
                    "status": "unknown",
                    "runtime": None,
                    "error": None,
                }

        # Integration Tests - use timing_summary logs with mode suffixes
        integration_tests = {
            "DeepSeek DP Attention Test": ("DeepSeek-V3-0324", "online_dp_attention"),
            "DeepSeek Torch Compile Test": ("DeepSeek-V3-0324", "online_torch_compile"),
            "DeepSeek DP+Torch Compile": (
                "DeepSeek-V3-0324",
                "online_dp_attention_torch_compile",
            ),
        }

        for task_name, (model_dir, mode_suffix) in integration_tests.items():
            timing_log = self.find_timing_summary_log(model_dir, mode_suffix, date_str)
            if timing_log:
                results[task_name] = self.parse_timing_summary_log(timing_log)
            else:
                results[task_name] = {
                    "exists": False,
                    "status": "unknown",
                    "runtime": None,
                    "error": None,
                }

        # Validation & Checks - use cron logs
        log_dir = os.path.join(
            self.base_dir, "cron", "cron_log", self.hardware, date_str
        )
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

    def create_summary_card(self, date_str: str, task_results: Dict) -> Dict:
        """
        Create adaptive card for daily summary

        Args:
            date_str: Date string in YYYYMMDD format
            task_results: Dictionary of task results

        Returns:
            Adaptive card JSON structure
        """
        # Use San Francisco time (Pacific Time) if pytz is available
        if PYTZ_AVAILABLE:
            pacific_tz = pytz.timezone("America/Los_Angeles")
            pacific_time = datetime.now(pacific_tz)
            current_date = pacific_time.strftime("%Y-%m-%d")
            tz_name = "PDT" if pacific_time.dst() else "PST"
            current_time = pacific_time.strftime(f"%H:%M:%S {tz_name}")
        else:
            current_date = datetime.now().strftime("%Y-%m-%d")
            current_time = datetime.now().strftime("%H:%M:%S UTC")

        # Format the report date
        try:
            report_date_obj = datetime.strptime(date_str, "%Y%m%d")
            report_date = report_date_obj.strftime("%Y-%m-%d")
        except ValueError:
            report_date = date_str

        # Parse sanity results to include individual model tests in counts
        sanity_results = self.parse_sanity_check_log(date_str)
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

        # Count task statuses (excluding "Sanity Check" from task_results as it's counted per-model)
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

        # Overall status is determined by task counts
        # No longer displaying status icon/color in "Overall Status:" section

        # Create card body
        body_elements = [
            {
                "type": "TextBlock",
                "size": "Large",
                "weight": "Bolder",
                "text": f"{report_date} Daily CI Summary Report",
            },
            {
                "type": "TextBlock",
                "size": "Small",
                "text": f"Generated on {current_date} at {current_time}",
                "isSubtle": True,
                "spacing": "None",
            },
            {
                "type": "TextBlock",
                "size": "Small",
                "text": f"Hostname: {socket.gethostname()}",
                "isSubtle": True,
                "spacing": "None",
            },
            {
                "type": "TextBlock",
                "size": "Small",
                "text": f"Hardware: {self.hardware}",
                "isSubtle": True,
                "spacing": "None",
            },
            {
                "type": "TextBlock",
                "text": "**Overall Status:**",
                "weight": "Bolder",
                "size": "Medium",
                "spacing": "Medium",
            },
            {
                "type": "TextBlock",
                "text": f"• Tasks run: **{total_tasks - not_run}/{total_tasks}**",
                "wrap": True,
                "size": "Small",
                "spacing": "Small",
            },
            {
                "type": "TextBlock",
                "text": f"• Passed: **{passed_tasks}**, Failed: **{failed_tasks}**, Unknown: **{unknown_tasks}**",
                "wrap": True,
                "size": "Small",
                "spacing": "None",
            },
        ]

        # Add task details section
        body_elements.append(
            {
                "type": "TextBlock",
                "text": "**Task Results:**",
                "weight": "Bolder",
                "size": "Medium",
                "spacing": "Medium",
            }
        )

        # Group tasks by category - reordered to show Validation & Checks first
        benchmarks = [
            "Grok Online Benchmark",
            "Grok 2 Online Benchmark",
            "DeepSeek Online Benchmark",
        ]
        tests = [
            "DeepSeek DP Attention Test",
            "DeepSeek Torch Compile Test",
            "DeepSeek DP+Torch Compile",
        ]
        validation = [
            "Unit Tests",
            "PD Disaggregation Tests",
            "Docker Image Check",
        ]

        # Add tasks grouped by category - Validation & Checks first
        for category, task_list in [
            ("Validation & Checks", validation),
            ("Performance Benchmarks", benchmarks),
            ("Integration Tests", tests),
        ]:
            body_elements.append(
                {
                    "type": "TextBlock",
                    "text": f"**{category}:**",
                    "weight": "Bolder",
                    "size": "Small",
                    "spacing": "Small",
                }
            )

            for task_name in task_list:
                if task_name not in task_results:
                    continue

                result = task_results[task_name]

                if not result["exists"]:
                    task_icon = "⏭️"
                    task_status_text = "Not run"
                    task_color = "Default"
                elif result["status"] == "pass":
                    task_icon = "✅"
                    task_status_text = "Pass"
                    task_color = "Good"
                elif result["status"] == "fail":
                    task_icon = "❌"
                    task_status_text = "Failed"
                    task_color = "Attention"
                else:
                    task_icon = "❓"
                    task_status_text = "Unknown"
                    task_color = "Warning"

                # Build task line with runtime and GSM8K accuracy if available
                task_line = f"{task_icon} {task_name}: **{task_status_text}**"

                # Add GSM8K accuracy for benchmarks
                if result.get("gsm8k_accuracy") is not None:
                    accuracy_pct = result["gsm8k_accuracy"] * 100
                    task_line += f" (GSM8K: {accuracy_pct:.1f}%)"

                if result.get("runtime"):
                    task_line += f" [{result['runtime']}]"

                # Add plot link for Performance Benchmarks on the same line
                if category == "Performance Benchmarks":
                    # Map benchmark names to model directories
                    benchmark_model_map = {
                        "Grok Online Benchmark": "GROK1",
                        "Grok 2 Online Benchmark": "GROK2",
                        "DeepSeek Online Benchmark": "DeepSeek-V3-0324",
                    }

                    if task_name in benchmark_model_map:
                        model_dir = benchmark_model_map[task_name]
                        plot_url = f"https://github.com/{self.github_repo}/blob/log/plot/{self.hardware}/{model_dir}/online/{date_str}_{model_dir}_online_standard.png"
                        task_line += f" 🔗 [View Plot]({plot_url})"

                body_elements.append(
                    {
                        "type": "TextBlock",
                        "text": task_line,
                        "wrap": True,
                        "size": "Small",
                        "spacing": "None",
                        "color": task_color,
                    }
                )

                # Add error details for failed tasks
                if result["status"] == "fail" and result.get("error"):
                    body_elements.append(
                        {
                            "type": "TextBlock",
                            "text": f"  Error: {result['error']}",
                            "wrap": True,
                            "size": "Small",
                            "spacing": "None",
                            "isSubtle": True,
                            "color": "Attention",
                        }
                    )

        # Add Sanity Check (Accuracy) section
        if sanity_results:
            # Count how many models passed
            model_results = sanity_results["model_results"]
            models_passed = sum(
                1 for r in model_results.values() if r["status"] == "pass"
            )

            # Overall sanity check (accuracy) passes if more than 1 model passes
            sanity_accuracy_passed = models_passed > 1

            # Add header with overall status
            sanity_icon = "✅" if sanity_accuracy_passed else "❌"
            sanity_status = "Pass" if sanity_accuracy_passed else "Failed"

            body_elements.append(
                {
                    "type": "TextBlock",
                    "text": f"**Sanity Check (Accuracy):** {sanity_icon} **{sanity_status}**",
                    "weight": "Bolder",
                    "size": "Small",
                    "spacing": "Small",
                }
            )

            # Map short model names to full display names
            model_display_names = {
                "llama4": "Llama-4-Maverick-17B-128E-Instruct-FP8",
                "QWEN-30B": "Qwen3-30B-A3B-Thinking-2507",
                "GPT-OSS-120B": "gpt-oss-120b-bf16",
                "GPT-OSS-20B": "gpt-oss-20b-bf16",
            }

            for model_name, result in model_results.items():
                accuracy = result["accuracy"]
                accuracy_percent = accuracy * 100
                status = result["status"]

                # Get threshold from model criteria
                threshold = _MODEL_CRITERIA.get(model_name)

                # Use full model name for display if available
                display_name = model_display_names.get(model_name, model_name)

                task_icon = "✅" if status == "pass" else "❌"
                task_color = "Good" if status == "pass" else "Attention"

                if threshold is not None:
                    threshold_percent = threshold * 100
                    task_line = f"{task_icon} {display_name} - GSM8K: {accuracy_percent:.1f}% (threshold ≥ {threshold_percent:.1f}%)"
                else:
                    task_line = (
                        f"{task_icon} {display_name} - GSM8K: {accuracy_percent:.1f}%"
                    )

                body_elements.append(
                    {
                        "type": "TextBlock",
                        "text": task_line,
                        "wrap": True,
                        "size": "Small",
                        "spacing": "None",
                        "color": task_color,
                    }
                )

        # Add action buttons
        actions = []

        # Add cron log link
        cron_log_url = f"https://github.com/{self.github_repo}/tree/log/cron_log/{self.hardware}/{date_str}"
        actions.append(
            {
                "type": "Action.OpenUrl",
                "title": "📋 View All Logs",
                "url": cron_log_url,
            }
        )

        # Create the adaptive card
        card = {
            "type": "message",
            "attachments": [
                {
                    "contentType": "application/vnd.microsoft.card.adaptive",
                    "content": {
                        "type": "AdaptiveCard",
                        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                        "version": "1.4",
                        "body": body_elements,
                        "actions": actions,
                    },
                }
            ],
        }

        return card

    def save_alert_log(self, card: Dict, date_str: str) -> bool:
        """
        Save alert message JSON to log directory

        Args:
            card: Adaptive card JSON structure
            date_str: Date string in YYYYMMDD format

        Returns:
            True if successful, False otherwise
        """
        try:
            # Create alert log directory if it doesn't exist
            os.makedirs(self.alert_log_dir, exist_ok=True)

            # Create log filename with timestamp
            if PYTZ_AVAILABLE:
                pacific_tz = pytz.timezone("America/Los_Angeles")
                pacific_time = datetime.now(pacific_tz)
                timestamp = pacific_time.strftime("%Y%m%d_%H%M%S")
            else:
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

            log_filename = f"daily_summary_{date_str}_{self.hardware}_{timestamp}.json"
            log_path = os.path.join(self.alert_log_dir, log_filename)

            # Save card JSON to file
            with open(log_path, "w", encoding="utf-8") as f:
                json.dump(card, f, indent=2, ensure_ascii=False)

            print(f"💾 Alert message saved to: {log_path}")
            return True

        except Exception as e:
            print(f"❌ Error saving alert log: {e}")
            return False

    def send_summary_notification(self, date_str: str) -> bool:
        """
        Send daily summary notification to Teams and save to log

        Args:
            date_str: Date string in YYYYMMDD format

        Returns:
            True if successful (log saved), False otherwise
        """
        try:
            # Collect task results
            print(f"📊 Collecting task results for {date_str}...")
            task_results = self.collect_task_results(date_str)

            # Create card
            card = self.create_summary_card(date_str, task_results)

            # Always save to log file
            log_saved = self.save_alert_log(card, date_str)

            # Send to Teams only if webhook URL is provided
            if self.webhook_url:
                card_json = json.dumps(card)
                headers = {"Content-Type": "application/json"}

                response = requests.post(
                    self.webhook_url, data=card_json, headers=headers, timeout=30
                )

                if response.status_code in [200, 202]:
                    print(f"✅ Successfully sent daily summary report to Teams")
                    if response.status_code == 202:
                        print(
                            "   (Power Automate flow accepted - message processing asynchronously)"
                        )
                else:
                    print(
                        f"❌ Failed to send Teams notification. Status: {response.status_code}"
                    )
                    print(f"Response: {response.text}")
            else:
                print("ℹ️  No Teams webhook URL provided, skipping Teams notification")

            return log_saved

        except requests.exceptions.RequestException as e:
            print(f"❌ Error sending Teams notification: {e}")
            return False
        except json.JSONDecodeError as e:
            print(f"❌ JSON encoding error: {e}")
            return False
        except Exception as e:
            print(f"❌ Error generating summary: {e}")
            return False

    def send_test_notification(self) -> bool:
        """
        Send a test notification to Teams

        Returns:
            True if successful, False otherwise
        """
        if not self.webhook_url:
            print("❌ Error: Teams webhook URL not provided for test mode")
            print("   Set TEAMS_WEBHOOK_URL environment variable or use --webhook-url")
            return False

        try:
            if PYTZ_AVAILABLE:
                pacific_tz = pytz.timezone("America/Los_Angeles")
                pacific_time = datetime.now(pacific_tz)
                current_time = pacific_time.strftime("%H:%M:%S %Z")
            else:
                current_time = datetime.now().strftime("%H:%M:%S UTC")

            card = {
                "type": "message",
                "attachments": [
                    {
                        "contentType": "application/vnd.microsoft.card.adaptive",
                        "content": {
                            "type": "AdaptiveCard",
                            "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                            "version": "1.4",
                            "body": [
                                {
                                    "type": "TextBlock",
                                    "size": "Large",
                                    "weight": "Bolder",
                                    "text": "🧪 Daily Summary Alert Test",
                                },
                                {
                                    "type": "TextBlock",
                                    "text": f"Sent at {current_time}",
                                    "isSubtle": True,
                                    "spacing": "None",
                                },
                                {
                                    "type": "TextBlock",
                                    "text": "✅ If you see this message, your Teams webhook is working correctly for daily summary alerts!",
                                    "wrap": True,
                                    "spacing": "Medium",
                                },
                            ],
                        },
                    }
                ],
            }

            card_json = json.dumps(card)
            headers = {"Content-Type": "application/json"}

            response = requests.post(
                self.webhook_url, data=card_json, headers=headers, timeout=30
            )

            if response.status_code in [200, 202]:
                print("✅ Test message sent successfully!")
                if response.status_code == 202:
                    print(
                        "   (Power Automate flow accepted - message processing asynchronously)"
                    )
                return True
            else:
                print(f"❌ Test failed. Status: {response.status_code}")
                print(f"Response: {response.text}")
                return False

        except requests.exceptions.RequestException as e:
            print(f"❌ Error sending test notification: {e}")
            return False
        except json.JSONDecodeError as e:
            print(f"❌ JSON encoding error: {e}")
            return False


def main():
    """Main function"""
    parser = argparse.ArgumentParser(
        description="Send daily CI summary report to Microsoft Teams",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    parser.add_argument(
        "--teams-webhook-url",
        type=str,
        help="Teams webhook URL (overrides TEAMS_WEBHOOK_URL env var)",
    )

    parser.add_argument(
        "--date",
        type=str,
        help="Date to generate report for (YYYYMMDD format, default: today)",
    )

    parser.add_argument(
        "--hardware",
        type=str,
        choices=["mi30x", "mi35x"],
        default=os.environ.get("HARDWARE_TYPE", "mi30x"),
        help="Hardware type",
    )

    parser.add_argument(
        "--base-dir",
        type=str,
        default=os.environ.get("SGL_BENCHMARK_CI_DIR", "/mnt/raid/michael/sglang-ci"),
        help="Base directory for CI logs",
    )

    parser.add_argument(
        "--test-mode",
        action="store_true",
        help="Send a simple test message to verify Teams connectivity",
    )

    args = parser.parse_args()

    # Get webhook URL (optional)
    webhook_url = args.teams_webhook_url or os.environ.get("TEAMS_WEBHOOK_URL")

    # Create reporter
    reporter = DailySummaryReporter(webhook_url, args.hardware, args.base_dir)

    # Handle test mode
    if args.test_mode:
        print("🧪 Test mode: Sending simple daily summary test")
        success = reporter.send_test_notification()
        return 0 if success else 1

    # Determine date
    if args.date:
        date_str = args.date
    else:
        date_str = datetime.now().strftime("%Y%m%d")

    print(f"📅 Generating daily summary report for {date_str}")
    print(f"🖥️  Hardware: {args.hardware}")
    print(f"📁 Base directory: {args.base_dir}")

    # Send summary notification
    success = reporter.send_summary_notification(date_str)

    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
