#!/usr/bin/env python3
"""
Send Nightly Plot Notifications to Microsoft Teams with Intelligent Analysis

This script sends performance plot notifications to Microsoft Teams channels via webhooks.
It includes intelligent analysis of GSM8K accuracy and performance regression detection
to provide actionable alerts about benchmark health.

USAGE:
    python send_teams_notification.py --model grok --mode online
    python send_teams_notification.py --model grok2 --mode online
    python send_teams_notification.py --model deepseek --mode offline
    python send_teams_notification.py --model deepseek --mode online --check-dp-attention
    python send_teams_notification.py --webhook-url "https://teams.webhook.url"
    python send_teams_notification.py --model grok2 --mode online --github-upload --github-repo "user/repo"
    python send_teams_notification.py --model grok2 --mode online --benchmark-date 20250922
    python send_teams_notification.py --test-mode --webhook-url "https://teams.webhook.url"

ENVIRONMENT VARIABLES:
    TEAMS_WEBHOOK_URL: Teams webhook URL (required if not provided via --webhook-url)
    TEAMS_SKIP_ANALYSIS: Set to "true" to skip intelligent analysis (default: false)
    TEAMS_ANALYSIS_DAYS: Days to look back for performance comparison (default: 7)
    PLOT_SERVER_HOST: Host where plots are served (default: hostname -I)
    PLOT_SERVER_PORT: Port where plots are served (default: 8000)
    PLOT_SERVER_BASE_URL: Full base URL override (overrides host/port)
    GITHUB_TOKEN: GitHub personal access token (required for --github-upload)

REQUIREMENTS:
    - requests library
    - pytz library (for timezone handling)
    - Plot server must be running and accessible
    - Teams webhook must be configured
"""

