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
Combined Offline Benchmark Data Processor and Plot Generator

This script combines the functionality of processing offline benchmark CSV files
and generating performance plots from the processed data.

USAGE EXAMPLES:

1. Process and plot GROK1 model with default settings:
   python process_and_generate_offline_plots.py --model grok

2. Process and plot DeepSeek model with default settings:
   python process_and_generate_offline_plots.py --model deepseek

3. Process only (skip plotting):
   python process_and_generate_offline_plots.py --model grok --process-only

4. Plot only (skip processing, use existing summary CSV):
   python process_and_generate_offline_plots.py --model grok --plot-only

5. Custom configuration with overrides:
   python process_and_generate_offline_plots.py \
     --model grok \
     --data-dir /path/to/data \
     --plot-dir /path/to/plots \
     --ilen 1024 \
     --olen 128

6. Process DeepSeek data with custom parameters:
   python process_and_generate_offline_plots.py \
     --model deepseek \
     --data-dir /home/michaezh/sgl_benchmark_ci/offline/DeepSeek-V3-0324 \
     --plot-dir /home/michaezh/sgl_benchmark_ci/plots_server \
     --ilen 1024 \
     --olen 128 \
     --days 5

WORKFLOW:

1. Data Processing Phase:
   - Scans dated folders for offline benchmark CSV files
   - Parses performance metrics (E2E Latency, E2E Throughput)
   - Extracts backend information from CSV or config.json
   - Filters to keep only complete datasets
   - Generates summary CSV file

2. Plot Generation Phase:
   - Reads the generated summary CSV
   - Creates performance visualization plots
   - Generates combined metrics plot showing both latency and throughput trends
   - Saves plots as PNG files

INPUT DATA FORMAT:
- Raw CSV files in dated folders: YYYYMMDD_MODEL_VARIANT_offline/ or v*-YYYYMMDD_MODEL_VARIANT_offline/
- Expected batch sizes: 1, 2, 4, 8, 16, 32, 64, 128, 256
- Expected metrics: E2E_Latency(s), E2E_Throughput(token/s)

