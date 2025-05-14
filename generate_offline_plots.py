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

class OfflineGraphPlotter:
    def __init__(self, batch_dir, plot_dir, model_name):
        self.batch_dir = batch_dir
        self.plot_dir = plot_dir
        self.model_name = model_name
        os.makedirs(self.plot_dir, exist_ok=True)

    def get_batch_files(self):
        """
        Get all CSV files for the batches
        """
        batch_files = [f for f in os.listdir(self.batch_dir) if f.endswith(".csv") and f.split('.')[0].isdigit()]
        batch_files.sort(key=lambda x: int(x.split('.')[0]))
        return batch_files

    def read_csv(self, file_path):
        """
        Reads a CSV file into a pandas DataFrame
        """
        df = pd.read_csv(file_path)
        df['date'] = pd.to_datetime(df['date'], format='%Y%m%d')
        return df.sort_values('date')

    def plot_latency_vs_date(self, batch_files):
        """
        Create a latency-date subplot for each batch size
        """
        total_files = len(batch_files)
        latency_date_fig, axes = plt.subplots(total_files, 1, figsize=(10, 4 * total_files), sharex=True)
        
        # Ensuring axes is iterable, even if there's only one subplot
        if total_files == 1:
            axes = [axes]

        # Subplot for each batch size
        for i, csv_file in enumerate(batch_files):
            csv_path = os.path.join(self.batch_dir, csv_file)
            batch_size = csv_file.split('.')[0]
            
            df = self.read_csv(csv_path)

            # Plot: Latency vs Date
            axes[i].plot(df['date'], df['E2E_Latency(s)'], marker='o', label=f'Batch Size {batch_size}')
            axes[i].set_title(f"Latency vs Date for Batch Size {batch_size}")
            axes[i].set_ylabel("E2E_Latency(s)")
            axes[i].grid(True)
            axes[i].legend()

        # Set common x-axis label , format dates
        axes[-1].set_xlabel("Date")
        plt.xticks(rotation=45)
        plt.tight_layout()

        # Return the plot
        return latency_date_fig
    
    def plot_throughput_vs_date(self, batch_files):
        """
        Create a throughput-date subplot for each batch size
        """
        total_files = len(batch_files)
        throughput_date_fig, axes = plt.subplots(total_files, 1, figsize=(10, 4 * total_files), sharex=True)
        
        # Ensuring axes is iterable, even if there's only one subplot
        if total_files == 1:
            axes = [axes]

        # Subplot for each batch size
        for i, csv_file in enumerate(batch_files):
            csv_path = os.path.join(self.batch_dir, csv_file)
            batch_size = csv_file.split('.')[0]
            
            df = self.read_csv(csv_path)

            # Plot: Latency vs Date
            axes[i].plot(df['date'], df['E2E_Throughput(token/s)'], marker='o', label=f'Batch Size {batch_size}')
            axes[i].set_title(f"Throughput vs Date for Batch Size {batch_size}")
            axes[i].set_ylabel("E2E_Throughput(token/s)")
            axes[i].grid(True)
            axes[i].legend()

        # Set common x-axis label , format dates
        axes[-1].set_xlabel("Date")
        plt.xticks(rotation=45)
        plt.tight_layout()

        # Return the plot
        return throughput_date_fig

    def save_plot(self, latency_date_fig, output_file):
        """
        Save the plot figure to a file
        """
        plt.savefig(output_file)
        plt.close()
        print(f"Plot figure saved to: {output_file}")

    def generate_and_save_plot(self):
        """
        Function to process the batch data, generate the plot with subplots for each batch size
        """
        #Get all the csv data
        csv_files = self.get_batch_files()

        # File name for the subplot figure
        current_date = datetime.now().strftime('%Y%m%d')
        op_latency_file=f"latency_vs_date_plots_{self.model_name}_{current_date}.png"
        op_latency_file = os.path.join(self.plot_dir, op_latency_file)

        op_throughput_file=f"throughput_vs_date_plots_{self.model_name}_{current_date}.png"
        op_throughput_file = os.path.join(self.plot_dir, op_throughput_file)

        # Generate the latency-date plot
        fig_latency = self.plot_latency_vs_date(csv_files)

        # Generate the throughput-date plot
        fig_throughput = self.plot_throughput_vs_date(csv_files)

        # Save the plot
        self.save_plot(fig_latency, op_latency_file)
        self.save_plot(fig_throughput, op_throughput_file)

# Generate the plots, call main
if __name__ == "__main__":
    batch_dir = "/mnt/raid/shikpate/sgl_benchmark_ci/offline/GROK1/plots"  # Dir where CSVs are located
    plot_dir = "/mnt/raid/shikpate/sgl_benchmark_ci/offline/GROK1/plots"# Dir where plots will be saved

    model_name="GROK1_MOE-I4F8_offline"
    plotter = OfflineGraphPlotter(batch_dir, plot_dir, model_name)

    # Generate and save the subplots for latency, throughput for the model
    plotter.generate_and_save_plot()


