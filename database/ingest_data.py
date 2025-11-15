#!/usr/bin/env python3
"""
Data Ingestion Script for SGLang CI Dashboard Database

Populates the database from CI logs and benchmark results.
Can run for specific dates or backfill historical data.

USAGE:
    # Ingest data for today
    python ingest_data.py

    # Ingest data for specific date
    python ingest_data.py --date 20251114

    # Ingest data for specific hardware
    python ingest_data.py --hardware mi30x

    # Backfill last 30 days
    python ingest_data.py --backfill 30

    # Backfill specific date range
    python ingest_data.py --from 20251101 --to 20251114
"""

import argparse
import os
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from dashboard.data_collector import DashboardDataCollector
from database.database import DashboardDatabase


class DataIngester:
    """Ingests CI data into the database"""

    def __init__(
        self,
        base_dir: str = "/mnt/raid/michael/sglang-ci",
        db_path: Optional[str] = None,
        github_repo: str = "ROCm/sglang-ci",
    ):
        """
        Initialize data ingester

        Args:
            base_dir: Base directory for CI logs
            db_path: Path to database file (default: dashboard/data/ci_dashboard.db)
            github_repo: GitHub repository in owner/repo format
        """
        self.base_dir = base_dir
        self.github_repo = github_repo

        # Initialize database
        self.db = DashboardDatabase(db_path)

        # Data collectors for each hardware type
        self.collectors = {
            "mi30x": DashboardDataCollector(hardware="mi30x", base_dir=base_dir),
            "mi35x": DashboardDataCollector(hardware="mi35x", base_dir=base_dir),
        }

    def get_docker_image(self, date_str: str, hardware: str) -> Optional[str]:
        """
        Extract docker image from logs

        Args:
            date_str: Date in YYYYMMDD format
            hardware: Hardware type

        Returns:
            Docker image tag or None
        """
        # Try to find docker image from docker_image_check.log
        log_path = os.path.join(
            self.base_dir,
            "cron",
            "cron_log",
            hardware,
            date_str,
            "docker_image_check.log",
        )

        if os.path.exists(log_path):
            try:
                with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read()

                    # Look for docker image pattern
                    match = re.search(
                        r"Docker image:\s*([\w\.\-:/]+)", content, re.IGNORECASE
                    )
                    if match:
                        return match.group(1)

                    # Try another pattern
                    match = re.search(r"Image:\s*([\w\.\-:/]+)", content, re.IGNORECASE)
                    if match:
                        return match.group(1)
            except Exception:
                # Ignore parsing errors, will return None
                pass

        return None

    def get_detail_log_url(
        self, benchmark_name: str, date_str: str, hardware: str
    ) -> str:
        """
        Generate specific detail log URL based on test type - points to actual log file

        Args:
            benchmark_name: Name of the benchmark/test
            date_str: Date in YYYYMMDD format
            hardware: Hardware type

        Returns:
            GitHub URL to specific log file for this test
        """
        # Benchmark tests - find specific result directory with timing_summary log
        if (
            "Online Benchmark" in benchmark_name
            or "DP Attention" in benchmark_name
            or "Torch Compile" in benchmark_name
        ):
            if hardware == "mi35x":
                model = "DeepSeek-R1-MXFP4-Preview"
            else:
                model = "DeepSeek-V3-0324"

            if "Grok 2" in benchmark_name:
                model = "GROK2"
            elif "Grok" in benchmark_name and "Grok 2" not in benchmark_name:
                model = "GROK1"

            # Try to find the specific result directory for this date
            # Check local online/ directory to find the actual result dir name
            online_path = os.path.join(self.base_dir, "online", model)
            result_dir = None

            if os.path.exists(online_path):
                # Find directories matching this date and test type
                for entry in os.listdir(online_path):
                    if date_str in entry and "_online" in entry:
                        # Check if this is the right type (e.g., dp_attention, torch_compile, or plain online)
                        if (
                            "DP Attention" in benchmark_name
                            and "dp_attention" in entry.lower()
                        ):
                            result_dir = entry
                            break
                        elif (
                            "Torch Compile" in benchmark_name
                            and "torch_compile" in entry.lower()
                            and "dp_attention" not in entry.lower()
                        ):
                            result_dir = entry
                            break
                        elif (
                            "DP+Torch Compile" in benchmark_name
                            and "dp_attention" in entry.lower()
                            and "torch_compile" in entry.lower()
                        ):
                            result_dir = entry
                            break
                        elif (
                            "Online Benchmark" in benchmark_name
                            and "dp_attention" not in entry.lower()
                            and "torch_compile" not in entry.lower()
                            and entry.endswith("_online")
                        ):
                            result_dir = entry
                            break

            # If found specific directory, point to it; otherwise point to model directory
            if result_dir:
                return f"https://github.com/{self.github_repo}/tree/log/online_benchmark_log/{hardware}/{model}/{result_dir}"
            else:
                return f"https://github.com/{self.github_repo}/tree/log/online_benchmark_log/{hardware}/{model}"

        # Sanity check - find specific date directory
        elif "Sanity" in benchmark_name:
            sanity_path = os.path.join(
                self.base_dir, "test", "sanity_check_log", hardware
            )
            if os.path.exists(sanity_path):
                # Find directory matching this date
                for entry in os.listdir(sanity_path):
                    if date_str in entry:
                        return f"https://github.com/{self.github_repo}/tree/log/test/sanity_check_log/{hardware}/{entry}"
            return f"https://github.com/{self.github_repo}/tree/log/test/sanity_check_log/{hardware}"

        # PD tests - specific log file
        elif "PD" in benchmark_name or "Disaggregation" in benchmark_name:
            return f"https://github.com/{self.github_repo}/blob/log/cron_log/{hardware}/{date_str}/test_nightly_pd.log"

        # Unit tests - specific log file
        elif "Unit Test" in benchmark_name:
            return f"https://github.com/{self.github_repo}/blob/log/cron_log/{hardware}/{date_str}/test_nightly.log"

        # Docker check - no detail log (only has cron log)
        elif "Docker" in benchmark_name:
            return None  # No detail log for docker check

        # Others - use cron log directory
        else:
            return f"https://github.com/{self.github_repo}/tree/log/cron_log/{hardware}/{date_str}"

    def parse_runtime(self, runtime_str: Optional[str]) -> Optional[int]:
        """
        Parse runtime string to minutes

        Args:
            runtime_str: Runtime string (e.g., "2h 30m", "45m")

        Returns:
            Runtime in minutes or None
        """
        if not runtime_str:
            return None

        try:
            hours = 0
            minutes = 0

            if "h" in runtime_str:
                parts = runtime_str.split("h")
                hours = int(parts[0].strip())
                if len(parts) > 1 and "m" in parts[1]:
                    minutes = int(parts[1].strip().replace("m", ""))
            elif "m" in runtime_str:
                minutes = int(runtime_str.replace("m", "").strip())

            return hours * 60 + minutes
        except Exception:
            return None

    def get_log_urls(
        self, date_str: str, hardware: str, log_type: str, log_name: str
    ) -> tuple:
        """
        Generate local path and GitHub URL for a log file

        Args:
            date_str: Date in YYYYMMDD format
            hardware: Hardware type
            log_type: Log type (cron, sanity, benchmark)
            log_name: Log file name

        Returns:
            Tuple of (local_path, github_url)
        """
        if log_type == "cron":
            local_path = os.path.join(
                self.base_dir, "cron", "cron_log", hardware, date_str, log_name
            )
            github_url = f"https://raw.githubusercontent.com/{self.github_repo}/log/cron_log/{hardware}/{date_str}/{log_name}"
        elif log_type == "sanity":
            # Sanity logs are in test/sanity_check_log/{hardware}/{docker_tag}/
            # For now, we'll use a pattern match
            local_path = None
            github_url = None
        else:
            local_path = None
            github_url = None

        # Check if local path exists
        if local_path and not os.path.exists(local_path):
            local_path = None

        return local_path, github_url

    def get_plot_urls(
        self, date_str: str, hardware: str, model_dir: str, suffix: str
    ) -> tuple:
        """
        Generate local path and GitHub URL for a plot file

        Args:
            date_str: Date in YYYYMMDD format
            hardware: Hardware type
            model_dir: Model directory name
            suffix: Plot suffix (standard, all, etc.)

        Returns:
            Tuple of (local_path, github_url)
        """
        plot_filename = f"{date_str}_{model_dir}_online_{suffix}.png"

        local_path = os.path.join(
            self.base_dir, "plots_server", model_dir, "online", plot_filename
        )

        github_url = f"https://raw.githubusercontent.com/{self.github_repo}/log/plot/{hardware}/{model_dir}/online/{plot_filename}"

        # Check if local path exists
        if not os.path.exists(local_path):
            local_path = None

        return local_path, github_url

    def ingest_date(self, date_str: str, hardware: str, verbose: bool = True):
        """
        Ingest data for a specific date and hardware

        Args:
            date_str: Date in YYYYMMDD format
            hardware: Hardware type (mi30x, mi35x)
            verbose: Print progress messages
        """
        if verbose:
            print(f"Ingesting data for {date_str} ({hardware})...")

        collector = self.collectors[hardware]

        try:
            # Collect task results and sanity checks
            task_results = collector.collect_task_results(date_str)
            sanity_results = collector.parse_sanity_check_log(date_str)

            # Calculate summary stats
            stats = collector.calculate_summary_stats(task_results, sanity_results)

            # Get docker image
            docker_image = self.get_docker_image(date_str, hardware)

            # Extract actual start time from logs
            run_datetime_pt = None

            # Try to parse from latest cron log or detail log
            # Priority: Detail log (more accurate) > Cron log
            from datetime import datetime

            # Try detail logs first (timing_summary from online benchmarks)
            online_dirs = []
            if hardware == "mi35x":
                model_names = ["DeepSeek-R1-MXFP4-Preview", "GROK2", "GROK1"]
            else:
                model_names = ["DeepSeek-V3-0324", "GROK2", "GROK1"]

            for model_name in model_names:
                online_path = os.path.join(self.base_dir, "online", model_name)
                if os.path.exists(online_path):
                    # Find directories matching this date
                    for entry in os.listdir(online_path):
                        if date_str in entry and "_online" in entry:
                            online_dirs.append(os.path.join(online_path, entry))

            # Sort by modification time (latest first)
            online_dirs.sort(
                key=lambda x: os.path.getmtime(x) if os.path.exists(x) else 0,
                reverse=True,
            )

            for online_dir in online_dirs:
                timing_logs = [
                    f
                    for f in os.listdir(online_dir)
                    if f.startswith("timing_summary") and f.endswith(".log")
                ]
                if timing_logs:
                    timing_log = os.path.join(online_dir, timing_logs[0])
                    try:
                        with open(
                            timing_log, "r", encoding="utf-8", errors="ignore"
                        ) as f:
                            content = f.read(2000)  # Read first 2KB
                            match = re.search(
                                r"Script started at:\s*(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})\s*(PST|PT)",
                                content,
                            )
                            if match:
                                timestamp_str = match.group(1)
                                dt = datetime.strptime(
                                    timestamp_str, "%Y-%m-%d %H:%M:%S"
                                )
                                run_datetime_pt = dt.strftime("%Y-%m-%d %I:%M %p PT")
                                break
                    except Exception:
                        # Ignore parsing errors, continue to next log
                        pass

            # Fallback to cron log if detail log didn't have timestamp
            if not run_datetime_pt:
                cron_logs = [
                    "deepseek_nightly_online.log",
                    "grok2_nightly_online.log",
                    "grok_nightly.log",
                ]
                for log_name in cron_logs:
                    cron_log = os.path.join(
                        self.base_dir, "cron", "cron_log", hardware, date_str, log_name
                    )
                    if os.path.exists(cron_log):
                        try:
                            with open(
                                cron_log, "r", encoding="utf-8", errors="ignore"
                            ) as f:
                                content = f.read(2000)
                                match = re.search(
                                    r"Start time:\s*(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})\s*(PST|PT)",
                                    content,
                                )
                                if match:
                                    timestamp_str = match.group(1)
                                    dt = datetime.strptime(
                                        timestamp_str, "%Y-%m-%d %H:%M:%S"
                                    )
                                    run_datetime_pt = dt.strftime(
                                        "%Y-%m-%d %I:%M %p PT"
                                    )
                                    break
                        except Exception:
                            # Ignore parsing errors, continue to next log
                            pass

            # Final fallback - use date only
            if not run_datetime_pt:
                run_datetime_pt = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]} (PT)"

            # Generate GitHub log URLs
            # Main summary log (daily_summary_alert.log)
            github_log_url = f"https://github.com/{self.github_repo}/blob/log/cron_log/{hardware}/{date_str}/daily_summary_alert.log"

            # Cron log varies by main test type
            # Use the primary benchmark log as the cron log (most comprehensive)
            if hardware == "mi35x":
                cron_log_file = "deepseek_nightly_online.log"  # Main DeepSeek run
            else:
                cron_log_file = "deepseek_nightly_online.log"  # Main DeepSeek run

            github_cron_log_url = f"https://github.com/{self.github_repo}/blob/log/cron_log/{hardware}/{date_str}/{cron_log_file}"

            # Detail logs organized by test type:
            # - Benchmarks: online/{model}/
            # - Sanity: test/sanity_check_log/{hardware}/
            # - PD: test/pd/pd_log/{hardware}/
            # - Unit tests: test/unit-test-backend-8-gpu-CAR-amd/{hardware}/
            # For now, point to main online logs directory
            if hardware == "mi35x":
                model_name = "DeepSeek-R1-MXFP4-Preview"
            else:
                model_name = "DeepSeek-V3-0324"

            # Main detail log is the online benchmark directory
            github_detail_log_url = (
                f"https://github.com/{self.github_repo}/tree/log/online/{model_name}"
            )

            # Generate plot GitHub URL (first available plot)
            plot_github_url = None
            if hardware == "mi35x":
                model_dir = "DeepSeek-R1-MXFP4-Preview"
                suffix = "all"
            else:
                model_dir = "DeepSeek-V3-0324"
                suffix = "standard"

            plot_filename = f"{date_str}_{model_dir}_online_{suffix}.png"
            plot_github_url = f"https://github.com/{self.github_repo}/blob/log/plot/{hardware}/{model_dir}/online/{plot_filename}"

            # Create or update test run
            test_run_id = self.db.upsert_test_run(
                run_date=date_str,
                hardware=hardware,
                docker_image=docker_image,
                overall_status=stats["overall_status"],
                total_tasks=stats["total_tasks"],
                passed_tasks=stats["passed_tasks"],
                failed_tasks=stats["failed_tasks"],
                unknown_tasks=stats["unknown_tasks"],
                not_run=stats["not_run"],
                run_datetime_pt=run_datetime_pt,
                github_log_url=github_log_url,
                github_cron_log_url=github_cron_log_url,
                github_detail_log_url=github_detail_log_url,
                plot_github_url=plot_github_url,
            )

            # Ingest benchmark results and validation tests
            # Note: We store all test types in benchmark_results for unified filtering
            all_test_tasks = [
                # Validation & Checks
                "Unit Tests",
                "PD Disaggregation Tests",
                "Docker Image Check",
                # Performance Benchmarks
                "Grok Online Benchmark",
                "Grok 2 Online Benchmark",
                "DeepSeek Online Benchmark",
                # Integration Tests
                "DeepSeek DP Attention Test",
                "DeepSeek Torch Compile Test",
                "DeepSeek DP+Torch Compile",
            ]

            for task_name in all_test_tasks:
                if task_name in task_results:
                    result = task_results[task_name]

                    # Generate detail log URL for this specific test (even if not run)
                    detail_log_url = self.get_detail_log_url(
                        task_name, date_str, hardware
                    )

                    # Store all tasks, including those that didn't run (exists=False)
                    # This ensures the dashboard can show "not run" status and accurate task counts
                    self.db.upsert_benchmark_result(
                        test_run_id=test_run_id,
                        benchmark_name=task_name,
                        status=result["status"],
                        gsm8k_accuracy=result.get("gsm8k_accuracy"),
                        runtime_minutes=self.parse_runtime(result.get("runtime")),
                        error_message=result.get("error"),
                        timing_log_path=None,  # Could extract from logs
                        github_detail_log_url=detail_log_url,
                    )

            # Ingest sanity check results
            if sanity_results:
                for model_name, model_result in sanity_results["model_results"].items():
                    self.db.upsert_sanity_check_result(
                        test_run_id=test_run_id,
                        model_name=model_name,
                        status=model_result["status"],
                        accuracy=model_result.get("accuracy"),
                    )

            # Ingest log file references
            cron_log_files = [
                "test_nightly.log",
                "test_nightly_pd.log",
                "sanity_check_nightly.log",
                "docker_image_check.log",
            ]

            for log_name in cron_log_files:
                local_path, github_url = self.get_log_urls(
                    date_str, hardware, "cron", log_name
                )

                if local_path or github_url:
                    self.db.upsert_log_file(
                        test_run_id=test_run_id,
                        log_type="cron",
                        log_name=log_name,
                        local_path=local_path,
                        github_url=github_url,
                    )

            # Ingest plot file references
            if hardware == "mi35x":
                plot_map = {
                    "Grok Online Benchmark": ("GROK1", ["standard"]),
                    "Grok 2 Online Benchmark": ("GROK2", ["standard"]),
                    "DeepSeek Online Benchmark": (
                        "DeepSeek-R1-MXFP4-Preview",
                        ["all"],  # Only "all" view for mi35x
                    ),
                }
            else:  # mi30x
                plot_map = {
                    "Grok Online Benchmark": ("GROK1", ["standard"]),
                    "Grok 2 Online Benchmark": ("GROK2", ["standard"]),
                    "DeepSeek Online Benchmark": ("DeepSeek-V3-0324", ["standard"]),
                }

            for benchmark_name, (model_dir, suffixes) in plot_map.items():
                for suffix in suffixes:
                    local_path, github_url = self.get_plot_urls(
                        date_str, hardware, model_dir, suffix
                    )

                    # For mi35x, prioritize GitHub URLs even if local path doesn't exist
                    if hardware == "mi35x":
                        # Always generate GitHub URL for mi35x plots
                        if not github_url:
                            plot_filename = (
                                f"{date_str}_{model_dir}_online_{suffix}.png"
                            )
                            github_url = f"https://raw.githubusercontent.com/{self.github_repo}/log/plot/{hardware}/{model_dir}/online/{plot_filename}"

                    if local_path or github_url:
                        self.db.upsert_plot_file(
                            test_run_id=test_run_id,
                            benchmark_name=benchmark_name,
                            plot_suffix=suffix,
                            local_path=local_path,
                            github_url=github_url,
                        )

            if verbose:
                print(
                    f"  ✅ Ingested: {stats['passed_tasks']}/{stats['total_tasks']} tasks passed"
                )

        except Exception as e:
            if verbose:
                print(f"  ❌ Error: {str(e)}")

    def backfill_dates(
        self,
        hardware: str,
        days: int = 30,
        from_date: Optional[str] = None,
        to_date: Optional[str] = None,
    ):
        """
        Backfill data for multiple dates

        Args:
            hardware: Hardware type
            days: Number of days to backfill (from today)
            from_date: Start date in YYYYMMDD format (overrides days)
            to_date: End date in YYYYMMDD format (default: today)
        """
        if from_date and to_date:
            # Parse date range
            start_date = datetime.strptime(from_date, "%Y%m%d")
            end_date = datetime.strptime(to_date, "%Y%m%d")

            dates = []
            current = start_date
            while current <= end_date:
                dates.append(current.strftime("%Y%m%d"))
                current += timedelta(days=1)
        else:
            # Generate dates from last N days
            today = datetime.now()
            dates = []
            for i in range(days):
                date = today - timedelta(days=i)
                dates.append(date.strftime("%Y%m%d"))

            dates.reverse()  # Process oldest to newest

        print(f"Backfilling {len(dates)} dates for {hardware}...")

        for date_str in dates:
            self.ingest_date(date_str, hardware)

        print(f"✅ Backfill complete for {hardware}")


