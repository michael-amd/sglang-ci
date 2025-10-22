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
    python send_teams_notification.py --model deepseek --mode online --enable-dp-test --enable-mtp-test
    python send_teams_notification.py --model deepseek --mode online --benchmark-date 20251009 \
        --benchmark-dir /mnt/raid/michael/sglang-ci --enable-dp-test --enable-mtp-test \
        --webhook-url "https://teams.webhook.url"
    python send_teams_notification.py --webhook-url "https://teams.webhook.url"
    python send_teams_notification.py --model grok2 --mode online --github-upload --github-repo "user/repo"
    python send_teams_notification.py --model grok2 --mode online --benchmark-date 20250922
    python send_teams_notification.py --test-mode --webhook-url "https://teams.webhook.url"
    python send_teams_notification.py --mode sanity --docker-image "v0.5.3rc0-rocm700-mi30x-20251011" --webhook-url "https://teams.webhook.url"
    python send_teams_notification.py --mode sanity --docker-image "v0.5.3rc0-rocm700-mi35x-20251011" --webhook-url "https://teams.webhook.url"

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
from typing import Dict, List, Optional, Set, Tuple

import requests

MODEL_NAME_VARIANTS = {
    "grok": ["GROK1"],
    "grok2": ["GROK2"],
    "deepseek": [
        "DeepSeek-R1-MXFP4-Preview",
        "DeepSeek-V3",
        "DeepSeek-V3-0324",
    ],
    "DeepSeek-V3": ["DeepSeek-V3", "DeepSeek-V3-0324"],
}


def _format_duration(seconds: Optional[int]) -> Optional[str]:
    """Convert seconds to a compact human-readable string."""
    if seconds is None:
        return None

    try:
        seconds = int(seconds)
    except (TypeError, ValueError):
        return None

    if seconds < 0:
        seconds = 0

    minutes, rem_seconds = divmod(seconds, 60)
    hours, rem_minutes = divmod(minutes, 60)

    parts: List[str] = []
    if hours:
        parts.append(f"{hours}h")
    if rem_minutes:
        parts.append(f"{rem_minutes}m")
    if rem_seconds or not parts:
        parts.append(f"{rem_seconds}s")

    return " ".join(parts)


def _normalize_detail_text(text: str) -> str:
    """Create a case-insensitive, punctuation-light key for duplicate suppression."""
    cleaned = re.sub(r"[•*`_]+", " ", text)
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned.strip().lower()


"""Utility: load accuracy thresholds from the local sanity_check test script.

We *cannot* rely on ``from test.sanity_check import ...`` because the ``test``
module name collides with Python’s stdlib package of the same name. Instead we
dynamically load the file via its absolute path relative to this repository
root.  The code is wrapped in a broad ``try`` so that the notifier still works
when the file is missing (for example, in a stripped-down deployment).
"""

from importlib import util as _importlib_util


def _load_model_criteria() -> dict[str, float]:
    repo_root = Path(__file__).resolve().parent.parent  # <repo>/
    sanity_path = repo_root / "test" / "sanity_check.py"

    if not sanity_path.exists():
        return {}

    spec = _importlib_util.spec_from_file_location("_sg_sanity_check", sanity_path)
    if spec is None or spec.loader is None:
        return {}

    module = _importlib_util.module_from_spec(spec)  # type: ignore[arg-type]
    try:
        spec.loader.exec_module(module)  # type: ignore[attr-defined]
    except Exception:
        return {}

    DEFAULT_MODELS = getattr(module, "DEFAULT_MODELS", {})
    return {
        name: cfg.get("criteria", {}).get("accuracy")
        for name, cfg in DEFAULT_MODELS.items()
        if isinstance(cfg, dict)
    }


_MODEL_CRITERIA = _load_model_criteria()

try:
    import pytz

    PYTZ_AVAILABLE = True
except ImportError:
    PYTZ_AVAILABLE = False
    print("⚠️  Warning: pytz not available, using UTC time instead of Pacific time")


