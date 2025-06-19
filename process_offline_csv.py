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

import os
import pandas as pd
from datetime import datetime, timedelta
from collections import defaultdict

class OfflineDataProcessor:
    def __init__(self, data_dir, output_model_name_prefix):
        """
        Initializes the OfflineDataProcessor class with the directory path where CSV files are stored.
        Args:
            data_dir: Path to the directory containing dated run folders
            output_model_name_prefix: Prefix for the output summary CSV file
        """
        self.data_dir = data_dir
        self.output_model_name_prefix = output_model_name_prefix
        self.ILEN = 1024
        self.OLEN = 128
        self.all_records = []
        current_date = datetime.today().date()
        # Generate list of dates for last 30 days excluding today
        self.date_prefixes = [(current_date - timedelta(days=i)).strftime('%Y%m%d') for i in range(1, 31)]

    def _parse_offline_csv_file(self, file_path, date_str):
        """
        Parse a single offline CSV file and extract batch data.
        Also tries to read backend information from config.json in the same directory.
        Returns: list of records for all batch sizes in the file
        """
        records = []
        backend = 'unknown'  # Default backend
        
        # Try to read backend info from config.json if it exists
        config_path = os.path.join(os.path.dirname(file_path), 'config.json')
        if os.path.exists(config_path):
            try:
                import json
                with open(config_path, 'r') as f:
                    config = json.load(f)
                    backend = config.get('attention_backend', 'unknown')
            except Exception as e:
                print(f"Warning: Could not read config.json from {config_path}: {e}")
        
        try:
            df = pd.read_csv(file_path)
            
            if df.empty:
                print(f"Warning: Empty CSV file: {file_path}")
                return records

            # Check if Backend column exists
            has_backend_column = 'Backend' in df.columns or (len(df.columns) > 10 and df.columns[4] == 'Backend')
            
            # Set column names based on whether Backend column exists
            if has_backend_column or len(df.columns) == 11:
                # New format with Backend column
                df.columns = ['TP','batch_size','IL','OL','Backend','Prefill_latency(s)','Median_decode_latency(s)',
                             'E2E_Latency(s)','Prefill_Throughput(token/s)','Median_Decode_Throughput(token/s)',
                             'E2E_Throughput(token/s)']
            else:
                # Old format without Backend column
                df.columns = ['TP','batch_size','IL','OL','Prefill_latency(s)','Median_decode_latency(s)',
                             'E2E_Latency(s)','Prefill_Throughput(token/s)','Median_Decode_Throughput(token/s)',
                             'E2E_Throughput(token/s)']
            
            # Process each batch size
            for batch_size in df['batch_size'].unique():
                batch_data = df[df['batch_size'] == batch_size]
                # Drop rows where E2E_Latency is NaN
                batch_data = batch_data.dropna(subset=['E2E_Latency(s)'])
                
                if batch_data.empty:
                    print(f"Warning: No valid data for batch size {batch_size} in {file_path}")
                    continue
                
                # Get backend from CSV if available, otherwise use from config.json
                if 'Backend' in batch_data.columns:
                    # Use the most common backend for this batch size
                    csv_backend = batch_data['Backend'].mode()[0] if not batch_data['Backend'].empty else backend
                else:
                    csv_backend = backend
                
                # Use mean for aggregation if multiple rows per batch size
                record = {
                    'date': date_str,
                    'batch_size': int(batch_size),
                    'backend': csv_backend,
                    'E2E_Latency(s)': batch_data['E2E_Latency(s)'].mean(),
                    'E2E_Throughput(token/s)': batch_data['E2E_Throughput(token/s)'].mean(),
                    'ILEN': self.ILEN,
                    'OLEN': self.OLEN
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
                # Check if folder matches any date prefix
                for date_prefix in self.date_prefixes:
                    if folder_name.startswith((date_prefix + "_", date_prefix + "rc" + "_")):
                        date_folders.append((folder_name, folder_path))
                        break
                        
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
                if file_name.endswith('.csv'):
                    # Extract date from filename
                    date_str = file_name.split('_')[0].replace("rc", "")
                    
                    # Validate date format
                    try:
                        datetime.strptime(date_str, '%Y%m%d')
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
        """
        if not self.all_records:
            print("No data processed. Skipping CSV generation.")
            return

        try:
            summary_df = pd.DataFrame(self.all_records)
        except Exception as e:
            print(f"Error creating DataFrame from records: {e}")
            return
        
        if summary_df.empty:
            print("No records to save after processing. Skipping CSV generation.")
            return

        # Sort by date and batch_size
        try:
            summary_df = summary_df.sort_values(by=['date', 'batch_size', 'backend'])
        except KeyError as e:
            print(f"Error: Missing expected columns for sorting: {e}")
            # Try to save anyway without sorting
        
        output_file = os.path.join(self.data_dir, f"{self.output_model_name_prefix}_summary.csv")
        try:
            # Ensure the directory exists
            os.makedirs(os.path.dirname(output_file), exist_ok=True)
            summary_df.to_csv(output_file, index=False)
            print(f"Offline summary CSV saved to: {output_file}")
        except PermissionError:
            print(f"Error: Permission denied writing to {output_file}")
        except Exception as e:
            print(f"Error saving summary CSV to {output_file}: {e}")
            
        # Clean up old individual batch size files if they exist
        self._cleanup_old_batch_files()

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
        Main orchestrator method.
        """
        self.read_and_process_files()
        self.save_summary_csv()

if __name__ == "__main__":
    # Path to the parent directory containing dated folders
    data_dir = "/mnt/raid/michael/sgl_benchmark_ci/offline/GROK1"
    
    # This prefix is used for the output summary CSV file name
    output_model_name_prefix = "GROK1_MOE-I4F8_offline"

    processor = OfflineDataProcessor(data_dir, output_model_name_prefix)
    processor.process_and_save()


