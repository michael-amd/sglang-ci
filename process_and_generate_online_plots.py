###############################################################################
#
# MIT License
#
# Copyright (c) 2025 Advanced Micro Devices, Inc.
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.
#
#################################################################################

"""
Combined Online Benchmark Data Processor and Plot Generator

This script combines the functionality of processing online benchmark CSV files
and generating performance plots from the processed data.

USAGE EXAMPLES:

1. Process and plot GROK1 model with default settings:
   python process_and_generate_online_plots.py --model grok

2. Process and plot DeepSeek model with default settings:
   python process_and_generate_online_plots.py --model deepseek

3. Process and plot DeepSeek-V3 model with default settings:
   python process_and_generate_online_plots.py --model DeepSeek-V3

4. Process only (skip plotting):
   python process_and_generate_online_plots.py --model grok --process-only

5. Plot only (skip processing, use existing summary CSV):
   python process_and_generate_online_plots.py --model grok --plot-only

6. Custom configuration with overrides:
   python process_and_generate_online_plots.py \
     --model grok \
     --data-dir /path/to/data \
     --plot-dir /path/to/plots \
     --mode-filter aiter \
     --split-request-rates

7. Process DeepSeek-V3 data with split request rate plots:
   python process_and_generate_online_plots.py \
     --model DeepSeek-V3 \
     --data-dir /home/michaezh/sgl_benchmark_ci/online/DeepSeek-V3 \
     --plot-dir /home/michaezh/sgl_benchmark_ci/plots_server \
     --mode-filter aiter \
     --split-request-rates

WORKFLOW:

1. Data Processing Phase:
   - Scans dated folders for online benchmark CSV files
   - Parses performance metrics (E2E Latency, TTFT, ITL) from CSV files
   - Parses GSM8K accuracy from separate GSM8K log files
   - Extracts KV cache information from server logs
   - Filters to keep only complete datasets
   - Generates summary CSV file

2. Plot Generation Phase:
   - Reads the generated summary CSV
   - Creates performance visualization plots
   - Supports both combined and split request rate layouts
   - Saves plots as PNG files

INPUT DATA FORMAT:
- Raw CSV files in dated folders: YYYYMMDD_MODEL_VARIANT_online/
- Expected metrics: GSM8K_Accuracy, E2E_Latency_ms, TTFT_ms, ITL_ms
- Request rates: 1, 2, 4, 8, 16 (powers of 2)
- Server logs for KV cache info

OUTPUT:
- Summary CSV: {output_prefix}_{mode_filter}_summary.csv
- Plot files: {date}_{base_model_name}_online.png (e.g. 20250717_GROK1_online.png, 20250717_DeepSeek_online.png)
"""

import argparse  # For command-line argument parsing
import os
import re  # Import re for regular expressions
import socket  # For hostname detection
from collections import defaultdict  # For cleaner dictionary handling
from datetime import datetime, timedelta

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import pandas as pd


