#!/usr/bin/env python3
"""
Send SGLang CI Comparison Report to Microsoft Teams

This script runs compare_suites.py to analyze NVIDIA vs AMD test coverage
and sends a comprehensive comparison report to Microsoft Teams (optional).
Alert messages are always saved to team_alert/alert_log directory.

USAGE:
    python send_compare_suites_alert.py --teams-webhook-url "https://teams.webhook.url"
    python send_compare_suites_alert.py  # Save to log only, no Teams alert
    python send_compare_suites_alert.py --test-mode

ENVIRONMENT VARIABLES:
    TEAMS_WEBHOOK_URL: Teams webhook URL (optional - if not provided, only logs are saved)
    SGL_BENCHMARK_CI_DIR: Base directory for CI logs - default: /mnt/raid/michael/sglang-ci

REQUIREMENTS:
    - requests library
    - pytz library (optional, for timezone handling)
"""

import argparse
import csv
import json
import os
import socket
import subprocess
import sys
from datetime import datetime
from io import StringIO
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import requests

try:
    import pytz

    PYTZ_AVAILABLE = True
except ImportError:
    PYTZ_AVAILABLE = False
    print("⚠️  Warning: pytz not available, using UTC time instead of Pacific time")


class CompareSuitesReporter:
    """Generate and send compare_suites.py reports to Teams"""

    def __init__(
        self,
        webhook_url: Optional[str] = None,
        base_dir: str = "/mnt/raid/michael/sglang-ci",
    ):
        """
        Initialize compare suites reporter

        Args:
            webhook_url: Microsoft Teams webhook URL (optional)
            base_dir: Base directory for CI logs
        """
        self.webhook_url = webhook_url
        self.base_dir = base_dir
        self.alert_log_dir = os.path.join(base_dir, "team_alert", "alert_log")
        self.github_repo = os.environ.get("GITHUB_REPO", "ROCm/sglang-ci")

    def run_compare_suites(self) -> Tuple[bool, List[Dict[str, str]], str]:
        """
        Run compare_suites.py and parse the CSV output

        Returns:
            Tuple of (success, parsed_csv_data, csv_output_string)
        """
        try:
            # Path to compare_suites.py
            compare_script = os.path.join(
                self.base_dir, "upstream_ci", "compare_suites.py"
            )

            if not os.path.exists(compare_script):
                print(f"❌ Error: compare_suites.py not found at {compare_script}")
                return False, [], ""

            # Run compare_suites.py with --stdout flag to get CSV output
            print(f"🔄 Running compare_suites.py...")
            result = subprocess.run(
                [sys.executable, compare_script, "--stdout"],
                capture_output=True,
                text=True,
                timeout=60,
            )

            if result.returncode != 0:
                print(f"❌ Error running compare_suites.py:")
                print(result.stderr)
                return False, [], ""

            # Parse CSV output
            csv_output = result.stdout.strip()
            if not csv_output:
                print("❌ Error: No output from compare_suites.py")
                return False, [], ""

            # Parse CSV using csv.DictReader
            csv_reader = csv.DictReader(StringIO(csv_output))
            rows = list(csv_reader)

            if not rows:
                print("❌ Error: Empty CSV output")
                return False, [], ""

            print(f"✅ Successfully parsed {len(rows)} test categories")
            return True, rows, csv_output

        except subprocess.TimeoutExpired:
            print("❌ Error: compare_suites.py timed out (>60s)")
            return False, [], ""
        except Exception as e:
            print(f"❌ Error running compare_suites.py: {e}")
            return False, [], ""

    def create_comparison_card(self, csv_data: List[Dict[str, str]]) -> Dict:
        """
        Create adaptive card for test comparison

        Args:
            csv_data: Parsed CSV data from compare_suites.py

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

        # Extract total row
        total_row = None
        category_rows = []
        for row in csv_data:
            if row["Test Category"] == "Total":
                total_row = row
            else:
                category_rows.append(row)

        # Create card body
        body_elements = [
            {
                "type": "TextBlock",
                "size": "Large",
                "weight": "Bolder",
                "text": "SGLang CI: AMD vs NVIDIA Test Coverage",
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
        ]

        # Add summary statistics if we have a total row
        if total_row:
            total_amd = total_row["AMD # of Tests"]
            total_nvidia = total_row["Nvidia # of Tests"]
            total_coverage = total_row["AMD Coverage (%)"]

            body_elements.extend(
                [
                    {
                        "type": "TextBlock",
                        "text": "**Overall Coverage:**",
                        "weight": "Bolder",
                        "size": "Medium",
                        "spacing": "Medium",
                    },
                    {
                        "type": "TextBlock",
                        "text": f"AMD: **{total_amd}** tests | NVIDIA: **{total_nvidia}** tests | Coverage: **{total_coverage}**",
                        "wrap": True,
                        "size": "Default",
                        "spacing": "Small",
                    },
                ]
            )

        # Add detailed breakdown
        body_elements.append(
            {
                "type": "TextBlock",
                "text": "**Test Category Breakdown:**",
                "weight": "Bolder",
                "size": "Medium",
                "spacing": "Medium",
            }
        )

        # Group categories
        backend_tests = []
        other_tests = []

        for row in category_rows:
            if "unit-test-backend" in row["Test Category"]:
                backend_tests.append(row)
            else:
                other_tests.append(row)

        # Display backend tests first
        if backend_tests:
            body_elements.append(
                {
                    "type": "TextBlock",
                    "text": "**Backend Unit Tests:**",
                    "weight": "Bolder",
                    "size": "Small",
                    "spacing": "Small",
                }
            )

            for row in backend_tests:
                category = row["Test Category"]
                amd_count = row["AMD # of Tests"]
                nvidia_count = row["Nvidia # of Tests"]
                coverage = row["AMD Coverage (%)"]

                text = f"{category}: AMD **{amd_count}** / NVIDIA **{nvidia_count}** ({coverage})"

                body_elements.append(
                    {
                        "type": "TextBlock",
                        "text": text,
                        "wrap": True,
                        "size": "Small",
                        "spacing": "None",
                    }
                )

        # Display other tests
        if other_tests:
            body_elements.append(
                {
                    "type": "TextBlock",
                    "text": "**Other Test Categories:**",
                    "weight": "Bolder",
                    "size": "Small",
                    "spacing": "Small",
                }
            )

            for row in other_tests:
                category = row["Test Category"]
                amd_count = row["AMD # of Tests"]
                nvidia_count = row["Nvidia # of Tests"]
                coverage = row["AMD Coverage (%)"]

                text = f"{category}: AMD **{amd_count}** / NVIDIA **{nvidia_count}** ({coverage})"

                body_elements.append(
                    {
                        "type": "TextBlock",
                        "text": text,
                        "wrap": True,
                        "size": "Small",
                        "spacing": "None",
                    }
                )

        # Add action button to view cron logs
        # Get current date for log link
        if PYTZ_AVAILABLE:
            pacific_tz = pytz.timezone("America/Los_Angeles")
            pacific_time = datetime.now(pacific_tz)
            date_str = pacific_time.strftime("%Y%m%d")
        else:
            date_str = datetime.now().strftime("%Y%m%d")

        actions = [
            {
                "type": "Action.OpenUrl",
                "title": "View Cron Logs",
                "url": f"https://github.com/{self.github_repo}/tree/log/cron_log/mi30x/{date_str}",
            }
        ]

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

    def save_alert_log(self, card: Dict) -> bool:
        """
        Save alert message JSON to log directory

        Args:
            card: Adaptive card JSON structure

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

            log_filename = f"compare_suites_{timestamp}.json"
            log_path = os.path.join(self.alert_log_dir, log_filename)

            # Save card JSON to file
            with open(log_path, "w", encoding="utf-8") as f:
                json.dump(card, f, indent=2, ensure_ascii=False)

            print(f"💾 Alert message saved to: {log_path}")
            return True

        except Exception as e:
            print(f"❌ Error saving alert log: {e}")
            return False

    def save_ci_report_csv(self, csv_output: str) -> bool:
        """
        Save CI report CSV to upstream_ci/ci_report directory

        Args:
            csv_output: CSV output string from compare_suites.py

        Returns:
            True if successful, False otherwise
        """
        try:
            # Create ci_report directory if it doesn't exist
            ci_report_dir = os.path.join(self.base_dir, "upstream_ci", "ci_report")
            os.makedirs(ci_report_dir, exist_ok=True)

            # Use Pacific time for filename
            if PYTZ_AVAILABLE:
                pacific_tz = pytz.timezone("America/Los_Angeles")
                pacific_time = datetime.now(pacific_tz)
                date_str = pacific_time.strftime("%Y%m%d")
            else:
                date_str = datetime.now().strftime("%Y%m%d")

            # Create filename: sglang_ci_report_YYYYMMDD.csv
            csv_filename = f"sglang_ci_report_{date_str}.csv"
            csv_path = os.path.join(ci_report_dir, csv_filename)

            # Save CSV to file
            with open(csv_path, "w", encoding="utf-8") as f:
                f.write(csv_output)

            print(f"💾 CI report CSV saved to: {csv_path}")
            return True

        except Exception as e:
            print(f"❌ Error saving CI report CSV: {e}")
            return False

    def send_comparison_notification(self) -> bool:
        """
        Send comparison notification to Teams and save to log

        Returns:
            True if successful (log saved), False otherwise
        """
        try:
            # Run compare_suites.py
            success, csv_data, csv_output = self.run_compare_suites()

            if not success or not csv_data:
                print("❌ Failed to run compare_suites.py")
                return False

            # Save CSV report to ci_report directory
            csv_saved = self.save_ci_report_csv(csv_output)
            if csv_saved:
                print("✅ CI report CSV saved successfully")

            # Create card
            card = self.create_comparison_card(csv_data)

            # Always save to log file
            log_saved = self.save_alert_log(card)

            # Send to Teams only if webhook URL is provided
            if self.webhook_url:
                card_json = json.dumps(card)
                headers = {"Content-Type": "application/json"}

                response = requests.post(
                    self.webhook_url, data=card_json, headers=headers, timeout=30
                )

                if response.status_code in [200, 202]:
                    print(f"✅ Successfully sent comparison report to Teams")
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

            return log_saved and csv_saved

        except requests.exceptions.RequestException as e:
            print(f"❌ Error sending Teams notification: {e}")
            return False
        except json.JSONDecodeError as e:
            print(f"❌ JSON encoding error: {e}")
            return False
        except Exception as e:
            print(f"❌ Error generating comparison report: {e}")
            return False

    def send_test_notification(self) -> bool:
        """
        Send a test notification to Teams

        Returns:
            True if successful, False otherwise
        """
        if not self.webhook_url:
            print("❌ Error: Teams webhook URL not provided for test mode")
            print(
                "   Set TEAMS_WEBHOOK_URL environment variable or use --teams-webhook-url"
            )
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
                                    "text": "[TEST] Compare Suites Alert Test",
                                },
                                {
                                    "type": "TextBlock",
                                    "text": f"Sent at {current_time}",
                                    "isSubtle": True,
                                    "spacing": "None",
                                },
                                {
                                    "type": "TextBlock",
                                    "text": "[SUCCESS] If you see this message, your Teams webhook is working correctly for compare_suites alerts!",
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
        description="Send SGLang CI comparison report to Microsoft Teams",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    parser.add_argument(
        "--teams-webhook-url",
        type=str,
        help="Teams webhook URL (overrides TEAMS_WEBHOOK_URL env var)",
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
    reporter = CompareSuitesReporter(webhook_url, args.base_dir)

    # Handle test mode
    if args.test_mode:
        print("🧪 Test mode: Sending simple compare_suites test")
        success = reporter.send_test_notification()
        return 0 if success else 1

    print(f"📊 Generating SGLang CI comparison report")
    print(f"📁 Base directory: {args.base_dir}")

    # Send comparison notification
    success = reporter.send_comparison_notification()

    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