OUTPUT:
- Summary CSV: {output_prefix}_summary.csv
- Plot file: {date}_{base_model_name}_offline.png (e.g. 20250617_GROK1_offline.png)
"""

import argparse
import os
import re  # Import re for regular expressions
from collections import defaultdict
from datetime import datetime, timedelta

import matplotlib
import pandas as pd

matplotlib.use("Agg")  # Use non-interactive backend

import matplotlib.pyplot as plt


class OfflineDataProcessor:
    def __init__(self, data_dir, output_model_name_prefix, ilen=None, olen=None, days_to_process=None):
        """
        Initializes the OfflineDataProcessor class with the directory path where CSV files are stored.
        Args:
            data_dir: Path to the directory containing dated run folders
            output_model_name_prefix: Prefix for the output summary CSV file
            ilen: Input length (default: 1024)
            olen: Output length (default: 128)
            days_to_process: Number of days to look back (default: 30)
        """
        self.data_dir = data_dir
        self.output_model_name_prefix = output_model_name_prefix
        self.ILEN = ilen or 1024
        self.OLEN = olen or 128
        self.all_records = []
        current_date = datetime.today().date()
        # Generate list of dates for specified number of days excluding today
        days_back = days_to_process or 30
        self.date_prefixes = [
            (current_date - timedelta(days=i)).strftime("%Y%m%d") for i in range(1, days_back + 1)
        ]

    def _extract_date_from_name(self, name):
        """
        Extract date from folder/file name supporting both old and new formats.

        Old format: YYYYMMDD_* or YYYYMMDDrc_*
        New format: v*-YYYYMMDD_* (e.g., v0.4.9.post2-rocm630-mi30x-20250715_*)

        Returns: date string (YYYYMMDD) or None if not found
        """
        # Use regex to find 8-digit date pattern (YYYYMMDD)
        date_match = re.search(r'(\d{8})', name)
        if date_match:
            return date_match.group(1)

        # Fallback: try old format (first part before underscore)
        first_part = name.split("_")[0]
        normalized_date = first_part.replace("rc", "")
        if len(normalized_date) == 8 and normalized_date.isdigit():
            return normalized_date

        return None

    def _parse_offline_csv_file(self, file_path, date_str):
        """
        Parse a single offline CSV file and extract batch data.
        Also tries to read backend information from config.json in the same directory.
        Returns: list of records for all batch sizes in the file
        """
        records = []
        backend = "unknown"  # Default backend

        # Try to read backend info from config.json if it exists
        config_path = os.path.join(os.path.dirname(file_path), "config.json")
        if os.path.exists(config_path):
            try:
                import json

                with open(config_path, "r") as f:
                    config = json.load(f)
                    backend = config.get("attention_backend", "unknown")
            except Exception as e:
                print(f"Warning: Could not read config.json from {config_path}: {e}")

        try:
            df = pd.read_csv(file_path)

            if df.empty:
                print(f"Warning: Empty CSV file: {file_path}")
                return records

            # Check if Backend column exists
            has_backend_column = "Backend" in df.columns or (
                len(df.columns) > 10 and df.columns[4] == "Backend"
            )

            # Set column names based on whether Backend column exists
            if has_backend_column or len(df.columns) == 11:
                # New format with Backend column
                df.columns = [
                    "TP",
                    "batch_size",
                    "IL",
                    "OL",
                    "Backend",
                    "Prefill_latency(s)",
                    "Median_decode_latency(s)",
                    "E2E_Latency(s)",
                    "Prefill_Throughput(token/s)",
                    "Median_Decode_Throughput(token/s)",
                    "E2E_Throughput(token/s)",
                ]
            else:
                # Old format without Backend column
                df.columns = [
                    "TP",
                    "batch_size",
                    "IL",
                    "OL",
                    "Prefill_latency(s)",
                    "Median_decode_latency(s)",
                    "E2E_Latency(s)",
                    "Prefill_Throughput(token/s)",
                    "Median_Decode_Throughput(token/s)",
                    "E2E_Throughput(token/s)",
                ]

            # Process each batch size
            for batch_size in df["batch_size"].unique():
                batch_data = df[df["batch_size"] == batch_size]
                # Drop rows where E2E_Latency is NaN
                batch_data = batch_data.dropna(subset=["E2E_Latency(s)"])

                if batch_data.empty:
                    print(
                        f"Warning: No valid data for batch size {batch_size} in {file_path}"
                    )
                    continue

                # Get backend from CSV if available, otherwise use from config.json
                if "Backend" in batch_data.columns:
                    # Use the most common backend for this batch size
                    csv_backend = (
                        batch_data["Backend"].mode()[0]
                        if not batch_data["Backend"].empty
                        else backend
                    )
                else:
                    csv_backend = backend

                # Use mean for aggregation if multiple rows per batch size
                record = {
                    "date": date_str,
                    "batch_size": int(batch_size),
                    "backend": csv_backend,
                    "E2E_Latency(s)": batch_data["E2E_Latency(s)"].mean(),
                    "E2E_Throughput(token/s)": batch_data[
                        "E2E_Throughput(token/s)"
                    ].mean(),
                    "ILEN": self.ILEN,
                    "OLEN": self.OLEN,
                }
                records.append(record)

        except pd.errors.EmptyDataError:
            print(f"Error: Empty or corrupted CSV file: {file_path}")
        except pd.errors.ParserError as e:
            print(f"Error parsing CSV file {file_path}: {e}")
        except Exception as e:
            print(f"Unable to process file {file_path}: {e}")

        return records

    def _get_date_folders(self):
        """
        Get list of folders that match our date prefixes.
        Supports both old and new folder naming formats.
        Returns: list of (folder_name, folder_path) tuples
        """
        date_folders = []

        try:
            if not os.path.exists(self.data_dir):
                print(f"Error: Data directory not found: {self.data_dir}")
                return date_folders

            folder_list = os.listdir(self.data_dir)
            if not folder_list:
                print(f"Warning: No folders found in {self.data_dir}")
                return date_folders
        except PermissionError:
            print(f"Error: Permission denied accessing directory {self.data_dir}")
            return date_folders
        except Exception as e:
            print(f"Error accessing data directory {self.data_dir}: {e}")
            return date_folders

        for folder_name in folder_list:
            folder_path = os.path.join(self.data_dir, folder_name)
            if os.path.isdir(folder_path):
                # Check if folder ends with exactly "_offline" (not "_offline_old" etc.)
                if not folder_name.endswith("_offline"):
                    continue

                # Extract date from folder name supporting both old and new formats
                # Old format: YYYYMMDD_MODEL_VARIANT_offline or YYYYMMDDrc_MODEL_VARIANT_offline
                # New format: v0.4.9.post2-rocm630-mi30x-YYYYMMDD_MODEL_VARIANT_offline
                normalized_folder_date = self._extract_date_from_name(folder_name)

                if normalized_folder_date and any(normalized_folder_date == dp for dp in self.date_prefixes):
                    date_folders.append((folder_name, folder_path))

        return date_folders

    def read_and_process_files(self):
        """
        Reads CSV files from dated folders and processes the data.
        """
        date_folders = self._get_date_folders()

        for folder_name, folder_path in date_folders:
            try:
                file_list = os.listdir(folder_path)
            except Exception as e:
                print(f"Error accessing folder {folder_path}: {e}")
                continue

            for file_name in file_list:
                if file_name.endswith(".csv"):
                    # Extract date from filename supporting both old and new formats
                    date_str = self._extract_date_from_name(file_name)

                    if not date_str:
                        print(f"Skipping file with no extractable date in name: {file_name}")
                        continue

                    # Validate date format
                    try:
                        datetime.strptime(date_str, "%Y%m%d")
                    except ValueError:
                        print(f"Skipping file with invalid date format: {file_name}")
                        continue

                    file_path = os.path.join(folder_path, file_name)
                    if not os.path.exists(file_path):
                        print(f"Warning: File not found: {file_path}")
                        continue

                    # Parse the file and get records
                    file_records = self._parse_offline_csv_file(file_path, date_str)
                    self.all_records.extend(file_records)

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

        # Sort by date and batch_size
        try:
            summary_df = summary_df.sort_values(by=["date", "batch_size", "backend"])
        except KeyError as e:
            print(f"Error: Missing expected columns for sorting: {e}")
            # Try to save anyway without sorting

        output_file = os.path.join(
            self.data_dir, f"{self.output_model_name_prefix}_summary.csv"
        )
        try:
            # Ensure the directory exists
            os.makedirs(os.path.dirname(output_file), exist_ok=True)
            summary_df.to_csv(output_file, index=False)
            print(f"Offline summary CSV saved to: {output_file}")

            # Clean up old individual batch size files if they exist
            self._cleanup_old_batch_files()

            return output_file

        except PermissionError:
            print(f"Error: Permission denied writing to {output_file}")
            return None
        except Exception as e:
            print(f"Error saving summary CSV to {output_file}: {e}")
            return None

    def _cleanup_old_batch_files(self):
        """
        Remove old individual batch size CSV files if they exist.
        """
        batch_sizes = [1, 2, 4, 8, 16, 32, 64, 128, 256]
        # Extract just the model name without "_offline" suffix for old files
        model_name = self.output_model_name_prefix.replace("_offline", "")

        for batch_size in batch_sizes:
            old_file = os.path.join(self.data_dir, f"{model_name}_{batch_size}.csv")
            if os.path.exists(old_file):
                try:
                    os.remove(old_file)
                    print(f"Removed old batch file: {old_file}")
                except Exception as e:
                    print(f"Error removing old batch file {old_file}: {e}")

    def process_and_save(self):
        """
        Main orchestrator method for data processing.
        Returns the path to the generated summary CSV file.
        """
        print("=== DATA PROCESSING PHASE ===")
        self.read_and_process_files()
        return self.save_summary_csv()


class OfflineGraphPlotter:
    def __init__(self, summary_csv_path, plot_dir, model_name_in_plot):
        self.summary_csv_path = summary_csv_path
        self.plot_dir = plot_dir
        self.model_name_in_plot = model_name_in_plot
        self.df = None
        os.makedirs(self.plot_dir, exist_ok=True)
        self.expected_batch_sizes = {1, 2, 4, 8, 16, 32, 64, 128, 256}

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

            if self.df.empty:
                print(f"Warning: Summary CSV file is empty: {self.summary_csv_path}")
                return

            # Check for required columns
            required_columns = [
                "date",
                "batch_size",
                "E2E_Latency(s)",
                "E2E_Throughput(token/s)",
            ]
            missing_columns = [
                col for col in required_columns if col not in self.df.columns
            ]
            if missing_columns:
                print(
                    f"Error: Missing required columns {missing_columns} in {self.summary_csv_path}"
                )
                self.df = pd.DataFrame()
                return

            # Convert date to datetime
            self.df["date"] = pd.to_datetime(self.df["date"], format="%Y%m%d")
            # Ensure batch_size is integer
            self.df["batch_size"] = self.df["batch_size"].astype(int)
            # Sort by date and batch_size
            self.df = self.df.sort_values(["date", "batch_size"])

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
            self.df = pd.DataFrame()

    def filter_complete_dates(self):
        """
        Filters dataframe to only keep dates that have data for all expected batch sizes.
        """
        if self.df.empty:
            print("No data to filter.")
            return

        print(
            f"Filtering for dates with all required batch sizes: {sorted(list(self.expected_batch_sizes))}"
        )

        # Group by date and check which dates have the required batch sizes
        date_completeness = self.df.groupby("date")["batch_size"].apply(set)

        complete_dates = date_completeness[
            date_completeness.apply(lambda x: x.issuperset(self.expected_batch_sizes))
        ].index

        # Log incomplete dates for user feedback
        incomplete_dates = date_completeness[
            ~date_completeness.index.isin(complete_dates)
        ].index
        for date in incomplete_dates:
            present_bs = date_completeness[date]
            missing_bs = self.expected_batch_sizes - present_bs
            if missing_bs:
                print(
                    f"Date {date.strftime('%Y-%m-%d')}: Incomplete data. Missing batch sizes: {sorted(list(missing_bs))}"
                )

        if len(complete_dates) == 0:
            print("\nNo dates found with complete data for all required batch sizes.")
            self.df = pd.DataFrame()
        else:
            self.df = self.df[self.df["date"].isin(complete_dates)]
            print(
                f"\nFound {len(complete_dates)} dates with complete data: {[d.strftime('%Y-%m-%d') for d in sorted(complete_dates)]}"
            )







    def plot_combined_metrics(self):
        """
        Create a combined plot showing both latency and throughput trends for all batch sizes.
        X-axis shows all available dates without gaps.
        """
        print("Generating combined metrics plot...")
        if self.df.empty:
            print("No data available to plot.")
            return

        # Get unique dates from the entire dataframe to create a unified x-axis
        all_unique_dates = sorted(self.df["date"].unique())
        date_to_idx = {date: i for i, date in enumerate(all_unique_dates)}

        # Get unique batch sizes
        batch_sizes = sorted(self.df["batch_size"].unique())

        # Create figure with two subplots side by side
        fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(20, 8))

        # Plot latency trends
        for batch_size in batch_sizes:
            batch_data = self.df[self.df["batch_size"] == batch_size].copy()
            if not batch_data.empty:
                batch_data = batch_data.sort_values("date")
                # Calculate mean latency per date (in case of duplicates)
                latency_by_date = batch_data.groupby("date")["E2E_Latency(s)"].mean()

                # Map dates to indices for plotting
                x_indices = [date_to_idx[d] for d in latency_by_date.index]
                ax1.plot(
                    x_indices,
                    latency_by_date.values,
                    marker="o",
                    linestyle="-",
                    label=f"BS={batch_size}",
                )

        ax1.set_title(f"E2E Latency Trends - {self.model_name_in_plot}")
        ax1.set_xlabel("Date")
        ax1.set_ylabel("E2E Latency (s)")
        ax1.legend(bbox_to_anchor=(1.05, 1), loc="upper left")
        ax1.grid(True, alpha=0.3)
        ax1.set_xticks(range(len(all_unique_dates)))
        ax1.set_xticklabels(
            [d.strftime("%Y-%m-%d") for d in all_unique_dates], rotation=45, ha="right"
        )

        # Plot throughput trends
        for batch_size in batch_sizes:
            batch_data = self.df[self.df["batch_size"] == batch_size].copy()
            if not batch_data.empty:
                batch_data = batch_data.sort_values("date")
                # Calculate mean throughput per date
                throughput_by_date = batch_data.groupby("date")[
                    "E2E_Throughput(token/s)"
                ].mean()

                # Map dates to indices for plotting
                x_indices = [date_to_idx[d] for d in throughput_by_date.index]
                ax2.plot(
                    x_indices,
                    throughput_by_date.values,
                    marker="o",
                    linestyle="-",
                    label=f"BS={batch_size}",
                )

        ax2.set_title(f"E2E Throughput Trends - {self.model_name_in_plot}")
        ax2.set_xlabel("Date")
        ax2.set_ylabel("E2E Throughput (token/s)")
        ax2.legend(bbox_to_anchor=(1.05, 1), loc="upper left")
        ax2.grid(True, alpha=0.3)
        ax2.set_xticks(range(len(all_unique_dates)))
        ax2.set_xticklabels(
            [d.strftime("%Y-%m-%d") for d in all_unique_dates], rotation=45, ha="right"
        )

        # Adjust layout
        plt.tight_layout()

        # Save plot
        current_date_str = datetime.now().strftime("%Y%m%d")

        # Extract just the base model name for filename (e.g. GROK1 from GROK1_MOE-I4F8_offline)
        # Generate filename format: YYYYMMDD_MODEL_offline.png (e.g. 20250617_GROK1_offline.png)
        base_model_name = self.model_name_in_plot.split('_')[0]

        output_file = os.path.join(
            self.plot_dir,
            f"{current_date_str}_{base_model_name}_offline.png",
        )
        try:
            plt.savefig(output_file, dpi=150, bbox_inches="tight")
            print(f"Combined plot saved to: {output_file}")
        except Exception as e:
            print(f"Error saving combined plot to {output_file}: {e}")
        finally:
            plt.close()

    def generate_and_save_plots(self):
        """
        Main method to orchestrate reading data and generating plots.
        """
        print("=== PLOT GENERATION PHASE ===")
        self.read_summary_csv()
        if not self.df.empty:
            self.filter_complete_dates()  # Filter for complete data
            if not self.df.empty:
                self.plot_combined_metrics()
        else:
            print("No data to plot. Please check the summary CSV file.")


def main():
    """
    Main function that orchestrates both data processing and plot generation.
    """
    MODEL_CONFIGS = {
        'grok': {
            'variant_name': 'GROK1',
            'output_prefix_template': '{variant_name}_MOE-I4F8_offline',
            'model_name_template': '{variant_name}_MOE-I4F8_offline',
            'ilen': 1024,
            'olen': 128,
        },
        'deepseek': {
            'variant_name': 'DeepSeek-V3-0324',
            'output_prefix_template': '{variant_name}_FP8_offline',
            'model_name_template': '{variant_name}_FP8_offline',
            'ilen': 1024,
            'olen': 128,
        }
    }

    parser = argparse.ArgumentParser(
        description="Process offline benchmark CSV files and generate plots",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    # Simplified model selection
    parser.add_argument(
        "-m", "--model",
        type=str,
        default='grok',
        choices=MODEL_CONFIGS.keys(),
        help="The model to process. Options: 'grok', 'deepseek'."
    )

    # Arguments for paths and names (default to None, will be set from config)
    parser.add_argument("--data-dir", type=str, default=None, help="Override data directory path.")
    parser.add_argument("--output-prefix", type=str, default=None, help="Override output CSV file prefix.")
    parser.add_argument("--plot-dir", type=str, default=None, help="Override plot directory path.")
    parser.add_argument("--model-name", type=str, default=None, help="Override model name in plot titles.")

    # Other arguments
    parser.add_argument(
        '--ilen',
        type=int,
        default=None,
        help='Input length for records. Overrides model-specific defaults.'
    )

    parser.add_argument(
        '--olen',
        type=int,
        default=None,
        help='Output length for records. Overrides model-specific defaults.'
    )

    parser.add_argument(
        '--days',
        type=int,
        default=30,
        help='Number of days to look back for processing'
    )

    # Control arguments
    parser.add_argument(
        '--process-only',
        action='store_true',
        help='Only process CSV files, skip plot generation'
    )

    parser.add_argument(
        '--plot-only',
        action='store_true',
        help='Only generate plots, skip CSV processing (requires existing summary CSV)'
    )

    parser.add_argument(
        '--summary-csv',
        type=str,
        help='Path to existing summary CSV file (for --plot-only mode)'
    )

    args = parser.parse_args()

    # --- Configuration Setup ---
    config = MODEL_CONFIGS[args.model]
    variant_name = config['variant_name']

    # Set values from config, allowing overrides from command line
    if args.data_dir is None:
        args.data_dir = f'/mnt/raid/michael/sgl_benchmark_ci/offline/{variant_name}'
    if args.output_prefix is None:
        args.output_prefix = config['output_prefix_template'].format(variant_name=variant_name)
    if args.plot_dir is None:
        args.plot_dir = f'/mnt/raid/michael/sgl_benchmark_ci/plots_server/{variant_name}/offline'
    if args.model_name is None:
        args.model_name = config['model_name_template'].format(variant_name=variant_name)
    if args.ilen is None:
        args.ilen = config['ilen']
    if args.olen is None:
        args.olen = config['olen']

    # Validate mutually exclusive options
    if args.process_only and args.plot_only:
        parser.error("--process-only and --plot-only are mutually exclusive")

    # Print configuration
    print("=== CONFIGURATION ===")
    print(f"Model: {args.model} (variant: {variant_name})")
    print(f"Data directory: {args.data_dir}")
    print(f"Output prefix: {args.output_prefix}")
    print(f"Plot directory: {args.plot_dir}")
    print(f"Model name: {args.model_name}")
    print(f"Input length: {args.ilen}")
    print(f"Output length: {args.olen}")
    print(f"Days to process: {args.days}")
    print(f"Process only: {args.process_only}")
    print(f"Plot only: {args.plot_only}")
    print()

    summary_csv_path = None

    # Phase 1: Data Processing
    if not args.plot_only:
        processor = OfflineDataProcessor(
            args.data_dir,
            args.output_prefix,
            args.ilen,
            args.olen,
            args.days
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
                # Auto-generate path
                summary_csv_path = os.path.join(
                    args.data_dir, f"{args.output_prefix}_summary.csv"
                )

        if not summary_csv_path or not os.path.exists(summary_csv_path):
            print(f"ERROR: Summary CSV not found: {summary_csv_path}")
            return 1

        # Create plotter and generate plots
        plotter = OfflineGraphPlotter(
            summary_csv_path,
            args.plot_dir,
            args.model_name
        )
        plotter.generate_and_save_plots()

    print("=== COMPLETE ===")
    print("Both processing and plotting completed successfully!")
    return 0


if __name__ == "__main__":
    exit(main())
