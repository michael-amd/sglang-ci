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
import matplotlib.pyplot as plt
from datetime import datetime
import re

class OfflineGraphPlotter:
    def __init__(self, batch_dir, plot_dir, model_name):
        self.batch_dir = batch_dir
        self.plot_dir = plot_dir
        self.model_name = model_name
        self.batch_files=[]
        os.makedirs(self.plot_dir, exist_ok=True)

    def get_batch_files(self):
        """
        Get all CSV files for the batches
        """
        try:
            if not os.path.exists(self.batch_dir):
                print(f"Error: Batch directory not found: {self.batch_dir}")
                self.batch_files = []
                return
                
            self.batch_files = [f for f in os.listdir(self.batch_dir) if f.endswith(".csv")]
            if not self.batch_files:
                print(f"Warning: No CSV files found in {self.batch_dir}")
                return
                
            self.batch_files = sorted(self.batch_files, key=lambda f: int(re.search(r'_(\d+)', f).group(1)))
        except AttributeError as e:
            print(f"Error: CSV files do not match expected naming pattern (expecting _<number>): {e}")
            self.batch_files = []
        except Exception as e:
            print(f"Error accessing batch directory {self.batch_dir}: {e}")
            self.batch_files = []

    def read_csv(self, file_path):
        """
        Reads a CSV file into a pandas DataFrame
        """
        try:
            if not os.path.exists(file_path):
                print(f"Error: CSV file not found: {file_path}")
                return pd.DataFrame()
                
            df = pd.read_csv(file_path)
            
            if df.empty:
                print(f"Warning: CSV file is empty: {file_path}")
                return df
                
            # Check for required columns
            required_columns = ['date']
            missing_columns = [col for col in required_columns if col not in df.columns]
            if missing_columns:
                print(f"Error: Missing required columns {missing_columns} in {file_path}")
                return pd.DataFrame()
                
            df['date'] = pd.to_datetime(df['date'], format='%Y%m%d')
            return df.sort_values('date')
        except pd.errors.EmptyDataError:
            print(f"Error: CSV file is empty or corrupted: {file_path}")
            return pd.DataFrame()
        except pd.errors.ParserError as e:
            print(f"Error parsing CSV file {file_path}: {e}")
            return pd.DataFrame()
        except Exception as e:
            print(f"Error reading CSV file {file_path}: {e}")
            return pd.DataFrame()

    def plot_latency_vs_date(self, output_file):
        """
        Create a latency-date subplot for each batch size (labeling x-axis as Image name)
        """
        total_files = len(self.batch_files)
        fig, axes = plt.subplots(total_files, 1, figsize=(10, 4 * total_files), sharex=True)
        
        if total_files == 1:
            axes = [axes]

        for i, csv_file in enumerate(self.batch_files):
            csv_path = os.path.join(self.batch_dir, csv_file)
            filename_part = csv_file.split('.')[0]
            model_prefix, numeric_batch_size = filename_part.rsplit('_', 1)
            
            df = self.read_csv(csv_path)
            
            # Skip if dataframe is empty
            if df.empty:
                axes[i].text(0.5, 0.5, f'No data available for Batch Size {numeric_batch_size}', 
                           ha='center', va='center', transform=axes[i].transAxes)
                axes[i].set_title(f"Latency vs Image name for Batch Size {numeric_batch_size} (No Data)")
                continue
                
            # Get ILEN and OLEN from the first row (assuming they are constant for the file)
            ilen = df['ILEN'].iloc[0] if 'ILEN' in df.columns and not df.empty else 'N/A'
            olen = df['OLEN'].iloc[0] if 'OLEN' in df.columns and not df.empty else 'N/A'

            axes[i].plot(df['date'], df['E2E_Latency(s)'], marker='o', label=f'Batch Size {numeric_batch_size}')
            axes[i].set_title(f"Latency vs Image name for Batch Size {numeric_batch_size} (ILEN={ilen}, OLEN={olen}, {model_prefix})")
            axes[i].set_ylabel("E2E_Latency(s)")
            axes[i].grid(True)
            axes[i].legend()

        axes[-1].set_xlabel("Image name (rocm/sgl-dev)")
        plt.xticks(rotation=45)
        plt.tight_layout()
        
        try:
            # Ensure the directory exists
            os.makedirs(os.path.dirname(output_file), exist_ok=True)
            plt.savefig(output_file)
            print(f"Plot figure saved to: {output_file}")
        except Exception as e:
            print(f"Error saving plot to {output_file}: {e}")
        finally:
            plt.close()

    
    def plot_throughput_vs_date(self, output_file):
        """
        Create a throughput-date subplot for each batch size (labeling x-axis as Image name)
        """
        total_files = len(self.batch_files)
        fig, axes = plt.subplots(total_files, 1, figsize=(10, 4 * total_files), sharex=True)
        
        if total_files == 1:
            axes = [axes]

        for i, csv_file in enumerate(self.batch_files):
            csv_path = os.path.join(self.batch_dir, csv_file)
            filename_part = csv_file.split('.')[0]
            model_prefix, numeric_batch_size = filename_part.rsplit('_', 1)
            
            df = self.read_csv(csv_path)
            
            # Skip if dataframe is empty
            if df.empty:
                axes[i].text(0.5, 0.5, f'No data available for Batch Size {numeric_batch_size}', 
                           ha='center', va='center', transform=axes[i].transAxes)
                axes[i].set_title(f"Throughput vs Image name for Batch Size {numeric_batch_size} (No Data)")
                continue
                
            # Get ILEN and OLEN from the first row
            ilen = df['ILEN'].iloc[0] if 'ILEN' in df.columns and not df.empty else 'N/A'
            olen = df['OLEN'].iloc[0] if 'OLEN' in df.columns and not df.empty else 'N/A'

            axes[i].plot(df['date'], df['E2E_Throughput(token/s)'], marker='o', label=f'Batch Size {numeric_batch_size}')
            axes[i].set_title(f"Throughput vs Image name for Batch Size {numeric_batch_size} (ILEN={ilen}, OLEN={olen}, {model_prefix})")
            axes[i].set_ylabel("E2E_Throughput(token/s)")
            axes[i].grid(True)
            axes[i].legend()

        axes[-1].set_xlabel("Image name (rocm/sgl-dev)")
        plt.xticks(rotation=45)
        plt.tight_layout()
        
        try:
            # Ensure the directory exists
            os.makedirs(os.path.dirname(output_file), exist_ok=True)
            plt.savefig(output_file)
            print(f"Plot figure saved to: {output_file}")
        except Exception as e:
            print(f"Error saving plot to {output_file}: {e}")
        finally:
            plt.close()


    def generate_and_save_plot(self):
        """
        Function to process the batch data, generate the plot with subplots for each batch size
        """
        self.get_batch_files()

        current_date_str = datetime.now().strftime('%Y%m%d')
        op_latency_file=f"latency_vs_image_plots_{self.model_name}_{current_date_str}.png"
        op_latency_file = os.path.join(self.plot_dir, op_latency_file)

        op_throughput_file=f"throughput_vs_image_plots_{self.model_name}_{current_date_str}.png"
        op_throughput_file = os.path.join(self.plot_dir, op_throughput_file)

        self.plot_latency_vs_date(op_latency_file)

        self.plot_throughput_vs_date(op_throughput_file)


# Generate the plots, call main
if __name__ == "__main__":
    batch_dir = "/mnt/raid/michael/sgl_benchmark_ci/offline/GROK1"  # Dir where CSVs are located
    plot_dir = "/mnt/raid/michael/sgl_benchmark_ci/plots_server/GROK1/offline"# Centralized plot directory

    model_name="GROK1_MOE-I4F8_offline"
    plotter = OfflineGraphPlotter(batch_dir, plot_dir, model_name)

    # Generate and save the subplots for latency, throughput for the model
    plotter.generate_and_save_plot()


