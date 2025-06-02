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
        self.batch_files = [f for f in os.listdir(self.batch_dir) if f.endswith(".csv")]
        self.batch_files = sorted(self.batch_files, key=lambda f: int(re.search(r'_(\d+)', f).group(1)))

    def read_csv(self, file_path):
        """
        Reads a CSV file into a pandas DataFrame
        """
        df = pd.read_csv(file_path)
        df['date'] = pd.to_datetime(df['date'], format='%Y%m%d')
        return df.sort_values('date')

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
        plt.savefig(output_file)
        plt.close()
        print(f"Plot figure saved to: {output_file}")

    
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
        plt.savefig(output_file)
        plt.close()
        print(f"Plot figure saved to: {output_file}")


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