import argparse
import base64
import csv
import glob
import json
import os
import re
import socket
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

    def __init__(
        self, base_dir: Optional[str] = None, check_dp_attention: bool = False, enable_torch_compile: bool = False, benchmark_date: Optional[str] = None
    ):
        # Use the provided base_dir, environment variable BENCHMARK_BASE_DIR, or a default path
        self.base_dir = base_dir or os.getenv(
            "BENCHMARK_BASE_DIR", os.path.expanduser("~/sglang-ci")
        )
        self.offline_dir = os.path.join(self.base_dir, "offline")
        self.online_dir = os.path.join(self.base_dir, "online")
        self.check_dp_attention = check_dp_attention
        self.enable_torch_compile = enable_torch_compile
        self.benchmark_date = benchmark_date

    def parse_gsm8k_accuracy(
        self, model: str, mode: str, date_str: str
    ) -> Optional[float]:
        """
        Parse GSM8K accuracy from timing_summary logs

        Args:
            model: Model name (grok, grok2, deepseek, DeepSeek-V3)
            mode: Benchmark mode (online, offline)
            date_str: Date string (YYYYMMDD)

        Returns:
            GSM8K accuracy as float (0.0-1.0) or None if not found
        """
        timing_log_file = self._find_timing_summary_log(model, mode, date_str)
        if timing_log_file:
            return self._extract_accuracy_from_log(timing_log_file)
        return None

    def _find_timing_summary_log(self, model: str, mode: str, date_str: str) -> Optional[str]:
        """
        Find timing_summary log file for the given model, mode, and date

        Args:
            model: Model name (grok, grok2, deepseek, DeepSeek-V3)
            mode: Benchmark mode (online, offline)
            date_str: Date string (YYYYMMDD)

        Returns:
            Path to timing_summary log file or None if not found or benchmark didn't run for this date
        """
        model_names = {
            "grok": "GROK1",
            "grok2": "GROK2",
            "deepseek": "DeepSeek-V3-0324",
            "DeepSeek-V3": "DeepSeek-V3",
        }
        model_name = model_names.get(model, model.upper())

        # Build mode suffix for DP attention and torch compile
        mode_suffix = ""
        if self.check_dp_attention:
            mode_suffix += "_dp_attention"
        if self.enable_torch_compile:
            mode_suffix += "_torch_compile"

        # Search for timing_summary logs in folders that contain the image date
        # The folder name contains the image date, but the timing log filename contains the run date
        search_patterns = []
        if mode == "online":
            # Use more specific patterns to avoid matching longer suffixes
            if mode_suffix:
                # For specific modes, ensure exact suffix match
                search_patterns.extend([
                    f"{self.online_dir}/{model_name}/*{date_str}*{model_name}*{mode}{mode_suffix}/timing_summary_*.log",
                    f"{self.online_dir}/{model_name}/*{date_str}*{model.lower()}*{mode}{mode_suffix}/timing_summary_*.log",
                ])
            else:
                # For standard mode, match folders ending with just the mode (no additional suffixes)
                search_patterns.extend([
                    f"{self.online_dir}/{model_name}/*{date_str}*{model_name}*{mode}/timing_summary_*.log",
                    f"{self.online_dir}/{model_name}/*{date_str}*{model.lower()}*{mode}/timing_summary_*.log",
                    # Also handle variant names like MOE-I4F8
                    f"{self.online_dir}/{model_name}/*{date_str}*{model_name}*_*_{mode}/timing_summary_*.log",
                ])
        else:
            if mode_suffix:
                search_patterns.extend([
                    f"{self.offline_dir}/{model_name}/*{date_str}*{model_name}*{mode}{mode_suffix}/timing_summary_*.log",
                    f"{self.offline_dir}/{model_name}/*{date_str}*{model.lower()}*{mode}{mode_suffix}/timing_summary_*.log",
                ])
            else:
                search_patterns.extend([
                    f"{self.offline_dir}/{model_name}/*{date_str}*{model_name}*{mode}/timing_summary_*.log",
                    f"{self.offline_dir}/{model_name}/*{date_str}*{model.lower()}*{mode}/timing_summary_*.log",
                ])

        # Find the most recent timing_summary log for the specific date
        timing_logs = []
        for pattern in search_patterns:
            timing_logs.extend(glob.glob(pattern))
        
        if timing_logs:
            # Sort by modification time and return the most recent
            timing_logs.sort(key=os.path.getmtime, reverse=True)
            print(f"   Found timing_summary log for {model} {mode} on {date_str}: {os.path.basename(timing_logs[0])}")
            return timing_logs[0]
        
        print(f"   No timing_summary log found for {model} {mode} on {date_str} - benchmark may not have run yet")
        return None

    def _extract_accuracy_from_log(self, log_file: str) -> Optional[float]:
        """Extract accuracy from timing_summary log file"""
        try:
            with open(log_file, "r") as f:
                content = f.read()

            # Look for GSM8K accuracy patterns in timing_summary logs
            patterns = [
                r"GSM8K accuracy[:\s]+([0-9]*\.?[0-9]+)",  # Primary pattern from timing_summary
                r"Average accuracy[:\s]+([0-9]*\.?[0-9]+)",  # Fallback pattern
                r"accuracy[:\s]+([0-9]*\.?[0-9]+)",  # Generic accuracy pattern
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

    def check_dp_attention_errors(
        self, model: str, mode: str, date_str: str
    ) -> Dict[str, any]:
        """
        Check for RuntimeError and other critical errors in timing_summary logs

        Args:
            model: Model name (grok, grok2, deepseek)
            mode: Benchmark mode (online, offline)
            date_str: Date string (YYYYMMDD)

        Returns:
            Dictionary with error status and details
        """
        result = {
            "status": "pass",  # pass, fail
            "errors": [],
            "log_file": None,
        }

        if not self.check_dp_attention:
            return result

        timing_log_file = self._find_timing_summary_log(model, mode, date_str)
        if timing_log_file:
            result["log_file"] = timing_log_file
            errors = self._extract_server_errors_from_timing_log(timing_log_file)
            
            if errors:
                result["status"] = "fail"
                result["errors"].extend(errors)
            
        return result

    def _extract_server_errors_from_timing_log(self, log_file: str) -> List[str]:
        """Extract server errors from timing_summary log file"""
        errors = []

        try:
            with open(log_file, "r") as f:
                content = f.read()

            # Look for server error status in timing_summary logs
            if "Server error status: FAIL" in content:
                # Extract RuntimeError details if present
                runtime_error_match = re.search(r"RuntimeError count: (\d+)", content)
                if runtime_error_match:
                    error_count = int(runtime_error_match.group(1))
                    if error_count > 0:
                        errors.append(f"RuntimeError: {error_count} error(s) found in server logs")
                        
                        # Try to extract specific error messages
                        error_lines = re.findall(r"    (RuntimeError:.*)", content)
                        for error_line in error_lines[:3]:  # Limit to first 3 errors
                            errors.append(error_line.strip())

            # Check for critical errors
            critical_error_match = re.search(r"Critical error count: (\d+)", content)
            if critical_error_match:
                error_count = int(critical_error_match.group(1))
                if error_count > 0:
                    errors.append(f"Critical errors: {error_count} error(s) found in server logs")

        except (FileNotFoundError, IOError) as e:
            print(f"   Warning: Could not read timing log {log_file}: {e}")
        except Exception as e:
            print(f"   Warning: Error parsing timing log {log_file}: {e}")

        return errors

    def _extract_server_errors(self, log_file: str) -> List[str]:
        """Legacy function - kept for backward compatibility"""
        return self._extract_server_errors_from_timing_log(log_file)

    def extract_additional_info(
        self, model: str, mode: str, date_str: str
    ) -> Dict[str, any]:
        """
        Extract additional information from timing_summary logs

        Args:
            model: Model name (grok, grok2, deepseek)
            mode: Benchmark mode (online, offline)
            date_str: Date string (YYYYMMDD)

        Returns:
            Dictionary with additional information
        """
        result = {
            "docker_image": None,
            "hardware": None,
            "runtime": None,
            "hostname": None,
            "start_time": None,
            "end_time": None,
            "torch_compile": False,
            "total_runtime_seconds": None,
            "total_runtime_minutes": None,
        }

        timing_log_file = self._find_timing_summary_log(model, mode, date_str)
        if timing_log_file:
            info = self._extract_additional_info_from_log(timing_log_file)
            
            # Merge all information from timing_summary log
            for key, value in info.items():
                if value is not None and (result[key] is None or result[key] is False):
                    result[key] = value

        return result

    def _extract_additional_info_from_log(self, log_file: str) -> Dict[str, any]:
        """Extract additional info (Docker image, hardware, runtime, torch compile) from log file"""
        info = {
            "docker_image": None,
            "hardware": None,
            "runtime": None,
            "hostname": None,
            "start_time": None,
            "end_time": None,
            "torch_compile": False,
            "total_runtime_seconds": None,
            "total_runtime_minutes": None,
        }

        try:
            with open(log_file, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()

            # Extract Docker image (timing_summary pattern)
            image_match = re.search(r"Docker image:\s*(.+)", content)
            if image_match:
                info["docker_image"] = image_match.group(1).strip()

            # Extract hardware (timing_summary pattern)
            hardware_match = re.search(r"Hardware:\s*(.+)", content)
            if hardware_match:
                hardware_text = hardware_match.group(1).strip()
                # Clean up hardware text (remove ROCM version for cleaner display)
                if ", ROCM Version:" in hardware_text:
                    hardware_text = hardware_text.split(", ROCM Version:")[0]
                info["hardware"] = hardware_text

            # Extract hostname (timing_summary pattern)
            hostname_match = re.search(r"Hostname:\s*(.+)", content)
            if hostname_match:
                info["hostname"] = hostname_match.group(1).strip()

            # Extract start and end times (timing_summary patterns)
            start_match = re.search(r"Script started at:\s*(.+)", content)
            if start_match:
                info["start_time"] = start_match.group(1).strip()

            end_match = re.search(r"Script ended at:\s*(.+)", content)
            if end_match:
                info["end_time"] = end_match.group(1).strip()

            # Check for torch compile status (timing_summary pattern)
            torch_compile_match = re.search(r"Torch Compile:\s*(true|false)", content, re.IGNORECASE)
            if torch_compile_match:
                info["torch_compile"] = torch_compile_match.group(1).lower() == "true"
            
            # Extract total runtime from timing summary logs (preferred method)
            runtime_match = re.search(r'Total execution time: (\d+) seconds \((\d+) minutes\)', content)
            if runtime_match:
                info["total_runtime_seconds"] = runtime_match.group(1)
                info["total_runtime_minutes"] = runtime_match.group(2)
                # Format for display
                seconds = int(runtime_match.group(1))
                minutes = int(runtime_match.group(2))
                if minutes >= 60:
                    hours = minutes // 60
                    remaining_minutes = minutes % 60
                    info["runtime"] = f"{hours}h {remaining_minutes}m"
                else:
                    info["runtime"] = f"{minutes}m"
            
            # Fallback: Calculate runtime if both start and end times are available
            elif info.get("start_time") and info.get("end_time"):
                start_str = info["start_time"]
                end_str = info["end_time"]

                try:
                    # Handle different possible formats
                    time_formats = [
                        "%Y-%m-%d %H:%M:%S %Z",  # 2025-09-03 22:42:28 CDT
                        "%Y-%m-%d %H:%M:%S %z",  # With timezone offset
                        "%Y-%m-%d %H:%M:%S",  # Without timezone
                    ]

                    start_dt = None
                    end_dt = None

                    for fmt in time_formats:
                        try:
                            # Remove timezone abbreviations like CDT, CST, etc. for parsing
                            start_clean = re.sub(r"\s+[A-Z]{3,4}$", "", start_str)
                            end_clean = re.sub(r"\s+[A-Z]{3,4}$", "", end_str)

                            if fmt == "%Y-%m-%d %H:%M:%S":
                                start_dt = datetime.strptime(start_clean, fmt)
                                end_dt = datetime.strptime(end_clean, fmt)
                                break
                        except ValueError:
                            continue

                    if start_dt and end_dt:
                        duration = end_dt - start_dt
                        total_seconds = int(duration.total_seconds())
                        info["total_runtime_seconds"] = str(total_seconds)
                        info["total_runtime_minutes"] = str(total_seconds // 60)

                        # Format duration as "5m 23s" or "1h 5m 23s"
                        hours = total_seconds // 3600
                        minutes = (total_seconds % 3600) // 60
                        seconds = total_seconds % 60

                        if hours > 0:
                            info["runtime"] = f"{hours}h {minutes}m {seconds}s"
                        else:
                            info["runtime"] = f"{minutes}m {seconds}s"

                except Exception as e:
                    print(
                        f"   Warning: Could not calculate runtime from {log_file}: {e}"
                    )

        except (FileNotFoundError, IOError) as e:
            print(f"   Warning: File error while parsing {log_file}: {e}")
        except Exception as e:
            print(f"   Warning: Error parsing additional info from {log_file}: {e}")

        return info

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
        model_names = {
            "grok": "GROK1",
            "grok2": "GROK2",
            "deepseek": "DeepSeek-V3-0324",
            "DeepSeek-V3": "DeepSeek-V3-0324",
        }
        model_name = model_names.get(model, model.upper())

        # Build mode suffix for DP attention and torch compile
        mode_suffix = ""
        if self.check_dp_attention:
            mode_suffix += "_dp_attention"
        if self.enable_torch_compile:
            mode_suffix += "_torch_compile"

        # Look for CSV files with online metrics
        csv_patterns = [
            f"{self.online_dir}/{model_name}/*{date_str}*{model_name}*online{mode_suffix}*/*.csv",
            f"{self.online_dir}/{model_name}/*{date_str}*{model.lower()}*online{mode_suffix}*/*.csv",
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
        github_upload: bool = False,
        github_repo: str = None,
        github_token: str = None,
        check_dp_attention: bool = False,
        enable_torch_compile: bool = False,
        benchmark_date: Optional[str] = None,
    ):
        """
        Initialize Teams notifier

        Args:
            webhook_url: Microsoft Teams webhook URL
            plot_server_base_url: Base URL where plots are served (e.g., http://host:8000)
            skip_analysis: If True, skip GSM8K accuracy and performance regression analysis
            analysis_days: Number of days to look back for performance comparison
            benchmark_dir: Base directory for benchmark data (overrides BENCHMARK_BASE_DIR env var)
            github_upload: If True, upload images to GitHub and link to them
            github_repo: GitHub repository in format 'owner/repo'
            github_token: GitHub personal access token
            check_dp_attention: If True, look for DP attention mode logs and check for errors
            enable_torch_compile: If True, look for torch compile mode logs
            benchmark_date: Date to look for benchmark logs (YYYYMMDD format). If not provided, uses current date.
        """
        self.webhook_url = webhook_url
        self.plot_server_base_url = (
            plot_server_base_url.rstrip("/") if plot_server_base_url else ""
        )
        self.skip_analysis = skip_analysis
        self.analysis_days = analysis_days
        self.github_upload = github_upload
        self.github_repo = github_repo
        self.github_token = github_token
        self.check_dp_attention = check_dp_attention
        self.enable_torch_compile = enable_torch_compile
        self.analyzer = BenchmarkAnalyzer(benchmark_dir, check_dp_attention, enable_torch_compile, benchmark_date)

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

        # Use benchmark_date if provided, otherwise use current date
        if self.analyzer.benchmark_date:
            current_date = self.analyzer.benchmark_date
        else:
            current_date = datetime.now().strftime("%Y%m%d")

        alert = {
            "status": "good",  # good, warning, error
            "title": "✅ Good: No Issues Detected",
            "details": [],
            "gsm8k_accuracy": None,
            "performance_regressions": [],
            "dp_attention_errors": [],
            "additional_info": {},
        }

        # Check GSM8K accuracy
        gsm8k_accuracy = self.analyzer.parse_gsm8k_accuracy(model, mode, current_date)
        
        # If no GSM8K data found, it means benchmark didn't run for this date
        if gsm8k_accuracy is None:
            # Check if this is due to no benchmark run for the date
            timing_log = self.analyzer._find_timing_summary_log(model, mode, current_date)
            if timing_log is None:
                alert["status"] = "info"
                alert["title"] = "ℹ️ No Benchmark Run Found"
                alert["details"] = [f"No benchmark results found for {current_date}", 
                                 "Benchmark may not have run yet for this date"]
                return alert
        
        if gsm8k_accuracy is not None:
            alert["gsm8k_accuracy"] = gsm8k_accuracy

            # Define thresholds based on model
            thresholds = {
                "grok": 0.8,  # 80% for GROK1
                "grok2": 0.92,  # 92% for GROK2
                "deepseek": 0.93,  # 93% for DeepSeek
                "DeepSeek-V3": 0.93,  # 93% for DeepSeek-V3
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

        # Extract additional info for online mode
        if mode == "online":
            additional_info = self.analyzer.extract_additional_info(
                model, mode, current_date
            )
            alert["additional_info"] = additional_info

        # Check DP attention errors if enabled
        if self.check_dp_attention:
            dp_error_results = self.analyzer.check_dp_attention_errors(
                model, mode, current_date
            )

            if dp_error_results["status"] == "fail":
                alert["status"] = "error"
                if alert["title"] == "✅ Good: No Issues Detected":
                    alert["title"] = "❌ DP Attention RuntimeError Detected"
                elif "GSM8K" in alert["title"]:
                    alert["title"] = "❌ GSM8K Failure + DP Attention RuntimeError"

                alert["dp_attention_errors"] = dp_error_results["errors"]

                # Only show the log file path, not individual error messages
                if dp_error_results["log_file"]:
                    # Convert absolute path to relative path from base directory
                    log_path = dp_error_results["log_file"]
                    if self.analyzer.base_dir in log_path:
                        # Remove the base directory part and show relative path from sglang-ci
                        relative_path = log_path.replace(
                            self.analyzer.base_dir, "/sglang-ci"
                        )
                        alert["details"].append(f"📋 Error found in: {relative_path}")
                    else:
                        # Fallback to filename if path manipulation fails
                        log_name = os.path.basename(log_path)
                        alert["details"].append(f"📋 Error found in: {log_name}")
            else:
                alert["details"].append("DP attention mode: No errors detected ✅")
                # Update title for successful DP attention check
                if (
                    alert["status"] == "good"
                    and alert["title"] == "✅ Good: No Issues Detected"
                ):
                    alert["title"] = "✅ DP Attention Test Passed"
                
        # Check torch compile status if enabled
        if self.enable_torch_compile and mode == "online":
            # Get additional info if not already retrieved
            if not alert.get("additional_info"):
                additional_info = self.analyzer.extract_additional_info(
                    model, mode, current_date
                )
                alert["additional_info"] = additional_info
            else:
                additional_info = alert["additional_info"]
            
            # If GSM8K benchmark completed successfully, torch compile test passed
            if alert.get("gsm8k_accuracy") is not None:
                alert["details"].append("Torch compile test: No errors detected ✅")
            else:
                # If no GSM8K results found, status is unclear
                alert["details"].append("Torch compile test: Status unclear ⚠️")
        
        # Add runtime information if available
        if mode == "online" and alert.get("additional_info"):
            runtime_info = alert["additional_info"].get("runtime")
            if runtime_info:
                alert["details"].append(f"Runtime: {runtime_info}")

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
            # Show model-specific threshold message
            thresholds = {
                "grok": "80%",
                "grok2": "92%",
                "deepseek": "93%",
                "DeepSeek-V3": "93%",
            }
            threshold_text = thresholds.get(model, "80%")
            alert["details"].append(f"Accuracy above threshold ({threshold_text}).")
        elif alert["status"] == "good":
            alert["title"] = "✅ Good: No Issues Detected"

        return alert

    def create_test_card(self) -> Dict:
        """
        Create a simple test adaptive card to verify Teams connectivity

        Returns:
            Simple test adaptive card JSON structure
        """
        # Use San Francisco time (Pacific Time) if pytz is available
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
                                "text": "🧪 Teams Notification Test",
                            },
                            {
                                "type": "TextBlock",
                                "text": f"Sent at {current_time}",
                                "isSubtle": True,
                                "spacing": "None",
                            },
                            {
                                "type": "TextBlock",
                                "text": "✅ If you see this message, your Teams webhook and adaptive card support are working correctly!",
                                "wrap": True,
                                "spacing": "Medium",
                            },
                        ],
                    },
                }
            ],
        }
        return card

    def send_test_notification(self) -> bool:
        """
        Send a simple test notification to Teams

        Returns:
            True if successful, False otherwise
        """
        try:
            card = self.create_test_card()
            card_json = json.dumps(card)
            payload_size_mb = len(card_json.encode("utf-8")) / (1024 * 1024)

            print("🧪 Sending test adaptive card (no images)")
            print(f"📊 Test payload size: {payload_size_mb:.3f}MB")

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

    def upload_to_github(self, image_path: str, model: str, mode: str) -> Optional[str]:
        """
        Upload image to GitHub repository and return public URL

        Args:
            image_path: Path to the image file
            model: Model name
            mode: Benchmark mode

        Returns:
            Public GitHub URL or None if upload fails
        """
        if not self.github_repo or not self.github_token:
            print("   Warning: GitHub repo or token not configured")
            return None

        try:
            print(f"   🔍 Uploading {os.path.basename(image_path)} to GitHub...")

            # Read and encode image
            with open(image_path, "rb") as f:
                image_data = f.read()

            base64_content = base64.b64encode(image_data).decode("utf-8")

            # Create file path matching plots_server structure: /model/mode/filename.png
            filename = os.path.basename(image_path)

            # Map model names to match directory structure
            model_names = {
                "grok": "GROK1",
                "grok2": "GROK2",
                "deepseek": "DeepSeek-V3",
                "DeepSeek-V3": "DeepSeek-V3",
            }
            model_dir = model_names.get(model, model.upper())

            repo_path = f"{model_dir}/{mode}/{filename}"

            # GitHub API endpoint for plots branch
            api_url = (
                f"https://api.github.com/repos/{self.github_repo}/contents/{repo_path}"
            )

            headers = {
                "Authorization": f"token {self.github_token}",
                "Accept": "application/vnd.github.v3+json",
            }

            # First, ensure the plots branch exists
            self._ensure_plots_branch_exists(headers)

            # Check if file already exists on plots branch
            params = {"ref": "plots"}
            existing_response = requests.get(api_url, headers=headers, params=params)
            sha = None
            if existing_response.status_code == 200:
                sha = existing_response.json().get("sha")
                print(f"   📝 Updating existing file: {repo_path}")
            else:
                print(f"   📄 Creating new file: {repo_path}")

            # Upload or update file on plots branch
            current_date = datetime.now().strftime("%Y-%m-%d")
            payload = {
                "message": f"Add {model} {mode} plot for {current_date}",
                "content": base64_content,
                "branch": "plots",
            }

            if sha:
                payload["sha"] = sha

            response = requests.put(api_url, json=payload, headers=headers)

            if response.status_code in [200, 201]:
                # Return public URL from plots branch
                public_url = f"https://raw.githubusercontent.com/{self.github_repo}/plots/{repo_path}"
                print(f"   ✅ Uploaded to GitHub (plots branch): {filename}")
                print(f"   🔗 GitHub URL: {public_url}")
                return public_url
            else:
                print(f"   ❌ GitHub upload failed: {response.status_code}")
                print(f"   📄 Response: {response.text[:200]}...")
                return None

        except Exception as e:
            print(f"   ❌ GitHub upload error: {e}")
            return None

    def _ensure_plots_branch_exists(self, headers: dict) -> bool:
        """Ensure the plots branch exists in the GitHub repository"""
        try:
            # Check if plots branch exists
            branch_url = (
                f"https://api.github.com/repos/{self.github_repo}/branches/plots"
            )
            response = requests.get(branch_url, headers=headers)

            if response.status_code == 200:
                print(f"   📋 Plots branch already exists")
                return True
            elif response.status_code == 404:
                print(f"   📋 Creating plots branch...")

                # Get main branch SHA
                main_branch_url = f"https://api.github.com/repos/{self.github_repo}/git/refs/heads/main"
                main_response = requests.get(main_branch_url, headers=headers)

                if main_response.status_code == 200:
                    main_sha = main_response.json()["object"]["sha"]

                    # Create plots branch from main
                    create_branch_url = (
                        f"https://api.github.com/repos/{self.github_repo}/git/refs"
                    )
                    create_payload = {"ref": "refs/heads/plots", "sha": main_sha}

                    create_response = requests.post(
                        create_branch_url, json=create_payload, headers=headers
                    )

                    if create_response.status_code == 201:
                        print(f"   ✅ Created plots branch successfully")
                        return True
                    else:
                        print(
                            f"   ❌ Failed to create plots branch: {create_response.status_code}"
                        )
                        return False
                else:
                    print(
                        f"   ❌ Failed to get main branch SHA: {main_response.status_code}"
                    )
                    return False
            else:
                print(f"   ❌ Error checking branch: {response.status_code}")
                return False

        except Exception as e:
            print(f"   ❌ Branch creation error: {e}")
            return False

    def discover_plot_files(
        self, model: str, mode: str, plot_dir: str
    ) -> List[Dict[str, str]]:
        """
        Discover plot files for the given model and mode

        Args:
            model: Model name (grok, grok2, deepseek, DeepSeek-V3)
            mode: Benchmark mode (online, offline)
            plot_dir: Base plot directory

        Returns:
            List of plot file info dictionaries
        """
        plots = []

        # Search for plots from the last 3 days to handle nightly runs that may complete
        # at different times (e.g., benchmark completes at 3 AM, notification sent later)
        search_dates = []
        for days_back in range(3):  # Today, yesterday, day before yesterday
            search_date = (datetime.now() - timedelta(days=days_back)).strftime(
                "%Y%m%d"
            )
            search_dates.append(search_date)

        # Model name mapping for file search
        model_names = {
            "grok": "GROK1",
            "grok2": "GROK2",
            "deepseek": "DeepSeek-V3-0324",
            "DeepSeek-V3": "DeepSeek-V3",
        }

        model_name = model_names.get(model, model.upper())

        # Search through each date (most recent first)
        for search_date in search_dates:
            # Search for plot files with flexible naming patterns
            # Support both uppercase model names (GROK1) and lowercase (grok)
            search_patterns = [
                f"{plot_dir}/{model_name}/{mode}/{search_date}_{model_name}_{mode}.png",
                f"{plot_dir}/{model_name}/{mode}/{search_date}_{model_name}_{mode}_split.png",
                f"{plot_dir}/{model_name}/{mode}/{search_date}_{model.lower()}_{mode}.png",
                f"{plot_dir}/{model_name}/{mode}/{search_date}_{model.lower()}_{mode}_split.png",
            ]

            # For deepseek model, also search for the actual generated filename pattern "DeepSeek-V3"
            # This handles the change in filename format from DeepSeek-V3-0324 to DeepSeek-V3
            if model == "deepseek":
                search_patterns.extend(
                    [
                        f"{plot_dir}/{model_name}/{mode}/{search_date}_DeepSeek-V3_{mode}.png",
                        f"{plot_dir}/{model_name}/{mode}/{search_date}_DeepSeek-V3_{mode}_split.png",
                    ]
                )

            # For DeepSeek-V3 model, also search for the legacy filename pattern "DeepSeek-V3-0324"
            # to maintain backward compatibility
            if model == "DeepSeek-V3":
                search_patterns.extend(
                    [
                        f"{plot_dir}/{model_name}/{mode}/{search_date}_DeepSeek-V3-0324_{mode}.png",
                        f"{plot_dir}/{model_name}/{mode}/{search_date}_DeepSeek-V3-0324_{mode}_split.png",
                    ]
                )

            # Check each pattern for this date
            for pattern in search_patterns:
                files = glob.glob(pattern)
                for file_path in files:
                    file_name = os.path.basename(file_path)
                    relative_path = file_path.replace(plot_dir, "").lstrip("/")

                    plot_info = {
                        "file_name": file_name,
                        "file_path": file_path,
                        "model": model_name,
                        "mode": mode,
                    }

                    # Determine how to handle the image
                    if self.github_upload:
                        # Upload to GitHub and get public URL
                        plot_info["public_url"] = self.upload_to_github(
                            file_path, model, mode
                        )
                        if plot_info["public_url"]:
                            plot_info["hosting_service"] = "GitHub"
                    elif self.plot_server_base_url:
                        # Use HTTP URL for server-hosted images
                        plot_info["plot_url"] = (
                            f"{self.plot_server_base_url}/{relative_path}"
                        )
                    # If no GitHub upload or server URL, file_path will be used as fallback

                    plots.append(plot_info)

            # If we found plots for this date, return them (most recent first)
            if plots:
                break

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

        # Customize title based on enabled modes
        mode_description = []
        if self.check_dp_attention:
            mode_description.append("DP Attention")
        if self.enable_torch_compile:
            mode_description.append("Torch Compile")
            
        if mode_description:
            mode_text = " + ".join(mode_description)
            if self.check_dp_attention and not self.enable_torch_compile:
                main_title = f"{current_date} {model.upper()} {mode.title()} {mode_text} Check"
            else:
                main_title = f"{current_date} {model.upper()} {mode.title()} {mode_text} Benchmark"
        else:
            main_title = (
                f"{current_date} {model.upper()} {mode.title()} Benchmark Results"
            )

        # Add main header first
        body_elements.extend(
            [
                {
                    "type": "TextBlock",
                    "size": "Large",
                    "weight": "Bolder",
                    "text": main_title,
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
        alert_color = {"good": "Good", "warning": "Warning", "error": "Attention", "info": "Default"}.get(
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

        # Add additional information for online mode
        if mode == "online" and summary_alert.get("additional_info"):
            additional_info = summary_alert["additional_info"]

            # Add Docker Image (only if available)
            if additional_info.get("docker_image"):
                body_elements.append(
                    {
                        "type": "TextBlock",
                        "text": f"• Docker Image: **{additional_info['docker_image']}**",
                        "wrap": True,
                        "size": "Small",
                        "spacing": "None",
                    }
                )

            # Add Hostname (only if available)
            if additional_info.get("hostname"):
                body_elements.append(
                    {
                        "type": "TextBlock",
                        "text": f"• Hostname: **{additional_info['hostname']}**",
                        "wrap": True,
                        "size": "Small",
                        "spacing": "None",
                    }
                )


            # Runtime is already shown in the status details section, so don't duplicate it here

        # Check if this is an MI35X machine (plots are not able to access on MI35X on conductor)
        is_mi35x_machine = False
        additional_info = summary_alert.get("additional_info", {})
        docker_image = additional_info.get("docker_image", "")
        if "mi35x" in docker_image.lower():
            is_mi35x_machine = True

        # Add Plot section only if not in DP attention mode, torch compile mode, or MI35X machine
        if not self.check_dp_attention and not self.enable_torch_compile and not is_mi35x_machine:
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
                # Add plot information based on hosting method
                for i, plot in enumerate(plots, 1):
                    # Add plot title
                    body_elements.append(
                        {
                            "type": "TextBlock",
                            "text": f"**{i}. {plot['file_name']}**",
                            "wrap": True,
                            "size": "Small",
                            "spacing": "Small",
                        }
                    )

                # Handle different hosting methods
                if plot.get("public_url") and plot.get("hosting_service"):
                    # GitHub or external hosting - show actual image with maximum size
                    service = plot["hosting_service"]

                    body_elements.append(
                        {
                            "type": "Image",
                            "url": plot["public_url"],
                            "altText": plot["file_name"],
                            "size": "Stretch",  # Use maximum size for all images
                            "spacing": "Small",
                            "width": "100%",  # Force full width display
                        }
                    )

                    # Only show direct link for GitHub uploads, not external uploads
                    if service != "External":
                        body_elements.append(
                            {
                                "type": "TextBlock",
                                "text": f"🔗 [Direct Link]({plot['public_url']}) (hosted on {service})",
                                "wrap": True,
                                "size": "Small",
                                "spacing": "None",
                                "isSubtle": True,
                            }
                        )
                elif plot.get("plot_url"):
                    # HTTP server mode - show link
                    body_elements.append(
                        {
                            "type": "TextBlock",
                            "text": f"🔗 [View Plot]({plot['plot_url']})",
                            "wrap": True,
                            "size": "Small",
                            "spacing": "Small",
                        }
                    )

                else:
                    # Fallback - show file path
                    body_elements.append(
                        {
                            "type": "TextBlock",
                            "text": f"📁 File: `{plot['file_path']}`",
                            "wrap": True,
                            "size": "Small",
                            "spacing": "Small",
                            "fontType": "Monospace",
                        }
                    )

        # Create actions
        actions = []
        if self.plot_server_base_url:
            # Add HTTP server links
            pass

        # Only add plot-related actions if not in DP attention mode, torch compile mode, or MI35X machine
        if not self.check_dp_attention and not self.enable_torch_compile and not is_mi35x_machine:
            if plots:
                # Add action to view all plots (link to the model's directory)
                model_names = {
                    "grok": "GROK1",
                    "grok2": "GROK2",
                    "deepseek": "DeepSeek-V3-0324",
                    "DeepSeek-V3": "DeepSeek-V3",
                }
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
            card_json = json.dumps(card)
            payload_size_mb = len(card_json.encode("utf-8")) / (1024 * 1024)

            # Debug: Show card structure and size for troubleshooting
            if self.github_upload:
                image_count = len([plot for plot in plots if plot.get("public_url")])
                print(
                    f"🔍 Sending adaptive card with {image_count} image(s) hosted on GitHub"
                )
                print(f"📊 Total payload size: {payload_size_mb:.2f}MB")
            else:
                print("🔍 Sending adaptive card with plot links")
                print(f"📊 Payload size: {payload_size_mb:.2f}MB")

            headers = {"Content-Type": "application/json"}

            response = requests.post(
                self.webhook_url, data=card_json, headers=headers, timeout=30
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
        choices=["grok", "grok2", "deepseek", "DeepSeek-V3"],
        help="Model name",
    )

    parser.add_argument(
        "--mode", type=str, choices=["online", "offline"], help="Benchmark mode"
    )

    parser.add_argument(
        "--webhook-url",
        type=str,
        help="Teams webhook URL (overrides TEAMS_WEBHOOK_URL env var)",
    )

    parser.add_argument(
        "--plot-dir",
        type=str,
        default=os.path.expanduser("~/sglang-ci/plots_server"),
        help="Base directory where plots are stored",
    )

    parser.add_argument(
        "--benchmark-dir",
        type=str,
        default=os.path.expanduser("~/sglang-ci"),
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

    parser.add_argument(
        "--test-mode",
        action="store_true",
        help="Send a simple test message to verify Teams connectivity and adaptive card support",
    )

    parser.add_argument(
        "--github-upload",
        action="store_true",
        help="Upload plot images to GitHub and include public links in Teams message",
    )

    parser.add_argument(
        "--github-repo",
        type=str,
        help="GitHub repository in format 'owner/repo' for plot uploads",
    )

    parser.add_argument(
        "--github-token",
        type=str,
        help="GitHub personal access token (or set GITHUB_TOKEN env var)",
    )

    parser.add_argument(
        "--check-dp-attention",
        action="store_true",
        help="Check DP attention mode logs for RuntimeError and other critical errors",
    )

    parser.add_argument(
        "--enable-torch-compile",
        action="store_true",
        help="Enable torch compile mode for performance analysis (affects log file discovery)",
    )
    parser.add_argument(
        "--benchmark-date",
        type=str,
        help="Date to look for benchmark logs (YYYYMMDD format). If not provided, uses current date.",
    )

    args = parser.parse_args()

    # Get webhook URL
    webhook_url = args.webhook_url or os.environ.get("TEAMS_WEBHOOK_URL")
    if not webhook_url:
        print("❌ Error: Teams webhook URL not provided")
        print("   Set TEAMS_WEBHOOK_URL environment variable or use --webhook-url")
        return 1

    # Handle test mode
    if args.test_mode:
        print("🧪 Test mode: Sending simple adaptive card to verify Teams connectivity")
        notifier = TeamsNotifier(webhook_url, "", False, 7, None, False, None, None, False, False, None)
        success = notifier.send_test_notification()
        if success:
            print("🎉 Test completed successfully!")
            print("💡 If you see the test message in Teams, adaptive cards work.")
            return 0
        else:
            print("💥 Test failed - check your webhook URL and Teams configuration")
            return 1

    # Validate required arguments for normal operation
    if not args.model or not args.mode:
        print("❌ Error: --model and --mode are required (unless using --test-mode)")
        return 1

    # Validate GitHub upload configuration
    if args.github_upload:
        github_token = args.github_token or os.environ.get("GITHUB_TOKEN")
        if not args.github_repo or not github_token:
            print(
                "❌ Error: --github-repo and --github-token (or GITHUB_TOKEN env var) required for GitHub upload"
            )
            return 1
        print(f"🐙 GitHub upload mode: Images will be uploaded to {args.github_repo}")
    else:
        github_token = None

    # Get plot server base URL (skip if using upload modes)
    if args.github_upload:
        plot_server_base_url = ""
        print(
            "🐙 GitHub upload mode: Images will be uploaded to GitHub and embedded in Teams"
        )
    else:
        plot_server_base_url = get_plot_server_base_url()
        print(f"📡 Plot server base URL: {plot_server_base_url}")

        # Check if plot server is accessible
        if args.check_server:
            try:
                response = requests.get(plot_server_base_url, timeout=10)
                if response.status_code != 200:
                    print(
                        f"⚠️  Warning: Plot server returned status {response.status_code}"
                    )
            except requests.exceptions.RequestException as e:
                print(f"⚠️  Warning: Could not reach plot server: {e}")
                print("   Plot links may not be accessible via provided URLs")

    print(f"📁 Plot directory: {args.plot_dir}")
    print(f"🗂️  Benchmark directory: {args.benchmark_dir}")

    if args.check_dp_attention:
        print(
            "🔍 DP attention mode: Checking for RuntimeError and critical errors in server logs"
        )

    if args.enable_torch_compile:
        print(
            "🔥 Torch compile mode: Looking for torch compile benchmark results"
        )

    # Create notifier and discover plots
    notifier = TeamsNotifier(
        webhook_url=webhook_url,
        plot_server_base_url=plot_server_base_url,
        skip_analysis=args.skip_analysis,
        analysis_days=args.analysis_days,
        benchmark_dir=args.benchmark_dir,
        github_upload=args.github_upload,
        github_repo=args.github_repo,
        github_token=github_token,
        check_dp_attention=args.check_dp_attention,
        enable_torch_compile=args.enable_torch_compile,
        benchmark_date=args.benchmark_date,
    )
    plots = notifier.discover_plot_files(args.model, args.mode, args.plot_dir)

    print(f"🔍 Discovered {len(plots)} plot file(s) for {args.model} {args.mode}")
    for plot in plots:
        if plot.get("public_url"):
            service = plot.get("hosting_service", "Unknown")
            print(f"   - {plot['file_name']} -> ✅ uploaded to {service}")
        elif plot.get("plot_url"):
            print(f"   - {plot['file_name']} -> {plot['plot_url']}")
        else:
            print(f"   - {plot['file_name']} -> 📁 {plot['file_path']}")

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
