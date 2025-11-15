"""
Database Module for SGLang CI Dashboard

Provides persistent storage for test results, logs, and plots.
Uses SQLite for local storage with GitHub sync capabilities.

Schema Overview:
- test_runs: Main table tracking each test run
- benchmark_results: Performance benchmark results (GSM8K, runtime, etc.)
- sanity_check_results: Sanity check model results
- log_files: Links to log files (local and GitHub)
- plot_files: Links to plot images (local and GitHub)
"""

import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from typing import Dict, List, Optional


class DashboardDatabase:
    """Database manager for SGLang CI Dashboard"""

    def __init__(self, db_path: str = None):
        """
        Initialize database connection

        Args:
            db_path: Path to SQLite database file (default: /mnt/raid/michael/sglang-ci/database/ci_dashboard.db)
        """
        if db_path is None:
            # Use database folder in sglang-ci root
            base_dir = os.environ.get(
                "SGL_BENCHMARK_CI_DIR", "/mnt/raid/michael/sglang-ci"
            )
            db_path = os.path.join(base_dir, "database", "ci_dashboard.db")

        self.db_path = db_path

        # Ensure directory exists
        os.makedirs(os.path.dirname(db_path), exist_ok=True)

        # Initialize database schema
        self._init_schema()

    @contextmanager
    def get_connection(self):
        """Context manager for database connections"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row  # Enable column access by name
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_schema(self):
        """Initialize database schema"""
        with self.get_connection() as conn:
            cursor = conn.cursor()

            # Test Runs table - main tracking table
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS test_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_date TEXT NOT NULL,
                    hardware TEXT NOT NULL,
                    docker_image TEXT,
                    overall_status TEXT,
                    total_tasks INTEGER DEFAULT 0,
                    passed_tasks INTEGER DEFAULT 0,
                    failed_tasks INTEGER DEFAULT 0,
                    unknown_tasks INTEGER DEFAULT 0,
                    not_run INTEGER DEFAULT 0,
                    run_datetime_pt TEXT,
                    github_log_url TEXT,
                    github_cron_log_url TEXT,
                    github_detail_log_url TEXT,
                    plot_github_url TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(run_date, hardware)
                )
            """
            )

            # Benchmark Results table
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS benchmark_results (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    test_run_id INTEGER NOT NULL,
                    benchmark_name TEXT NOT NULL,
                    status TEXT,
                    gsm8k_accuracy REAL,
                    runtime_minutes INTEGER,
                    error_message TEXT,
                    timing_log_path TEXT,
                    github_detail_log_url TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (test_run_id) REFERENCES test_runs (id) ON DELETE CASCADE,
                    UNIQUE(test_run_id, benchmark_name)
                )
            """
            )

            # Sanity Check Results table
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS sanity_check_results (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    test_run_id INTEGER NOT NULL,
                    model_name TEXT NOT NULL,
                    status TEXT,
                    accuracy REAL,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (test_run_id) REFERENCES test_runs (id) ON DELETE CASCADE,
                    UNIQUE(test_run_id, model_name)
                )
            """
            )

            # Log Files table
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS log_files (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    test_run_id INTEGER NOT NULL,
                    log_type TEXT NOT NULL,
                    log_name TEXT NOT NULL,
                    local_path TEXT,
                    github_url TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (test_run_id) REFERENCES test_runs (id) ON DELETE CASCADE,
                    UNIQUE(test_run_id, log_type, log_name)
                )
            """
            )

            # Plot Files table
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS plot_files (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    test_run_id INTEGER NOT NULL,
                    benchmark_name TEXT NOT NULL,
                    plot_suffix TEXT,
                    local_path TEXT,
                    github_url TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (test_run_id) REFERENCES test_runs (id) ON DELETE CASCADE,
                    UNIQUE(test_run_id, benchmark_name, plot_suffix)
                )
            """
            )

            # Create indexes for common queries
            cursor.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_test_runs_date_hw
                ON test_runs(run_date, hardware)
            """
            )

            cursor.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_benchmark_results_run
                ON benchmark_results(test_run_id)
            """
            )

            cursor.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_sanity_check_results_run
                ON sanity_check_results(test_run_id)
            """
            )

            conn.commit()

    def upsert_test_run(
        self,
        run_date: str,
        hardware: str,
        docker_image: Optional[str] = None,
        overall_status: Optional[str] = None,
        total_tasks: int = 0,
        passed_tasks: int = 0,
        failed_tasks: int = 0,
        unknown_tasks: int = 0,
        not_run: int = 0,
        run_datetime_pt: Optional[str] = None,
        github_log_url: Optional[str] = None,
        github_cron_log_url: Optional[str] = None,
        github_detail_log_url: Optional[str] = None,
        plot_github_url: Optional[str] = None,
    ) -> int:
        """
        Insert or update a test run record

        Args:
            run_date: Date in YYYYMMDD format
            hardware: Hardware type (mi30x, mi35x)
            docker_image: Docker image tag
            overall_status: Overall status (passed, failed, partial, unknown)
            total_tasks: Total number of tasks
            passed_tasks: Number of passed tasks
            failed_tasks: Number of failed tasks
            unknown_tasks: Number of unknown tasks
            not_run: Number of tasks not run

        Returns:
            Test run ID
        """
        with self.get_connection() as conn:
            cursor = conn.cursor()

            cursor.execute(
                """
                INSERT INTO test_runs (
                    run_date, hardware, docker_image, overall_status,
                    total_tasks, passed_tasks, failed_tasks, unknown_tasks, not_run,
                    run_datetime_pt, github_log_url, github_cron_log_url,
                    github_detail_log_url, plot_github_url,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(run_date, hardware) DO UPDATE SET
                    docker_image = excluded.docker_image,
                    overall_status = excluded.overall_status,
                    total_tasks = excluded.total_tasks,
                    passed_tasks = excluded.passed_tasks,
                    failed_tasks = excluded.failed_tasks,
                    unknown_tasks = excluded.unknown_tasks,
                    not_run = excluded.not_run,
                    run_datetime_pt = excluded.run_datetime_pt,
                    github_log_url = excluded.github_log_url,
                    github_cron_log_url = excluded.github_cron_log_url,
                    github_detail_log_url = excluded.github_detail_log_url,
                    plot_github_url = excluded.plot_github_url,
                    updated_at = CURRENT_TIMESTAMP
            """,
                (
                    run_date,
                    hardware,
                    docker_image,
                    overall_status,
                    total_tasks,
                    passed_tasks,
                    failed_tasks,
                    unknown_tasks,
                    not_run,
                    run_datetime_pt,
                    github_log_url,
                    github_cron_log_url,
                    github_detail_log_url,
                    plot_github_url,
                ),
            )

            # Get the ID of inserted/updated record
            cursor.execute(
                """
                SELECT id FROM test_runs WHERE run_date = ? AND hardware = ?
            """,
                (run_date, hardware),
            )
            return cursor.fetchone()[0]

    def upsert_benchmark_result(
        self,
        test_run_id: int,
        benchmark_name: str,
        status: str,
        gsm8k_accuracy: Optional[float] = None,
        runtime_minutes: Optional[int] = None,
        error_message: Optional[str] = None,
        timing_log_path: Optional[str] = None,
        github_detail_log_url: Optional[str] = None,
    ):
        """
        Insert or update a benchmark result

        Args:
            test_run_id: Test run ID
            benchmark_name: Name of benchmark
            status: Status (pass, fail, unknown)
            gsm8k_accuracy: GSM8K accuracy (0-1)
            runtime_minutes: Runtime in minutes
            error_message: Error message if failed
            timing_log_path: Path to timing summary log
        """
        with self.get_connection() as conn:
            cursor = conn.cursor()

            cursor.execute(
                """
                INSERT INTO benchmark_results (
                    test_run_id, benchmark_name, status, gsm8k_accuracy,
                    runtime_minutes, error_message, timing_log_path, github_detail_log_url
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(test_run_id, benchmark_name) DO UPDATE SET
                    status = excluded.status,
                    gsm8k_accuracy = excluded.gsm8k_accuracy,
                    runtime_minutes = excluded.runtime_minutes,
                    error_message = excluded.error_message,
                    timing_log_path = excluded.timing_log_path,
                    github_detail_log_url = excluded.github_detail_log_url
            """,
                (
                    test_run_id,
                    benchmark_name,
                    status,
                    gsm8k_accuracy,
                    runtime_minutes,
                    error_message,
                    timing_log_path,
                    github_detail_log_url,
                ),
            )

    def upsert_sanity_check_result(
        self,
        test_run_id: int,
        model_name: str,
        status: str,
        accuracy: Optional[float] = None,
    ):
        """
        Insert or update a sanity check result

        Args:
            test_run_id: Test run ID
            model_name: Model name
            status: Status (pass, fail, unknown)
            accuracy: Accuracy score
        """
        with self.get_connection() as conn:
            cursor = conn.cursor()

            cursor.execute(
                """
                INSERT INTO sanity_check_results (
                    test_run_id, model_name, status, accuracy
                )
                VALUES (?, ?, ?, ?)
                ON CONFLICT(test_run_id, model_name) DO UPDATE SET
                    status = excluded.status,
                    accuracy = excluded.accuracy
            """,
                (test_run_id, model_name, status, accuracy),
            )

    def upsert_log_file(
        self,
        test_run_id: int,
        log_type: str,
        log_name: str,
        local_path: Optional[str] = None,
        github_url: Optional[str] = None,
    ):
        """
        Insert or update a log file reference

        Args:
            test_run_id: Test run ID
            log_type: Type of log (cron, sanity, benchmark, unit_test, pd)
            log_name: Log file name
            local_path: Local filesystem path
            github_url: GitHub URL
        """
        with self.get_connection() as conn:
            cursor = conn.cursor()

            cursor.execute(
                """
                INSERT INTO log_files (
                    test_run_id, log_type, log_name, local_path, github_url
                )
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(test_run_id, log_type, log_name) DO UPDATE SET
                    local_path = excluded.local_path,
                    github_url = excluded.github_url
            """,
                (test_run_id, log_type, log_name, local_path, github_url),
            )

    def upsert_plot_file(
        self,
        test_run_id: int,
        benchmark_name: str,
        plot_suffix: str,
        local_path: Optional[str] = None,
        github_url: Optional[str] = None,
    ):
        """
        Insert or update a plot file reference

        Args:
            test_run_id: Test run ID
            benchmark_name: Benchmark name
            plot_suffix: Plot suffix (standard, all, etc.)
            local_path: Local filesystem path
            github_url: GitHub URL
        """
        with self.get_connection() as conn:
            cursor = conn.cursor()

            cursor.execute(
                """
                INSERT INTO plot_files (
                    test_run_id, benchmark_name, plot_suffix, local_path, github_url
                )
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(test_run_id, benchmark_name, plot_suffix) DO UPDATE SET
                    local_path = excluded.local_path,
                    github_url = excluded.github_url
            """,
                (test_run_id, benchmark_name, plot_suffix, local_path, github_url),
            )

    def get_test_run(self, run_date: str, hardware: str) -> Optional[Dict]:
        """
        Get test run record

        Args:
            run_date: Date in YYYYMMDD format
            hardware: Hardware type

        Returns:
            Test run record as dictionary or None
        """
        with self.get_connection() as conn:
            cursor = conn.cursor()

            cursor.execute(
                """
                SELECT * FROM test_runs
                WHERE run_date = ? AND hardware = ?
            """,
                (run_date, hardware),
            )

            row = cursor.fetchone()
            return dict(row) if row else None

    def get_benchmark_results(self, test_run_id: int) -> List[Dict]:
        """
        Get all benchmark results for a test run

        Args:
            test_run_id: Test run ID

        Returns:
            List of benchmark result dictionaries
        """
        with self.get_connection() as conn:
            cursor = conn.cursor()

            cursor.execute(
                """
                SELECT * FROM benchmark_results
                WHERE test_run_id = ?
                ORDER BY benchmark_name
            """,
                (test_run_id,),
            )

            return [dict(row) for row in cursor.fetchall()]

    def get_sanity_check_results(self, test_run_id: int) -> List[Dict]:
        """
        Get all sanity check results for a test run

        Args:
            test_run_id: Test run ID

        Returns:
            List of sanity check result dictionaries
        """
        with self.get_connection() as conn:
            cursor = conn.cursor()

            cursor.execute(
                """
                SELECT * FROM sanity_check_results
                WHERE test_run_id = ?
                ORDER BY model_name
            """,
                (test_run_id,),
            )

            return [dict(row) for row in cursor.fetchall()]

    def get_log_files(self, test_run_id: int) -> List[Dict]:
        """
        Get all log files for a test run

        Args:
            test_run_id: Test run ID

        Returns:
            List of log file dictionaries
        """
        with self.get_connection() as conn:
            cursor = conn.cursor()

            cursor.execute(
                """
                SELECT * FROM log_files
                WHERE test_run_id = ?
                ORDER BY log_type, log_name
            """,
                (test_run_id,),
            )

            return [dict(row) for row in cursor.fetchall()]

    def get_plot_files(self, test_run_id: int) -> List[Dict]:
        """
        Get all plot files for a test run

        Args:
            test_run_id: Test run ID

        Returns:
            List of plot file dictionaries
        """
        with self.get_connection() as conn:
            cursor = conn.cursor()

            cursor.execute(
                """
                SELECT * FROM plot_files
                WHERE test_run_id = ?
                ORDER BY benchmark_name, plot_suffix
            """,
                (test_run_id,),
            )

            return [dict(row) for row in cursor.fetchall()]

    def get_available_dates(self, hardware: str, max_days: int = 90) -> List[str]:
        """
        Get list of available test run dates

        Args:
            hardware: Hardware type
            max_days: Maximum number of days to return

        Returns:
            List of date strings in YYYYMMDD format, sorted newest first
        """
        with self.get_connection() as conn:
            cursor = conn.cursor()

            cursor.execute(
                """
                SELECT run_date FROM test_runs
                WHERE hardware = ?
                ORDER BY run_date DESC
                LIMIT ?
            """,
                (hardware, max_days),
            )

            return [row[0] for row in cursor.fetchall()]

    def get_historical_trends(self, hardware: str, days: int = 30) -> Dict:
        """
        Get historical trends for a hardware type

        Args:
            hardware: Hardware type
            days: Number of days to include

        Returns:
            Dictionary with trend data
        """
        with self.get_connection() as conn:
            cursor = conn.cursor()

            # Get test runs
            cursor.execute(
                """
                SELECT * FROM test_runs
                WHERE hardware = ?
                ORDER BY run_date DESC
                LIMIT ?
            """,
                (hardware, days),
            )

            test_runs = [dict(row) for row in cursor.fetchall()]

            # Reverse to show oldest to newest
            test_runs = list(reversed(test_runs))

            trends = {
                "dates": [],
                "overall_status": [],
                "passed_tasks": [],
                "failed_tasks": [],
                "total_tasks": [],
                "pass_rate": [],
                "benchmarks": {},
            }

            # Track benchmarks
            benchmark_names = [
                "Grok Online Benchmark",
                "Grok 2 Online Benchmark",
                "DeepSeek Online Benchmark",
            ]

            for benchmark_name in benchmark_names:
                trends["benchmarks"][benchmark_name] = {
                    "status": [],
                    "gsm8k_accuracy": [],
                    "runtime_minutes": [],
                }

            for test_run in test_runs:
                # Format date
                date_str = test_run["run_date"]
                date_obj = datetime.strptime(date_str, "%Y%m%d")
                display_date = date_obj.strftime("%Y-%m-%d")

                trends["dates"].append(display_date)
                trends["overall_status"].append(test_run["overall_status"])
                trends["passed_tasks"].append(test_run["passed_tasks"])
                trends["failed_tasks"].append(test_run["failed_tasks"])
                trends["total_tasks"].append(test_run["total_tasks"])

                # Calculate pass rate
                tasks_run = test_run["total_tasks"] - test_run["not_run"]
                if tasks_run > 0:
                    pass_rate = (test_run["passed_tasks"] / tasks_run) * 100
                else:
                    pass_rate = 0
                trends["pass_rate"].append(round(pass_rate, 1))

                # Get benchmark results for this test run
                benchmark_results = self.get_benchmark_results(test_run["id"])

                # Create lookup dict
                benchmark_lookup = {
                    br["benchmark_name"]: br for br in benchmark_results
                }

                for benchmark_name in benchmark_names:
                    if benchmark_name in benchmark_lookup:
                        br = benchmark_lookup[benchmark_name]
                        trends["benchmarks"][benchmark_name]["status"].append(
                            br["status"]
                        )

                        if br["gsm8k_accuracy"] is not None:
                            trends["benchmarks"][benchmark_name][
                                "gsm8k_accuracy"
                            ].append(round(br["gsm8k_accuracy"] * 100, 1))
                        else:
                            trends["benchmarks"][benchmark_name][
                                "gsm8k_accuracy"
                            ].append(None)

                        trends["benchmarks"][benchmark_name]["runtime_minutes"].append(
                            br["runtime_minutes"]
                        )
                    else:
                        trends["benchmarks"][benchmark_name]["status"].append("unknown")
                        trends["benchmarks"][benchmark_name]["gsm8k_accuracy"].append(
                            None
                        )
                        trends["benchmarks"][benchmark_name]["runtime_minutes"].append(
                            None
                        )

            return trends

    def get_complete_test_run_data(
        self, run_date: str, hardware: str
    ) -> Optional[Dict]:
        """
        Get complete test run data including all related records

        Args:
            run_date: Date in YYYYMMDD format
            hardware: Hardware type

        Returns:
            Complete test run data as dictionary or None
        """
        test_run = self.get_test_run(run_date, hardware)
        if not test_run:
            return None

        test_run_id = test_run["id"]

        return {
            "test_run": test_run,
            "benchmark_results": self.get_benchmark_results(test_run_id),
            "sanity_check_results": self.get_sanity_check_results(test_run_id),
            "log_files": self.get_log_files(test_run_id),
            "plot_files": self.get_plot_files(test_run_id),
        }

    def vacuum(self):
        """Vacuum database to reclaim space and optimize"""
        with self.get_connection() as conn:
            conn.execute("VACUUM")
