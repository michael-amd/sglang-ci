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
import re # Import re for regular expressions

class OnlineDataProcessor:
    def __init__(self, data_dir, output_model_name_prefix):
        """
        Initializes the OnlineDataProcessor.
        Args:
            data_dir: Path to the directory containing dated run folders (e.g., .../online/GROK1).
            output_model_name_prefix: Prefix for the output summary CSV file (e.g., GROK1_MOE-I4F8_online).
        """
        self.data_dir = data_dir
        self.output_model_name_prefix = output_model_name_prefix
        self.all_records = []
        current_date = datetime.today().date()
        # Generate list of dates for last 30 days excluding today
        self.date_prefixes = [(current_date - timedelta(days=i)).strftime('%Y%m%d') for i in range(1, 31)]

    def _parse_single_online_csv(self, file_path, date_str):
        """
        Parses a single online benchmark summary CSV file.
        The CSV contains multiple tables for E2E Latency, TTFT, and ITL.
        Also parses KV cache info from server log files in the same directory.
        """
        file_records = []
        num_tokens = pd.NA
        kv_size_gb = pd.NA
        
        # Get the directory containing the CSV file
        csv_dir = os.path.dirname(file_path)
        
        # Look for server log files and parse KV cache info
        server_logs = ['server_output_aiter.log', 'server_output_aiter_decode.log']
        for log_file in server_logs:
            log_path = os.path.join(csv_dir, log_file)
            if os.path.exists(log_path):
                try:
                    with open(log_path, 'r') as f:
                        for line in f:
                            if "KV Cache is allocated." in line and "#tokens:" in line:
                                match = re.search(r"#tokens: (\d+), K size: ([\d\.]+) GB, V size: ([\d\.]+) GB", line)
                                if match:
                                    try:
                                        num_tokens = int(match.group(1))
                                        k_size = float(match.group(2))
                                        v_size = float(match.group(3))
                                        kv_size_gb = k_size + v_size  # Total KV size is K + V
                                        break # Found KV cache info
                                    except ValueError:
                                        print(f"Warning: Could not parse KV cache info from line: {line.strip()} in {log_path}")
                                else:
                                    print(f"Warning: Found KV Cache allocation line but failed to parse details with regex: {line.strip()} in {log_path}")
                    if not pd.isna(num_tokens):  # If we found the values, stop looking
                        break
                except Exception as e:
                    print(f"Error reading server log {log_path}: {e}")
        
        try:
            with open(file_path, 'r') as f:
                lines = f.readlines()
        except Exception as e:
            print(f"Error opening or reading file {file_path}: {e}")
            return file_records

        metrics_map = {}  # Stores { (mode, request_rate): {metric_name: value} }
        request_rates = []

        # First, find request rates (they are common for all metrics sections)
        # Typically found after a section header like "Median E2E Latency..."
        temp_iter_for_rates = iter(lines)
        try:
            line = next(temp_iter_for_rates)
            while True: # Loop until found or end of file
                # Check if current line is a known metric section header
                if any(header in line for header in ["Median E2E Latency (ms, lower better)", 
                                                     "Median TTFT (ms, lower better)", 
                                                     "Median ITL (ms, lower better)"]):
                    req_rate_line_candidate = next(temp_iter_for_rates) # The line after header
                    if "request rate" in req_rate_line_candidate:
                        parts = req_rate_line_candidate.strip().split('\t')
                        if len(parts) > 1:
                            request_rates = [int(r) for r in parts[1:]]
                            break # Found request rates
                line = next(temp_iter_for_rates)
        except StopIteration:
            pass # Will be handled by the check below

        if not request_rates:
            print(f"Could not find or parse request rates line in {file_path}.")
            return file_records

        # Parse each metric section
        for metric_type_label, metric_df_name in [
            ("Median E2E Latency (ms, lower better)", "E2E_Latency_ms"),
            ("Median TTFT (ms, lower better)", "TTFT_ms"),
            ("Median ITL (ms, lower better)", "ITL_ms")
        ]:
            section_iter = iter(lines) # Fresh iterator for each section search
            try:
                line = next(section_iter)
                while metric_type_label not in line: # Find section header
                    line = next(section_iter)
                
                # Skip the "request rate" line (rates already parsed)
                next(section_iter) 
                # Skip the "H100" data line
                next(section_iter)
                
                # MI300x-aiter line
                aiter_values_str = next(section_iter).strip().split('\t')[1:]
                for i, rr in enumerate(request_rates):
                    key = ('aiter', rr)
                    if key not in metrics_map: metrics_map[key] = {}
                    try:
                        metrics_map[key][metric_df_name] = float(aiter_values_str[i])
                    except (ValueError, IndexError):
                        metrics_map[key][metric_df_name] = pd.NA
                        if IndexError: print(f"Warning: Index out of bounds for aiter data, {metric_type_label}, rate {rr} in {file_path}")


                # MI300x-aiter_decode line
                decode_values_str = next(section_iter).strip().split('\t')[1:]
                for i, rr in enumerate(request_rates):
                    key = ('aiter_decode', rr)
                    if key not in metrics_map: metrics_map[key] = {}
                    try:
                        metrics_map[key][metric_df_name] = float(decode_values_str[i])
                    except (ValueError, IndexError):
                        metrics_map[key][metric_df_name] = pd.NA
                        if IndexError: print(f"Warning: Index out of bounds for aiter_decode data, {metric_type_label}, rate {rr} in {file_path}")
                
                # Subsequent lines (ratios, empty line) are implicitly skipped 
                # as the outer loop starts a new search with section_iter.

            except StopIteration:
                print(f"Warning: Section for '{metric_type_label}' not found or incomplete in {file_path}.")
            except Exception as e:
                print(f"Warning: Error parsing section '{metric_type_label}' in {file_path}: {e}")
        
        # Consolidate records from metrics_map
        for (mode, rate), metrics_values in metrics_map.items():
            record = {
                'date': date_str,
                'mode': mode,
                'request_rate': rate,
                'E2E_Latency_ms': metrics_values.get("E2E_Latency_ms", pd.NA),
                'TTFT_ms': metrics_values.get("TTFT_ms", pd.NA),
                'ITL_ms': metrics_values.get("ITL_ms", pd.NA),
                'num_tokens': num_tokens,      # Add parsed #tokens
                'KV_size_GB': kv_size_gb       # Add parsed KV size
            }
            file_records.append(record)
            
        return file_records

    def read_and_process_files(self):
        """
        Iterates through dated folders, finds online CSVs, and processes them.
        """
        for folder_name in os.listdir(self.data_dir):
            folder_path = os.path.join(self.data_dir, folder_name)
            if os.path.isdir(folder_path):
                # Folder name format: ${LATEST_TAG}_${MODEL_NAME}_MOE-I4F8_online
                # LATEST_TAG can be YYYYMMDD or YYYYMMDDrc
                folder_date_part = folder_name.split('_')[0]
                normalized_folder_date = folder_date_part.replace("rc", "")

                if any(normalized_folder_date == dp for dp in self.date_prefixes):
                    for file_name in os.listdir(folder_path):
                        # CSV filename: ${LATEST_TAG}_${MODEL_NAME}_MOE-I4F8_online.csv
                        if file_name.endswith('_online.csv'): 
                            date_str_from_file = file_name.split('_')[0].replace("rc", "")
                            try: # Validate date string format
                                datetime.strptime(date_str_from_file, '%Y%m%d')
                            except ValueError:
                                print(f"Skipping file with invalid date format in name: {file_name} in folder {folder_name}")
                                continue

                            file_path = os.path.join(folder_path, file_name)
                            file_specific_records = self._parse_single_online_csv(file_path, date_str_from_file)
                            self.all_records.extend(file_specific_records)
    
    def save_summary_csv(self):
        """
        Saves the aggregated data into a single summary CSV file.
        """
        if not self.all_records:
            print("No data processed. Skipping CSV generation.")
            return

        summary_df = pd.DataFrame(self.all_records)
        
        if summary_df.empty:
            print("No records to save after processing. Skipping CSV generation.")
            return

        # Sort by date, then mode, then request_rate for consistent output
        summary_df = summary_df.sort_values(by=['date', 'mode', 'request_rate'])
        
        output_file = os.path.join(self.data_dir, f"{self.output_model_name_prefix}_summary.csv")
        try:
            summary_df.to_csv(output_file, index=False)
            print(f"Online summary CSV saved to: {output_file}")
        except Exception as e:
            print(f"Error saving summary CSV to {output_file}: {e}")

    def process_and_save(self):
        """
        Main orchestrator method.
        """
        self.read_and_process_files()
        self.save_summary_csv()

if __name__ == "__main__":
    # data_dir is the parent directory of the dated run folders.
    # For online benchmarks, this is typically .../online/${MODEL_NAME_FROM_SCRIPT}
    # e.g., /mnt/raid/michael/sgl_benchmark_ci/online/GROK1
    data_dir = "/mnt/raid/michael/sgl_benchmark_ci/online/GROK1" 
    
    # This prefix is used for the output summary CSV file name.
    # e.g., GROK1_MOE-I4F8_online_summary.csv
    # This should match the model configuration used in the benchmark script.
    output_model_name_prefix = "GROK1_MOE-I4F8_online" 

    processor = OnlineDataProcessor(data_dir, output_model_name_prefix)
    processor.process_and_save() 