class OnlineDataProcessor:
    def __init__(
        self,
        data_dir,
        output_model_name_prefix,
        mode_filter="aiter",
        days=30,
        expected_rates=None,
        load_metric_name="request_rate",
    ):
        """
        Initializes the OnlineDataProcessor.
        Args:
            data_dir: Path to the directory containing dated run folders (e.g., .../online/GROK1).
            output_model_name_prefix: Prefix for the output summary CSV file (e.g., GROK1_MOE-I4F8_online).
            mode_filter: Mode(s) to process. Can be:
                - "all": Process all modes
                - "aiter": Process only aiter mode (default)
                - "triton": Process only triton mode
                - list of modes: e.g., ["aiter", "triton"]
            days: Number of days to look back for processing (default: 30)
            expected_rates: A list of integers for expected request rates.
            load_metric_name: The name for the load metric column (e.g. 'request_rate', 'concurrency')
        """
        self.data_dir = data_dir
        self.output_model_name_prefix = output_model_name_prefix
        self.mode_filter = mode_filter
        self.load_metric_name = load_metric_name
        self.all_records = []
        current_date = datetime.today().date()
        # Generate list of dates for last N days including today
        self.date_prefixes = [
            (current_date - timedelta(days=i)).strftime("%Y%m%d")
            for i in range(0, days)
        ]

        # Compile regex patterns for KV cache info parsing (used repeatedly)
        # Updated to handle format: "KV size: 63.62 GB" instead of separate K and V sizes
        self.kv_cache_pattern_combined = re.compile(
            r"#tokens: (\d+), KV size: ([\d\.]+) GB"
        )
        self.kv_cache_pattern_separate = re.compile(
            r"#tokens: (\d+), K size: ([\d\.]+) GB, V size: ([\d\.]+) GB"
        )

        # Expected request rates for complete data
        if expected_rates is None:
            self.expected_request_rates = [1, 2, 4, 8, 16]  # Default
        else:
            self.expected_request_rates = sorted(expected_rates)

        # Convert mode_filter to a set for efficient checking
        if isinstance(mode_filter, str):
            if mode_filter.lower() == "all":
                self.modes_to_process = None  # None means process all modes
            else:
                self.modes_to_process = {mode_filter}
        elif isinstance(mode_filter, list):
            self.modes_to_process = set(mode_filter)
        else:
            raise ValueError(
                f"Invalid mode_filter: {mode_filter}. Must be 'all', a string mode name, or a list of mode names."
            )

        # Get hostname for node identification
        self.hostname = self._get_hostname()

    def _get_hostname(self):
        """Get the hostname for node identification."""
        try:
            hostname = socket.gethostname()
            return hostname if hostname else "unknown"
        except Exception:
            return "unknown"

    def _should_process_mode(self, mode):
        """Check if a mode should be processed based on the filter."""
        if self.modes_to_process is None:  # Process all modes
            return True
        return mode in self.modes_to_process

    def _parse_kv_cache_info(self, csv_dir):
        """
        Parse KV cache information from server log files.
        Returns: tuple of (num_tokens, kv_size_gb) or (pd.NA, pd.NA) if not found
        """
        num_tokens = pd.NA
        kv_size_gb = pd.NA

        server_logs = ["sglang_server.log", "server_output_aiter.log"]
        for log_file in server_logs:
            log_path = os.path.join(csv_dir, log_file)
            if not os.path.exists(log_path):
                continue

            try:
                with open(log_path, "r") as f:
                    for line in f:
                        if "KV Cache is allocated." in line and "#tokens:" in line:
                            # Try combined format first: "KV size: 63.62 GB"
                            match = self.kv_cache_pattern_combined.search(line)
                            if match:
                                try:
                                    num_tokens = int(match.group(1))
                                    kv_size_gb = float(match.group(2))
                                    return (num_tokens, kv_size_gb)
                                except ValueError:
                                    print(
                                        f"Warning: Could not parse KV cache info from line: {line.strip()} in {log_path}"
                                    )
                            else:
                                # Try separate format: "K size: 70.03 GB, V size: 70.03 GB"
                                match = self.kv_cache_pattern_separate.search(line)
                                if match:
                                    try:
                                        num_tokens = int(match.group(1))
                                        k_size_gb = float(match.group(2))
                                        v_size_gb = float(match.group(3))
                                        kv_size_gb = (
                                            k_size_gb + v_size_gb
                                        )  # Total KV size
                                        return (num_tokens, kv_size_gb)
                                    except ValueError:
                                        print(
                                            f"Warning: Could not parse KV cache info from line: {line.strip()} in {log_path}"
                                        )
                                else:
                                    print(
                                        f"Warning: Found KV Cache allocation line but failed to parse: {line.strip()} in {log_path}"
                                    )
            except Exception as e:
                print(f"Error reading server log {log_path}: {e}")

        return num_tokens, kv_size_gb

    def _parse_gsm8k_accuracy_from_csv(self, lines):
        """
        Parse GSM8K accuracy directly from CSV file lines (e.g., DeepSeek format).
        Returns: GSM8K accuracy value or None if not found
        """
        try:
            for line in lines:
                line = line.strip()
                # Look for patterns like "Average Accuracy\t0.941"
                if line.lower().startswith("average accuracy"):
                    parts = line.split("\t")
                    if len(parts) >= 2:
                        try:
                            accuracy = float(parts[1])
                            print(f"Found GSM8K accuracy in CSV: {accuracy}")
                            return accuracy
                        except ValueError:
                            continue
        except Exception as e:
            print(f"Error parsing GSM8K accuracy from CSV: {e}")

        return None

    def _parse_gsm8k_accuracy(self, csv_dir):
        """
        Parse GSM8K accuracy from GSM8K log files.
        Returns: dict mapping mode to GSM8K accuracy or empty dict if not found
        """
        gsm8k_accuracy = {}

        try:
            file_list = os.listdir(csv_dir)
        except Exception as e:
            print(f"Error accessing directory {csv_dir}: {e}")
            return gsm8k_accuracy

        # Look for GSM8K log files (pattern: sglang_client_log_*_gsm8k_*.log)
        gsm8k_log_files = [
            f for f in file_list if "gsm8k" in f.lower() and f.endswith(".log")
        ]

        for log_file in gsm8k_log_files:
            log_path = os.path.join(csv_dir, log_file)
            if not os.path.exists(log_path):
                continue

            try:
                # Extract mode from filename (e.g., "aiter" from "sglang_client_log_GROK1_gsm8k_aiter.log")
                mode = None
                if "_aiter" in log_file:
                    mode = "aiter"
                elif "_triton" in log_file:
                    mode = "triton"
                elif "gsm8k" in log_file.lower():
                    # Fallback: extract mode from filename pattern
                    parts = log_file.replace(".log", "").split("_")
                    if len(parts) > 0:
                        # For files like "sglang_client_log_DeepSeek_gsm8k.log"
                        # where there's no explicit mode, we need to determine it differently
                        last_part = parts[-1]
                        if last_part == "gsm8k":
                            # No explicit mode in filename, will determine from CSV data
                            mode = None  # Will be determined later from CSV context
                        else:
                            mode = last_part  # Last part should be the mode

                if not mode:
                    print(
                        f"Warning: Could not determine mode from GSM8K log file: {log_file}, will use default"
                    )

                # Read the log file and find the average accuracy
                with open(log_path, "r") as f:
                    for line in f:
                        # Look for lines like: "Average Accuracy over 5 runs for mode aiter: 0.813"
                        if "Average Accuracy over" in line and "runs for mode" in line:
                            try:
                                # Extract the accuracy value (last part after colon)
                                accuracy_str = line.split(":")[-1].strip()
                                accuracy = float(accuracy_str)
                                if mode:
                                    gsm8k_accuracy[mode] = accuracy
                                    print(
                                        f"Found GSM8K accuracy for mode {mode}: {accuracy}"
                                    )
                                else:
                                    # Store with a generic key, will be mapped later
                                    gsm8k_accuracy["_default"] = accuracy
                                    print(
                                        f"Found GSM8K accuracy (no mode specified): {accuracy}"
                                    )
                                break
                            except (ValueError, IndexError) as e:
                                print(
                                    f"Warning: Could not parse GSM8K accuracy from line: {line.strip()} in {log_path}: {e}"
                                )

                        # Alternative pattern: Just "Accuracy: 0.813" from individual runs
                        elif line.strip().startswith("Accuracy:"):
                            try:
                                accuracy_str = line.split(":")[-1].strip()
                                accuracy = float(accuracy_str)
                                # Only use this if we don't have an average accuracy
                                mode_key = mode if mode else "_default"
                                if mode_key not in gsm8k_accuracy:
                                    gsm8k_accuracy[mode_key] = accuracy
                                    print(
                                        f"Found GSM8K accuracy (single run) for mode {mode_key}: {accuracy}"
                                    )
                            except (ValueError, IndexError):
                                continue

            except Exception as e:
                print(f"Error reading GSM8K log {log_path}: {e}")

        return gsm8k_accuracy

    def _find_request_rates(self, lines):
        """
        Find and parse request rates from the CSV lines.
        Returns: list of request rates (integers) or empty list if not found
        """
        request_rates = []
        temp_iter = iter(lines)

        try:
            line = next(temp_iter)
            while True:  # Loop until found or end of file
                # This loop will NOT be infinite because:
                # 1. It breaks when request rates are found (see 'break' below)
                # 2. next() will raise StopIteration when reaching end of file, which is caught below
                # Check if current line is a known metric section header
                if any(
                    header in line
                    for header in [
                        "Median E2E Latency (ms, lower better)",
                        "Median TTFT (ms, lower better)",
                        "Median ITL (ms, lower better)",
                    ]
                ):
                    req_rate_line_candidate = next(temp_iter)  # The line after header
                    if (
                        "request rate" in req_rate_line_candidate
                        or "concurrency" in req_rate_line_candidate
                    ):
                        parts = req_rate_line_candidate.strip().split("\t")
                        if len(parts) > 1:
                            # Filter out non-integer values from parts
                            request_rates = [int(r) for r in parts[1:] if r.isdigit()]
                            break  # Found request rates
                line = next(temp_iter)
                # Skip empty lines but keep searching
                if not line.strip():
                    continue
        except StopIteration:
            pass  # End of file reached

        return request_rates

    def _parse_metric_section(
        self, lines, metric_type_label, metric_df_name, request_rates
    ):
        """
        Parse a single metric section (E2E Latency, TTFT, or ITL) from the CSV lines.
        Returns: dict mapping (mode, request_rate) tuples to metric values
        """
        metrics_data = {}
        section_iter = iter(lines)

        try:
            # Find the section header
            line = next(section_iter)
            while metric_type_label not in line:
                line = next(section_iter)
                # Check for empty line to prevent infinite loop
                if not line.strip():
                    continue  # Skip empty lines but keep searching

            # Skip the "request rate" / "concurrency" line (rates already parsed)
            next(section_iter)

            # Parse data lines (can be multiple modes, or H100 which is skipped)
            while True:
                try:
                    line = next(section_iter).strip()
                    if not line or "H100/MI300x" in line:
                        break  # End of data rows for this metric

                    # Skip H100 line if present, but don't require it
                    if "H100" in line:
                        continue

                    # Extract mode/backend from the line
                    # Format can be:
                    # - MI300x-aiter, node_name\t...
                    # - MI300x-triton, node_name\t...
                    # - DeepSeek-FP8\t... (no mode specified, assume default)
                    parts = line.split("\t")
                    if len(parts) > 1:
                        first_part = parts[0]
                        mode = None

                        # Case 1: Handle specific MI300x modes
                        if "MI300x-aiter" in first_part:
                            mode = (
                                "aiter_decode"
                                if "aiter_decode" in first_part
                                else "aiter"
                            )
                        elif "MI300x-triton" in first_part:
                            mode = "triton"
                        # Case 2: General MI300x mode extraction
                        elif first_part.startswith("MI300x-"):
                            mode_match = re.search(r"MI300x-(\w+)", first_part)
                            if mode_match:
                                mode = mode_match.group(1).split()[0]
                        # Case 3: Not a MI300x line (e.g., DeepSeek) - assume aiter if it's in the filter
                        elif self.modes_to_process and "aiter" in self.modes_to_process:
                            mode = "aiter"

                        if not mode:
                            # If no mode could be determined, use the first part as a fallback
                            # and print a warning. This ensures data is still processed.
                            mode = first_part.split(",")[0].strip()
                            print(
                                f"Warning: Could not determine standard mode from line: '{first_part}'. Using fallback mode: '{mode}'"
                            )

                        # Parse values
                        values_str = parts[1:]
                        for i, rr in enumerate(request_rates):
                            try:
                                if i < len(values_str) and values_str[i].strip():
                                    value = float(values_str[i])
                                else:
                                    value = pd.NA
                                    print(
                                        f"Warning: Missing data for {mode}, {metric_df_name}, {self.load_metric_name} {rr}"
                                    )
                            except (ValueError, IndexError):
                                value = pd.NA
                                print(
                                    f"Warning: Could not parse value for {mode}, {metric_df_name}, {self.load_metric_name} {rr}: '{values_str[i] if i < len(values_str) else 'INDEX_OUT_OF_BOUNDS'}'"
                                )
                            metrics_data[(mode, rr)] = value

                except StopIteration:
                    break  # No more lines

        except StopIteration:
            print(
                f"Warning: Section for '{metric_type_label}' not found or incomplete."
            )
        except Exception as e:
            print(f"Warning: Error parsing section '{metric_type_label}': {e}")

        return metrics_data

    def _parse_single_online_csv(self, file_path, date_str):
        """
        Parses a single online benchmark summary CSV file.
        The CSV contains multiple tables for E2E Latency, TTFT, and ITL.
        Dynamically extracts backend modes (aiter, triton, etc.) from row labels.
        Also parses KV cache info from server log files in the same directory.
        """
        file_records = []
        num_tokens, kv_size_gb = self._parse_kv_cache_info(os.path.dirname(file_path))

        try:
            with open(file_path, "r") as f:
                lines = f.readlines()
        except Exception as e:
            print(f"Error opening or reading file {file_path}: {e}")
            return file_records

        request_rates = self._find_request_rates(lines)

        if not request_rates:
            print(f"Could not find or parse request rates line in {file_path}.")
            return file_records

        # First try to parse GSM8K accuracy from CSV file content
        gsm8k_acc_from_csv = self._parse_gsm8k_accuracy_from_csv(lines)

        # Then try to parse from log files
        gsm8k_accuracy = self._parse_gsm8k_accuracy(os.path.dirname(file_path))

        # Parse each metric section and collect all metrics
        metrics_map = defaultdict(
            dict
        )  # Stores { (mode, request_rate): {metric_name: value} }

        for metric_type_label, metric_df_name in [
            ("Median E2E Latency (ms, lower better)", "E2E_Latency_ms"),
            ("Median TTFT (ms, lower better)", "TTFT_ms"),
            ("Median ITL (ms, lower better)", "ITL_ms"),
        ]:
            section_metrics = self._parse_metric_section(
                lines, metric_type_label, metric_df_name, request_rates
            )
            for (mode, rate), value in section_metrics.items():
                key = (mode, rate)
                metrics_map[key][metric_df_name] = value

        # If we found GSM8K accuracy in CSV but not in logs, map it to all found modes
        if gsm8k_acc_from_csv is not None and not gsm8k_accuracy:
            # Get all modes from the metrics data
            found_modes = set(mode for (mode, rate) in metrics_map.keys())
            for mode in found_modes:
                gsm8k_accuracy[mode] = gsm8k_acc_from_csv
                print(
                    f"Mapped GSM8K accuracy from CSV to mode {mode}: {gsm8k_acc_from_csv}"
                )

        # Handle case where GSM8K accuracy was found in logs but with "_default" key
        if "_default" in gsm8k_accuracy and gsm8k_acc_from_csv is None:
            default_accuracy = gsm8k_accuracy["_default"]
            del gsm8k_accuracy["_default"]
            # Map to all found modes
            found_modes = set(mode for (mode, rate) in metrics_map.keys())
            for mode in found_modes:
                gsm8k_accuracy[mode] = default_accuracy
                print(
                    f"Mapped default GSM8K accuracy to mode {mode}: {default_accuracy}"
                )

        # Build records from metrics_map
        for (mode, rate), metrics in metrics_map.items():
            # Filter based on mode
            if not self._should_process_mode(mode):
                continue

            # Get GSM8K accuracy for this mode (same for all request rates)
            gsm8k_acc = gsm8k_accuracy.get(mode, pd.NA)

            record = {
                "date": date_str,
                "mode": mode,
                self.load_metric_name: rate,
                "node_name": self.hostname,
                "GSM8K_Accuracy": gsm8k_acc,
                "E2E_Latency_ms": metrics.get("E2E_Latency_ms", pd.NA),
                "TTFT_ms": metrics.get("TTFT_ms", pd.NA),
                "ITL_ms": metrics.get("ITL_ms", pd.NA),
                "num_tokens": num_tokens,
                "KV_size_GB": kv_size_gb,
            }
            file_records.append(record)

        return file_records

    def _extract_date_from_name(self, name):
        """
        Extract date from folder/file name supporting both old and new formats.

        Old format: YYYYMMDD_* or YYYYMMDDrc_*
        New format: v*-YYYYMMDD_* (e.g., v0.4.9.post2-rocm630-mi30x-20250715_*)

        Returns: date string (YYYYMMDD) or None if not found
        """
        # Use regex to find 8-digit date pattern (YYYYMMDD)
        date_match = re.search(r"(\d{8})", name)
        if date_match:
            return date_match.group(1)

        # Fallback: try old format (first part before underscore)
        first_part = name.split("_")[0]
        normalized_date = first_part.replace("rc", "")
        if len(normalized_date) == 8 and normalized_date.isdigit():
            return normalized_date

        return None

    def read_and_process_files(self):
        """
        Iterates through dated folders, finds online CSVs, and processes them.
        """
        try:
            if not os.path.exists(self.data_dir):
                print(f"Error: Data directory not found: {self.data_dir}")
                return

            folder_list = os.listdir(self.data_dir)
            if not folder_list:
                print(f"Warning: No folders found in {self.data_dir}")
                return
        except PermissionError:
            print(f"Error: Permission denied accessing directory {self.data_dir}")
            return
        except Exception as e:
            print(f"Error accessing data directory {self.data_dir}: {e}")
            return

        for folder_name in folder_list:
            folder_path = os.path.join(self.data_dir, folder_name)
            if os.path.isdir(folder_path):
                # Check if folder ends with exactly "_online" (not "_online_old" etc.)
                if not folder_name.endswith(("_online", "_serving")):
                    continue

                # Extract date from folder name supporting both old and new formats
                # Old format: YYYYMMDD_MODEL_VARIANT_online or YYYYMMDDrc_MODEL_VARIANT_online
                # New format: v0.4.9.post2-rocm630-mi30x-YYYYMMDD_MODEL_VARIANT_online
                normalized_folder_date = self._extract_date_from_name(folder_name)

                if normalized_folder_date and any(
                    normalized_folder_date == dp for dp in self.date_prefixes
                ):
                    try:
                        file_list = os.listdir(folder_path)
                    except Exception as e:
                        print(f"Error accessing folder {folder_path}: {e}")
                        continue

                    for file_name in file_list:
                        # CSV filename supports both formats
                        # Old: YYYYMMDD_MODEL_VARIANT_online.csv
                        # New: v0.4.9.post2-rocm630-mi30x-YYYYMMDD_MODEL_VARIANT_online.csv
                        # And serving variants like _serving.csv
                        if file_name.endswith(("_online.csv", "_serving.csv")):
                            date_str_from_file = self._extract_date_from_name(file_name)
                            if not date_str_from_file:
                                print(
                                    f"Skipping file with no extractable date in name: {file_name} in folder {folder_name}"
                                )
                                continue
                            try:  # Validate date string format
                                datetime.strptime(date_str_from_file, "%Y%m%d")
                            except ValueError:
                                print(
                                    f"Skipping file with invalid date format in name: {file_name} in folder {folder_name}"
                                )
                                continue

                            file_path = os.path.join(folder_path, file_name)
                            if not os.path.exists(file_path):
                                print(f"Warning: File not found: {file_path}")
                                continue

                            file_specific_records = self._parse_single_online_csv(
                                file_path, date_str_from_file
                            )
                            self.all_records.extend(file_specific_records)

    def filter_complete_dates(self):
        """
        Filters records to only keep dates that have valid data for all expected request rates (1, 2, 4, 8, 16).
        Valid data means ALL performance metrics (GSM8K_Accuracy, E2E_Latency_ms, TTFT_ms, ITL_ms) are not NA.
        """
        if not self.all_records:
            return

        # Convert records to DataFrame for easier filtering
        df = pd.DataFrame(self.all_records)

        # Performance metric columns to check
        metric_columns = ["GSM8K_Accuracy", "E2E_Latency_ms", "TTFT_ms", "ITL_ms"]

        # Group by date and mode to check completeness
        complete_dates = set()

        for date in df["date"].unique():
            date_df = df[df["date"] == date]

            # Get unique modes for this date
            modes_in_date = date_df["mode"].unique()

            # Check if each mode has all expected request rates with valid data
            is_complete = True
            for mode in modes_in_date:
                mode_df = date_df[date_df["mode"] == mode]

                # Check request rates
                request_rates_found = sorted(mode_df[self.load_metric_name].unique())
                if request_rates_found != self.expected_request_rates:
                    is_complete = False
                    missing_rates = set(self.expected_request_rates) - set(
                        request_rates_found
                    )
                    extra_rates = set(request_rates_found) - set(
                        self.expected_request_rates
                    )
                    if missing_rates:
                        print(
                            f"Date {date}, Mode {mode}: Missing {self.load_metric_name}s: {sorted(missing_rates)}"
                        )
                    if extra_rates:
                        print(
                            f"Date {date}, Mode {mode}: Extra {self.load_metric_name}s: {sorted(extra_rates)}"
                        )
                    break

                # Check that each request rate has valid data (at least one non-NA metric)
                for rr in self.expected_request_rates:
                    rr_df = mode_df[mode_df[self.load_metric_name] == rr]
                    if rr_df.empty:
                        is_complete = False
                        print(
                            f"Date {date}, Mode {mode}: No data for {self.load_metric_name} {rr}"
                        )
                        break

                    # Check if ALL performance metrics have valid data
                    has_valid_data = True
                    for metric in metric_columns:
                        if metric not in rr_df.columns or pd.isna(
                            rr_df[metric].iloc[0]
                        ):
                            has_valid_data = False
                            break

                    if not has_valid_data:
                        is_complete = False
                        print(
                            f"Date {date}, Mode {mode}, {self.load_metric_name.capitalize()} {rr}: Missing valid data for one or more performance metrics"
                        )
                        break

                if not is_complete:
                    break

            if is_complete:
                complete_dates.add(date)
                print(
                    f"Date {date}: Complete and valid data for all modes and {self.load_metric_name}s"
                )

        # Filter records to only keep complete dates
        if complete_dates:
            self.all_records = [
                r for r in self.all_records if r["date"] in complete_dates
            ]
            print(
                f"\nKept {len(complete_dates)} dates with complete and valid data: {sorted(complete_dates)}"
            )
            print(f"Total records after filtering: {len(self.all_records)}")
        else:
            print(
                f"\nNo dates found with complete and valid data for all {self.load_metric_name}s {self.expected_request_rates}"
            )
            self.all_records = []

    def save_summary_csv(self):
        """
        Saves the aggregated data into a single summary CSV file.
        Returns the path to the saved CSV file.
        """
        if not self.all_records:
            print("No data processed. Skipping CSV generation.")
            return None

        try:
            summary_df = pd.DataFrame(self.all_records)
        except Exception as e:
            print(f"Error creating DataFrame from records: {e}")
            return None

        if summary_df.empty:
            print("No records to save after processing. Skipping CSV generation.")
            return None

        # Sort by date, then mode, then request_rate for consistent output
        try:
            summary_df = summary_df.sort_values(
                by=["date", "mode", self.load_metric_name]
            )
        except KeyError as e:
            print(f"Error: Missing expected columns for sorting: {e}")
            # Try to save anyway without sorting

        # Add mode suffix to output filename if not processing all modes
        if self.modes_to_process is not None:
            mode_suffix = "_" + "_".join(sorted(self.modes_to_process))
        else:
            mode_suffix = "_all"

        output_file = os.path.join(
            self.data_dir,
            f"{self.output_model_name_prefix}{mode_suffix}_summary_{self.hostname}.csv",
        )
        try:
            # Ensure the directory exists
            os.makedirs(os.path.dirname(output_file), exist_ok=True)
            summary_df.to_csv(output_file, index=False)
            print(f"Online summary CSV saved to: {output_file}")

            # Print summary of processed modes
            if self.modes_to_process is None:
                print("Processed all modes")
            else:
                print(f"Processed modes: {', '.join(sorted(self.modes_to_process))}")

            # Show unique modes found in the data
            unique_modes = summary_df["mode"].unique()
            print(f"Modes found in output: {', '.join(sorted(unique_modes))}")

            return output_file

        except PermissionError:
            # Try to make the directory writable first
            try:
                import stat

                dir_path = os.path.dirname(output_file)
                current_permissions = os.stat(dir_path).st_mode
                os.chmod(dir_path, current_permissions | stat.S_IWUSR | stat.S_IWGRP)
                print(f"Made directory writable: {dir_path}")

                # Try writing again after changing permissions
                summary_df.to_csv(output_file, index=False)
                print(f"Online summary CSV saved to: {output_file}")

                # Print summary of processed modes
                if self.modes_to_process is None:
                    print("Processed all modes")
                else:
                    print(
                        f"Processed modes: {', '.join(sorted(self.modes_to_process))}"
                    )

                # Show unique modes found in the data
                unique_modes = summary_df["mode"].unique()
                print(f"Modes found in output: {', '.join(sorted(unique_modes))}")

                return output_file

            except Exception as chmod_e:
                print(f"Could not make directory writable: {chmod_e}")
                # Fall back to current directory if making writable fails
                fallback_output_file = f"{self.output_model_name_prefix}{mode_suffix}_summary_{self.hostname}.csv"
                try:
                    summary_df.to_csv(fallback_output_file, index=False)
                    print(f"Permission denied for {output_file}")
                    print(
                        f"Online summary CSV saved to fallback location: {fallback_output_file}"
                    )

                    # Print summary of processed modes
                    if self.modes_to_process is None:
                        print("Processed all modes")
                    else:
                        print(
                            f"Processed modes: {', '.join(sorted(self.modes_to_process))}"
                        )

                    # Show unique modes found in the data
                    unique_modes = summary_df["mode"].unique()
                    print(f"Modes found in output: {', '.join(sorted(unique_modes))}")

                    return fallback_output_file
                except Exception as fallback_e:
                    print(
                        f"Error: Could not write to fallback location {fallback_output_file}: {fallback_e}"
                    )
                    return None
        except Exception as e:
            print(f"Error saving summary CSV to {output_file}: {e}")
            return None

    def process_and_save(self):
        """
        Main orchestrator method for data processing.
        Returns the path to the generated summary CSV file.
        """
        print("=== DATA PROCESSING PHASE ===")
        self.read_and_process_files()
        self.filter_complete_dates()  # Filter to only keep dates with complete data
        return self.save_summary_csv()


