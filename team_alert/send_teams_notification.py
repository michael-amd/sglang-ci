#!/usr/bin/env python3
"""
Send Nightly Plot Notifications to Microsoft Teams with Intelligent Analysis

This script sends performance plot notifications to Microsoft Teams channels via webhooks.
It includes intelligent analysis of GSM8K accuracy and performance regression detection
to provide actionable alerts about benchmark health.

USAGE:
    python send_teams_notification.py --model grok --mode online
    python send_teams_notification.py --model deepseek --mode offline
    python send_teams_notification.py --webhook-url "https://teams.webhook.url"

ENVIRONMENT VARIABLES:
    TEAMS_WEBHOOK_URL: Teams webhook URL (required if not provided via --webhook-url)
    TEAMS_SKIP_ANALYSIS: Set to "true" to skip intelligent analysis (default: false)
    TEAMS_ANALYSIS_DAYS: Days to look back for performance comparison (default: 7)
    PLOT_SERVER_HOST: Host where plots are served (default: hostname -I)
    PLOT_SERVER_PORT: Port where plots are served (default: 8000)
    PLOT_SERVER_BASE_URL: Full base URL override (overrides host/port)

REQUIREMENTS:
    - requests library
    - pytz library (for timezone handling)
    - Plot server must be running and accessible
    - Teams webhook must be configured
"""

import argparse
import csv
import glob
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import requests

try:
    import pytz

    PYTZ_AVAILABLE = True
except ImportError:
    PYTZ_AVAILABLE = False
    print("⚠️  Warning: pytz not available, using UTC time instead of Pacific time")