def main():
    """Main function"""
    parser = argparse.ArgumentParser(
        description="Ingest CI data into dashboard database",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    parser.add_argument(
        "--date",
        type=str,
        help="Date to ingest in YYYYMMDD format (default: today)",
    )

    parser.add_argument(
        "--hardware",
        type=str,
        choices=["mi30x", "mi35x", "both"],
        default="both",
        help="Hardware type to ingest data for",
    )

    parser.add_argument(
        "--backfill",
        type=int,
        help="Backfill last N days",
    )

    parser.add_argument(
        "--from",
        dest="from_date",
        type=str,
        help="Start date for backfill in YYYYMMDD format",
    )

    parser.add_argument(
        "--to",
        dest="to_date",
        type=str,
        help="End date for backfill in YYYYMMDD format",
    )

    parser.add_argument(
        "--base-dir",
        type=str,
        default=os.environ.get("SGL_BENCHMARK_CI_DIR", "/mnt/raid/michael/sglang-ci"),
        help="Base directory for CI logs",
    )

    parser.add_argument(
        "--db-path",
        type=str,
        help="Path to database file",
    )

    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress progress messages",
    )

    args = parser.parse_args()

    # Initialize ingester
    ingester = DataIngester(
        base_dir=args.base_dir,
        db_path=args.db_path,
    )

    # Determine hardware types to process
    hardware_types = ["mi30x", "mi35x"] if args.hardware == "both" else [args.hardware]

    # Determine operation mode
    if args.backfill or args.from_date:
        # Backfill mode
        for hardware in hardware_types:
            ingester.backfill_dates(
                hardware=hardware,
                days=args.backfill or 30,
                from_date=args.from_date,
                to_date=args.to_date or datetime.now().strftime("%Y%m%d"),
            )
    else:
        # Single date mode
        date_str = args.date or datetime.now().strftime("%Y%m%d")

        for hardware in hardware_types:
            ingester.ingest_date(date_str, hardware, verbose=not args.quiet)

    # Vacuum database to optimize
    if not args.quiet:
        print("Optimizing database...")
    ingester.db.vacuum()

    if not args.quiet:
        print("✅ Done!")


if __name__ == "__main__":
    main()
