#!/usr/bin/env python3
"""
Send Docker Image Status Notifications to Microsoft Teams

This script sends alerts about Docker image availability to Microsoft Teams channels via webhooks.
It's designed to work with the nightly_image_check.sh script to provide automated alerts
when Docker images are missing or have issues.

USAGE:
    python send_docker_image_alert.py --status success --message "All images available"
    python send_docker_image_alert.py --status warning --message "Some images missing" --details "mi30x (20250108): no image found for any available ROCM version"
    python send_docker_image_alert.py --status error --message "Multiple images missing" --details "mi30x (20250108): not found" "mi35x (20250108): not pullable"
    python send_docker_image_alert.py --test-mode

ENVIRONMENT VARIABLES:
    TEAMS_WEBHOOK_URL: Teams webhook URL (required)
    DOCKER_IMAGE_REPO: Docker repository name (default: rocm/sgl-dev)
    GITHUB_WORKFLOW_URL: GitHub workflow URL for troubleshooting

REQUIREMENTS:
    - requests library
    - pytz library (optional, for timezone handling)
"""

import argparse
import json
import os
import sys
from datetime import datetime
from typing import List, Optional

import requests

try:
    import pytz

    PYTZ_AVAILABLE = True
except ImportError:
    PYTZ_AVAILABLE = False
    print("⚠️  Warning: pytz not available, using UTC time instead of Pacific time")