class BenchmarkAnalyzer:
    """Analyze benchmark results for accuracy and performance regressions"""

    def __init__(self, base_dir: Optional[str] = None):
        # Use the provided base_dir, environment variable BENCHMARK_BASE_DIR, or a default path
        self.base_dir = base_dir or os.getenv(
            "BENCHMARK_BASE_DIR", "/mnt/raid/michael/sgl_benchmark_ci"
        )
        self.offline_dir = os.path.join(self.base_dir, "offline")
        self.online_dir = os.path.join(self.base_dir, "online")

    def parse_gsm8k_accuracy(
        self, model: str, mode: str, date_str: str
    ) -> Optional[float]:
        """
        Parse GSM8K accuracy from benchmark logs

        Args:
            model: Model name (grok, deepseek)
            mode: Benchmark mode (online, offline)
            date_str: Date string (YYYYMMDD)

        Returns:
            GSM8K accuracy as float (0.0-1.0) or None if not found
        """
        model_names = {"grok": "GROK1", "deepseek": "DeepSeek-V3-0324"}
        model_name = model_names.get(model, model.upper())

        # Search for GSM8K log files
        search_patterns = [
            f"{self.offline_dir}/{model_name}/*{date_str}*{model_name}*{mode}*/gsm8k*.log",
            f"{self.online_dir}/{model_name}/*{date_str}*{model_name}*{mode}*/gsm8k*.log",
            f"{self.offline_dir}/{model_name}/*{date_str}*{model.lower()}*{mode}*/gsm8k*.log",
            f"{self.online_dir}/{model_name}/*{date_str}*{model.lower()}*{mode}*/gsm8k*.log",
        ]

        for pattern in search_patterns:
            log_files = glob.glob(pattern)
            for log_file in log_files:
                accuracy = self._extract_accuracy_from_log(log_file)
                if accuracy is not None:
                    return accuracy

        return None

    def _extract_accuracy_from_log(self, log_file: str) -> Optional[float]:
        """Extract accuracy from GSM8K log file"""
        try:
            with open(log_file, "r") as f:
                content = f.read()

            # Look for accuracy patterns
            patterns = [
                r"accuracy[:\s]+([0-9]*\.?[0-9]+)",
                r"Accuracy[:\s]+([0-9]*\.?[0-9]+)",
                r"GSM8K accuracy[:\s]+([0-9]*\.?[0-9]+)",
                r"final accuracy[:\s]+([0-9]*\.?[0-9]+)",
            ]

            for pattern in patterns:
                matches = re.findall(pattern, content, re.IGNORECASE)
                if matches:
                    accuracy = float(matches[-1])  # Take the last match (final result)
                    # Convert to 0.0-1.0 range if needed
                    if accuracy > 1.0:
                        accuracy = accuracy / 100.0
                    return accuracy

        except (FileNotFoundError, IOError) as e:
            print(f"   Warning: File error while parsing {log_file}: {e}")
        except ValueError as e:
            print(f"   Warning: Value error while parsing {log_file}: {e}")
        return None

    def compare_performance_metrics(
        self, model: str, mode: str, current_date: str, days_back: int = 7
    ) -> Dict:
        """
        Compare current performance with historical data

        Args:
            model: Model name
            mode: Benchmark mode
            current_date: Current date string (YYYYMMDD)
            days_back: Number of days to look back for comparison

        Returns:
            Dictionary with performance comparison results
        """
        results = {
            "has_regression": False,
            "regressions": [],
            "current_metrics": {},
            "baseline_metrics": {},
            "comparison_date": None,
        }

        if mode != "online":
            return results  # Only analyze online performance for now

        # Get current metrics
        current_metrics = self._get_online_metrics(model, current_date)
        if not current_metrics:
            return results

        results["current_metrics"] = current_metrics

        # Find baseline metrics from recent runs
        baseline_metrics, baseline_date = self._find_baseline_metrics(
            model, current_date, days_back
        )
        if not baseline_metrics:
            return results

        results["baseline_metrics"] = baseline_metrics
        results["comparison_date"] = baseline_date

        # Compare key metrics (lower is better for latency)
        metrics_to_check = ["e2e_latency", "ttft", "itl"]
        regression_threshold = 0.05  # 5% increase = regression

        for metric in metrics_to_check:
            if metric in current_metrics and metric in baseline_metrics:
                current_val = current_metrics[metric]
                baseline_val = baseline_metrics[metric]

                if baseline_val > 0:  # Avoid division by zero
                    change_pct = (current_val - baseline_val) / baseline_val

                    if change_pct > regression_threshold:
                        results["has_regression"] = True
                        results["regressions"].append(
                            {
                                "metric": metric,
                                "current": current_val,
                                "baseline": baseline_val,
                                "change_pct": change_pct * 100,
                                "baseline_date": baseline_date,
                            }
                        )

        return results

    def _get_online_metrics(self, model: str, date_str: str) -> Dict:
        """Get online performance metrics for a specific date"""
        model_names = {"grok": "GROK1", "deepseek": "DeepSeek-V3-0324"}
        model_name = model_names.get(model, model.upper())

        # Look for CSV files with online metrics
        csv_patterns = [
            f"{self.online_dir}/{model_name}/*{date_str}*{model_name}*online*/*.csv",
            f"{self.online_dir}/{model_name}/*{date_str}*{model.lower()}*online*/*.csv",
        ]

        for pattern in csv_patterns:
            csv_files = glob.glob(pattern)
            for csv_file in csv_files:
                metrics = self._parse_online_csv(csv_file)
                if metrics:
                    return metrics

        return {}

    def _parse_online_csv(self, csv_file: str) -> Dict:
        """Parse online performance metrics from CSV file"""
        try:
            with open(csv_file, "r") as f:
                reader = csv.DictReader(f)
                rows = list(reader)

            if not rows:
                return {}

            # Get median metrics or best representative values
            metrics = {}

            # For GROK, look for request_rate columns
            # For DeepSeek, look for concurrency columns
            for row in rows:
                # Extract E2E latency (median)
                for col in row.keys():
                    if col and "e2e" in col.lower() and "median" in col.lower():
                        try:
                            metrics["e2e_latency"] = float(row[col])
                        except (ValueError, TypeError):
                            pass
                    elif col and "ttft" in col.lower() and "median" in col.lower():
                        try:
                            metrics["ttft"] = float(row[col])
                        except (ValueError, TypeError):
                            pass
                    elif col and "itl" in col.lower() and "median" in col.lower():
                        try:
                            metrics["itl"] = float(row[col])
                        except (ValueError, TypeError):
                            pass

            return metrics

        except Exception as e:
            print(f"   Warning: Could not parse {csv_file}: {e}")
            return {}

    def _find_baseline_metrics(
        self, model: str, current_date: str, days_back: int
    ) -> Tuple[Dict, str]:
        """Find baseline metrics from recent successful runs"""
        current_dt = datetime.strptime(current_date, "%Y%m%d")

        for days_ago in range(1, days_back + 1):
            baseline_dt = current_dt - timedelta(days=days_ago)
            baseline_date = baseline_dt.strftime("%Y%m%d")

            baseline_metrics = self._get_online_metrics(model, baseline_date)
            if baseline_metrics:
                return baseline_metrics, baseline_date

        return {}, None


