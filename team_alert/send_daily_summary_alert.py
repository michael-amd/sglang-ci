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
    DASHBOARD_URL: Base URL for the CI dashboard (e.g., http://10.194.129.138:5000)

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
        self.dashboard_url = os.environ.get(
            "DASHBOARD_URL", "http://10.194.129.138:5000"
        )

        # Initialize data collector for fallback parsing (when database unavailable)
        sys.path.insert(0, str(Path(__file__).parent.parent))
        from dashboard.data_collector import DashboardDataCollector

        self._fallback_collector = DashboardDataCollector(
            hardware=hardware,
            base_dir=base_dir,
        )

    def find_timing_summary_log(
        self, model_dir, mode_suffix: str, date_str: str
    ) -> Optional[str]:
        """Delegate to fallback collector"""
        return self._fallback_collector.find_timing_summary_log(
            model_dir, mode_suffix, date_str
        )

    def parse_timing_summary_log(self, log_path: str) -> Dict:
        """Delegate to fallback collector"""
        return self._fallback_collector.parse_timing_summary_log(log_path)

    def parse_cron_log_file(self, log_path: str) -> Dict:
        """Delegate to fallback collector"""
        return self._fallback_collector.parse_cron_log_file(log_path)

    def parse_sanity_check_log(self, date_str: str) -> Optional[Dict]:
        """Delegate to fallback collector"""
        return self._fallback_collector.parse_sanity_check_log(date_str)

    def extract_docker_image(self, date_str: str) -> Optional[str]:
        """
        Extract the Docker image used for tests/benchmarks

        Args:
            date_str: Date string in YYYYMMDD format

        Returns:
            Docker image name or None
        """
        # Try to extract from various log files
        log_dir = os.path.join(
            self.base_dir, "cron", "cron_log", self.hardware, date_str
        )

        # Priority order: unit test logs (most likely to have been run)
        log_files = [
            "test_nightly.log",
            "test_nightly_pd.log",
            "sanity_check_nightly.log",
            "grok_nightly.log",
            "grok2_nightly_online.log",
            "deepseek_nightly_online.log",
        ]

        for log_file in log_files:
            log_path = os.path.join(log_dir, log_file)
            if not os.path.exists(log_path):
                continue

            try:
                with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read()

                # Look for Docker image patterns
                # Pattern 1: [test] Selected image to run tests on: rocm/sgl-dev:...
                match = re.search(
                    r"\[test\] Selected image to run tests on:\s*(\S+)", content
                )
                if match:
                    return match.group(1)

                # Pattern 2: Using Docker image: rocm/sgl-dev:...
                match = re.search(r"Using Docker image:\s*(\S+)", content)
                if match:
                    return match.group(1)

                # Pattern 3: [test] Starting tests for image: rocm/sgl-dev:...
                match = re.search(
                    r"\[test\] Starting tests for image:\s*(\S+)", content
                )
                if match:
                    return match.group(1)

            except Exception:
                continue

        return None

    def collect_task_results(self, date_str: str) -> Dict:
        """
        Collect results from all nightly tasks (FALLBACK - delegates to DashboardDataCollector)

        Args:
            date_str: Date string in YYYYMMDD format

        Returns:
            Dictionary with all task results
        """
        # Delegate to DashboardDataCollector which has the PRIMARY parsing logic
        return self._fallback_collector.collect_task_results(date_str)

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

        # Extract Docker image used for runs
        docker_image = self.extract_docker_image(date_str)

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
        ]

        # Add Docker Image information if available
        if docker_image:
            body_elements.append(
                {
                    "type": "TextBlock",
                    "size": "Small",
                    "text": f"Docker Image: {docker_image}",
                    "isSubtle": True,
                    "spacing": "None",
                }
            )

        # Add Overall Status section
        body_elements.extend(
            [
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
        )

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
        ]

        # Only show DP+Torch Compile if both DP and Torch tests passed/exist
        dp_result = task_results.get("DeepSeek DP Attention Test", {})
        torch_result = task_results.get("DeepSeek Torch Compile Test", {})
        show_dp_torch_combo = (
            dp_result.get("exists")
            and dp_result.get("status") != "fail"
            and torch_result.get("exists")
            and torch_result.get("status") != "fail"
        )
        if show_dp_torch_combo:
            tests.append("DeepSeek DP+Torch Compile")

        # MTP tests only run on mi35x hardware
        if self.hardware != "mi30x":
            tests.append("DeepSeek MTP Test")

            # Only show DP+MTP if both DP and MTP tests passed/exist
            mtp_result = task_results.get("DeepSeek MTP Test", {})
            dp_attention_result = task_results.get("DeepSeek DP Attention Test", {})
            show_dp_mtp_combo = (
                mtp_result.get("exists")
                and mtp_result.get("status") != "fail"
                and dp_attention_result.get("exists")
                and dp_attention_result.get("status") != "fail"
            )
            if show_dp_mtp_combo:
                tests.append("DeepSeek DP+MTP Test")

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

                # Add plot link for Performance Benchmarks on the same line (only if benchmark ran)
                if category == "Performance Benchmarks" and result["exists"]:
                    # Map benchmark names to model directories and plot suffixes (hardware-specific for DeepSeek)
                    if self.hardware == "mi35x":
                        benchmark_model_map = {
                            "Grok Online Benchmark": ("GROK1", "standard"),
                            "Grok 2 Online Benchmark": ("GROK2", "standard"),
                            "DeepSeek Online Benchmark": (
                                "DeepSeek-R1-MXFP4-Preview",
                                "all",
                            ),
                        }
                    else:  # mi30x and other hardware
                        benchmark_model_map = {
                            "Grok Online Benchmark": ("GROK1", "standard"),
                            "Grok 2 Online Benchmark": ("GROK2", "standard"),
                            "DeepSeek Online Benchmark": (
                                "DeepSeek-V3-0324",
                                "standard",
                            ),
                        }

                    if task_name in benchmark_model_map:
                        model_dir, plot_suffix = benchmark_model_map[task_name]
                        plot_url = f"https://github.com/{self.github_repo}/blob/log/plot/{self.hardware}/{model_dir}/online/{date_str}_{model_dir}_online_{plot_suffix}.png"
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

        # Add CI Dashboard link
        if self.dashboard_url:
            dashboard_url = f"{self.dashboard_url}/hardware/{self.hardware}"
            actions.append(
                {
                    "type": "Action.OpenUrl",
                    "title": "📊 CI Dashboard",
                    "url": dashboard_url,
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

    def _print_summary_to_log(self, date_str: str, task_results: Dict) -> None:
        """
        Print detailed summary to console/log file

        Args:
            date_str: Date string in YYYYMMDD format
            task_results: Dictionary of task results
        """
        # Format the report date
        try:
            report_date_obj = datetime.strptime(date_str, "%Y%m%d")
            report_date = report_date_obj.strftime("%Y-%m-%d")
        except ValueError:
            report_date = date_str

        # Get current time
        if PYTZ_AVAILABLE:
            pacific_tz = pytz.timezone("America/Los_Angeles")
            pacific_time = datetime.now(pacific_tz)
            current_date = pacific_time.strftime("%Y-%m-%d")
            tz_name = "PDT" if pacific_time.dst() else "PST"
            current_time = pacific_time.strftime(f"%H:%M:%S {tz_name}")
        else:
            current_date = datetime.now().strftime("%Y-%m-%d")
            current_time = datetime.now().strftime("%H:%M:%S UTC")

        # Parse sanity results for counts
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

        # Count task statuses
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

        # Extract Docker image
        docker_image = self.extract_docker_image(date_str)

        # Print header
        print("\n" + "=" * 80)
        print(f"Alert sent: {report_date} Daily CI Summary Report")
        print("=" * 80)
        print(f"\nGenerated on {current_date} at {current_time}")
        print(f"Hostname: {socket.gethostname()}")
        print(f"Hardware: {self.hardware}")
        if docker_image:
            print(f"Docker Image: {docker_image}")

        # Print overall status
        print("\nOverall Status:")
        print(f"• Tasks run: {total_tasks - not_run}/{total_tasks}")
        print(
            f"• Passed: {passed_tasks}, Failed: {failed_tasks}, Unknown: {unknown_tasks}"
        )

        # Print task results by category
        print("\nTask Results:")

        # Define task groups
        validation = [
            "Unit Tests",
            "PD Disaggregation Tests",
            "Docker Image Check",
        ]
        benchmarks = [
            "Grok Online Benchmark",
            "Grok 2 Online Benchmark",
            "DeepSeek Online Benchmark",
        ]
        tests = [
            "DeepSeek DP Attention Test",
            "DeepSeek Torch Compile Test",
        ]

        # Only show DP+Torch Compile if both DP and Torch tests passed/exist
        dp_result = task_results.get("DeepSeek DP Attention Test", {})
        torch_result = task_results.get("DeepSeek Torch Compile Test", {})
        show_dp_torch_combo = (
            dp_result.get("exists")
            and dp_result.get("status") != "fail"
            and torch_result.get("exists")
            and torch_result.get("status") != "fail"
        )
        if show_dp_torch_combo:
            tests.append("DeepSeek DP+Torch Compile")

        # MTP tests only run on mi35x hardware
        if self.hardware != "mi30x":
            tests.append("DeepSeek MTP Test")

            # Only show DP+MTP if both DP and MTP tests passed/exist
            mtp_result = task_results.get("DeepSeek MTP Test", {})
            dp_attention_result = task_results.get("DeepSeek DP Attention Test", {})
            show_dp_mtp_combo = (
                mtp_result.get("exists")
                and mtp_result.get("status") != "fail"
                and dp_attention_result.get("exists")
                and dp_attention_result.get("status") != "fail"
            )
            if show_dp_mtp_combo:
                tests.append("DeepSeek DP+MTP Test")

        # Print Validation & Checks first
        for category, task_list in [
            ("Validation & Checks", validation),
            ("Performance Benchmarks", benchmarks),
            ("Integration Tests", tests),
        ]:
            print(f"\n{category}:")

            for task_name in task_list:
                if task_name not in task_results:
                    continue

                result = task_results[task_name]

                if not result["exists"]:
                    task_icon = "⏭️"
                    task_status_text = "Not run"
                elif result["status"] == "pass":
                    task_icon = "✅"
                    task_status_text = "Pass"
                elif result["status"] == "fail":
                    task_icon = "❌"
                    task_status_text = "Failed"
                else:
                    task_icon = "❓"
                    task_status_text = "Unknown"

                # Build task line
                task_line = f"{task_icon} {task_name}: {task_status_text}"

                # Add GSM8K accuracy for benchmarks
                if result.get("gsm8k_accuracy") is not None:
                    accuracy_pct = result["gsm8k_accuracy"] * 100
                    task_line += f" (GSM8K: {accuracy_pct:.1f}%)"

                if result.get("runtime"):
                    task_line += f" [{result['runtime']}]"

                print(f"  {task_line}")

                # Print error details for failed tasks
                if result["status"] == "fail" and result.get("error"):
                    print(f"    Error: {result['error']}")

        # Print Sanity Check results
        if sanity_results:
            model_results = sanity_results["model_results"]
            models_passed = sum(
                1 for r in model_results.values() if r["status"] == "pass"
            )
            sanity_accuracy_passed = models_passed > 1

            sanity_icon = "✅" if sanity_accuracy_passed else "❌"
            sanity_status = "Pass" if sanity_accuracy_passed else "Failed"

            print(f"\nSanity Check (Accuracy): {sanity_icon} {sanity_status}")

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

                if threshold is not None:
                    threshold_percent = threshold * 100
                    task_line = f"{task_icon} {display_name} - GSM8K: {accuracy_percent:.1f}% (threshold ≥ {threshold_percent:.1f}%)"
                else:
                    task_line = (
                        f"{task_icon} {display_name} - GSM8K: {accuracy_percent:.1f}%"
                    )

                print(f"  {task_line}")

        print("\n" + "=" * 80 + "\n")

    def should_send_alert(self, date_str: str, docker_image: Optional[str]) -> bool:
        """
        Check if alert should be sent based on Docker image date

        Only send alerts if today's Docker image is being used.
        Skip alerts if using yesterday's image (fallback scenario).

        Args:
            date_str: Expected date string in YYYYMMDD format
            docker_image: Docker image name (e.g., rocm/sgl-dev:v0.5.5.post2-rocm700-mi30x-20251114)

        Returns:
            True if alert should be sent, False otherwise
        """
        if not docker_image:
            # No docker image found, allow alert (could be an error condition we want to report)
            print(
                "⚠️  No Docker image found - skipping alert to avoid reporting on fallback runs"
            )
            return False

        # Extract date from Docker image tag
        # Format: rocm/sgl-dev:v0.5.5.post2-rocm700-mi30x-YYYYMMDD
        date_match = re.search(r"-(\d{8})(?:$|[^0-9])", docker_image)
        if not date_match:
            print(f"⚠️  Could not extract date from Docker image: {docker_image}")
            print("   Skipping alert to avoid reporting on fallback runs")
            return False

        image_date = date_match.group(1)

        if image_date != date_str:
            print(
                f"🔔 Alert suppressed: Docker image is from {image_date}, expected {date_str}"
            )
            print(f"   Using yesterday's image as fallback - not sending alert")
            return False

        print(
            f"✅ Docker image date matches expected date ({date_str}) - proceeding with alert"
        )
        return True

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

            # Extract Docker image to check if we should send alert
            docker_image = self.extract_docker_image(date_str)

            # Check if we should send alert (only for today's image)
            if not self.should_send_alert(date_str, docker_image):
                print("ℹ️  Alert sending skipped - not using today's Docker image")
                return False

            # Create card
            card = self.create_summary_card(date_str, task_results)

            # Print detailed summary to log
            self._print_summary_to_log(date_str, task_results)

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

    parser.add_argument(
        "--use-database",
        action="store_true",
        default=os.environ.get("USE_DATABASE", "").lower() in ["true", "1", "yes"],
        help="Use database for data collection (faster, more reliable)",
    )

    args = parser.parse_args()

    # Get webhook URL (optional)
    webhook_url = args.teams_webhook_url or os.environ.get("TEAMS_WEBHOOK_URL")

    # Create reporter (use database-aware version if requested)
    if args.use_database:
        try:
            # Import database-aware collector
            sys.path.insert(0, args.base_dir)
            from team_alert.db_alert_data_collector import DatabaseAlertDataCollector

            reporter = DatabaseAlertDataCollector(
                webhook_url=webhook_url,
                hardware=args.hardware,
                base_dir=args.base_dir,
                use_database=True,
            )
            print("📊 Using database for data collection")
        except Exception as e:
            print(f"⚠️  Could not use database: {e}")
            print("   Falling back to filesystem parsing")
            reporter = DailySummaryReporter(webhook_url, args.hardware, args.base_dir)
    else:
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
