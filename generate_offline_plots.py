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
import matplotlib
matplotlib.use('Agg')  # Use non-interactive backend
import matplotlib.pyplot as plt
from datetime import datetime

class OfflineGraphPlotter:
    def __init__(self, summary_csv_path, plot_dir, model_name_in_plot):
        self.summary_csv_path = summary_csv_path
        self.plot_dir = plot_dir
        self.model_name_in_plot = model_name_in_plot
        self.df = None
        os.makedirs(self.plot_dir, exist_ok=True)

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
            required_columns = ['date', 'batch_size', 'E2E_Latency(s)', 'E2E_Throughput(token/s)']
            missing_columns = [col for col in required_columns if col not in self.df.columns]
            if missing_columns:
                print(f"Error: Missing required columns {missing_columns} in {self.summary_csv_path}")
                self.df = pd.DataFrame()
                return
                
            # Convert date to datetime
            self.df['date'] = pd.to_datetime(self.df['date'], format='%Y%m%d')
            # Ensure batch_size is integer
            self.df['batch_size'] = self.df['batch_size'].astype(int)
            # Sort by date and batch_size
            self.df = self.df.sort_values(['date', 'batch_size'])
            
        except pd.errors.EmptyDataError:
            print(f"Error: CSV file is empty or corrupted: {self.summary_csv_path}")
            self.df = pd.DataFrame()
        except pd.errors.ParserError as e:
            print(f"Error parsing CSV file {self.summary_csv_path}: {e}")
            self.df = pd.DataFrame()
        except Exception as e:
            print(f"Error reading or processing summary CSV {self.summary_csv_path}: {e}")
            self.df = pd.DataFrame()

    def _setup_subplot_axis(self, ax, dates, batch_size, metric_label, ilen, olen, backend=None):
        """Helper method to set up common axis properties for subplots."""
        backend_text = f" [{backend}]" if backend and backend != 'unknown' else ""
        ax.set_title(f"{metric_label} vs Image name for Batch Size {batch_size} (ILEN={ilen}, OLEN={olen}){backend_text}")
        ax.set_ylabel(metric_label)
        ax.grid(True)
        ax.legend()
        # Format x-axis dates
        ax.set_xticks(range(len(dates)))
        ax.set_xticklabels([d.strftime('%Y-%m-%d') for d in dates], rotation=45, ha="right")

    def _plot_metric_for_batch_sizes(self, metric_col, metric_label, output_file):
        """
        Create subplot for each batch size showing metric vs date.
        """
        if self.df.empty:
            print("No data available to plot.")
            return
            
        # Get unique batch sizes
        batch_sizes = sorted(self.df['batch_size'].unique())
        total_subplots = len(batch_sizes)
        
        if total_subplots == 0:
            print("No batch sizes found in data.")
            return
        
        # Create subplots
        fig, axes = plt.subplots(total_subplots, 1, figsize=(12, 5 * total_subplots), sharex=True)
        
        if total_subplots == 1:
            axes = [axes]
        
        for i, batch_size in enumerate(batch_sizes):
            ax = axes[i]
            
            # Filter data for this batch size
            batch_data = self.df[self.df['batch_size'] == batch_size].copy()
            
            if batch_data.empty:
                ax.text(0.5, 0.5, f'No data available for Batch Size {batch_size}', 
                       ha='center', va='center', transform=ax.transAxes)
                ax.set_title(f"{metric_label} vs Image name for Batch Size {batch_size} (No Data)")
                continue
            
            # Sort by date
            batch_data = batch_data.sort_values('date')
            
            # Get ILEN and OLEN (should be constant, but use mean to be safe)
            ilen = int(batch_data['ILEN'].mean()) if 'ILEN' in batch_data.columns else 'N/A'
            olen = int(batch_data['OLEN'].mean()) if 'OLEN' in batch_data.columns else 'N/A'
            
            # Get backend (should be constant for a batch size, use mode to be safe)
            backend = None
            if 'backend' in batch_data.columns:
                backend = batch_data['backend'].mode()[0] if not batch_data['backend'].empty else 'unknown'
            
            # Plot the metric
            ax.plot(batch_data['date'], batch_data[metric_col], 
                   marker='o', linestyle='-', label=f'Batch Size {batch_size}')
            
            # Add value annotations on data points
            for idx, row in batch_data.iterrows():
                ax.annotate(f'{row[metric_col]:.2f}', 
                          (row['date'], row[metric_col]),
                          textcoords="offset points", xytext=(0, 7),
                          ha='center', fontsize='x-small')
            
            # Setup axis
            self._setup_subplot_axis(ax, batch_data['date'].tolist(), batch_size, 
                                   metric_label, ilen, olen, backend)
        
        # Set common x-label
        axes[-1].set_xlabel("Image name (rocm/sgl-dev)")
        
        # Adjust layout
        plt.tight_layout()
        
        # Save plot
        try:
            os.makedirs(os.path.dirname(output_file), exist_ok=True)
            plt.savefig(output_file, dpi=150, bbox_inches='tight')
            print(f"Plot saved to: {output_file}")
        except Exception as e:
            print(f"Error saving plot to {output_file}: {e}")
        finally:
            plt.close()

    def plot_latency_vs_date(self):
        """Create latency vs date plot for all batch sizes."""
        print("Generating latency plot...")
        current_date_str = datetime.now().strftime('%Y%m%d')
        output_file = os.path.join(self.plot_dir, 
                                  f"latency_vs_image_plots_{self.model_name_in_plot}_{current_date_str}.png")
        self._plot_metric_for_batch_sizes('E2E_Latency(s)', 'E2E Latency (s)', output_file)

    def plot_throughput_vs_date(self):
        """Create throughput vs date plot for all batch sizes."""
        print("Generating throughput plot...")
        current_date_str = datetime.now().strftime('%Y%m%d')
        output_file = os.path.join(self.plot_dir, 
                                  f"throughput_vs_image_plots_{self.model_name_in_plot}_{current_date_str}.png")
        self._plot_metric_for_batch_sizes('E2E_Throughput(token/s)', 'E2E Throughput (token/s)', output_file)

    def plot_combined_metrics(self):
        """
        Create a combined plot showing both latency and throughput trends for all batch sizes.
        """
        print("Generating combined metrics plot...")
        if self.df.empty:
            print("No data available to plot.")
            return
            
        # Get unique batch sizes
        batch_sizes = sorted(self.df['batch_size'].unique())
        
        # Create figure with two subplots side by side
        fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(20, 8))
        
        # Plot latency trends
        for batch_size in batch_sizes:
            batch_data = self.df[self.df['batch_size'] == batch_size].copy()
            if not batch_data.empty:
                batch_data = batch_data.sort_values('date')
                # Calculate mean latency per date (in case of duplicates)
                latency_by_date = batch_data.groupby('date')['E2E_Latency(s)'].mean()
                ax1.plot(latency_by_date.index, latency_by_date.values, 
                        marker='o', linestyle='-', label=f'BS={batch_size}')
        
        ax1.set_title(f'E2E Latency Trends - {self.model_name_in_plot}')
        ax1.set_xlabel('Date')
        ax1.set_ylabel('E2E Latency (s)')
        ax1.legend(bbox_to_anchor=(1.05, 1), loc='upper left')
        ax1.grid(True, alpha=0.3)
        ax1.tick_params(axis='x', rotation=45)
        
        # Plot throughput trends
        for batch_size in batch_sizes:
            batch_data = self.df[self.df['batch_size'] == batch_size].copy()
            if not batch_data.empty:
                batch_data = batch_data.sort_values('date')
                # Calculate mean throughput per date
                throughput_by_date = batch_data.groupby('date')['E2E_Throughput(token/s)'].mean()
                ax2.plot(throughput_by_date.index, throughput_by_date.values, 
                        marker='o', linestyle='-', label=f'BS={batch_size}')
        
        ax2.set_title(f'E2E Throughput Trends - {self.model_name_in_plot}')
        ax2.set_xlabel('Date')
        ax2.set_ylabel('E2E Throughput (token/s)')
        ax2.legend(bbox_to_anchor=(1.05, 1), loc='upper left')
        ax2.grid(True, alpha=0.3)
        ax2.tick_params(axis='x', rotation=45)
        
        # Adjust layout
        plt.tight_layout()
        
        # Save plot
        current_date_str = datetime.now().strftime('%Y%m%d')
        output_file = os.path.join(self.plot_dir, 
                                  f"combined_metrics_{self.model_name_in_plot}_{current_date_str}.png")
        try:
            plt.savefig(output_file, dpi=150, bbox_inches='tight')
            print(f"Combined plot saved to: {output_file}")
        except Exception as e:
            print(f"Error saving combined plot to {output_file}: {e}")
        finally:
            plt.close()

    def generate_and_save_plots(self):
        """
        Main method to orchestrate reading data and generating plots.
        """
        self.read_summary_csv()
        if not self.df.empty:
            self.plot_latency_vs_date()
            self.plot_throughput_vs_date()
            self.plot_combined_metrics()
        else:
            print("No data to plot. Please check the summary CSV file.")

if __name__ == "__main__":
    # Path to the aggregated summary CSV generated by process_offline_csv.py
    summary_csv_path = "/mnt/raid/michael/sgl_benchmark_ci/offline/GROK1/GROK1_MOE-I4F8_offline_summary.csv"
    
    # Directory where the plots will be saved
    plot_dir = "/mnt/raid/michael/sgl_benchmark_ci/plots_server/GROK1/offline"
    
    # Model name to be used in plot titles
    model_name_in_plot = "GROK1_MOE-I4F8_offline"

    plotter = OfflineGraphPlotter(summary_csv_path, plot_dir, model_name_in_plot)
    plotter.generate_and_save_plots()