class DockerImageTeamsNotifier:
    """Handle sending Docker image status notifications to Microsoft Teams"""

    def __init__(self, webhook_url: str):
        """
        Initialize Teams notifier for Docker images

        Args:
            webhook_url: Microsoft Teams webhook URL
        """
        self.webhook_url = webhook_url
        self.repo = os.environ.get("DOCKER_IMAGE_REPO", "rocm/sgl-dev")
        self.github_workflow_url = os.environ.get(
            "GITHUB_WORKFLOW_URL",
            "https://github.com/sgl-project/sglang/actions/workflows/release-docker-amd-nightly.yml",
        )

    def create_image_status_card(
        self,
        status: str,
        message: str,
        details: Optional[List[str]] = None,
        checked_count: int = 0,
        found_count: int = 0,
        date_checked: str = None,
        available_images: Optional[List[str]] = None,
    ) -> dict:
        """
        Create adaptive card for Docker image status

        Args:
            status: Status level (success, warning, error)
            message: Main status message
            details: List of detailed status messages
            checked_count: Total number of images checked
            found_count: Number of images found
            date_checked: Date that was checked (YYYYMMDD format)
            available_images: List of available images with their details

        Returns:
            Adaptive card JSON structure
        """
        # Use San Francisco time (Pacific Time) if pytz is available
        if PYTZ_AVAILABLE:
            pacific_tz = pytz.timezone("America/Los_Angeles")
            pacific_time = datetime.now(pacific_tz)
            current_date = pacific_time.strftime("%Y-%m-%d")
            # Determine if it's PST or PDT
            tz_name = "PDT" if pacific_time.dst() else "PST"
            current_time = pacific_time.strftime(f"%H:%M:%S {tz_name}")
        else:
            current_date = datetime.now().strftime("%Y-%m-%d")
            current_time = datetime.now().strftime("%H:%M:%S UTC")

        # Determine status icon and color
        status_config = {
            "success": {
                "icon": "✅",
                "color": "Good",
                "title": "All Docker Images Available",
            },
            "warning": {
                "icon": "⚠️",
                "color": "Warning",
                "title": "Some Docker Images Missing",
            },
            "error": {
                "icon": "❌",
                "color": "Attention",
                "title": "Docker Image Availability Issues",
            },
        }

        config = status_config.get(status, status_config["error"])

        # Format the checked date for display
        title_date = ""
        if date_checked:
            try:
                date_obj = datetime.strptime(date_checked, "%Y%m%d")
                title_date = date_obj.strftime("%Y-%m-%d")
            except ValueError:
                title_date = date_checked
        else:
            title_date = current_date

        # Create card body elements
        # Host where the script is executed – helpful when multiple machines run nightly checks
        try:
            import socket

            host_name = socket.gethostname()
        except Exception:
            host_name = "unknown"

        # Build the card body. Use separate TextBlocks for repo / host / timestamp so Teams renders
        # each item on its own line (newline characters are ignored in Adaptive Cards).
        body_elements = [
            {
                "type": "TextBlock",
                "size": "Large",
                "weight": "Bolder",
                "text": f"{title_date} Nightly Docker Image Check",
            },
            {
                "type": "TextBlock",
                "size": "Small",
                "text": f"Repository: {self.repo}",
                "isSubtle": True,
                "spacing": "None",
            },
            {
                "type": "TextBlock",
                "size": "Small",
                "text": f"Host: {host_name}",
                "isSubtle": True,
                "spacing": "None",
            },
            {
                "type": "TextBlock",
                "size": "Small",
                "text": f"Checked on {current_date} at {current_time}",
                "isSubtle": True,
                "spacing": "None",
            },
            {
                "type": "TextBlock",
                "text": "**Status:**",
                "weight": "Bolder",
                "size": "Medium",
                "spacing": "Medium",
            },
            {
                "type": "TextBlock",
                "size": "Medium",
                "weight": "Bolder",
                "text": f"{config['icon']} {config['title']}",
                "color": config["color"],
                "wrap": True,
                "spacing": "Small",
            },
        ]

        # Add statistics if provided
        if checked_count > 0:
            missing_count = checked_count - found_count
            body_elements.append(
                {
                    "type": "TextBlock",
                    "text": f"• Images checked: **{checked_count}**, Available: **{found_count}**, Missing: **{missing_count}**",
                    "wrap": True,
                    "size": "Small",
                    "spacing": "Small",
                }
            )

        # Add available images details for success status
        if status == "success" and available_images:
            for available_image in available_images:
                body_elements.append(
                    {
                        "type": "TextBlock",
                        "text": f"• {available_image}",
                        "wrap": True,
                        "size": "Small",
                        "spacing": "Small",
                    }
                )
        elif message and status != "success":
            # Add main message for non-success statuses
            body_elements.append(
                {
                    "type": "TextBlock",
                    "text": f"• {message}",
                    "wrap": True,
                    "size": "Small",
                    "spacing": "Small",
                }
            )

        # Add details section if we have details
        if details:
            body_elements.append(
                {
                    "type": "TextBlock",
                    "text": "**Details:**",
                    "weight": "Bolder",
                    "size": "Medium",
                    "spacing": "Medium",
                }
            )

            for detail in details:
                body_elements.append(
                    {
                        "type": "TextBlock",
                        "text": f"• {detail}",
                        "wrap": True,
                        "size": "Small",
                        "spacing": "None",
                    }
                )

        # Add troubleshooting section for warnings and errors
        if status in ["warning", "error"]:
            body_elements.extend(
                [
                    {
                        "type": "TextBlock",
                        "text": "**Troubleshooting:**",
                        "weight": "Bolder",
                        "size": "Medium",
                        "spacing": "Medium",
                    },
                    {
                        "type": "TextBlock",
                        "text": "• Missing images may indicate build failures in the nightly pipeline",
                        "wrap": True,
                        "size": "Small",
                        "spacing": "Small",
                    },
                    {
                        "type": "TextBlock",
                        "text": "• Check the GitHub workflow for recent build errors or resource issues",
                        "wrap": True,
                        "size": "Small",
                        "spacing": "None",
                    },
                ]
            )

        # Create actions
        actions = []

        # Add cron log link
        # Try to determine hardware type from multiple sources
        hardware_type = None
        try:
            import re

            # Priority 1: Extract from available_images list (hardware info)
            if available_images:
                for image_str in available_images:
                    hw_match = re.search(r"mi[0-9]+x", image_str)
                    if hw_match:
                        hardware_type = hw_match.group(0)
                        break

            # Priority 2: Extract from hostname
            if not hardware_type:
                import socket

                hostname = socket.gethostname()
                # Try full pattern first (mi30x, mi35x)
                hw_match = re.search(r"mi[0-9]+x", hostname)
                if hw_match:
                    hardware_type = hw_match.group(0)
                else:
                    # Try abbreviated patterns and convert to mi30x, mi35x
                    # Pattern 1: 300x, 350x (with 'x')
                    abbrev_match = re.search(r"([0-9]+)x", hostname)
                    if abbrev_match:
                        abbrev = abbrev_match.group(1)
                        # Map 300x -> mi30x, 350x -> mi35x, 355x -> mi35x
                        if abbrev in ["300", "30"]:
                            hardware_type = "mi30x"
                        elif abbrev in ["350", "355", "35"]:
                            hardware_type = "mi35x"
                    # Pattern 2: 300, 355 (without 'x')
                    elif not hardware_type:
                        abbrev_match = re.search(r"\b(300|355|350|30|35)\b", hostname)
                        if abbrev_match:
                            abbrev = abbrev_match.group(1)
                            # Map 300 -> mi30x, 355/350 -> mi35x
                            if abbrev in ["300", "30"]:
                                hardware_type = "mi30x"
                            elif abbrev in ["350", "355", "35"]:
                                hardware_type = "mi35x"
        except Exception:
            pass

        # Determine date for cron log link
        if date_checked:
            log_date = date_checked
        else:
            log_date = datetime.now().strftime("%Y%m%d")

        # Add cron log link if we have hardware type
        if hardware_type:
            cron_log_url = f"https://github.com/michael-amd/sglang-ci-data/tree/main/cron_log/{hardware_type}/{log_date}"
            actions.append(
                {
                    "type": "Action.OpenUrl",
                    "title": "📋 Cron Logs",
                    "url": cron_log_url,
                }
            )

        if status in ["warning", "error"]:
            actions.append(
                {
                    "type": "Action.OpenUrl",
                    "title": "🔧 View GitHub Workflow",
                    "url": self.github_workflow_url,
                }
            )

        # Add Docker Hub link
        docker_hub_url = f"https://hub.docker.com/r/{self.repo}/tags"
        actions.append(
            {
                "type": "Action.OpenUrl",
                "title": "🐳 View Docker Hub",
                "url": docker_hub_url,
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

    def create_test_card(self) -> dict:
        """
        Create a simple test card for Docker image alerts

        Returns:
            Simple test adaptive card JSON structure
        """
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
                                "text": "🧪 Docker Image Alert Test",
                            },
                            {
                                "type": "TextBlock",
                                "text": f"Sent at {current_time}",
                                "isSubtle": True,
                                "spacing": "None",
                            },
                            {
                                "type": "TextBlock",
                                "text": "✅ If you see this message, your Teams webhook is working correctly for Docker image alerts!",
                                "wrap": True,
                                "spacing": "Medium",
                            },
                        ],
                    },
                }
            ],
        }
        return card

    def send_notification(
        self,
        status: str,
        message: str,
        details: Optional[List[str]] = None,
        checked_count: int = 0,
        found_count: int = 0,
        date_checked: str = None,
        available_images: Optional[List[str]] = None,
    ) -> bool:
        """
        Send Docker image status notification to Teams

        Args:
            status: Status level (success, warning, error)
            message: Main status message
            details: List of detailed status messages
            checked_count: Total number of images checked
            found_count: Number of images found
            date_checked: Date that was checked
            available_images: List of available images with their details

        Returns:
            True if successful, False otherwise
        """
        try:
            card = self.create_image_status_card(
                status,
                message,
                details,
                checked_count,
                found_count,
                date_checked,
                available_images,
            )
            card_json = json.dumps(card)

            headers = {"Content-Type": "application/json"}

            response = requests.post(
                self.webhook_url, data=card_json, headers=headers, timeout=30
            )

            if response.status_code in [200, 202]:
                print(f"✅ Successfully sent Docker image {status} alert to Teams")
                if response.status_code == 202:
                    print(
                        "   (Power Automate flow accepted - message processing asynchronously)"
                    )
                return True
            else:
                print(
                    f"❌ Failed to send Teams notification. Status: {response.status_code}"
                )
                print(f"Response: {response.text}")
                return False

        except requests.exceptions.RequestException as e:
            print(f"❌ Error sending Teams notification: {e}")
            return False
        except json.JSONDecodeError as e:
            print(f"❌ JSON encoding error: {e}")
            return False

    def send_test_notification(self) -> bool:
        """
        Send a test notification to Teams

        Returns:
            True if successful, False otherwise
        """
        try:
            card = self.create_test_card()
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
        description="Send Docker image status notifications to Microsoft Teams",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    parser.add_argument(
        "--status",
        type=str,
        choices=["success", "warning", "error"],
        help="Status level of the notification",
    )

    parser.add_argument("--message", type=str, help="Main status message")

    parser.add_argument(
        "--details", type=str, nargs="*", help="List of detailed status messages"
    )

    parser.add_argument(
        "--webhook-url",
        type=str,
        help="Teams webhook URL (overrides TEAMS_WEBHOOK_URL env var)",
    )

    parser.add_argument(
        "--checked-count", type=int, default=0, help="Total number of images checked"
    )

    parser.add_argument(
        "--found-count", type=int, default=0, help="Number of images found/available"
    )

    parser.add_argument(
        "--date-checked", type=str, help="Date that was checked (YYYYMMDD format)"
    )

    parser.add_argument(
        "--available-images",
        type=str,
        nargs="*",
        help="List of available images with their details",
    )

    parser.add_argument(
        "--test-mode",
        action="store_true",
        help="Send a simple test message to verify Teams connectivity",
    )

    args = parser.parse_args()

    # Get webhook URL
    webhook_url = args.webhook_url or os.environ.get("TEAMS_WEBHOOK_URL")
    if not webhook_url:
        print("❌ Error: Teams webhook URL not provided")
        print("   Set TEAMS_WEBHOOK_URL environment variable or use --webhook-url")
        return 1

    # Create notifier
    notifier = DockerImageTeamsNotifier(webhook_url)

    # Handle test mode
    if args.test_mode:
        print("🧪 Test mode: Sending simple Docker image alert test")
        success = notifier.send_test_notification()
        return 0 if success else 1

    # Validate required arguments for normal operation
    if not args.status or not args.message:
        print(
            "❌ Error: --status and --message are required (unless using --test-mode)"
        )
        return 1

    # Send notification
    success = notifier.send_notification(
        status=args.status,
        message=args.message,
        details=args.details or [],
        checked_count=args.checked_count,
        found_count=args.found_count,
        date_checked=args.date_checked,
        available_images=args.available_images or [],
    )

    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
