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

class BatchDataProcessor:
    def __init__(self, data_dir, model_name):
        """
        Initializes the BatchDataProcessor class with the directory path where CSV files are stored.
        """
        self.data_dir = data_dir
        self.model_name=model_name
        self.ILEN = 1024
        self.OLEN = 128
        # List to store the file names for the new CSV files
        # We are creating new CSV files for each batch_size, and associated data
        self.batch_size_files = {}
        current_date=datetime.today().date()
        # Generate list of dates for last 30 days excluding today, YYYYMMDD format
        # TBD: excluded today since cron job didn't collect any perf for today
        self.date_prefixes=[(current_date - timedelta(days=i)).strftime('%Y%m%d') for i in range(1, 31)]

    def read_and_process_file(self):
        """
        Reads a CSV file, processes the data for each batch size.
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
                for unique_date_prefix in self.date_prefixes:
                    if folder_name.startswith((unique_date_prefix + "_", unique_date_prefix + "rc" + "_")):
                        try:
                            file_list = os.listdir(folder_path)
                        except Exception as e:
                            print(f"Error accessing folder {folder_path}: {e}")
                            continue
                            
                        for file_name in file_list:
                            if file_name.endswith('.csv'):
                                date_str = file_name.split('_')[0]
                                try:
                                    # Reading the CSV file into a DataFrame
                                    file_path = os.path.join(folder_path, file_name)
                                    if not os.path.exists(file_path):
                                        print(f"Warning: File not found: {file_path}")
                                        continue
                                        
                                    df = pd.read_csv(file_path)
                                    
                                    if df.empty:
                                        print(f"Warning: Empty CSV file: {file_path}")
                                        continue

                                    # Columns from offline csv file
                                    #TBD: Can adjust columns accordingly
                                    expected_columns = 10
                                    if len(df.columns) != expected_columns:
                                        print(f"Warning: Expected {expected_columns} columns but found {len(df.columns)} in {file_path}")
                                        # Try to proceed anyway if we have at least the required columns
                                        
                                    df.columns = ['TP','batch_size','IL','OL','Prefill_latency(s)','Median_decode_latency(s)','E2E_Latency(s)','Prefill_Throughput(token/s)','Median_Decode_Throughput(token/s)','E2E_Throughput(token/s)']
                            
                                    # Process each batch size separately and create a csv accordingly
                                    for batch_size in df['batch_size'].unique():
                                        # Filter the rows corresponding to the current batch size and store relevant data
                                        batch_data = df[df['batch_size'] == batch_size][['batch_size','E2E_Latency(s)','E2E_Throughput(token/s)']]
                                        # Drop rows where 'E2E_Latency(s)' is NaN (empty) in the batch_data
                                        batch_data = batch_data.dropna(subset=['E2E_Latency(s)'])
                                        
                                        if batch_data.empty:
                                            print(f"Warning: No valid data for batch size {batch_size} in {file_path}")
                                            continue
                                            
                                        # Add a date column in the batch_data
                                        batch_data.loc[:, 'date'] = date_str  # Add the date column
                                        batch_data.loc[:, 'ILEN'] = self.ILEN # Add ILEN column
                                        batch_data.loc[:, 'OLEN'] = self.OLEN # Add OLEN column
            
                                        if batch_size not in self.batch_size_files:
                                            self.batch_size_files[batch_size] = []

                                        # Append the batch data for the current batch_size
                                        self.batch_size_files[batch_size].append(batch_data[['date', 'batch_size', 'E2E_Latency(s)', 'E2E_Throughput(token/s)', 'ILEN', 'OLEN']])
                                except pd.errors.EmptyDataError:
                                    print(f"Error: Empty or corrupted CSV file: {file_path}")
                                except pd.errors.ParserError as e:
                                    print(f"Error parsing CSV file {file_path}: {e}")
                                except Exception as e:
                                    print(f"Unable to open the file {file_path}: {e}")

    def save_files(self):
        """
        Writes the processed data per batch size into a separate CSV files.
        """
        if not self.batch_size_files:
            print("Warning: No data to save. No batch files were processed successfully.")
            return
            
        # Write the data to new CSV files, one for each batch size
        for batch_size, data_list in self.batch_size_files.items():
            if not data_list:
                print(f"Warning: No data to save for batch size {batch_size}")
                continue
                
            # Concatenating all the dataframes for the current batch size
            try:
                total_data = pd.concat(data_list, ignore_index=True)
            except Exception as e:
                print(f"Error concatenating data for batch size {batch_size}: {e}")
                continue
    
            # Writing the full data for the current batch size to a CSV file
            output_file = os.path.join(self.data_dir, f"{self.model_name}_{batch_size}.csv")
            
            try:
                # Ensure the directory exists
                os.makedirs(os.path.dirname(output_file), exist_ok=True)
                total_data.to_csv(output_file, index=False)
                print(f"CSV created for batch size {batch_size}: {output_file}")
            except PermissionError:
                print(f"Error: Permission denied writing to {output_file}")
            except Exception as e:
                print(f"Error saving CSV file {output_file}: {e}")

    def process_and_save(self):
        """
        Function to process the csv files and save the reorganized data per batch size
        """
        # Process all csv files in the data dir
        self.read_and_process_file()

        # Save the processed data as csv files per batch
        self.save_files()

if __name__ == "__main__":
    
    # Path to the parent directory containing dated folders
    # TBD: Modify this directory accordingly to where you have data and want to save data
    data_dir = "/mnt/raid/michael/sgl_benchmark_ci/offline/GROK1"

    model_name="GROK1_MOE-I4F8"
    batch_proc = BatchDataProcessor(data_dir, model_name)

    # Process and save the batch size files
    batch_proc.process_and_save()