class OnlineGraphPlotter:
    def __init__(
        self,
        summary_csv_path,
        plot_dir,
        model_name_in_plot,
        mode_filter=None,
        split_request_rates=False,
        expected_rates=None,
        load_metric_name="request_rate",
    ):
        """
        Initialize the OnlineGraphPlotter.
        Args:
            summary_csv_path: Path to the summary CSV file
            plot_dir: Directory where plots will be saved
            model_name_in_plot: Model name to use in plot titles
            mode_filter: Optional mode filter. Can be:
                - None: Plot all modes in the CSV (default)
                - "aiter": Plot only aiter mode
                - "triton": Plot only triton mode
                - list of modes: e.g., ["aiter", "triton"]
            split_request_rates: If True, create separate plots for low (1,2,4) and high (8,16) request rates
            expected_rates: A list of integers for expected request rates.
            load_metric_name: The name for the load metric column (e.g. 'request_rate', 'concurrency')
        """
        self.summary_csv_path = summary_csv_path
        self.plot_dir = plot_dir
        self.model_name_in_plot = model_name_in_plot  # e.g. "GROK1 MOE-I4F8 Online"
        self.mode_filter = mode_filter
        self.split_request_rates = split_request_rates
        self.load_metric_name = load_metric_name

        # Try to create plot directory, attempt to make writable first if permission denied
        try:
            os.makedirs(self.plot_dir, exist_ok=True)
        except PermissionError:
            print(f"Permission denied creating plot directory: {self.plot_dir}")
            # Try to make parent directory writable
            try:
                import stat

                parent_dir = os.path.dirname(self.plot_dir)
                if os.path.exists(parent_dir):
                    current_permissions = os.stat(parent_dir).st_mode
                    os.chmod(
                        parent_dir, current_permissions | stat.S_IWUSR | stat.S_IWGRP
                    )
                    print(f"Made parent directory writable: {parent_dir}")
                    # Try creating again
                    os.makedirs(self.plot_dir, exist_ok=True)
                    print(f"Successfully created plot directory: {self.plot_dir}")
                else:
                    raise Exception(f"Parent directory does not exist: {parent_dir}")
            except Exception as chmod_e:
                print(f"Could not make parent directory writable: {chmod_e}")
                self.plot_dir = "."  # Use current directory as fallback
                print(f"Using fallback plot directory: {self.plot_dir}")
        except Exception as e:
            print(f"Error creating plot directory {self.plot_dir}: {e}")
            self.plot_dir = "."  # Use current directory as fallback
            print(f"Using fallback plot directory: {self.plot_dir}")

        self.df = None

        # Expected request rates for complete data
        if expected_rates is None:
            self.expected_request_rates = [1, 2, 4, 8, 16]  # Default
        else:
            self.expected_request_rates = sorted(expected_rates)

        # Define low and high request rate groups dynamically
        if len(self.expected_request_rates) >= 4:
            split_point = len(self.expected_request_rates) // 2
            # For odd numbers, give the extra one to the low group
            if len(self.expected_request_rates) % 2 != 0:
                split_point += 1
            self.low_request_rates = self.expected_request_rates[:split_point]
            self.high_request_rates = self.expected_request_rates[split_point:]
        else:
            self.low_request_rates = self.expected_request_rates
            self.high_request_rates = []

        # Convert mode_filter to a set for efficient checking
        if mode_filter is None:
            self.modes_to_plot = None  # None means plot all modes
        elif isinstance(mode_filter, str):
            self.modes_to_plot = {mode_filter}
        elif isinstance(mode_filter, list):
            self.modes_to_plot = set(mode_filter)
        else:
            raise ValueError(
                f"Invalid mode_filter: {mode_filter}. Must be None, a string mode name, or a list of mode names."
            )

    def _should_plot_mode(self, mode):
        """Check if a mode should be plotted based on the filter."""
        if self.modes_to_plot is None:  # Plot all modes
            return True
        return mode in self.modes_to_plot

    def read_summary_csv(self):
        """
        Reads the summary CSV file into a pandas DataFrame.
        """
        try:
            if not os.path.exists(self.summary_csv_path):
                print(f"Error: Summary CSV file not found: {self.summary_csv_path}")
                self.df = pd.DataFrame()
                return

            self.df = pd.read_csv(self.summary_csv_path)

            # Check if the dataframe is empty
            if self.df.empty:
                print(f"Warning: Summary CSV file is empty: {self.summary_csv_path}")
                return

            # Check for required columns
            required_columns = ["date", self.load_metric_name]
            missing_columns = [
                col for col in required_columns if col not in self.df.columns
            ]
            if missing_columns:
                print(
                    f"Error: Missing required columns {missing_columns} in {self.summary_csv_path}"
                )
                self.df = pd.DataFrame()
                return

            # Filter modes if mode_filter is specified
            if self.modes_to_plot is not None and "mode" in self.df.columns:
                self.df = self.df[self.df["mode"].isin(self.modes_to_plot)]
                if self.df.empty:
                    print(
                        f"Warning: No data found for modes {self.modes_to_plot} in {self.summary_csv_path}"
                    )
                    return

            self.df["date"] = pd.to_datetime(self.df["date"], format="%Y%m%d")
            # Ensure new columns are numeric, coercing errors to NaN
            if "num_tokens" in self.df.columns:
                self.df["num_tokens"] = pd.to_numeric(
                    self.df["num_tokens"], errors="coerce"
                )
            if "KV_size_GB" in self.df.columns:
                self.df["KV_size_GB"] = pd.to_numeric(
                    self.df["KV_size_GB"], errors="coerce"
                )
            self.df = self.df.sort_values("date")
        except pd.errors.EmptyDataError:
            print(f"Error: CSV file is empty or corrupted: {self.summary_csv_path}")
            self.df = pd.DataFrame()
        except pd.errors.ParserError as e:
            print(f"Error parsing CSV file {self.summary_csv_path}: {e}")
            self.df = pd.DataFrame()
        except Exception as e:
            print(
                f"Error reading or processing summary CSV {self.summary_csv_path}: {e}"
            )
            self.df = pd.DataFrame()  # Ensure df is an empty DataFrame on error

    def filter_complete_dates(self):
        """
        Filters dataframe to only keep dates that have valid data for all expected request rates (1, 2, 4, 8, 16).
        Valid data means ALL performance metrics (GSM8K_Accuracy, E2E_Latency_ms, TTFT_ms, ITL_ms) are not NA.
        """
        if self.df.empty:
            return

        # Performance metric columns to check
        metric_columns = ["GSM8K_Accuracy", "E2E_Latency_ms", "TTFT_ms", "ITL_ms"]

        # Group by date and mode to check completeness
        complete_dates = set()

        for date in self.df["date"].unique():
            date_df = self.df[self.df["date"] == date]

            # Get unique modes for this date
            modes_in_date = date_df["mode"].unique()

            # Check if each mode has all expected request rates with valid data
            is_complete = True
            for mode in modes_in_date:
                mode_df = date_df[date_df["mode"] == mode]

                # Check request rates
                request_rates_found = sorted(mode_df[self.load_metric_name].unique())
                if request_rates_found != self.expected_request_rates:
                    is_complete = False
                    missing_rates = set(self.expected_request_rates) - set(
                        request_rates_found
                    )
                    extra_rates = set(request_rates_found) - set(
                        self.expected_request_rates
                    )
                    if missing_rates:
                        print(
                            f"Date {date.strftime('%Y%m%d')}, Mode {mode}: Missing {self.load_metric_name}s: {sorted(missing_rates)}"
                        )
                    if extra_rates:
                        print(
                            f"Date {date.strftime('%Y%m%d')}, Mode {mode}: Extra {self.load_metric_name}s: {sorted(extra_rates)}"
                        )
                    break

                # Check that each request rate has valid data (at least one non-NA metric)
                for rr in self.expected_request_rates:
                    rr_df = mode_df[mode_df[self.load_metric_name] == rr]
                    if len(rr_df) == 0:
                        is_complete = False
                        print(
                            f"Date {date.strftime('%Y%m%d')}, Mode {mode}: No data for {self.load_metric_name} {rr}"
                        )
                        break

                    # Check if ALL performance metrics have valid data
                    has_valid_data = True
                    for metric in metric_columns:
                        if (
                            metric not in rr_df.columns
                            or not rr_df[metric].notna().any()
                        ):
                            has_valid_data = False
                            break

                    if not has_valid_data:
                        is_complete = False
                        print(
                            f"Date {date.strftime('%Y%m%d')}, Mode {mode}, {self.load_metric_name.capitalize()} {rr}: Missing valid data for one or more performance metrics"
                        )
                        break

                if not is_complete:
                    break

            if is_complete:
                complete_dates.add(date)
                print(
                    f"Date {date.strftime('%Y%m%d')}: Complete and valid data for all modes and {self.load_metric_name}s"
                )

        # Filter dataframe to only keep complete dates
        if complete_dates:
            self.df = self.df[self.df["date"].isin(complete_dates)]
            print(
                f"\nKept {len(complete_dates)} dates with complete and valid data for plotting"
            )
            print(f"Total records after filtering: {len(self.df)}")
        else:
            print(
                f"\nNo dates found with complete and valid data for all {self.load_metric_name}s {self.expected_request_rates}"
            )
            self.df = pd.DataFrame()

    def _setup_subplot_axis(self, ax, ordered_dates, y_label, title):
        """Helper method to set up common axis properties for subplots."""
        ax.set_title(title)
        ax.set_xlabel("Date")
        ax.set_ylabel(y_label)
        ax.grid(True)
        ax.set_xticks(range(len(ordered_dates)))
        ax.set_xticklabels(
            [d.strftime("%Y-%m-%d") for d in ordered_dates], rotation=45, ha="right"
        )

    def _filter_overlapping_annotations(self, annotations):
        """Filter annotations to prevent overlap on the plot."""
        if not annotations:
            return []

        # First, handle annotations with the same y value - keep only the leftmost one
        y_value_to_annotations = {}
        for ann in annotations:
            y_val = ann["y"]
            if y_val not in y_value_to_annotations:
                y_value_to_annotations[y_val] = []
            y_value_to_annotations[y_val].append(ann)

        # For each unique y value, keep only the annotation with the smallest x
        unique_y_annotations = []
        for y_val, anns in y_value_to_annotations.items():
            # Sort by x position and keep the leftmost one
            leftmost = min(anns, key=lambda a: a["x"])
            unique_y_annotations.append(leftmost)

        # Now apply the original overlap filtering on the remaining annotations
        # Sort annotations by x, then by y (descending for y to prioritize higher values)
        unique_y_annotations.sort(key=lambda a: (a["x"], -a["y"]))

        # Estimate y-axis range for overlap threshold calculation
        y_values = [a["y"] for a in unique_y_annotations]
        y_range = max(y_values) - min(y_values) if len(y_values) > 1 else 1
        # Handle corner case where all y values are the same
        if y_range == 0:
            y_range = 1
        y_overlap_threshold = y_range * 0.05  # 5% of y-range as threshold
        x_overlap_threshold = (
            0.15  # x positions within 0.15 units considered overlapping
        )

        filtered_annotations = []
        for ann in unique_y_annotations:
            # Check if this annotation would overlap with any already accepted annotation
            overlap_found = False
            for accepted in filtered_annotations:
                x_dist = abs(ann["x"] - accepted["x"])
                y_dist = abs(ann["y"] - accepted["y"])

                # Skip y distance check if values are exactly the same (already handled above)
                if ann["y"] == accepted["y"]:
                    continue

                if x_dist <= x_overlap_threshold and y_dist <= y_overlap_threshold:
                    overlap_found = True
                    break

            if not overlap_found:
                filtered_annotations.append(ann)

        return filtered_annotations

    def _add_annotations(self, ax, annotations):
        """Add filtered annotations to the plot."""
        filtered_annotations = self._filter_overlapping_annotations(annotations)
        for ann in filtered_annotations:
            ax.annotate(
                ann["text"],
                (ann["x"], ann["y"]),
                textcoords="offset points",
                xytext=(0, 7),
                ha="center",
                fontsize="x-small",
            )

    def _plot_performance_metrics(
        self, ax, metric_col, y_label, unique_modes, unique_load_values
    ):
        """Plot performance metrics (E2E Latency, TTFT, ITL) as line plots."""
        if self.df.empty:
            ax.set_title(f"{y_label} vs. Date for {self.model_name_in_plot} (No Data)")
            ax.set_xticks([])
            ax.set_yticks([])
            return

        plotted_dates = set()
        plot_data_collections = []
        all_annotations = []

        # Collect data for each mode and request rate combination
        for mode in unique_modes:
            for rr in unique_load_values:
                subset = self.df[
                    (self.df["mode"] == mode)
                    & (self.df[self.load_metric_name] == rr)
                    & self.df[metric_col].notna()
                ]
                if not subset.empty:
                    plotted_dates.update(subset["date"])
                    label_prefix = (
                        "Conc" if self.load_metric_name == "concurrency" else "RR"
                    )
                    plot_data_collections.append(
                        {
                            "dates": subset["date"],
                            "values": subset[metric_col],
                            "label": f"{mode} {label_prefix}={rr}",
                        }
                    )

        if not plotted_dates:
            ax.set_title(f"{y_label} vs. Date for {self.model_name_in_plot} (No Data)")
            ax.set_xticks([])
            ax.set_yticks([])
            return

        # Create date to index mapping
        ordered_dates = sorted(list(plotted_dates))
        date_to_idx = {date_obj: k for k, date_obj in enumerate(ordered_dates)}

        # Plot each series
        for data_item in plot_data_collections:
            x_indices = [date_to_idx[d] for d in data_item["dates"]]
            values = data_item["values"]
            ax.plot(
                x_indices, values, marker="o", linestyle="-", label=data_item["label"]
            )

            # Collect annotations
            for k_idx, x_val_idx in enumerate(x_indices):
                y_val = (
                    values.iloc[k_idx]
                    if isinstance(values, pd.Series)
                    else values[k_idx]
                )
                all_annotations.append(
                    {"x": x_val_idx, "y": y_val, "text": f"{y_val:.0f}"}
                )

        # Add annotations and setup axis
        self._add_annotations(ax, all_annotations)
        ax.legend(loc="upper left", bbox_to_anchor=(1, 1), fontsize="small")
        self._setup_subplot_axis(
            ax,
            ordered_dates,
            y_label,
            f"{y_label} vs. Date for {self.model_name_in_plot}",
        )

    def _plot_num_tokens(self, ax, unique_modes):
        """Plot num_tokens as a line plot aggregated by mode."""
        metric_col = "num_tokens"
        y_label = "# Tokens*"

        if self.df.empty or metric_col not in self.df.columns:
            ax.set_title(f"{y_label} vs. Date for {self.model_name_in_plot} (No Data)")
            ax.set_xticks([])
            ax.set_yticks([])
            return

        plotted_dates = set()
        plot_data_collections = []
        all_annotations = []

        # Process data for each mode
        for mode in unique_modes:
            mode_data = self.df[self.df["mode"] == mode]
            mode_data = mode_data[mode_data[metric_col].notna()]
            if not mode_data.empty:
                data_by_date = mode_data.groupby("date")[metric_col].mean()
                if not data_by_date.empty:
                    plotted_dates.update(data_by_date.index)
                    plot_data_collections.append(
                        {
                            "dates": data_by_date.index,
                            "values": data_by_date.values,
                            "label": f"{mode} - # Tokens",
                        }
                    )

        if not plotted_dates:
            ax.set_title(f"{y_label} vs. Date for {self.model_name_in_plot} (No Data)")
            ax.set_xticks([])
            ax.set_yticks([])
            return

        # Create date to index mapping
        ordered_dates = sorted(list(plotted_dates))
        date_to_idx = {date_obj: k for k, date_obj in enumerate(ordered_dates)}

        # Plot each series
        for data_item in plot_data_collections:
            x_indices = [date_to_idx[d] for d in data_item["dates"]]
            values = data_item["values"]
            ax.plot(
                x_indices, values, marker="o", linestyle="-", label=data_item["label"]
            )

            # Collect annotations
            for k_idx, x_val_idx in enumerate(x_indices):
                y_val = values[k_idx]
                all_annotations.append(
                    {"x": x_val_idx, "y": y_val, "text": f"{y_val:.0f}"}
                )

        # Add annotations and setup axis
        self._add_annotations(ax, all_annotations)
        ax.legend(loc="best", fontsize="small")
        self._setup_subplot_axis(
            ax,
            ordered_dates,
            y_label,
            f"{y_label} vs. Date for {self.model_name_in_plot}",
        )

        # Add explanation text below the plot (positioned lower to avoid overlap with date labels)
        explanation_text = 'Note: "# Tokens*" refers to the number of tokens for which the\nKey-Value (KV) Cache is allocated at server startup.'
        ax.text(
            0.5,
            -0.25,
            explanation_text,
            transform=ax.transAxes,
            ha="center",
            va="top",
            fontsize="small",
            color="gray",
            bbox=dict(boxstyle="round,pad=0.3", fc="white", alpha=0.5),
        )

    def _plot_kv_cache_usage(self, ax, unique_modes):
        """Plot KV cache usage as a bar plot aggregated by mode."""
        metric_col = "KV_size_GB"
        y_label = "KV Cache Usage (GB)"

        if self.df.empty or metric_col not in self.df.columns:
            ax.set_title(f"{y_label} vs. Date for {self.model_name_in_plot} (No Data)")
            ax.set_xticks([])
            ax.set_yticks([])
            return

        plotted_dates = set()
        plot_data_collections = []

        # Process data for each mode
        for mode_idx, mode in enumerate(unique_modes):
            mode_data = self.df[self.df["mode"] == mode]
            mode_data = mode_data[mode_data[metric_col].notna()]
            if not mode_data.empty:
                data_by_date = mode_data.groupby("date")[metric_col].mean()
                if not data_by_date.empty:
                    plotted_dates.update(data_by_date.index)
                    plot_data_collections.append(
                        {
                            "mode_idx": mode_idx,
                            "dates": data_by_date.index,
                            "values": data_by_date.values,
                            "label": f"{mode} - KV Cache Usage",
                        }
                    )

        if not plotted_dates:
            ax.set_title(f"{y_label} vs. Date for {self.model_name_in_plot} (No Data)")
            ax.set_xticks([])
            ax.set_yticks([])
            return

        # Create date to index mapping
        ordered_dates = sorted(list(plotted_dates))
        date_to_idx = {date_obj: k for k, date_obj in enumerate(ordered_dates)}

        # Bar plot settings
        num_modes = len(unique_modes)
        bar_width = 0.8 / num_modes if num_modes > 0 else 0.4

        # Plot bars for each mode
        for data_item in plot_data_collections:
            x_indices = [date_to_idx[d] for d in data_item["dates"]]
            values = data_item["values"]

            # Calculate offset for this mode
            offset = (data_item["mode_idx"] - (num_modes - 1) / 2) * bar_width
            x_positions = [x + offset for x in x_indices]

            ax.bar(x_positions, values, label=data_item["label"], width=bar_width)

        # Setup axis
        ax.legend(loc="best", fontsize="small")
        self._setup_subplot_axis(
            ax,
            ordered_dates,
            y_label,
            f"{y_label} vs. Date for {self.model_name_in_plot}",
        )

    def _plot_gsm8k_accuracy(self, ax, unique_modes):
        """Plot GSM8K accuracy as a line plot aggregated by mode."""
        metric_col = "GSM8K_Accuracy"
        y_label = "GSM8K Accuracy (%)"

        if self.df.empty or metric_col not in self.df.columns:
            ax.set_title(f"{y_label} vs. Date for {self.model_name_in_plot} (No Data)")
            ax.set_xticks([])
            ax.set_yticks([])
            return

        plotted_dates = set()
        plot_data_collections = []
        all_annotations = []

        # Process data for each mode
        for mode in unique_modes:
            mode_data = self.df[self.df["mode"] == mode]
            mode_data = mode_data[mode_data[metric_col].notna()]
            if not mode_data.empty:
                data_by_date = mode_data.groupby("date")[metric_col].mean()
                if not data_by_date.empty:
                    plotted_dates.update(data_by_date.index)
                    # Convert to percentage (multiply by 100)
                    plot_data_collections.append(
                        {
                            "dates": data_by_date.index,
                            "values": data_by_date.values
                            * 100,  # Convert to percentage
                            "label": f"{mode} - GSM8K Accuracy",
                        }
                    )

        if not plotted_dates:
            ax.set_title(f"{y_label} vs. Date for {self.model_name_in_plot} (No Data)")
            ax.set_xticks([])
            ax.set_yticks([])
            return

        # Create date to index mapping
        ordered_dates = sorted(list(plotted_dates))
        date_to_idx = {date_obj: k for k, date_obj in enumerate(ordered_dates)}

        # Plot each series
        for data_item in plot_data_collections:
            x_indices = [date_to_idx[d] for d in data_item["dates"]]
            values = data_item["values"]
            ax.plot(
                x_indices, values, marker="o", linestyle="-", label=data_item["label"]
            )

            # Collect annotations
            for k_idx, x_val_idx in enumerate(x_indices):
                y_val = values[k_idx]
                all_annotations.append(
                    {"x": x_val_idx, "y": y_val, "text": f"{y_val:.1f}%"}
                )

        # Add annotations and setup axis
        self._add_annotations(ax, all_annotations)
        ax.legend(loc="best", fontsize="small")
        self._setup_subplot_axis(
            ax,
            ordered_dates,
            y_label,
            f"{y_label} vs. Date for {self.model_name_in_plot}",
        )

        # Set y-axis to show percentages in a reasonable range
        y_min = min([min(item["values"]) for item in plot_data_collections]) - 2
        y_max = min(
            100, max([max(item["values"]) for item in plot_data_collections]) + 2
        )
        ax.set_ylim(max(0, y_min), y_max)

    def plot_metrics_vs_date(self):
        """
        Creates a plot with subplots for E2E Latency, TTFT, ITL, #Tokens and KV Cache Usage vs. Date.
        Each subplot shows lines for different request_rate and mode combinations for performance metrics.
        #Tokens is a line plot and KV Cache Usage is a bar plot, showing separate entries per mode for each date.
        All available dates are shown on the x-axis for each plot.
        """
        if self.df.empty:
            print("No data available to plot.")
            return

        # Get unique modes and request rates
        unique_modes = self.df["mode"].unique()
        unique_load_values = sorted(self.df[self.load_metric_name].unique())

        # Print summary of modes being plotted
        print(f"Plotting modes: {', '.join(sorted(unique_modes))}")

        if self.split_request_rates:
            # Create separate plots for low and high request rates
            self._create_split_plots(unique_modes, unique_load_values)
        else:
            # Create single plot with all request rates
            self._create_single_plot(unique_modes, unique_load_values)

    def _create_single_plot(self, unique_modes, unique_load_values):
        """Create a single plot with all request rates."""
        has_token_data = (
            "num_tokens" in self.df.columns and self.df["num_tokens"].notna().any()
        )
        has_kv_data = (
            "KV_size_GB" in self.df.columns and self.df["KV_size_GB"].notna().any()
        )
        has_gsm8k_data = (
            "GSM8K_Accuracy" in self.df.columns
            and self.df["GSM8K_Accuracy"].notna().any()
        )

        # Use 2x2 layout if both token and KV cache data are empty, otherwise use 3x2
        if not has_token_data and not has_kv_data:
            fig, axes = plt.subplots(2, 2, figsize=(20, 12))
            print("Using 2x2 layout (no token/KV cache data)")
        else:
            fig, axes = plt.subplots(3, 2, figsize=(20, 18))
            print("Using 3x2 layout (with token/KV cache data)")

        axes = axes.flatten()

        plot_idx = 0

        # Row 1: GSM8K Accuracy (left) and E2E Latency (right)
        if has_gsm8k_data:
            self._plot_gsm8k_accuracy(axes[plot_idx], unique_modes)
        else:
            axes[plot_idx].set_title(
                f"GSM8K Accuracy vs. Date for {self.model_name_in_plot} (No Data)"
            )
            axes[plot_idx].set_xticks([])
            axes[plot_idx].set_yticks([])
        plot_idx += 1

        self._plot_performance_metrics(
            axes[plot_idx],
            "E2E_Latency_ms",
            "E2E Latency (ms)",
            unique_modes,
            unique_load_values,
        )
        plot_idx += 1

        # Row 2: TTFT (left) and ITL (right)
        self._plot_performance_metrics(
            axes[plot_idx], "TTFT_ms", "TTFT (ms)", unique_modes, unique_load_values
        )
        plot_idx += 1

        self._plot_performance_metrics(
            axes[plot_idx], "ITL_ms", "ITL (ms)", unique_modes, unique_load_values
        )
        plot_idx += 1

        # Row 3: num_tokens (left) and KV cache usage (right) - only if we have data
        if has_token_data or has_kv_data:
            if has_token_data:
                self._plot_num_tokens(axes[plot_idx], unique_modes)
            else:
                axes[plot_idx].set_title(
                    f"# Tokens vs. Date for {self.model_name_in_plot} (No Data)"
                )
                axes[plot_idx].set_xticks([])
                axes[plot_idx].set_yticks([])
            plot_idx += 1

            if has_kv_data:
                self._plot_kv_cache_usage(axes[plot_idx], unique_modes)
            else:
                axes[plot_idx].set_title(
                    f"KV Cache Usage vs. Date for {self.model_name_in_plot} (No Data)"
                )
                axes[plot_idx].set_xticks([])
                axes[plot_idx].set_yticks([])

        # Adjust layout with reduced horizontal spacing and space for notes below
        plt.tight_layout(
            rect=[0, 0.08, 0.95, 1], w_pad=1.0
        )  # Reduced horizontal padding, increased bottom space, reduced right margin

        # Save the plot
        current_date_str = datetime.now().strftime("%Y%m%d")

        # Extract base model name for filename (e.g. GROK1 from "GROK1 MOE-I4F8 Online")
        # Generate filename format: YYYYMMDD_MODEL_Online.png (e.g. 20250717_GROK1_Online.png)
        base_model_name = self.model_name_in_plot.split()[0]

        plot_filename = f"{current_date_str}_{base_model_name}_online.png"
        output_file_path = os.path.join(self.plot_dir, plot_filename)

        try:
            plt.savefig(output_file_path)
            print(f"Plot saved to: {output_file_path}")
        except Exception as e:
            print(f"Error saving plot to {output_file_path}: {e}")
        plt.close()

    def _create_split_plots(self, unique_modes, unique_load_values):
        """Create a single plot with low and high request rates in separate rows."""
        # Filter request rates into low and high groups
        low_rr = [rr for rr in unique_load_values if rr in self.low_request_rates]
        high_rr = [rr for rr in unique_load_values if rr in self.high_request_rates]

        current_date_str = datetime.now().strftime("%Y%m%d")

        # Extract base model name for filename (e.g. GROK1 from "GROK1 MOE-I4F8 Online")
        base_model_name = self.model_name_in_plot.split()[0]

        print(
            f"\nCreating combined plot with low {self.load_metric_name}: {low_rr} and high {self.load_metric_name}: {high_rr}"
        )

        # Create figure with 3 rows and 3 columns
        fig, axes = plt.subplots(3, 3, figsize=(30, 24))

        # First row: Low request rate performance metrics
        if low_rr:
            self._plot_performance_metrics(
                axes[0, 0],
                "E2E_Latency_ms",
                "E2E Latency (ms) - Low",
                unique_modes,
                low_rr,
            )
            self._plot_performance_metrics(
                axes[0, 1], "TTFT_ms", "TTFT (ms) - Low", unique_modes, low_rr
            )
            self._plot_performance_metrics(
                axes[0, 2], "ITL_ms", "ITL (ms) - Low", unique_modes, low_rr
            )
        else:
            # If no low RR data, show empty plots
            for i in range(3):
                axes[0, i].set_title(f"Low {self.load_metric_name} Metrics (No Data)")
                axes[0, i].set_xticks([])
                axes[0, i].set_yticks([])

        # Second row: High request rate performance metrics
        if high_rr:
            self._plot_performance_metrics(
                axes[1, 0],
                "E2E_Latency_ms",
                "E2E Latency (ms) - High",
                unique_modes,
                high_rr,
            )
            self._plot_performance_metrics(
                axes[1, 1], "TTFT_ms", "TTFT (ms) - High", unique_modes, high_rr
            )
            self._plot_performance_metrics(
                axes[1, 2], "ITL_ms", "ITL (ms) - High", unique_modes, high_rr
            )
        else:
            # If no high RR data, show empty plots
            for i in range(3):
                axes[1, i].set_title(f"High {self.load_metric_name} Metrics (No Data)")
                axes[1, i].set_xticks([])
                axes[1, i].set_yticks([])

        # Third row: num_tokens (left), KV cache usage (center), GSM8K accuracy (right)
        has_token_data = (
            "num_tokens" in self.df.columns and self.df["num_tokens"].notna().any()
        )
        has_kv_data = (
            "KV_size_GB" in self.df.columns and self.df["KV_size_GB"].notna().any()
        )
        has_gsm8k_data = (
            "GSM8K_Accuracy" in self.df.columns
            and self.df["GSM8K_Accuracy"].notna().any()
        )

        # Only show token plot if data exists
        if has_token_data:
            self._plot_num_tokens(axes[2, 0], unique_modes)
        else:
            # Hide the subplot by removing it entirely
            fig.delaxes(axes[2, 0])

        # Only show KV cache plot if data exists
        if has_kv_data:
            self._plot_kv_cache_usage(axes[2, 1], unique_modes)
        else:
            # Hide the subplot by removing it entirely
            fig.delaxes(axes[2, 1])

        # GSM8K Accuracy in bottom right position
        if has_gsm8k_data:
            self._plot_gsm8k_accuracy(axes[2, 2], unique_modes)
        else:
            axes[2, 2].set_title(
                f"GSM8K Accuracy vs. Date for {self.model_name_in_plot} (No Data)"
            )
            axes[2, 2].set_xticks([])
            axes[2, 2].set_yticks([])

        # Adjust layout with reduced horizontal spacing and space for notes below
        plt.tight_layout(
            rect=[0, 0.08, 0.95, 1], w_pad=1.0
        )  # Reduced horizontal padding, increased bottom space, reduced right margin

        # Save the plot with split request rates filename format
        plot_filename = f"{current_date_str}_{base_model_name}_online_split.png"
        output_file_path = os.path.join(self.plot_dir, plot_filename)

        try:
            plt.savefig(output_file_path)
            print(f"Split {self.load_metric_name} plot saved to: {output_file_path}")
        except Exception as e:
            print(
                f"Error saving split {self.load_metric_name} plot to {output_file_path}: {e}"
            )
        plt.close()

    def generate_and_save_plots(self):
        """
        Main method to orchestrate reading data and generating plots.
        """
        print("=== PLOT GENERATION PHASE ===")
        self.read_summary_csv()
        self.filter_complete_dates()  # Filter to only keep dates with complete data
        self.plot_metrics_vs_date()