class BenchmarkAnalyzer:
    """Analyze benchmark results for accuracy and performance regressions"""

    def __init__(
        self,
        base_dir: Optional[str] = None,
        check_dp_attention: bool = False,
        enable_torch_compile: bool = False,
        enable_dp_test: bool = False,
        enable_mtp_test: bool = False,
        benchmark_date: Optional[str] = None,
    ):
        # Use the provided base_dir, environment variable BENCHMARK_BASE_DIR, or a default path
        self.base_dir = base_dir or os.getenv(
            "BENCHMARK_BASE_DIR", os.path.expanduser("~/sglang-ci")
        )
        self.offline_dir = os.path.join(self.base_dir, "offline")
        self.online_dir = os.path.join(self.base_dir, "online")
        self.check_dp_attention = check_dp_attention
        self.enable_torch_compile = enable_torch_compile
        self.enable_dp_test = enable_dp_test
        self.enable_mtp_test = enable_mtp_test
        self.benchmark_date = benchmark_date

    def get_model_variants(self, model: str) -> List[str]:
        """Return ordered list of directory names to search for a model."""
        variants = MODEL_NAME_VARIANTS.get(model)
        if variants:
            return variants

        fallback = [model.upper()]
        if model not in fallback:
            fallback.append(model)
        lower = model.lower()
        if lower not in fallback:
            fallback.append(lower)
        return list(dict.fromkeys(fallback))

    def to_relative_path(self, path: Optional[str]) -> Optional[str]:
        """Convert absolute paths under the benchmark directory to a friendly display path."""
        if not path:
            return None

        try:
            base_dir = self.base_dir
            if not base_dir:
                return path

            normalized_base = os.path.abspath(base_dir)
            normalized_path = os.path.abspath(path)

            common_root = os.path.commonpath([normalized_base, normalized_path])
            if common_root == normalized_base:
                relative_path = os.path.relpath(normalized_path, normalized_base)
                return f"/sglang-ci/{relative_path}".rstrip("/")

        except Exception:
            return path

        return path

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

    def _find_timing_summary_log(
        self, model: str, mode: str, date_str: str
    ) -> Optional[str]:
        """
        Find timing_summary log file for the given model, mode, and date

        Args:
            model: Model name (grok, grok2, deepseek, DeepSeek-V3)
            mode: Benchmark mode (online, offline)
            date_str: Date string (YYYYMMDD)

        Returns:
            Path to timing_summary log file or None if not found or benchmark didn't run for this date
        """
        # Build mode suffix for DP attention, torch compile, and MTP flags
        mode_suffix = ""
        if self.check_dp_attention:
            mode_suffix += "_dp_attention"
        if self.enable_torch_compile:
            mode_suffix += "_torch_compile"
        if self.enable_mtp_test:
            mode_suffix += "_mtp_test"

        suffix_candidates: List[str] = []
        if mode_suffix:
            suffix_candidates.append(f"{mode}{mode_suffix}")
        suffix_candidates.append(mode)

        search_root = self.online_dir if mode == "online" else self.offline_dir
        model_variants = self.get_model_variants(model)

        for model_name in model_variants:
            timing_logs: List[str] = []
            variant_root = os.path.join(search_root, model_name)

            for suffix in suffix_candidates:
                # Match directories that include the expected suffix (e.g., _online_dp_attention_mtp_test)
                patterns = [
                    f"{variant_root}/*{date_str}*{suffix}*/timing_summary_*.log",
                    f"{variant_root}/*{date_str}*{suffix}/timing_summary_*.log",
                ]
                for pattern in patterns:
                    timing_logs.extend(glob.glob(pattern))

            # Fallback: look for any timing summary that contains the date if suffix match failed
            if not timing_logs:
                fallback_pattern = f"{variant_root}/*{date_str}*/timing_summary_*.log"
                timing_logs.extend(glob.glob(fallback_pattern))

            if timing_logs:
                timing_logs.sort(key=os.path.getmtime, reverse=True)
                print(
                    "   Found timing_summary log for "
                    f"{model} {mode} on {date_str}: {os.path.basename(timing_logs[0])}"
                )
                return timing_logs[0]

        print(
            f"   No timing_summary log found for {model} {mode} on {date_str} - benchmark may not have run yet"
        )
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
                        errors.append(
                            f"RuntimeError: {error_count} error(s) found in server logs"
                        )

                        # Try to extract specific error messages
                        error_lines = re.findall(r"    (RuntimeError:.*)", content)
                        for error_line in error_lines[:3]:  # Limit to first 3 errors
                            errors.append(error_line.strip())

            # Check for critical errors
            critical_error_match = re.search(r"Critical error count: (\d+)", content)
            if critical_error_match:
                error_count = int(critical_error_match.group(1))
                if error_count > 0:
                    errors.append(
                        f"Critical errors: {error_count} error(s) found in server logs"
                    )

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
            "server_startup_seconds": None,
            "gsm8k_duration_seconds": None,
            "serving_total_seconds": None,
            "serving_per_concurrency": {},
            "dp_test_enabled": False,
            "mtp_enabled": False,
            "mtp_output_dir": None,
            "mtp_csv": None,
            "mtp_csv_status": None,
            "mtp_plot": None,
            "mtp_plot_status": None,
        }

        timing_log_file = self._find_timing_summary_log(model, mode, date_str)
        if timing_log_file:
            info = self._extract_additional_info_from_log(timing_log_file)

            # Merge all information from timing_summary log
            for key, value in info.items():
                if key not in result:
                    result[key] = value
                    continue

                if isinstance(value, dict):
                    if not value:
                        continue
                    existing = result.get(key) or {}
                    merged = {**existing, **value}
                    result[key] = merged
                    continue

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
            "server_startup_seconds": None,
            "gsm8k_duration_seconds": None,
            "serving_total_seconds": None,
            "serving_per_concurrency": {},
            "dp_test_enabled": False,
            "mtp_enabled": False,
            "mtp_output_dir": None,
            "mtp_csv": None,
            "mtp_csv_status": None,
            "mtp_plot": None,
            "mtp_plot_status": None,
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
            torch_compile_match = re.search(
                r"Torch Compile:\s*(true|false)", content, re.IGNORECASE
            )
            if torch_compile_match:
                info["torch_compile"] = torch_compile_match.group(1).lower() == "true"

            # Capture MTP configuration flag
            mtp_enabled_match = re.search(
                r"MTP Test Enabled:\s*(true|false)", content, re.IGNORECASE
            )
            if mtp_enabled_match:
                info["mtp_enabled"] = mtp_enabled_match.group(1).lower() == "true"

            # Capture DP test configuration flag
            dp_test_enabled_match = re.search(
                r"DP Test Enabled:\s*(true|false)", content, re.IGNORECASE
            )
            if dp_test_enabled_match:
                info["dp_test_enabled"] = (
                    dp_test_enabled_match.group(1).lower() == "true"
                )

            # Extract server startup time
            startup_match = re.search(
                r"Server startup time:\s*(\d+)\s*seconds", content
            )
            if startup_match:
                info["server_startup_seconds"] = int(startup_match.group(1))

            # Extract GSM8K total duration
            gsm_match = re.search(
                r"GSM8K Test Results:\s*(?:\n\s+.+)*?\n\s+Total duration:\s*(\d+)\s*seconds",
                content,
            )
            if gsm_match:
                info["gsm8k_duration_seconds"] = int(gsm_match.group(1))

            # Extract serving benchmark duration and per-concurrency breakdown
            serving_total_match = re.search(
                r"Serving Benchmark Results:\s*(?:\n\s+.+)*?\n\s+Total duration:\s*(\d+)\s*seconds",
                content,
            )
            if serving_total_match:
                info["serving_total_seconds"] = int(serving_total_match.group(1))

            per_concurrency_matches = re.findall(
                r"Completed concurrency\s+(\d+)\s+-\s+Total time:\s*(\d+)\s*seconds",
                content,
            )
            if per_concurrency_matches:
                per_concurrency: Dict[str, int] = {}
                total_from_breakdown = 0
                for conc, secs in per_concurrency_matches:
                    seconds = int(secs)
                    per_concurrency[conc] = seconds
                    total_from_breakdown += seconds
                info["serving_per_concurrency"] = per_concurrency
                if info["serving_total_seconds"] is None and total_from_breakdown:
                    info["serving_total_seconds"] = total_from_breakdown

            # Capture MTP artifact paths/status block when present
            mtp_section_match = re.search(
                r"MTP Benchmark Outputs:\s*\n((?:\s{2}.+\n)+)",
                content,
            )
            if mtp_section_match:
                info["mtp_enabled"] = True
                mtp_block = mtp_section_match.group(1)
                for line in mtp_block.splitlines():
                    stripped = line.strip()
                    if not stripped:
                        continue

                    lower = stripped.lower()
                    if lower.startswith("output directory:"):
                        info["mtp_output_dir"] = (
                            stripped.split(":", 1)[1].strip() or None
                        )
                    elif lower.startswith("csv:"):
                        value = stripped.split(":", 1)[1].strip()
                        if not value:
                            continue
                        lowered_value = value.lower()
                        if lowered_value.startswith("not ") or lowered_value.startswith(
                            "failed"
                        ):
                            info["mtp_csv"] = None
                            info["mtp_csv_status"] = value
                        else:
                            info["mtp_csv"] = value
                            info["mtp_csv_status"] = "Generated"
                    elif lower.startswith("plot:"):
                        value = stripped.split(":", 1)[1].strip()
                        if not value:
                            continue
                        lowered_value = value.lower()
                        if lowered_value.startswith("not ") or lowered_value.startswith(
                            "failed"
                        ):
                            info["mtp_plot"] = None
                            info["mtp_plot_status"] = value
                        else:
                            info["mtp_plot"] = value
                            info["mtp_plot_status"] = "Generated"

            # Extract total runtime from timing summary logs (preferred method)
            runtime_match = re.search(
                r"Total execution time: (\d+) seconds \((\d+) minutes\)", content
            )
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
        # Build mode suffix for DP attention, torch compile, and MTP flags
        mode_suffix = ""
        if self.check_dp_attention:
            mode_suffix += "_dp_attention"
        if self.enable_torch_compile:
            mode_suffix += "_torch_compile"
        if self.enable_mtp_test:
            mode_suffix += "_mtp_test"

        suffix = f"online{mode_suffix}"
        model_variants = self.get_model_variants(model)

        for model_name in model_variants:
            variant_root = os.path.join(self.online_dir, model_name)
            csv_patterns = [
                f"{variant_root}/*{date_str}*{model_name}*{suffix}*/*.csv",
                f"{variant_root}/*{date_str}*{model.lower()}*{suffix}*/*.csv",
                f"{variant_root}/*{date_str}*{suffix}*/*.csv",
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


def find_sanity_check_log(
    docker_image: str,
    base_log_root: str = "/mnt/raid/michael/sglang-ci/test/sanity_check_log",
) -> Optional[str]:
    """
    Find the most recent timing summary log file for a given Docker image

    Args:
        docker_image: Docker image tag (e.g., "v0.5.3rc0-rocm630-mi30x-20250929")
        base_log_root: Root directory (`.../test`). The function selects the
            subfolder matching the hardware extracted from *docker_image*.

    Returns:
        Path to the most recent timing summary log file, or None if not found
    """
    # Extract date from docker image tag (last 8 digits)
    date_match = re.search(r"(\d{8})$", docker_image)
    if not date_match:
        print(f"❌ Error: Could not extract date from Docker image tag: {docker_image}")
        return None

    image_date = date_match.group(1)

    # Determine hardware (mi30x / mi35x) from the image tag
    hw_match = re.search(r"mi[0-9]+x", docker_image)
    hardware = hw_match.group(0) if hw_match else "mi30x"

    # Construct the log directory path: test/sanity_check_log/<hardware>/<image-tag>
    log_dir = os.path.join(base_log_root, hardware, docker_image)

    if not os.path.exists(log_dir):
        print(f"❌ Error: Log directory not found: {log_dir}")
        return None

    # Find all timing_summary logs (try matching date first, then any log in directory)
    pattern = os.path.join(log_dir, f"timing_summary_{image_date}_*.log")
    log_files = glob.glob(pattern)

    # If no logs found matching the image date, try finding any timing_summary logs
    if not log_files:
        pattern = os.path.join(log_dir, "timing_summary_*.log")
        log_files = glob.glob(pattern)

    if not log_files:
        print(f"❌ Error: No timing summary logs found in {log_dir}")
        return None

    # Return the most recent log file (sorted by timestamp in filename)
    most_recent = sorted(log_files)[-1]
    print(f"📋 Found sanity check log: {most_recent}")
    return most_recent


def parse_sanity_check_log(log_file_path: str) -> Dict:
    """
    Parse the sanity check timing summary log file

    Args:
        log_file_path: Path to the timing_summary log file

    Returns:
        Dictionary containing parsed sanity check information
    """
    parsed_data = {
        "status": "unknown",
        "docker_image": None,
        "platform": None,
        "models": [],
        "total_models": 0,
        "trials": None,
        "total_time": None,
        "start_time": None,
        "end_time": None,
        "model_results": {},  # {model_name: {"status": "pass/fail", "accuracies": [], "time": "...", "average_accuracy": X.XX}}
        "passed_count": 0,
        "total_count": 0,
        "tested_count": 0,
        "skipped_count": 0,
    }

    try:
        with open(log_file_path, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()

        # Extract Docker image
        image_match = re.search(r"Docker image:\s*(.+)", content)
        if image_match:
            parsed_data["docker_image"] = image_match.group(1).strip()

        # Extract platform
        platform_match = re.search(r"Platform:\s*(.+)", content)
        if platform_match:
            parsed_data["platform"] = platform_match.group(1).strip()

        # Extract models
        models_match = re.search(r"Models:\s*(.+)", content)
        if models_match:
            models_str = models_match.group(1).strip()
            parsed_data["models"] = [m.strip() for m in models_str.split(",")]
            parsed_data["total_models"] = len(parsed_data["models"])

        # Extract trials per model
        trials_match = re.search(r"Trials per model:\s*(\d+)", content)
        if trials_match:
            parsed_data["trials"] = int(trials_match.group(1))

        # Extract start and end times
        start_match = re.search(r"Start time:\s*(.+?)(?:\n|$)", content)
        if start_match:
            parsed_data["start_time"] = start_match.group(1).strip()

        # Look for end time in OVERALL SUMMARY section
        end_match = re.search(r"End time:\s*(.+?)(?:\n|$)", content)
        if end_match:
            parsed_data["end_time"] = end_match.group(1).strip()

        # Extract total execution time
        total_time_match = re.search(
            r"Total execution time:\s*([\d.]+)s\s*\(([\d.]+)\s*minutes\)", content
        )
        if total_time_match:
            total_seconds = int(float(total_time_match.group(1)))
            hours = total_seconds // 3600
            minutes_part = (total_seconds % 3600) // 60
            seconds = total_seconds % 60

            if hours > 0:
                parsed_data["total_time"] = f"{hours}h {minutes_part}m {seconds}s"
            else:
                parsed_data["total_time"] = f"{minutes_part}m {seconds}s"

        # Extract model results
        # Pattern: === MODEL_NAME on PLATFORM ===
        model_sections = re.findall(
            r"===\s+(\S+)\s+on\s+(\S+)\s+===(.*?)(?====|$)", content, re.DOTALL
        )

        for model_name, _platform, section_content in model_sections:
            result_data = {
                "status": "unknown",
                "accuracies": [],
                "time": None,
                "average_accuracy": None,
            }

            # Extract final result
            result_match = re.search(
                r"Final result:\s*(PASS \[OK\]|FAIL \[X\])", section_content
            )
            if result_match:
                result_data["status"] = (
                    "pass" if "PASS" in result_match.group(1) else "fail"
                )
            else:
                # Check if server startup failed (no final result means server didn't start)
                startup_failed_match = re.search(
                    r"Server startup:\s*FAILED", section_content
                )
                if startup_failed_match:
                    result_data["status"] = "fail"

            # Extract accuracies
            accuracies_match = re.search(
                r"Accuracies:\s*\[([\d.,\s]+)\]", section_content
            )
            if accuracies_match:
                acc_str = accuracies_match.group(1)
                result_data["accuracies"] = [
                    float(a.strip()) for a in acc_str.split(",") if a.strip()
                ]

            # Extract average accuracy from log
            avg_acc_match = re.search(r"Average accuracy:\s*([\d.]+)", section_content)
            if avg_acc_match:
                result_data["average_accuracy"] = float(avg_acc_match.group(1))
            elif result_data["accuracies"]:
                # Calculate average from individual trial accuracies if not in log
                result_data["average_accuracy"] = sum(result_data["accuracies"]) / len(
                    result_data["accuracies"]
                )

            # Extract total time for this model
            model_time_match = re.search(r"Total time:\s*([\d.]+)s", section_content)
            if model_time_match:
                total_sec = int(float(model_time_match.group(1)))
                hours = total_sec // 3600
                minutes = (total_sec % 3600) // 60
                seconds = total_sec % 60

                if hours > 0:
                    result_data["time"] = f"{hours}h {minutes}m {seconds}s"
                else:
                    result_data["time"] = f"{minutes}m {seconds}s"

            parsed_data["model_results"][model_name] = result_data

        # Extract overall summary - models passed count
        tested_match = re.search(r"Models tested:\s*(\d+)/(\d+)", content)
        if tested_match:
            parsed_data["tested_count"] = int(tested_match.group(1))
            parsed_data["total_models"] = max(
                parsed_data.get("total_models", 0), int(tested_match.group(2))
            )

        skipped_match = re.search(r"Models skipped:\s*(\d+)/(\d+)", content)
        if skipped_match:
            parsed_data["skipped_count"] = int(skipped_match.group(1))
            parsed_data["total_models"] = max(
                parsed_data.get("total_models", 0), int(skipped_match.group(2))
            )

        passed_match = re.search(r"Models passed:\s*(\d+)/(\d+)", content)
        if passed_match:
            parsed_data["passed_count"] = int(passed_match.group(1))
            parsed_data["total_count"] = int(passed_match.group(2))
            if parsed_data["tested_count"] == 0:
                parsed_data["tested_count"] = parsed_data["total_count"]
            # Determine overall status
            parsed_data["status"] = (
                "pass"
                if parsed_data["passed_count"] == parsed_data["total_count"]
                else "fail"
            )

        # If we didn't find the summary, count from model results
        if parsed_data["total_count"] == 0 and parsed_data["model_results"]:
            parsed_data["total_count"] = len(parsed_data["model_results"])
            parsed_data["tested_count"] = parsed_data["total_count"]
            parsed_data["passed_count"] = sum(
                1
                for result in parsed_data["model_results"].values()
                if result["status"] == "pass"
            )
            parsed_data["status"] = (
                "pass"
                if parsed_data["passed_count"] == parsed_data["total_count"]
                else "fail"
            )

        # Derive skipped count when not explicitly reported
        if parsed_data["skipped_count"] == 0 and parsed_data["models"]:
            parsed_data["skipped_count"] = max(
                len(parsed_data["models"]) - len(parsed_data["model_results"]), 0
            )

        # Ensure tested count excludes skipped models when total counts are known
        if parsed_data["tested_count"] == 0 and parsed_data["total_models"]:
            parsed_data["tested_count"] = max(
                parsed_data["total_models"] - parsed_data["skipped_count"], 0
            )

        # Keep total_count aligned with tested_count so pass/fail ratios ignore skips
        if parsed_data["tested_count"]:
            parsed_data["total_count"] = parsed_data["tested_count"]

    except FileNotFoundError:
        print(f"❌ Error: Log file not found: {log_file_path}")
        return None
    except Exception as e:
        print(f"❌ Error parsing sanity check log file: {e}")
        import traceback

        traceback.print_exc()
        return None

    return parsed_data


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
        enable_dp_test: bool = False,
        enable_mtp_test: bool = False,
        benchmark_date: Optional[str] = None,
        hardware: str = "mi30x",
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
            enable_dp_test: If True, highlight DeepSeek DP throughput test results
            enable_mtp_test: If True, include DeepSeek MTP throughput artifacts in notifications
            benchmark_date: Date to look for benchmark logs (YYYYMMDD format). If not provided, uses current date.
            hardware: Hardware type (mi30x or mi35x) for GitHub upload path structure
        """
        self.webhook_url = webhook_url
        self.plot_server_base_url = (
            plot_server_base_url.rstrip("/") if plot_server_base_url else ""
        )
        self.skip_analysis = skip_analysis
        self.analysis_days = analysis_days
        self.github_upload = github_upload
        self.github_repo = github_repo or os.environ.get(
            "GITHUB_REPO", "ROCm/sglang-ci"
        )
        self.github_token = github_token
        self.hardware = hardware
        self.check_dp_attention = check_dp_attention
        self.enable_torch_compile = enable_torch_compile
        self.enable_dp_test = enable_dp_test
        self.enable_mtp_test = enable_mtp_test
        self.analyzer = BenchmarkAnalyzer(
            benchmark_dir,
            check_dp_attention,
            enable_torch_compile,
            enable_dp_test,
            enable_mtp_test,
            benchmark_date,
        )

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
            timing_log = self.analyzer._find_timing_summary_log(
                model, mode, current_date
            )
            if timing_log is None:
                alert["status"] = "info"
                alert["title"] = "ℹ️ No Benchmark Run Found"
                alert["details"] = [
                    f"No benchmark results found for {current_date}",
                    "Benchmark may not have run yet for this date",
                ]
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
            if additional_info.get("mtp_output_dir"):
                additional_info["mtp_output_dir_relative"] = (
                    self.analyzer.to_relative_path(additional_info["mtp_output_dir"])
                )
            if additional_info.get("mtp_csv"):
                additional_info["mtp_csv_relative"] = self.analyzer.to_relative_path(
                    additional_info["mtp_csv"]
                )
            if additional_info.get("mtp_plot"):
                additional_info["mtp_plot_relative"] = self.analyzer.to_relative_path(
                    additional_info["mtp_plot"]
                )
            alert["additional_info"] = additional_info

            if model.lower().startswith("deepseek") and (
                self.enable_dp_test or self.enable_mtp_test
            ):
                server_display = _format_duration(
                    additional_info.get("server_startup_seconds")
                )
                if server_display:
                    alert["details"].append(f"Server startup: {server_display}")

                gsm_display = _format_duration(
                    additional_info.get("gsm8k_duration_seconds")
                )
                if gsm_display:
                    alert["details"].append(f"GSM8K runtime: {gsm_display}")

                if self.enable_dp_test or self.enable_mtp_test:
                    serving_display = _format_duration(
                        additional_info.get("serving_total_seconds")
                    )
                    if serving_display:
                        alert["details"].append(f"Serving runtime: {serving_display}")

                    # Serving breakdown details are verbose; omit them from the summary.

            if self.enable_dp_test:
                dp_flag = additional_info.get("dp_test_enabled")
                if dp_flag:
                    alert["details"].append(
                        "DP throughput test: Serving benchmarks executed ✅"
                    )
                elif dp_flag is False:
                    alert["details"].append(
                        "DP throughput test requested but not confirmed in timing log ⚠️"
                    )

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

        # Summarize MTP artifacts when enabled
        if (
            mode == "online"
            and (self.enable_mtp_test or self.enable_dp_test)
            and alert.get("additional_info")
        ):
            additional_info = alert["additional_info"]
            if additional_info.get("mtp_enabled"):
                csv_status = additional_info.get("mtp_csv_status")
                plot_status = additional_info.get("mtp_plot_status")

                if additional_info.get("mtp_csv"):
                    alert["details"].append("MTP CSV artifacts generated ✅")
                elif csv_status:
                    alert["details"].append(f"MTP CSV: {csv_status}")
                else:
                    alert["details"].append("MTP CSV artifacts not available ⚠️")

                if additional_info.get("mtp_plot"):
                    alert["details"].append("MTP latency/throughput plot generated ✅")
                elif not plot_status or "not generated" not in plot_status.lower():
                    if plot_status:
                        alert["details"].append(f"MTP plot: {plot_status}")
                    else:
                        alert["details"].append("MTP plot not available ⚠️")

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

    def create_sanity_check_card(self, parsed_data: Dict) -> dict:
        """
        Create adaptive card for sanity check status

        Args:
            parsed_data: Parsed sanity check data from parse_sanity_check_log()

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

        # Create card body elements
        body_elements = [
            {
                "type": "TextBlock",
                "size": "Large",
                "weight": "Bolder",
                "text": f"{current_date} SGL Sanity Check Results",
            },
            {
                "type": "TextBlock",
                "size": "Small",
                "text": f"Completed on {current_date} at {current_time}",
                "isSubtle": True,
                "spacing": "None",
            },
            # Emit the hostname of the machine that generated the report. This helps
            # operators quickly locate the host that produced a failing sanity
            # check without having to look at the log directory structure.
            {
                "type": "TextBlock",
                "size": "Small",
                "text": f"Hostname: {socket.gethostname()}",
                "isSubtle": True,
                "spacing": "None",
            },
            # The overall pass/fail banner is intentionally omitted to keep the
            # card concise.  Operators can still infer the run health from the
            # per-model summary and the aggregate "models passed" count below.
        ]

        # Add summary section
        passed_count = parsed_data.get("passed_count", 0)
        tested_count = parsed_data.get("tested_count")
        if tested_count is None or tested_count == 0:
            tested_count = parsed_data.get("total_count", 0)
        body_elements.append(
            {
                "type": "TextBlock",
                "text": f"**Models: {passed_count}/{tested_count} passed**",
                "weight": "Bolder",
                "size": "Medium",
                "spacing": "Medium",
            }
        )

        # Add Docker image if provided
        docker_image = parsed_data.get("docker_image")
        if docker_image:
            body_elements.append(
                {
                    "type": "TextBlock",
                    "text": f"• Docker Image: **{docker_image}**",
                    "wrap": True,
                    "size": "Small",
                    "spacing": "Small",
                }
            )

        # Add platform if provided
        platform = parsed_data.get("platform")
        if platform:
            body_elements.append(
                {
                    "type": "TextBlock",
                    "text": f"• Platform: **{platform}**",
                    "wrap": True,
                    "size": "Small",
                    "spacing": "None",
                }
            )

        # Add trials if provided
        trials = parsed_data.get("trials")
        if trials:
            body_elements.append(
                {
                    "type": "TextBlock",
                    "text": f"• Trials per model: **{trials}**",
                    "wrap": True,
                    "size": "Small",
                    "spacing": "None",
                }
            )

        # Add total runtime if provided
        total_time = parsed_data.get("total_time")
        if total_time:
            body_elements.append(
                {
                    "type": "TextBlock",
                    "text": f"• Total Runtime: **{total_time}**",
                    "wrap": True,
                    "size": "Small",
                    "spacing": "None",
                }
            )

        # Add model results section
        model_results = parsed_data.get("model_results", {})
        if model_results:
            body_elements.append(
                {
                    "type": "TextBlock",
                    "text": "**Model Results:**",
                    "weight": "Bolder",
                    "size": "Medium",
                    "spacing": "Medium",
                }
            )

            # Map short model names to full names for display
            model_display_names = {
                "llama4": "Llama-4-Maverick-17B-128E-Instruct-FP8",
                "QWEN-30B": "Qwen3-30B-A3B-Thinking-2507",
            }
            # Hardware-specific model mappings
            hardware_type = None
            docker_image = parsed_data.get("docker_image", "")
            if docker_image:
                hw_match = re.search(r"mi[0-9]+x", docker_image)
                if hw_match:
                    hardware_type = hw_match.group(0)

            if hardware_type == "mi30x":
                model_display_names["GPT-OSS-120B"] = "gpt-oss-120b-bf16"
                model_display_names["GPT-OSS-20B"] = "gpt-oss-20b-bf16"

            for model_name, result in model_results.items():
                model_status = result.get("status", "unknown")
                model_icon = "✅" if model_status == "pass" else "❌"
                model_time = result.get("time", "N/A")
                avg_accuracy = result.get("average_accuracy")

                # Use full model name for display if available
                display_name = model_display_names.get(model_name, model_name)

                # Model name and status
                body_elements.append(
                    {
                        "type": "TextBlock",
                        "text": f"{model_icon} **{display_name}** - {model_time}",
                        "wrap": True,
                        "size": "Small",
                        "spacing": "Small",
                        "color": "Good" if model_status == "pass" else "Attention",
                    }
                )

                # GSM8K accuracy as percentage if available
                if avg_accuracy is not None:
                    accuracy_percent = avg_accuracy * 100

                    # Look up the expected accuracy threshold for this model, if
                    # defined in the sanity_check configuration.
                    threshold = _MODEL_CRITERIA.get(model_name)

                    if threshold is not None:
                        threshold_percent = threshold * 100
                        accuracy_line = (
                            f"  GSM8K accuracy: {accuracy_percent:.1f}% "
                            f"(threshold ≥ {threshold_percent:.1f}%)"
                        )
                    else:
                        accuracy_line = f"  GSM8K accuracy: {accuracy_percent:.1f}%"

                    body_elements.append(
                        {
                            "type": "TextBlock",
                            "text": accuracy_line,
                            "wrap": True,
                            "size": "Small",
                            "spacing": "None",
                            "isSubtle": True,
                        }
                    )

        # Add action buttons
        actions = []

        # Add sanity log link
        # Priority 1: Extract from hardware info (docker image)
        hardware_type = None
        docker_image = parsed_data.get("docker_image", "")
        if docker_image:
            hw_match = re.search(r"mi[0-9]+x", docker_image)
            if hw_match:
                hardware_type = hw_match.group(0)

        # Priority 2: Extract from hostname
        if not hardware_type:
            try:
                hostname_str = socket.gethostname()
                # Try full pattern first (mi30x, mi35x)
                hw_match = re.search(r"mi[0-9]+x", hostname_str)
                if hw_match:
                    hardware_type = hw_match.group(0)
                else:
                    # Try abbreviated patterns and convert to mi30x, mi35x
                    # Pattern 1: 300x, 350x, 355x (with 'x')
                    abbrev_match = re.search(r"([0-9]+)x", hostname_str)
                    if abbrev_match:
                        abbrev = abbrev_match.group(1)
                        # Map 300x -> mi30x, 350x/355x -> mi35x
                        if abbrev in ["300", "30"]:
                            hardware_type = "mi30x"
                        elif abbrev in ["350", "355", "35"]:
                            hardware_type = "mi35x"
                    # Pattern 2: 300, 355 (without 'x')
                    elif not hardware_type:
                        abbrev_match = re.search(
                            r"\b(300|355|350|30|35)\b", hostname_str
                        )
                        if abbrev_match:
                            abbrev = abbrev_match.group(1)
                            # Map 300 -> mi30x, 355/350 -> mi35x
                            if abbrev in ["300", "30"]:
                                hardware_type = "mi30x"
                            elif abbrev in ["350", "355", "35"]:
                                hardware_type = "mi35x"
            except Exception:
                pass

        # For sanity checks, link to the hardware directory
        if hardware_type:
            sanity_log_url = f"https://github.com/{self.github_repo}/tree/log/test/sanity_check_log/{hardware_type}"
            actions.append(
                {
                    "type": "Action.OpenUrl",
                    "title": "📋 Sanity Logs",
                    "url": sanity_log_url,
                }
            )

            # Add cron log link (sanity checks are triggered from cron jobs)
            # Extract date from docker image tag (last 8 digits: YYYYMMDD)
            date_match = re.search(r"(\d{8})$", docker_image)
            if date_match:
                log_date = date_match.group(1)
            else:
                # Fallback to current date if we can't extract from docker image
                log_date = datetime.now().strftime("%Y%m%d")

            cron_log_url = f"https://github.com/{self.github_repo}/tree/log/cron_log/{hardware_type}/{log_date}"
            actions.append(
                {
                    "type": "Action.OpenUrl",
                    "title": "📋 Cron Logs",
                    "url": cron_log_url,
                }
            )

        # Create the adaptive card
        card_content = {
            "type": "AdaptiveCard",
            "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
            "version": "1.4",
            "body": body_elements,
        }

        # Only add actions if the list is not empty
        if actions:
            card_content["actions"] = actions

        card = {
            "type": "message",
            "attachments": [
                {
                    "contentType": "application/vnd.microsoft.card.adaptive",
                    "content": card_content,
                }
            ],
        }

        return card

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

            # Create file path: plot/hardware/model/mode/filename.png
            filename = os.path.basename(image_path)

            # Extract model directory name from the actual image path for accurate naming
            # e.g., /plots_server/DeepSeek-V3-0324/online/file.png -> DeepSeek-V3-0324
            path_parts = image_path.split(os.sep)
            if "plots_server" in path_parts:
                idx = path_parts.index("plots_server")
                if len(path_parts) > idx + 2:
                    model_dir = path_parts[idx + 1]
                else:
                    # Fallback to model variants
                    model_variants = self.analyzer.get_model_variants(model)
                    model_dir = model_variants[0]
            else:
                # Fallback to model variants
                model_variants = self.analyzer.get_model_variants(model)
                model_dir = model_variants[0]

            repo_path = f"plot/{self.hardware}/{model_dir}/{mode}/{filename}"

            # GitHub API endpoint for log branch
            api_url = (
                f"https://api.github.com/repos/{self.github_repo}/contents/{repo_path}"
            )

            headers = {
                "Authorization": f"token {self.github_token}",
                "Accept": "application/vnd.github.v3+json",
            }

            # Check if file already exists on log branch
            params = {"ref": "log"}
            existing_response = requests.get(api_url, headers=headers, params=params)
            sha = None
            if existing_response.status_code == 200:
                sha = existing_response.json().get("sha")
                print(f"   📝 Updating existing file: {repo_path}")
            else:
                print(f"   📄 Creating new file: {repo_path}")

            # Upload or update file on log branch
            current_date = datetime.now().strftime("%Y-%m-%d")
            payload = {
                "message": f"Add {model} {mode} plot for {current_date}",
                "content": base64_content,
                "branch": "log",
            }

            if sha:
                payload["sha"] = sha

            response = requests.put(api_url, json=payload, headers=headers)

            if response.status_code in [200, 201]:
                # Return public URL from log branch
                public_url = f"https://raw.githubusercontent.com/{self.github_repo}/log/{repo_path}"
                print(f"   ✅ Uploaded to GitHub (log branch): {filename}")
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

        model_variants = self.analyzer.get_model_variants(model)
        seen_paths: Set[str] = set()

        # Search through each date (most recent first)
        for search_date in search_dates:
            date_found = False
            for model_name in model_variants:
                variant_dir = os.path.join(plot_dir, model_name, mode)
                search_patterns = [
                    f"{variant_dir}/{search_date}_*_{mode}.png",
                    f"{variant_dir}/{search_date}_*_{mode}_standard.png",
                    f"{variant_dir}/{search_date}_*_{mode}_split.png",
                ]

                for pattern in search_patterns:
                    files = glob.glob(pattern)
                    for file_path in files:
                        if file_path in seen_paths:
                            continue
                        seen_paths.add(file_path)

                        file_name = os.path.basename(file_path)
                        relative_path = file_path.replace(plot_dir, "").lstrip("/")

                        plot_info = {
                            "file_name": file_name,
                            "file_path": file_path,
                            "model": model_name,
                            "mode": mode,
                            "category": "standard",
                        }

                        if self.github_upload:
                            plot_info["public_url"] = self.upload_to_github(
                                file_path, model, mode
                            )
                            if plot_info.get("public_url"):
                                plot_info["hosting_service"] = "GitHub"
                        elif self.plot_server_base_url:
                            plot_info["plot_url"] = (
                                f"{self.plot_server_base_url}/{relative_path}"
                            )

                        plots.append(plot_info)
                        date_found = True

            if date_found:
                break

        # Include MTP-specific plot artifacts when available
        if (self.enable_mtp_test or self.enable_dp_test) and mode == "online":
            mtp_plot_path: Optional[str] = None
            for search_date in search_dates:
                mtp_info = self.analyzer.extract_additional_info(
                    model, mode, search_date
                )
                candidate_path = mtp_info.get("mtp_plot")
                if candidate_path and os.path.exists(candidate_path):
                    mtp_plot_path = candidate_path
                    break

            if mtp_plot_path:
                mtp_plot_info = {
                    "file_name": os.path.basename(mtp_plot_path),
                    "file_path": mtp_plot_path,
                    "model": model_name,
                    "mode": mode,
                    "category": "mtp",
                }

                if self.github_upload:
                    public_url = self.upload_to_github(mtp_plot_path, model, mode)
                    if public_url:
                        mtp_plot_info["public_url"] = public_url
                        mtp_plot_info["hosting_service"] = "GitHub"

                plots.append(mtp_plot_info)

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

        # Separate standard plots from MTP-specific plots for nuanced presentation
        standard_plots: List[Dict[str, str]] = []
        for plot in plots:
            if plot.get("category") == "mtp":
                continue  # Skip dedicated MTP plots to keep the card concise
            # Skip plots without successful upload when GitHub upload mode is enabled
            if self.github_upload and not plot.get("public_url"):
                continue  # Don't show plot info if GitHub upload failed
            standard_plots.append(plot)

        # Create card body elements starting with run name
        body_elements = []

        # Customize title based on enabled modes
        mode_description = []
        if self.enable_mtp_test:
            mode_description.append("MTP")
        if self.enable_dp_test:
            mode_description.append("DP")
        elif self.check_dp_attention:
            # Only show "DP Attention" if enable_dp_test is not set
            # (DP test already includes DP attention)
            mode_description.append("DP Attention")
        if self.enable_torch_compile:
            mode_description.append("Torch Compile")

        if mode_description:
            mode_text = " + ".join(mode_description)
            if (
                self.check_dp_attention
                and not self.enable_torch_compile
                and not self.enable_mtp_test
                and not self.enable_dp_test
            ):
                main_title = (
                    f"{current_date} {model.upper()} {mode.title()} {mode_text} Check"
                )
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
        alert_color = {
            "good": "Good",
            "warning": "Warning",
            "error": "Attention",
            "info": "Default",
        }.get(summary_alert["status"], "Default")

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

        existing_detail_keys: Set[str] = set()

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
                existing_detail_keys.add(_normalize_detail_text(detail))

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

            if self.enable_dp_test:
                dp_flag = additional_info.get("dp_test_enabled")
                if dp_flag:
                    dp_text = "• DP Test: **Enabled** ✅"
                    dp_color = "Good"
                else:
                    dp_text = "• DP Test: Not recorded in timing log ⚠️"
                    dp_color = "Warning"

                if not any(
                    "dp throughput" in detail.lower()
                    for detail in summary_alert.get("details", [])
                ):
                    body_elements.append(
                        {
                            "type": "TextBlock",
                            "text": dp_text,
                            "wrap": True,
                            "size": "Small",
                            "spacing": "None",
                            "color": dp_color,
                        }
                    )

            if model.lower().startswith("deepseek") and (
                self.enable_dp_test or self.enable_mtp_test
            ):
                server_display = _format_duration(
                    additional_info.get("server_startup_seconds")
                )
                if server_display:
                    plain_server = f"Server startup: {server_display}"
                    if _normalize_detail_text(plain_server) not in existing_detail_keys:
                        body_elements.append(
                            {
                                "type": "TextBlock",
                                "text": f"• Server startup: **{server_display}**",
                                "wrap": True,
                                "size": "Small",
                                "spacing": "None",
                            }
                        )
                        existing_detail_keys.add(_normalize_detail_text(plain_server))

                gsm_display = _format_duration(
                    additional_info.get("gsm8k_duration_seconds")
                )
                if gsm_display:
                    plain_gsm = f"GSM8K runtime: {gsm_display}"
                    if _normalize_detail_text(plain_gsm) not in existing_detail_keys:
                        body_elements.append(
                            {
                                "type": "TextBlock",
                                "text": f"• GSM8K runtime: **{gsm_display}**",
                                "wrap": True,
                                "size": "Small",
                                "spacing": "None",
                            }
                        )
                        existing_detail_keys.add(_normalize_detail_text(plain_gsm))

                if self.enable_dp_test or self.enable_mtp_test:
                    serving_display = _format_duration(
                        additional_info.get("serving_total_seconds")
                    )
                    if serving_display:
                        plain_serving = f"Serving runtime: {serving_display}"
                        if (
                            _normalize_detail_text(plain_serving)
                            not in existing_detail_keys
                        ):
                            body_elements.append(
                                {
                                    "type": "TextBlock",
                                    "text": f"• Serving runtime: **{serving_display}**",
                                    "wrap": True,
                                    "size": "Small",
                                    "spacing": "None",
                                }
                            )
                            existing_detail_keys.add(
                                _normalize_detail_text(plain_serving)
                            )

                        # Skip serving breakdown list in the detailed section to keep the card concise.

            # Runtime is already shown in the status details section, so don't duplicate it here

        # Add Plot section only if not in DP attention mode or torch compile mode
        if not self.check_dp_attention and not self.enable_torch_compile:
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

            if not standard_plots:
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
                for i, plot in enumerate(standard_plots, 1):
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

                    # Handle different hosting methods for each plot
                    if plot.get("public_url") and plot.get("hosting_service"):
                        # GitHub hosting - show as clickable link
                        service = plot["hosting_service"]

                        body_elements.append(
                            {
                                "type": "TextBlock",
                                "text": f"🔗 [View Plot]({plot['public_url']}) (hosted on {service})",
                                "wrap": True,
                                "size": "Medium",
                                "spacing": "Small",
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

        # Add cron log link
        # Priority 1: Extract from hardware info (docker image)
        hardware_type = None
        if summary_alert.get("additional_info"):
            docker_image = summary_alert["additional_info"].get("docker_image", "")
            if docker_image:
                hw_match = re.search(r"mi[0-9]+x", docker_image)
                if hw_match:
                    hardware_type = hw_match.group(0)

        # Priority 2: Extract from hostname
        if not hardware_type:
            try:
                hostname_str = socket.gethostname()
                # Try full pattern first (mi30x, mi35x)
                hw_match = re.search(r"mi[0-9]+x", hostname_str)
                if hw_match:
                    hardware_type = hw_match.group(0)
                else:
                    # Try abbreviated patterns and convert to mi30x, mi35x
                    # Pattern 1: 300x, 350x, 355x (with 'x')
                    abbrev_match = re.search(r"([0-9]+)x", hostname_str)
                    if abbrev_match:
                        abbrev = abbrev_match.group(1)
                        # Map 300x -> mi30x, 350x/355x -> mi35x
                        if abbrev in ["300", "30"]:
                            hardware_type = "mi30x"
                        elif abbrev in ["350", "355", "35"]:
                            hardware_type = "mi35x"
                    # Pattern 2: 300, 355 (without 'x')
                    elif not hardware_type:
                        abbrev_match = re.search(
                            r"\b(300|355|350|30|35)\b", hostname_str
                        )
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
        if self.analyzer.benchmark_date:
            log_date = self.analyzer.benchmark_date
        else:
            log_date = datetime.now().strftime("%Y%m%d")

        # Add cron log link if we have hardware type
        if hardware_type:
            cron_log_url = f"https://github.com/{self.github_repo}/tree/log/cron_log/{hardware_type}/{log_date}"
            actions.append(
                {
                    "type": "Action.OpenUrl",
                    "title": "📋 Cron Logs",
                    "url": cron_log_url,
                }
            )

        if self.plot_server_base_url:
            # Add HTTP server links
            pass

        # Only add plot-related actions if not in DP attention mode or torch compile mode
        if not self.check_dp_attention and not self.enable_torch_compile:
            if standard_plots:
                # Add action to view all plots (link to the model's directory)
                model_variants = self.analyzer.get_model_variants(model)
                primary_model_name = model_variants[0]
                # Browse All and Dashboard links removed per user request
                pass

        # Create the adaptive card
        card_content = {
            "type": "AdaptiveCard",
            "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
            "version": "1.4",
            "body": body_elements,
        }

        # Only add actions if the list is not empty
        if actions:
            card_content["actions"] = actions

        card = {
            "type": "message",
            "attachments": [
                {
                    "contentType": "application/vnd.microsoft.card.adaptive",
                    "content": card_content,
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
                # Debug: Print image URLs being sent
                for plot in plots:
                    if plot.get("public_url"):
                        print(f"   📷 Image URL: {plot['public_url']}")
                # Save card JSON for debugging
                debug_file = "/tmp/teams_card_debug.json"
                with open(debug_file, "w") as f:
                    json.dump(card, f, indent=2)
                print(f"   💾 Debug: Card JSON saved to {debug_file}")
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

    def send_sanity_notification(
        self,
        docker_image: str,
        base_log_root: str = "/mnt/raid/michael/sglang-ci/test/sanity_check_log",
    ) -> bool:
        """
        Send sanity check status notification to Teams

        Args:
            docker_image: Docker image tag (e.g., "v0.5.3rc0-rocm630-mi30x-20250929")
            base_log_root: Root directory for sanity check logs (".../test")

        Returns:
            True if successful, False otherwise
        """
        try:
            # Find the log file
            log_file_path = find_sanity_check_log(docker_image, base_log_root)
            if log_file_path is None:
                return False

            # Parse the log file
            print(f"📋 Parsing sanity check log: {log_file_path}")
            parsed_data = parse_sanity_check_log(log_file_path)
            if parsed_data is None:
                return False

            # Create and send the card
            card = self.create_sanity_check_card(parsed_data)
            card_json = json.dumps(card)

            headers = {"Content-Type": "application/json"}

            response = requests.post(
                self.webhook_url, data=card_json, headers=headers, timeout=30
            )

            if response.status_code in [200, 202]:
                print(
                    f"✅ Successfully sent sanity check {parsed_data['status']} alert to Teams"
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
        "--mode",
        type=str,
        choices=["online", "offline", "sanity"],
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
        "--enable-dp-test",
        action="store_true",
        help="Highlight DeepSeek DP throughput test (enables serving benchmark summaries)",
    )
    parser.add_argument(
        "--enable-mtp-test",
        action="store_true",
        help="Include DeepSeek MTP throughput artifacts in the Teams summary",
    )
    parser.add_argument(
        "--benchmark-date",
        type=str,
        help="Date to look for benchmark logs (YYYYMMDD format). If not provided, uses current date.",
    )

    parser.add_argument(
        "--hardware",
        type=str,
        choices=["mi30x", "mi35x"],
        default="mi30x",
        help="Hardware type (mi30x or mi35x) for GitHub upload path structure",
    )

    parser.add_argument(
        "--docker-image",
        type=str,
        help="Docker image tag for sanity check mode (e.g., 'v0.5.3rc0-rocm630-mi30x-20251001')",
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
        notifier = TeamsNotifier(
            webhook_url,
            "",
            False,
            7,
            None,
            False,
            None,
            None,
            False,
            False,
            False,
            False,
            None,
            "mi30x",
        )
        success = notifier.send_test_notification()
        if success:
            print("🎉 Test completed successfully!")
            print("💡 If you see the test message in Teams, adaptive cards work.")
            return 0
        else:
            print("💥 Test failed - check your webhook URL and Teams configuration")
            return 1

    # Handle sanity mode first
    if args.mode == "sanity":
        if not args.docker_image:
            print("❌ Error: --docker-image is required for sanity check mode")
            print("   Example: --docker-image 'v0.5.3rc0-rocm630-mi30x-20251001'")
            return 1

        print("🔍 Sanity check mode: Processing sanity check results")
        notifier = TeamsNotifier(
            webhook_url,
            "",
            False,
            7,
            None,
            False,
            None,
            None,
            False,
            False,
            False,
            False,
            None,
            "mi30x",
        )
        success = notifier.send_sanity_notification(docker_image=args.docker_image)
        return 0 if success else 1

    # Validate required arguments for normal operation
    if not args.model or not args.mode:
        print(
            "❌ Error: --model and --mode are required (unless using --test-mode or --mode sanity)"
        )
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
        print("🔥 Torch compile mode: Looking for torch compile benchmark results")

    if args.enable_dp_test:
        print("🧬 DP test mode: Highlighting DP throughput benchmark and serving logs")

    if args.enable_mtp_test:
        print("🚀 MTP mode: Including DeepSeek R1 throughput artifacts")

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
        enable_dp_test=args.enable_dp_test,
        enable_mtp_test=args.enable_mtp_test,
        benchmark_date=args.benchmark_date,
        hardware=args.hardware,
    )
    plots = notifier.discover_plot_files(args.model, args.mode, args.plot_dir)

    print(f"🔍 Discovered {len(plots)} plot file(s) for {args.model} {args.mode}")
    for plot in plots:
        prefix = "[MTP] " if plot.get("category") == "mtp" else ""
        if plot.get("public_url"):
            service = plot.get("hosting_service", "Unknown")
            print(f"   - {prefix}{plot['file_name']} -> ✅ uploaded to {service}")
        elif plot.get("plot_url"):
            print(f"   - {prefix}{plot['file_name']} -> {plot['plot_url']}")
        else:
            print(f"   - {prefix}{plot['file_name']} -> 📁 {plot['file_path']}")

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
