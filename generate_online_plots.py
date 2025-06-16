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
from datetime import datetime, timedelta

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
            if not os.path.exists(self.summary_csv_path):
                print(f"Error: Summary CSV file not found: {self.summary_csv_path}")
                self.df = pd.DataFrame()
                return
                
            self.df = pd.read_csv(self.summary_csv_path)
            
            # Check if the dataframe is empty
            if self.df.empty:
                print(f"Warning: Summary CSV file is empty: {self.summary_csv_path}")
                return
                
            # Check for required columns
            required_columns = ['date']
            missing_columns = [col for col in required_columns if col not in self.df.columns]
            if missing_columns:
                print(f"Error: Missing required columns {missing_columns} in {self.summary_csv_path}")
                self.df = pd.DataFrame()
                return
                
            self.df['date'] = pd.to_datetime(self.df['date'], format='%Y%m%d')
            # Ensure new columns are numeric, coercing errors to NaN
            if 'num_tokens' in self.df.columns:
                self.df['num_tokens'] = pd.to_numeric(self.df['num_tokens'], errors='coerce')
            if 'KV_size_GB' in self.df.columns:
                self.df['KV_size_GB'] = pd.to_numeric(self.df['KV_size_GB'], errors='coerce')
            self.df = self.df.sort_values('date')
        except pd.errors.EmptyDataError:
            print(f"Error: CSV file is empty or corrupted: {self.summary_csv_path}")
            self.df = pd.DataFrame()
        except pd.errors.ParserError as e:
            print(f"Error parsing CSV file {self.summary_csv_path}: {e}")
            self.df = pd.DataFrame()
        except Exception as e:
            print(f"Error reading or processing summary CSV {self.summary_csv_path}: {e}")
            self.df = pd.DataFrame() # Ensure df is an empty DataFrame on error

    def _setup_subplot_axis(self, ax, ordered_dates, y_label, title):
        """Helper method to set up common axis properties for subplots."""
        ax.set_title(title)
        ax.set_xlabel("Date")
        ax.set_ylabel(y_label)
        ax.grid(True)
        ax.set_xticks(range(len(ordered_dates)))
        ax.set_xticklabels([d.strftime('%Y-%m-%d') for d in ordered_dates], rotation=45, ha="right")
    
    def _filter_overlapping_annotations(self, annotations):
        """Filter annotations to prevent overlap on the plot."""
        if not annotations:
            return []
        
        # Sort annotations by x, then by y (descending for y to prioritize higher values)
        annotations.sort(key=lambda a: (a['x'], -a['y']))
        
        # Estimate y-axis range for overlap threshold calculation
        y_values = [a['y'] for a in annotations]
        y_range = max(y_values) - min(y_values) if len(y_values) > 1 else 1
        # Handle corner case where all y values are the same
        if y_range == 0:
            y_range = 1
        y_overlap_threshold = y_range * 0.05  # 5% of y-range as threshold
        x_overlap_threshold = 0.15  # x positions within 0.15 units considered overlapping
        
        filtered_annotations = []
        for ann in annotations:
            # Check if this annotation would overlap with any already accepted annotation
            overlap_found = False
            for accepted in filtered_annotations:
                x_dist = abs(ann['x'] - accepted['x'])
                y_dist = abs(ann['y'] - accepted['y'])
                
                if x_dist <= x_overlap_threshold and y_dist <= y_overlap_threshold:
                    overlap_found = True
                    break
            
            if not overlap_found:
                filtered_annotations.append(ann)
        
        return filtered_annotations
    
    def _add_annotations(self, ax, annotations):
        """Add filtered annotations to the plot."""
        filtered_annotations = self._filter_overlapping_annotations(annotations)
        for ann in filtered_annotations:
            ax.annotate(ann['text'],
                       (ann['x'], ann['y']),
                       textcoords="offset points", xytext=(0, 7),
                       ha='center', fontsize='x-small')

    def _plot_performance_metrics(self, ax, metric_col, y_label, unique_modes, unique_request_rates):
        """Plot performance metrics (E2E Latency, TTFT, ITL) as line plots."""
        if self.df.empty:
            ax.set_title(f"{y_label} vs. Date for {self.model_name_in_plot} (No Data)")
            ax.set_xticks([])
            ax.set_yticks([])
            return
        
        plotted_dates = set()
        plot_data_collections = []
        all_annotations = []
        
        # Collect data for each mode and request rate combination
        for mode in unique_modes:
            for rr in unique_request_rates:
                subset = self.df[
                    (self.df['mode'] == mode) & 
                    (self.df['request_rate'] == rr) & 
                    self.df[metric_col].notna()
                ]
                if not subset.empty:
                    plotted_dates.update(subset['date'])
                    plot_data_collections.append({
                        'dates': subset['date'],
                        'values': subset[metric_col],
                        'label': f"{mode} RR={rr}"
                    })
        
        if not plotted_dates:
            ax.set_title(f"{y_label} vs. Date for {self.model_name_in_plot} (No Data)")
            ax.set_xticks([])
            ax.set_yticks([])
            return
        
        # Create date to index mapping
        ordered_dates = sorted(list(plotted_dates))
        date_to_idx = {date_obj: k for k, date_obj in enumerate(ordered_dates)}
        
        # Plot each series
        for data_item in plot_data_collections:
            x_indices = [date_to_idx[d] for d in data_item['dates']]
            values = data_item['values']
            ax.plot(x_indices, values, marker='o', linestyle='-', label=data_item['label'])
            
            # Collect annotations
            for k_idx, x_val_idx in enumerate(x_indices):
                y_val = values.iloc[k_idx] if isinstance(values, pd.Series) else values[k_idx]
                all_annotations.append({
                    'x': x_val_idx,
                    'y': y_val,
                    'text': f'{y_val:.1f}'
                })
        
        # Add annotations and setup axis
        self._add_annotations(ax, all_annotations)
        ax.legend(loc='upper left', bbox_to_anchor=(1, 1), fontsize='small')
        self._setup_subplot_axis(ax, ordered_dates, y_label, 
                                f"{y_label} vs. Date for {self.model_name_in_plot}")
    
    def _plot_num_tokens(self, ax, unique_modes):
        """Plot num_tokens as a line plot aggregated by mode."""
        metric_col = 'num_tokens'
        y_label = '# Tokens*'
        
        if self.df.empty or metric_col not in self.df.columns:
            ax.set_title(f"{y_label} vs. Date for {self.model_name_in_plot} (No Data)")
            ax.set_xticks([])
            ax.set_yticks([])
            return
        
        plotted_dates = set()
        plot_data_collections = []
        all_annotations = []
        
        for mode in unique_modes:
            mode_data = self.df[(self.df['mode'] == mode) & self.df[metric_col].notna()]
            if not mode_data.empty:
                data_by_date = mode_data.groupby('date')[metric_col].mean()
                if not data_by_date.empty:
                    plotted_dates.update(data_by_date.index)
                    plot_data_collections.append({
                        'dates': data_by_date.index,
                        'values': data_by_date.values,
                        'label': f"{mode} - # Tokens"
                    })
        
        if not plotted_dates:
            ax.set_title(f"{y_label} vs. Date for {self.model_name_in_plot} (No Data)")
            ax.set_xticks([])
            ax.set_yticks([])
            return
        
        # Create date to index mapping
        ordered_dates = sorted(list(plotted_dates))
        date_to_idx = {date_obj: k for k, date_obj in enumerate(ordered_dates)}
        
        # Plot each series
        for data_item in plot_data_collections:
            x_indices = [date_to_idx[d] for d in data_item['dates']]
            values = data_item['values']
            ax.plot(x_indices, values, marker='o', linestyle='-', label=data_item['label'])
            
            # Collect annotations
            for k_idx, x_val_idx in enumerate(x_indices):
                y_val = values[k_idx]
                all_annotations.append({
                    'x': x_val_idx,
                    'y': y_val,
                    'text': f'{y_val:.1f}'
                })
        
        # Add annotations and setup axis
        self._add_annotations(ax, all_annotations)
        ax.legend(loc='best', fontsize='small')
        self._setup_subplot_axis(ax, ordered_dates, y_label, 
                                f"{y_label} vs. Date for {self.model_name_in_plot}")
        
        # Add explanation text
        explanation_text = 'Note: "# Tokens*" refers to the number of tokens for which the\nKey-Value (KV) Cache is allocated at server startup.'
        ax.text(1.02, 0.5, explanation_text, transform=ax.transAxes, 
                ha='left', va='center', fontsize='small', color='gray',
                bbox=dict(boxstyle='round,pad=0.3', fc='white', alpha=0.5))
    
    def _plot_kv_cache_usage(self, ax, unique_modes):
        """Plot KV cache usage as a bar plot aggregated by mode."""
        metric_col = 'KV_size_GB'
        y_label = 'KV Cache Usage (GB)'
        
        if self.df.empty or metric_col not in self.df.columns:
            ax.set_title(f"{y_label} vs. Date for {self.model_name_in_plot} (No Data)")
            ax.set_xticks([])
            ax.set_yticks([])
            return
        
        plotted_dates = set()
        plot_data_collections = []
        
        for mode_idx, mode in enumerate(unique_modes):
            mode_data = self.df[(self.df['mode'] == mode) & self.df[metric_col].notna()]
            if not mode_data.empty:
                data_by_date = mode_data.groupby('date')[metric_col].mean()
                if not data_by_date.empty:
                    plotted_dates.update(data_by_date.index)
                    plot_data_collections.append({
                        'mode_idx': mode_idx,
                        'dates': data_by_date.index,
                        'values': data_by_date.values,
                        'label': f"{mode} - KV Cache Usage"
                    })
        
        if not plotted_dates:
            ax.set_title(f"{y_label} vs. Date for {self.model_name_in_plot} (No Data)")
            ax.set_xticks([])
            ax.set_yticks([])
            return
        
        # Create date to index mapping
        ordered_dates = sorted(list(plotted_dates))
        date_to_idx = {date_obj: k for k, date_obj in enumerate(ordered_dates)}
        
        # Bar plot settings
        group_width = 0.8
        bar_width = group_width / len(unique_modes) if len(unique_modes) > 0 else group_width
        
        # Plot bars for each mode
        for data_item in plot_data_collections:
            x_indices = [date_to_idx[d] for d in data_item['dates']]
            values = data_item['values']
            mode_idx = data_item['mode_idx']
            
            # Calculate offset for grouped bars
            offset = (mode_idx - (len(unique_modes) - 1) / 2.0) * bar_width
            x_positions = [x_idx + offset for x_idx in x_indices]
            
            ax.bar(x_positions, values, label=data_item['label'], width=bar_width)
        
        # Setup axis
        ax.legend(loc='best', fontsize='small')
        self._setup_subplot_axis(ax, ordered_dates, y_label, 
                                f"{y_label} vs. Date for {self.model_name_in_plot}")
    
    def plot_metrics_vs_date(self):
        """
        Creates a plot with subplots for E2E Latency, TTFT, ITL, #Tokens and KV Cache Usage vs. Date.
        Each subplot shows lines for different request_rate and mode combinations for performance metrics.
        #Tokens is a line plot and KV Cache Usage is a bar plot, showing separate entries per mode for each date.
        All available dates are shown on the x-axis for each plot.
        """
        if self.df.empty:
            print("No data available to plot.")
            return

        # Get unique modes and request rates
        unique_modes = self.df['mode'].unique()
        unique_request_rates = sorted(self.df['request_rate'].unique())

        # Create figure with subplots
        fig, axes = plt.subplots(3, 2, figsize=(20, 18))
        axes = axes.flatten()

        # Plot performance metrics
        self._plot_performance_metrics(axes[0], "E2E_Latency_ms", "E2E Latency (ms)", 
                                      unique_modes, unique_request_rates)
        self._plot_performance_metrics(axes[1], "TTFT_ms", "TTFT (ms)", 
                                      unique_modes, unique_request_rates)
        self._plot_performance_metrics(axes[2], "ITL_ms", "ITL (ms)", 
                                      unique_modes, unique_request_rates)
        
        # Plot num_tokens
        self._plot_num_tokens(axes[3], unique_modes)
        
        # Plot KV cache usage
        self._plot_kv_cache_usage(axes[4], unique_modes)

        # Remove the unused subplot
        fig.delaxes(axes[5])

        # Adjust layout to make space for legends and potential side notes
        plt.tight_layout(rect=[0, 0, 0.88, 1])  # Adjusted right padding for side note
        
        # Save the plot
        current_date_str = datetime.now().strftime('%Y%m%d')
        plot_filename = f"online_metrics_vs_date_{self.model_name_in_plot.replace(' ', '_')}_{current_date_str}.png"
        output_file_path = os.path.join(self.plot_dir, plot_filename)
        
        try:
            plt.savefig(output_file_path)
            print(f"Plot saved to: {output_file_path}")
        except Exception as e:
            print(f"Error saving plot to {output_file_path}: {e}")
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