def parse_mode_filter(mode_str):
    """Parse mode filter string into appropriate format."""
    if not mode_str or mode_str.lower() == "none":
        return None
    elif mode_str.lower() == "all":
        return None  # None means process/plot all modes
    elif "," in mode_str:
        # Multiple modes separated by comma
        return [m.strip() for m in mode_str.split(",") if m.strip()]
    else:
        # Single mode
        return mode_str.strip()


def main():
    """
    Main function that orchestrates both data processing and plot generation.
    """
    MODEL_CONFIGS = {
        "grok": {
            "variant_name": "GROK1",
            "output_prefix_template": "{variant_name}_MOE-I4F8_online",
            "model_name_template": "{variant_name} MOE-I4F8 Online",
            "expected_rates": [1, 2, 4, 8, 16],
            "load_metric_name": "request_rate",
        },
        "deepseek": {
            "variant_name": "DeepSeek-V3",
            "output_prefix_template": "{variant_name}_FP8_online",
            "model_name_template": "{variant_name} FP8 Online",
            "expected_rates": [1, 4, 16, 64, 128],
            "load_metric_name": "concurrency",
        },
        "DeepSeek-V3": {
            "variant_name": "DeepSeek-V3",
            "output_prefix_template": "{variant_name}_FP8_online",
            "model_name_template": "{variant_name} FP8 Online",
            "expected_rates": [1, 4, 16, 64, 128],
            "load_metric_name": "concurrency",
        },
    }

    DEFAULT_BASE_DIR = os.path.abspath(os.path.dirname(__file__))

    parser = argparse.ArgumentParser(
        description="Process online benchmark CSV files and generate plots",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    # Simplified model selection
    parser.add_argument(
        "-m",
        "--model",
        type=str,
        default="grok",
        choices=MODEL_CONFIGS.keys(),
        help="The model to process. Options: 'grok', 'deepseek', 'DeepSeek-V3'.",
    )

    # Arguments for paths and names (default to None, will be set from config)
    parser.add_argument(
        "--base-dir",
        type=str,
        default=DEFAULT_BASE_DIR,
        help="Base directory for default paths (can be overridden by specific path arguments).",
    )
    parser.add_argument(
        "--data-dir", type=str, default=None, help="Override data directory path."
    )
    parser.add_argument(
        "--output-prefix",
        type=str,
        default=None,
        help="Override output CSV file prefix.",
    )
    parser.add_argument(
        "--plot-dir", type=str, default=None, help="Override plot directory path."
    )
    parser.add_argument(
        "--model-name",
        type=str,
        default=None,
        help="Override model name in plot titles.",
    )

    # Other arguments
    parser.add_argument(
        "--days",
        type=int,
        default=30,
        help="Number of days to look back for processing",
    )
    parser.add_argument(
        "--request-rates",
        type=str,
        default=None,
        help="Comma-separated list of expected request rates/concurrencies. Overrides model-specific defaults.",
    )
    parser.add_argument(
        "--split-request-rates",
        action="store_true",
        help="Create separate plots for low and high request rates/concurrencies",
    )
    parser.add_argument(
        "--mode-filter",
        type=str,
        default="aiter",
        help="Mode(s) to process/plot. Options: 'all', 'aiter', 'triton', or comma-separated list like 'aiter,triton'",
    )
    parser.add_argument(
        "--process-only",
        action="store_true",
        help="Only process CSV files, skip plot generation",
    )
    parser.add_argument(
        "--plot-only",
        action="store_true",
        help="Only generate plots, skip CSV processing (requires existing summary CSV)",
    )
    parser.add_argument(
        "--summary-csv",
        type=str,
        help="Path to existing summary CSV file (for --plot-only mode)",
    )

    args = parser.parse_args()

    # --- Configuration Setup ---
    config = MODEL_CONFIGS[args.model]
    variant_name = config["variant_name"]

    # Set values from config, allowing overrides from command line
    directory_name = "DeepSeek-V3" if args.model == "DeepSeek-V3" else variant_name
    if args.data_dir is None:
        args.data_dir = os.path.join(args.base_dir, "online", directory_name)
    if args.output_prefix is None:
        args.output_prefix = config["output_prefix_template"].format(
            variant_name=variant_name
        )
    if args.plot_dir is None:
        args.plot_dir = os.path.join(
            args.base_dir, "plots_server", directory_name, "online"
        )
    elif not args.plot_dir.endswith(("online", "offline")):
        # If plot_dir is explicitly provided but doesn't include the mode subdirectory,
        # append the model-specific subdirectory structure for consistency
        directory_name = "DeepSeek-V3" if args.model == "DeepSeek-V3" else variant_name
        args.plot_dir = os.path.join(args.plot_dir, directory_name, "online")
    if args.model_name is None:
        args.model_name = config["model_name_template"].format(
            variant_name=variant_name
        )

    # Determine expected request rates and load metric name
    if args.request_rates:
        try:
            expected_rates = sorted(
                [int(r.strip()) for r in args.request_rates.split(",")]
            )
        except (ValueError, AttributeError):
            parser.error("--request-rates must be a comma-separated list of integers.")
    else:
        expected_rates = config["expected_rates"]

    load_metric_name = config["load_metric_name"]

    # Validate mutually exclusive options
    if args.process_only and args.plot_only:
        parser.error("--process-only and --plot-only are mutually exclusive")

    # Parse mode filter
    mode_filter = parse_mode_filter(args.mode_filter)

    # Print configuration
    print("=== CONFIGURATION ===")
    print(f"Model: {args.model} (variant: {variant_name})")
    print(f"Data directory: {args.data_dir}")
    print(f"Output prefix: {args.output_prefix}")
    print(f"Plot directory: {args.plot_dir}")
    print(f"Model name: {args.model_name}")
    print(f"Mode filter: {mode_filter if mode_filter is not None else 'all modes'}")
    print(f"Load metric: {load_metric_name}")
    print(f"Expected {load_metric_name}s: {expected_rates}")
    print(f"Days to process: {args.days}")
    print(f"Split request rates: {args.split_request_rates}")
    print(f"Process only: {args.process_only}")
    print(f"Plot only: {args.plot_only}")
    print()

    summary_csv_path = None

    # Phase 1: Data Processing
    if not args.plot_only:
        processor = OnlineDataProcessor(
            args.data_dir,
            args.output_prefix,
            mode_filter,
            args.days,
            expected_rates=expected_rates,
            load_metric_name=load_metric_name,
        )
        summary_csv_path = processor.process_and_save()

        if not summary_csv_path:
            print("ERROR: Failed to generate summary CSV. Exiting.")
            return 1

        if args.process_only:
            print("Processing complete. Exiting as requested.")
            return 0

    # Phase 2: Plot Generation
    if not args.process_only:
        # Determine summary CSV path
        if args.plot_only:
            if args.summary_csv:
                summary_csv_path = args.summary_csv
            else:
                # Auto-generate path based on mode filter
                if mode_filter is None:
                    mode_suffix = "_all"
                elif isinstance(mode_filter, str):
                    mode_suffix = f"_{mode_filter}"
                elif isinstance(mode_filter, list):
                    mode_suffix = "_" + "_".join(sorted(mode_filter))
                else:
                    mode_suffix = "_all"

                # Get hostname for consistent naming with processor output
                try:
                    hostname = socket.gethostname()
                except Exception:
                    hostname = "unknown"

                summary_csv_path = os.path.join(
                    args.data_dir,
                    f"{args.output_prefix}{mode_suffix}_summary_{hostname}.csv",
                )

        if not summary_csv_path or not os.path.exists(summary_csv_path):
            print(f"ERROR: Summary CSV not found: {summary_csv_path}")
            return 1

        # Create plotter and generate plots
        plotter = OnlineGraphPlotter(
            summary_csv_path,
            args.plot_dir,
            args.model_name,
            mode_filter=mode_filter,
            split_request_rates=args.split_request_rates,
            expected_rates=expected_rates,
            load_metric_name=load_metric_name,
        )
        plotter.generate_and_save_plots()

    print("=== COMPLETE ===")
    print("Both processing and plotting completed successfully!")
    return 0


if __name__ == "__main__":
    exit(main())
