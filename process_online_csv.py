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
from collections import defaultdict  # For cleaner dictionary handling

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
        
        # Compile regex pattern for KV cache info parsing (used repeatedly)
        self.kv_cache_pattern = re.compile(r"#tokens: (\d+), K size: ([\d\.]+) GB, V size: ([\d\.]+) GB")

    def _parse_kv_cache_info(self, csv_dir):
        """
        Parse KV cache information from server log files.
        Returns: tuple of (num_tokens, kv_size_gb) or (pd.NA, pd.NA) if not found
        """
        num_tokens = pd.NA
        kv_size_gb = pd.NA
        
        server_logs = ['server_output_aiter.log', 'server_output_aiter_decode.log']
        for log_file in server_logs:
            log_path = os.path.join(csv_dir, log_file)
            if not os.path.exists(log_path):
                continue
                
            try:
                with open(log_path, 'r') as f:
                    for line in f:
                        if "KV Cache is allocated." in line and "#tokens:" in line:
                            match = self.kv_cache_pattern.search(line)
                            if match:
                                try:
                                    num_tokens = int(match.group(1))
                                    k_size = float(match.group(2))
                                    v_size = float(match.group(3))
                                    kv_size_gb = k_size + v_size
                                    return num_tokens, kv_size_gb  # Found values, return early
                                except ValueError:
                                    print(f"Warning: Could not parse KV cache info from line: {line.strip()} in {log_path}")
                            else:
                                print(f"Warning: Found KV Cache allocation line but failed to parse: {line.strip()} in {log_path}")
            except Exception as e:
                print(f"Error reading server log {log_path}: {e}")
        
        return num_tokens, kv_size_gb

    def _find_request_rates(self, lines):
        """
        Find and parse request rates from the CSV lines.
        Returns: list of request rates (integers) or empty list if not found
        """
        request_rates = []
        temp_iter = iter(lines)
        
        try:
            line = next(temp_iter)
            while True: # Loop until found or end of file
                # This loop will NOT be infinite because:
                # 1. It breaks when request rates are found (see 'break' below)
                # 2. next() will raise StopIteration when reaching end of file, which is caught below
                # Check if current line is a known metric section header
                if any(header in line for header in ["Median E2E Latency (ms, lower better)", 
                                                     "Median TTFT (ms, lower better)", 
                                                     "Median ITL (ms, lower better)"]):
                    req_rate_line_candidate = next(temp_iter) # The line after header
                    if "request rate" in req_rate_line_candidate:
                        parts = req_rate_line_candidate.strip().split('\t')
                        if len(parts) > 1:
                            request_rates = [int(r) for r in parts[1:]]
                            break # Found request rates
                line = next(temp_iter)
                # Skip empty lines but keep searching
                if not line.strip():
                    continue
        except StopIteration:
            pass # End of file reached
        
        return request_rates

    def _parse_metric_section(self, lines, metric_type_label, metric_df_name, request_rates):
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
            
            # Skip the "request rate" line (rates already parsed)
            next(section_iter)
            # Skip the "H100" data line
            next(section_iter)
            
            # Parse MI300x-aiter line
            aiter_values_str = next(section_iter).strip().split('\t')[1:]
            for i, rr in enumerate(request_rates):
                try:
                    value = float(aiter_values_str[i])
                except (ValueError, IndexError):
                    value = pd.NA
                    if IndexError:
                        print(f"Warning: Index out of bounds for aiter data, {metric_type_label}, rate {rr}")
                metrics_data[('aiter', rr)] = value

            # Parse MI300x-aiter_decode line
            decode_values_str = next(section_iter).strip().split('\t')[1:]
            for i, rr in enumerate(request_rates):
                try:
                    value = float(decode_values_str[i])
                except (ValueError, IndexError):
                    value = pd.NA
                    if IndexError:
                        print(f"Warning: Index out of bounds for aiter_decode data, {metric_type_label}, rate {rr}")
                metrics_data[('aiter_decode', rr)] = value
                
        except StopIteration:
            print(f"Warning: Section for '{metric_type_label}' not found or incomplete.")
        except Exception as e:
            print(f"Warning: Error parsing section '{metric_type_label}': {e}")
        
        return metrics_data

    def _parse_single_online_csv(self, file_path, date_str):
        """
        Parses a single online benchmark summary CSV file.
        The CSV contains multiple tables for E2E Latency, TTFT, and ITL.
        Also parses KV cache info from server log files in the same directory.
        """
        file_records = []
        num_tokens, kv_size_gb = self._parse_kv_cache_info(os.path.dirname(file_path))
        
        try:
            with open(file_path, 'r') as f:
                lines = f.readlines()
        except Exception as e:
            print(f"Error opening or reading file {file_path}: {e}")
            return file_records

        request_rates = self._find_request_rates(lines)

        if not request_rates:
            print(f"Could not find or parse request rates line in {file_path}.")
            return file_records

        # Parse each metric section and collect all metrics
        metrics_map = defaultdict(dict)  # Stores { (mode, request_rate): {metric_name: value} }
        
        for metric_type_label, metric_df_name in [
            ("Median E2E Latency (ms, lower better)", "E2E_Latency_ms"),
            ("Median TTFT (ms, lower better)", "TTFT_ms"),
            ("Median ITL (ms, lower better)", "ITL_ms")
        ]:
            section_metrics = self._parse_metric_section(lines, metric_type_label, metric_df_name, request_rates)
            for (mode, rate), value in section_metrics.items():
                key = (mode, rate)
                metrics_map[key][metric_df_name] = value
        
        # Build records from metrics_map
        for (mode, rate), metrics in metrics_map.items():
            record = {
                'date': date_str,
                'mode': mode,
                'request_rate': rate,
                'E2E_Latency_ms': metrics.get("E2E_Latency_ms", pd.NA),
                'TTFT_ms': metrics.get("TTFT_ms", pd.NA),
                'ITL_ms': metrics.get("ITL_ms", pd.NA),
                'num_tokens': num_tokens,
                'KV_size_GB': kv_size_gb
            }
            file_records.append(record)
            
        return file_records

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
                # Folder name format: ${LATEST_TAG}_${MODEL_NAME}_MOE-I4F8_online
                # LATEST_TAG can be YYYYMMDD or YYYYMMDDrc
                folder_date_part = folder_name.split('_')[0]
                normalized_folder_date = folder_date_part.replace("rc", "")

                if any(normalized_folder_date == dp for dp in self.date_prefixes):
                    try:
                        file_list = os.listdir(folder_path)
                    except Exception as e:
                        print(f"Error accessing folder {folder_path}: {e}")
                        continue
                        
                    for file_name in file_list:
                        # CSV filename: ${LATEST_TAG}_${MODEL_NAME}_MOE-I4F8_online.csv
                        if file_name.endswith('_online.csv'): 
                            date_str_from_file = file_name.split('_')[0].replace("rc", "")
                            try: # Validate date string format
                                datetime.strptime(date_str_from_file, '%Y%m%d')
                            except ValueError:
                                print(f"Skipping file with invalid date format in name: {file_name} in folder {folder_name}")
                                continue

                            file_path = os.path.join(folder_path, file_name)
                            if not os.path.exists(file_path):
                                print(f"Warning: File not found: {file_path}")
                                continue
                                
                            file_specific_records = self._parse_single_online_csv(file_path, date_str_from_file)
                            self.all_records.extend(file_specific_records)
    
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

        # Sort by date, then mode, then request_rate for consistent output
        try:
            summary_df = summary_df.sort_values(by=['date', 'mode', 'request_rate'])
        except KeyError as e:
            print(f"Error: Missing expected columns for sorting: {e}")
            # Try to save anyway without sorting
        
        output_file = os.path.join(self.data_dir, f"{self.output_model_name_prefix}_summary.csv")
        try:
            # Ensure the directory exists
            os.makedirs(os.path.dirname(output_file), exist_ok=True)
            summary_df.to_csv(output_file, index=False)
            print(f"Online summary CSV saved to: {output_file}")
        except PermissionError:
            print(f"Error: Permission denied writing to {output_file}")
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