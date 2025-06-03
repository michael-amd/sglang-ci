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
import matplotlib.dates as mdates
from datetime import datetime

class OnlineGraphPlotter:
    def __init__(self, summary_csv_path, plot_dir, model_name_in_plot):
        self.summary_csv_path = summary_csv_path
        self.plot_dir = plot_dir
        self.model_name_in_plot = model_name_in_plot # e.g. "GROK1 MOE-I4F8 Online"
        os.makedirs(self.plot_dir, exist_ok=True)
        self.df = None

    def read_summary_csv(self):
        """
        Reads the summary CSV file into a pandas DataFrame.
        """
        try:
            self.df = pd.read_csv(self.summary_csv_path)
            self.df['date'] = pd.to_datetime(self.df['date'], format='%Y%m%d')
            # Ensure new columns are numeric, coercing errors to NaN
            if 'num_tokens' in self.df.columns:
                self.df['num_tokens'] = pd.to_numeric(self.df['num_tokens'], errors='coerce')
            if 'KV_size_GB' in self.df.columns:
                self.df['KV_size_GB'] = pd.to_numeric(self.df['KV_size_GB'], errors='coerce')
            self.df = self.df.sort_values('date')
        except Exception as e:
            print(f"Error reading or processing summary CSV {self.summary_csv_path}: {e}")
            self.df = pd.DataFrame() # Ensure df is an empty DataFrame on error

    def plot_metrics_vs_date(self):
        """
        Creates a plot with subplots for E2E Latency, TTFT, ITL, #Tokens and KV Cache Usage vs. Date.
        Each subplot shows lines for different request_rate and mode combinations for performance metrics.
        #Tokens is a line plot and KV Cache Usage is a bar plot, showing one value per date.
        """
        if self.df.empty:
            print("No data available to plot.")
            return

        metrics_to_plot = [
            ("E2E_Latency_ms", "E2E Latency (ms)", "line"),
            ("TTFT_ms", "TTFT (ms)", "line"),
            ("ITL_ms", "ITL (ms)", "line"),
            ("num_tokens", "# Tokens", "line"),
            ("KV_size_GB", "KV Cache Usage (GB)", "bar")
        ]

        unique_modes = self.df['mode'].unique()
        unique_request_rates = sorted(self.df['request_rate'].unique())

        # Create a 3x2 subplot grid
        fig, axes = plt.subplots(3, 2, figsize=(20, 18)) # Adjusted for 5 plots
        axes = axes.flatten() # Flatten to 1D array for easier iteration

        for i, (metric_col, y_label, plot_type) in enumerate(metrics_to_plot):
            ax = axes[i]
            
            if metric_col in ['num_tokens', 'KV_size_GB']:
                # These metrics have one value per date.
                # Group by date and take the first non-NA value.
                # Drop rows where metric_col is NA before grouping and plotting
                metric_data_per_date = self.df.dropna(subset=[metric_col]).groupby('date')[metric_col].first()
                if not metric_data_per_date.empty:
                    dates = metric_data_per_date.index
                    values = metric_data_per_date.values
                    if plot_type == "line":
                        ax.plot(dates, values, marker='o', linestyle='-', label=y_label)
                    elif plot_type == "bar":
                        ax.bar(dates, values, label=y_label, width=0.9 * (dates[1]-dates[0]).days if len(dates) > 1 else 20) # Adjust bar width
                    ax.legend(loc='best', fontsize='small')
                else:
                    print(f"No data for {metric_col} after dropping NA.")
            else:
                # For E2E, TTFT, ITL, plot lines for each mode and request_rate
                for mode in unique_modes:
                    for rr in unique_request_rates:
                        subset = self.df[(self.df['mode'] == mode) & (self.df['request_rate'] == rr) & self.df[metric_col].notna()]
                        if not subset.empty:
                            ax.plot(subset['date'], subset[metric_col], marker='o', linestyle='-',
                                    label=f"{mode} RR={rr}")
                ax.legend(loc='upper left', bbox_to_anchor=(1, 1), fontsize='small')

            ax.set_title(f"{y_label} vs. Date for {self.model_name_in_plot}")
            ax.set_xlabel("Date") # Simplified X-axis label
            ax.set_ylabel(y_label)
            ax.grid(True)
            ax.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m-%d'))
            plt.setp(ax.get_xticklabels(), rotation=45, ha="right")

        # Remove the unused subplot if any (e.g., if 5 plots in 3x2 grid)
        if len(metrics_to_plot) < len(axes):
             for j in range(len(metrics_to_plot), len(axes)):
                 fig.delaxes(axes[j])

        plt.tight_layout(rect=[0, 0, 0.90, 1]) # Adjust layout to make space for legend if it's outside
        
        current_date_str = datetime.now().strftime('%Y%m%d')
        plot_filename = f"online_metrics_vs_date_{self.model_name_in_plot.replace(' ', '_')}_{current_date_str}.png"
        output_file_path = os.path.join(self.plot_dir, plot_filename)
        
        try:
            plt.savefig(output_file_path)
            print(f"Plot saved to: {output_file_path}")
        except Exception as e:
            print(f"Error saving plot: {e}")
        plt.close()

    def generate_and_save_plots(self):
        """
        Main method to orchestrate reading data and generating plots.
        """
        self.read_summary_csv()
        self.plot_metrics_vs_date()

if __name__ == "__main__":
    # Path to the aggregated summary CSV generated by process_online_csv.py
    summary_csv_path = "/mnt/raid/michael/sgl_benchmark_ci/online/GROK1/GROK1_MOE-I4F8_online_summary.csv"
    
    # Directory where the plots will be saved
    plot_dir = "/mnt/raid/michael/sgl_benchmark_ci/plots_server/GROK1/online"
    
    # Model name to be used in plot titles
    model_name_in_plot = "GROK1 MOE-I4F8 Online"

    plotter = OnlineGraphPlotter(summary_csv_path, plot_dir, model_name_in_plot)
    plotter.generate_and_save_plots() 