class TeamsNotifier:
    """Handle sending plot notifications to Microsoft Teams"""

    def __init__(
        self,
        webhook_url: str,
        plot_server_base_url: str,
        skip_analysis: bool = False,
        analysis_days: int = 7,
        benchmark_dir: Optional[str] = None,
    ):
        """
        Initialize Teams notifier

        Args:
            webhook_url: Microsoft Teams webhook URL
            plot_server_base_url: Base URL where plots are served (e.g., http://host:8000)
            skip_analysis: If True, skip GSM8K accuracy and performance regression analysis
            analysis_days: Number of days to look back for performance comparison
            benchmark_dir: Base directory for benchmark data (overrides BENCHMARK_BASE_DIR env var)
        """
        self.webhook_url = webhook_url
        self.plot_server_base_url = plot_server_base_url.rstrip("/")
        self.skip_analysis = skip_analysis
        self.analysis_days = analysis_days
        self.analyzer = BenchmarkAnalyzer(benchmark_dir)

    def create_summary_alert(self, model: str, mode: str) -> Dict:
        """
        Create intelligent summary alert for accuracy and performance

        Args:
            model: Model name
            mode: Benchmark mode

        Returns:
            Dictionary with alert information
        """
        if self.skip_analysis:
            return {
                "status": "good",
                "title": "📊 Benchmark Results",
                "details": ["Analysis skipped - plots only"],
                "gsm8k_accuracy": None,
                "performance_regressions": [],
            }

        current_date = datetime.now().strftime("%Y%m%d")

        alert = {
            "status": "good",  # good, warning, error
            "title": "✅ Good: No Issues Detected",
            "details": [],
            "gsm8k_accuracy": None,
            "performance_regressions": [],
        }

        # Check GSM8K accuracy
        gsm8k_accuracy = self.analyzer.parse_gsm8k_accuracy(model, mode, current_date)
        if gsm8k_accuracy is not None:
            alert["gsm8k_accuracy"] = gsm8k_accuracy

            # Define thresholds based on model
            thresholds = {
                "grok": 0.8,  # 80% for GROK
                "deepseek": 0.93,  # 93% for DeepSeek
            }

            threshold = thresholds.get(model, 0.8)

            if gsm8k_accuracy < threshold:
                alert["status"] = "error"
                alert["title"] = "❌ GSM8K Accuracy Failure Detected"
                alert["details"].append(
                    f"GSM8K accuracy: {gsm8k_accuracy:.1%} (below {threshold:.1%} threshold)"
                )
            else:
                alert["details"].append(f"GSM8K accuracy: {gsm8k_accuracy:.1%} ✅")

        # Check performance regressions (online mode only)
        if mode == "online":
            perf_results = self.analyzer.compare_performance_metrics(
                model, mode, current_date, self.analysis_days
            )

            if perf_results["has_regression"]:
                if alert["status"] == "good":
                    alert["status"] = "warning"
                    alert["title"] = "⚠️ Performance Regression Detected"
                elif alert["status"] == "error":
                    alert["title"] = "❌ Accuracy Failure + Performance Regression"

                alert["performance_regressions"] = perf_results["regressions"]

                for regression in perf_results["regressions"]:
                    metric_name = regression["metric"].replace("_", " ").title()
                    change_pct = regression["change_pct"]
                    alert["details"].append(
                        f"{metric_name}: +{change_pct:.1f}% vs {regression['baseline_date']} ⚠️"
                    )
            elif perf_results["current_metrics"]:
                alert["details"].append(
                    "Performance metrics: No regression detected ✅"
                )

        # Update title if everything is good
        if alert["status"] == "good" and not alert["details"]:
            alert["details"].append("No accuracy or performance regression detected.")
        elif alert["status"] == "good":
            alert["title"] = "✅ Good: No Regression Detected"

        return alert

    def discover_plot_files(
        self, model: str, mode: str, plot_dir: str
    ) -> List[Dict[str, str]]:
        """
        Discover plot files for the given model and mode

        Args:
            model: Model name (grok, deepseek)
            mode: Benchmark mode (online, offline)
            plot_dir: Base plot directory

        Returns:
            List of plot file info dictionaries
        """
        plots = []
        current_date = datetime.now().strftime("%Y%m%d")

        # Model name mapping for file search
        model_names = {"grok": "GROK1", "deepseek": "DeepSeek-V3-0324"}

        model_name = model_names.get(model, model.upper())

        # Search for plot files with flexible naming patterns
        # Support both uppercase model names (GROK1) and lowercase (grok)
        search_patterns = [
            f"{plot_dir}/{model_name}/{mode}/{current_date}_{model_name}_{mode}.png",
            f"{plot_dir}/{model_name}/{mode}/{current_date}_{model_name}_{mode}_split.png",
            f"{plot_dir}/{model_name}/{mode}/{current_date}_{model.lower()}_{mode}.png",
            f"{plot_dir}/{model_name}/{mode}/{current_date}_{model.lower()}_{mode}_split.png",
        ]

        for pattern in search_patterns:
            files = glob.glob(pattern)
            for file_path in files:
                file_name = os.path.basename(file_path)
                relative_path = file_path.replace(plot_dir, "").lstrip("/")
                plot_url = f"{self.plot_server_base_url}/{relative_path}"

                plots.append(
                    {
                        "file_name": file_name,
                        "file_path": file_path,
                        "plot_url": plot_url,
                        "model": model_name,
                        "mode": mode,
                    }
                )

        return plots

    def create_adaptive_card(
        self, plots: List[Dict[str, str]], model: str, mode: str
    ) -> Dict:
        """
        Create an adaptive card for Teams with plot information and summary alerts

        Args:
            plots: List of plot file information
            model: Model name
            mode: Benchmark mode

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
            current_time = pacific_time.strftime(f"%H:%M:%S {tz_name} (San Francisco)")
        else:
            # Fallback to UTC if pytz is not available
            current_date = datetime.now().strftime("%Y-%m-%d")
            current_time = datetime.now().strftime("%H:%M:%S UTC")

        # Generate summary alert
        if not self.skip_analysis:
            print("🔍 Analyzing benchmark results for accuracy and performance...")
        else:
            print("📊 Generating plot summary (analysis skipped)...")
        summary_alert = self.create_summary_alert(model, mode)

        # Create card body elements starting with run name
        body_elements = []

        # Add main header first
        body_elements.extend(
            [
                {
                    "type": "TextBlock",
                    "size": "Large",
                    "weight": "Bolder",
                    "text": f"{current_date} {model.upper()} {mode.title()} Benchmark Results",
                },
                {
                    "type": "TextBlock",
                    "size": "Small",
                    "text": f"Generated on {current_date} at {current_time}",
                    "isSubtle": True,
                    "spacing": "None",
                },
            ]
        )

        # Add Status section title
        body_elements.append(
            {
                "type": "TextBlock",
                "text": "**Status:**",
                "weight": "Bolder",
                "size": "Medium",
                "spacing": "Medium",
            }
        )

        # Add summary alert section
        alert_color = {"good": "Good", "warning": "Warning", "error": "Attention"}.get(
            summary_alert["status"], "Default"
        )

        # Add status title directly (no container wrapper)
        body_elements.append(
            {
                "type": "TextBlock",
                "size": "Medium",
                "weight": "Bolder",
                "text": summary_alert["title"],
                "color": alert_color,
                "wrap": True,
                "spacing": "Small",
            }
        )

        # Add alert details as individual bullet points
        if summary_alert["details"]:
            for detail in summary_alert["details"]:
                body_elements.append(
                    {
                        "type": "TextBlock",
                        "text": f"• {detail}",
                        "wrap": True,
                        "size": "Small",
                        "spacing": "None",
                    }
                )

        # Add Plot section title
        body_elements.append(
            {
                "type": "TextBlock",
                "text": "**Plot:**",
                "weight": "Bolder",
                "size": "Medium",
                "spacing": "Medium",
            }
        )

        if not plots:
            body_elements.append(
                {
                    "type": "TextBlock",
                    "text": "⚠️ No plot files found for this benchmark run.",
                    "color": "Warning",
                    "wrap": True,
                }
            )
        else:
            # Add plot information (always text-only for private/internal servers)
            for i, plot in enumerate(plots, 1):
                # Combine plot name and link in one line to save space
                body_elements.append(
                    {
                        "type": "TextBlock",
                        "text": f"**{i}. {plot['file_name']}** - 🔗 [View Plot]({plot['plot_url']})",
                        "wrap": True,
                        "size": "Small",
                        "spacing": "Small",
                    }
                )

        # Create actions
        actions = []
        if plots:
            # Add action to view all plots (link to the model's directory)
            model_names = {"grok": "GROK1", "deepseek": "DeepSeek-V3-0324"}
            model_name = model_names.get(model, model.upper())
            all_plots_url = f"{self.plot_server_base_url}/{model_name}/{mode}/"

            actions.append(
                {
                    "type": "Action.OpenUrl",
                    "title": f"📁 Browse All",
                    "url": all_plots_url,
                }
            )

        # Add dashboard link
        actions.append(
            {
                "type": "Action.OpenUrl",
                "title": "🌐 Dashboard",
                "url": self.plot_server_base_url,
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

    def send_notification(
        self, plots: List[Dict[str, str]], model: str, mode: str
    ) -> bool:
        """
        Send notification to Teams

        Args:
            plots: List of plot file information
            model: Model name
            mode: Benchmark mode

        Returns:
            True if successful, False otherwise
        """
        try:
            card = self.create_adaptive_card(plots, model, mode)

            # Debug: Show card structure for troubleshooting
            # Always text-only mode, so no Image elements to count
            print("🔍 Sending text-only adaptive card (no Image elements)")

            headers = {"Content-Type": "application/json"}

            response = requests.post(
                self.webhook_url, data=json.dumps(card), headers=headers, timeout=30
            )

            if response.status_code in [200, 202]:
                print(
                    f"✅ Successfully sent Teams notification for {model} {mode} plots"
                )
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


def get_plot_server_base_url() -> str:
    """
    Get the plot server base URL from environment or default configuration

    Returns:
        Base URL for the plot server
    """
    # Check for full URL override first
    base_url = os.environ.get("PLOT_SERVER_BASE_URL")
    if base_url:
        return base_url.rstrip("/")

    # Build URL from host and port
    host = os.environ.get("PLOT_SERVER_HOST")
    if not host:
        try:
            # Get the first IP address from hostname -I
            result = subprocess.run(
                ["hostname", "-I"], capture_output=True, text=True, check=True
            )
            host = result.stdout.strip().split()[0]
        except (subprocess.CalledProcessError, IndexError):
            host = "localhost"

    port = os.environ.get("PLOT_SERVER_PORT", "8000")
    return f"http://{host}:{port}"


def main():
    """Main function"""
    parser = argparse.ArgumentParser(
        description="Send nightly plot notifications to Microsoft Teams",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    parser.add_argument(
        "--model",
        type=str,
        choices=["grok", "deepseek"],
        required=True,
        help="Model name",
    )

    parser.add_argument(
        "--mode",
        type=str,
        choices=["online", "offline"],
        required=True,
        help="Benchmark mode",
    )

    parser.add_argument(
        "--webhook-url",
        type=str,
        help="Teams webhook URL (overrides TEAMS_WEBHOOK_URL env var)",
    )

    parser.add_argument(
        "--plot-dir",
        type=str,
        default="/mnt/raid/michael/sgl_benchmark_ci/plots_server",
        help="Base directory where plots are stored",
    )

    parser.add_argument(
        "--benchmark-dir",
        type=str,
        default="/mnt/raid/michael/sgl_benchmark_ci",
        help="Base directory for benchmark data (overrides BENCHMARK_BASE_DIR env var)",
    )

    parser.add_argument(
        "--check-server",
        action="store_true",
        help="Check if plot server is accessible before sending notification",
    )

    parser.add_argument(
        "--skip-analysis",
        action="store_true",
        help="Skip GSM8K accuracy and performance regression analysis",
    )

    parser.add_argument(
        "--analysis-days",
        type=int,
        default=7,
        help="Number of days to look back for performance comparison (default: 7)",
    )

    args = parser.parse_args()

    # Get webhook URL
    webhook_url = args.webhook_url or os.environ.get("TEAMS_WEBHOOK_URL")
    if not webhook_url:
        print("❌ Error: Teams webhook URL not provided")
        print("   Set TEAMS_WEBHOOK_URL environment variable or use --webhook-url")
        return 1

    # Get plot server base URL
    plot_server_base_url = get_plot_server_base_url()
    print(f"📡 Plot server base URL: {plot_server_base_url}")

    # Check if plot server is accessible
    if args.check_server:
        try:
            response = requests.get(plot_server_base_url, timeout=10)
            if response.status_code != 200:
                print(f"⚠️  Warning: Plot server returned status {response.status_code}")
        except requests.exceptions.RequestException as e:
            print(f"⚠️  Warning: Could not reach plot server: {e}")
            print("   Plot links may not be accessible via provided URLs")

    print(f"📁 Plot directory: {args.plot_dir}")
    print(f"🗂️  Benchmark directory: {args.benchmark_dir}")

    # Create notifier and discover plots
    notifier = TeamsNotifier(
        webhook_url,
        plot_server_base_url,
        args.skip_analysis,
        args.analysis_days,
        args.benchmark_dir,
    )
    plots = notifier.discover_plot_files(args.model, args.mode, args.plot_dir)

    print(f"🔍 Discovered {len(plots)} plot file(s) for {args.model} {args.mode}")
    for plot in plots:
        print(f"   - {plot['file_name']} -> {plot['plot_url']}")

    # Send notification
    success = notifier.send_notification(plots, args.model, args.mode)

    if success:
        if args.skip_analysis:
            print("🎉 Teams notification sent successfully! (analysis skipped)")
        else:
            print(
                "🎉 Teams notification sent successfully! (with intelligent analysis)"
            )
        return 0
    else:
        print("💥 Failed to send Teams notification")
        return 1


if __name__ == "__main__":
    sys.exit(main())
