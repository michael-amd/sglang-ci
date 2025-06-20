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
import argparse  # For command-line argument parsing

class OnlineGraphPlotter:
    def __init__(self, summary_csv_path, plot_dir, model_name_in_plot, mode_filter=None, split_request_rates=False):
        """
        Initialize the OnlineGraphPlotter.
        Args:
            summary_csv_path: Path to the summary CSV file
            plot_dir: Directory where plots will be saved
            model_name_in_plot: Model name to use in plot titles
            mode_filter: Optional mode filter. Can be:
                - None: Plot all modes in the CSV (default)
                - "aiter": Plot only aiter mode
                - "triton": Plot only triton mode
                - list of modes: e.g., ["aiter", "triton"]
            split_request_rates: If True, create separate plots for low (1,2,4) and high (8,16) request rates
        """
        self.summary_csv_path = summary_csv_path
        self.plot_dir = plot_dir
        self.model_name_in_plot = model_name_in_plot # e.g. "GROK1 MOE-I4F8 Online"
        self.mode_filter = mode_filter
        self.split_request_rates = split_request_rates
        os.makedirs(self.plot_dir, exist_ok=True)
        self.df = None
        
        # Expected request rates for complete data
        self.expected_request_rates = [1, 2, 4, 8, 16]  # Powers of 2
        
        # Define low and high request rate groups
        self.low_request_rates = [1, 2, 4]
        self.high_request_rates = [8, 16]
        
        # Convert mode_filter to a set for efficient checking
        if mode_filter is None:
            self.modes_to_plot = None  # None means plot all modes
        elif isinstance(mode_filter, str):
            self.modes_to_plot = {mode_filter}
        elif isinstance(mode_filter, list):
            self.modes_to_plot = set(mode_filter)
        else:
            raise ValueError(f"Invalid mode_filter: {mode_filter}. Must be None, a string mode name, or a list of mode names.")

    def _should_plot_mode(self, mode):
        """Check if a mode should be plotted based on the filter."""
        if self.modes_to_plot is None:  # Plot all modes
            return True
        return mode in self.modes_to_plot

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
                
            # Filter modes if mode_filter is specified
            if self.modes_to_plot is not None and 'mode' in self.df.columns:
                self.df = self.df[self.df['mode'].isin(self.modes_to_plot)]
                if self.df.empty:
                    print(f"Warning: No data found for modes {self.modes_to_plot} in {self.summary_csv_path}")
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

    def filter_complete_dates(self):
        """
        Filters dataframe to only keep dates that have valid data for all expected request rates (1, 2, 4, 8, 16).
        Valid data means at least one performance metric (E2E_Latency_ms, TTFT_ms, ITL_ms) is not NA.
        """
        if self.df.empty:
            return
        
        # Performance metric columns to check
        metric_columns = ['E2E_Latency_ms', 'TTFT_ms', 'ITL_ms']
        
        # Group by date and mode to check completeness
        complete_dates = set()
        
        for date in self.df['date'].unique():
            date_df = self.df[self.df['date'] == date]
            
            # Get unique modes for this date
            modes_in_date = date_df['mode'].unique()
            
            # Check if each mode has all expected request rates with valid data
            is_complete = True
            for mode in modes_in_date:
                mode_df = date_df[date_df['mode'] == mode]
                
                # Check request rates
                request_rates_found = sorted(mode_df['request_rate'].unique())
                if request_rates_found != self.expected_request_rates:
                    is_complete = False
                    missing_rates = set(self.expected_request_rates) - set(request_rates_found)
                    extra_rates = set(request_rates_found) - set(self.expected_request_rates)
                    if missing_rates:
                        print(f"Date {date.strftime('%Y%m%d')}, Mode {mode}: Missing request rates: {sorted(missing_rates)}")
                    if extra_rates:
                        print(f"Date {date.strftime('%Y%m%d')}, Mode {mode}: Extra request rates: {sorted(extra_rates)}")
                    break
                
                # Check that each request rate has valid data (at least one non-NA metric)
                for rr in self.expected_request_rates:
                    rr_df = mode_df[mode_df['request_rate'] == rr]
                    if len(rr_df) == 0:
                        is_complete = False
                        print(f"Date {date.strftime('%Y%m%d')}, Mode {mode}: No data for request rate {rr}")
                        break
                    
                    # Check if at least one performance metric has valid data
                    has_valid_data = False
                    for metric in metric_columns:
                        if metric in rr_df.columns and rr_df[metric].notna().any():
                            has_valid_data = True
                            break
                    
                    if not has_valid_data:
                        is_complete = False
                        print(f"Date {date.strftime('%Y%m%d')}, Mode {mode}, RR {rr}: No valid performance metrics (all NA)")
                        break
                
                if not is_complete:
                    break
            
            if is_complete:
                complete_dates.add(date)
                print(f"Date {date.strftime('%Y%m%d')}: Complete and valid data for all modes and request rates")
        
        # Filter dataframe to only keep complete dates
        if complete_dates:
            self.df = self.df[self.df['date'].isin(complete_dates)]
            print(f"\nKept {len(complete_dates)} dates with complete and valid data for plotting")
            print(f"Total records after filtering: {len(self.df)}")
        else:
            print("\nNo dates found with complete and valid data for all request rates [1, 2, 4, 8, 16]")
            self.df = pd.DataFrame()

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
        
        # First, handle annotations with the same y value - keep only the leftmost one
        y_value_to_annotations = {}
        for ann in annotations:
            y_val = ann['y']
            if y_val not in y_value_to_annotations:
                y_value_to_annotations[y_val] = []
            y_value_to_annotations[y_val].append(ann)
        
        # For each unique y value, keep only the annotation with the smallest x
        unique_y_annotations = []
        for y_val, anns in y_value_to_annotations.items():
            # Sort by x position and keep the leftmost one
            leftmost = min(anns, key=lambda a: a['x'])
            unique_y_annotations.append(leftmost)
        
        # Now apply the original overlap filtering on the remaining annotations
        # Sort annotations by x, then by y (descending for y to prioritize higher values)
        unique_y_annotations.sort(key=lambda a: (a['x'], -a['y']))
        
        # Estimate y-axis range for overlap threshold calculation
        y_values = [a['y'] for a in unique_y_annotations]
        y_range = max(y_values) - min(y_values) if len(y_values) > 1 else 1
        # Handle corner case where all y values are the same
        if y_range == 0:
            y_range = 1
        y_overlap_threshold = y_range * 0.05  # 5% of y-range as threshold
        x_overlap_threshold = 0.15  # x positions within 0.15 units considered overlapping
        
        filtered_annotations = []
        for ann in unique_y_annotations:
            # Check if this annotation would overlap with any already accepted annotation
            overlap_found = False
            for accepted in filtered_annotations:
                x_dist = abs(ann['x'] - accepted['x'])
                y_dist = abs(ann['y'] - accepted['y'])
                
                # Skip y distance check if values are exactly the same (already handled above)
                if ann['y'] == accepted['y']:
                    continue
                    
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
                    'text': f'{y_val:.0f}'
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
        
        # Process data for each mode
        for mode in unique_modes:
            mode_data = self.df[self.df['mode'] == mode]
            mode_data = mode_data[mode_data[metric_col].notna()]
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
                    'text': f'{y_val:.0f}'
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
        
        # Process data for each mode
        for mode_idx, mode in enumerate(unique_modes):
            mode_data = self.df[self.df['mode'] == mode]
            mode_data = mode_data[mode_data[metric_col].notna()]
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
        num_modes = len(unique_modes)
        bar_width = 0.8 / num_modes if num_modes > 0 else 0.4
        
        # Plot bars for each mode
        for data_item in plot_data_collections:
            x_indices = [date_to_idx[d] for d in data_item['dates']]
            values = data_item['values']
            
            # Calculate offset for this mode
            offset = (data_item['mode_idx'] - (num_modes - 1) / 2) * bar_width
            x_positions = [x + offset for x in x_indices]
            
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
        
        # Print summary of modes being plotted
        print(f"Plotting modes: {', '.join(sorted(unique_modes))}")

        if self.split_request_rates:
            # Create separate plots for low and high request rates
            self._create_split_plots(unique_modes, unique_request_rates)
        else:
            # Create single plot with all request rates
            self._create_single_plot(unique_modes, unique_request_rates)

    def _create_single_plot(self, unique_modes, unique_request_rates):
        """Create a single plot with all request rates."""
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
        
        # Add mode suffix to plot filename if filtering modes
        if self.modes_to_plot is not None:
            mode_suffix = "_" + "_".join(sorted(self.modes_to_plot))
        else:
            mode_suffix = "_all"
            
        plot_filename = f"online_metrics_vs_date_{self.model_name_in_plot.replace(' ', '_')}{mode_suffix}_{current_date_str}.png"
        output_file_path = os.path.join(self.plot_dir, plot_filename)
        
        try:
            plt.savefig(output_file_path)
            print(f"Plot saved to: {output_file_path}")
        except Exception as e:
            print(f"Error saving plot to {output_file_path}: {e}")
        plt.close()

    def _create_split_plots(self, unique_modes, unique_request_rates):
        """Create separate plots for low and high request rates."""
        # Filter request rates into low and high groups
        low_rr = [rr for rr in unique_request_rates if rr in self.low_request_rates]
        high_rr = [rr for rr in unique_request_rates if rr in self.high_request_rates]
        
        current_date_str = datetime.now().strftime('%Y%m%d')
        
        # Add mode suffix to plot filename if filtering modes
        if self.modes_to_plot is not None:
            mode_suffix = "_" + "_".join(sorted(self.modes_to_plot))
        else:
            mode_suffix = "_all"
        
        # Create plot for low request rates
        if low_rr:
            print(f"\nCreating plot for low request rates: {low_rr}")
            fig, axes = plt.subplots(3, 2, figsize=(20, 18))
            axes = axes.flatten()

            # Plot performance metrics
            self._plot_performance_metrics(axes[0], "E2E_Latency_ms", "E2E Latency (ms) - Low RR", 
                                          unique_modes, low_rr)
            self._plot_performance_metrics(axes[1], "TTFT_ms", "TTFT (ms) - Low RR", 
                                          unique_modes, low_rr)
            self._plot_performance_metrics(axes[2], "ITL_ms", "ITL (ms) - Low RR", 
                                          unique_modes, low_rr)
            
            # Plot num_tokens
            self._plot_num_tokens(axes[3], unique_modes)
            
            # Plot KV cache usage
            self._plot_kv_cache_usage(axes[4], unique_modes)

            # Remove the unused subplot
            fig.delaxes(axes[5])

            # Adjust layout
            plt.tight_layout(rect=[0, 0, 0.88, 1])
            
            # Save the plot
            plot_filename = f"online_metrics_vs_date_{self.model_name_in_plot.replace(' ', '_')}{mode_suffix}_low_rr_{current_date_str}.png"
            output_file_path = os.path.join(self.plot_dir, plot_filename)
            
            try:
                plt.savefig(output_file_path)
                print(f"Low request rate plot saved to: {output_file_path}")
            except Exception as e:
                print(f"Error saving low RR plot to {output_file_path}: {e}")
            plt.close()
        
        # Create plot for high request rates
        if high_rr:
            print(f"\nCreating plot for high request rates: {high_rr}")
            fig, axes = plt.subplots(3, 2, figsize=(20, 18))
            axes = axes.flatten()

            # Plot performance metrics
            self._plot_performance_metrics(axes[0], "E2E_Latency_ms", "E2E Latency (ms) - High RR", 
                                          unique_modes, high_rr)
            self._plot_performance_metrics(axes[1], "TTFT_ms", "TTFT (ms) - High RR", 
                                          unique_modes, high_rr)
            self._plot_performance_metrics(axes[2], "ITL_ms", "ITL (ms) - High RR", 
                                          unique_modes, high_rr)
            
            # Plot num_tokens
            self._plot_num_tokens(axes[3], unique_modes)
            
            # Plot KV cache usage
            self._plot_kv_cache_usage(axes[4], unique_modes)

            # Remove the unused subplot
            fig.delaxes(axes[5])

            # Adjust layout
            plt.tight_layout(rect=[0, 0, 0.88, 1])
            
            # Save the plot
            plot_filename = f"online_metrics_vs_date_{self.model_name_in_plot.replace(' ', '_')}{mode_suffix}_high_rr_{current_date_str}.png"
            output_file_path = os.path.join(self.plot_dir, plot_filename)
            
            try:
                plt.savefig(output_file_path)
                print(f"High request rate plot saved to: {output_file_path}")
            except Exception as e:
                print(f"Error saving high RR plot to {output_file_path}: {e}")
            plt.close()

    def generate_and_save_plots(self):
        """
        Main method to orchestrate reading data and generating plots.
        """
        self.read_summary_csv()
        self.filter_complete_dates()  # Filter to only keep dates with complete data
        self.plot_metrics_vs_date()

def parse_mode_filter(mode_str):
    """Parse mode filter string into appropriate format."""
    if not mode_str or mode_str.lower() == "none":
        return None
    elif mode_str.lower() == "all":
        return None  # None means plot all modes
    elif "," in mode_str:
        # Multiple modes separated by comma
        return [m.strip() for m in mode_str.split(",") if m.strip()]
    else:
        # Single mode
        return mode_str.strip()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Generate plots from online benchmark summary CSV",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    
    parser.add_argument(
        "--summary-csv",
        type=str,
        help="Path to the summary CSV file (if not provided, will be auto-generated based on other options)"
    )
    
    parser.add_argument(
        "--plot-dir",
        type=str,
        default="/mnt/raid/michael/sgl_benchmark_ci/plots_server/GROK1/online",
        help="Directory where plots will be saved"
    )
    
    parser.add_argument(
        "--model-name",
        type=str,
        default="GROK1 MOE-I4F8 Online",
        help="Model name to use in plot titles"
    )
    
    parser.add_argument(
        "--mode-filter",
        type=str,
        default="none",
        help="Mode(s) to plot. Options: 'none' (plot all from CSV), 'aiter', 'triton', or comma-separated list"
    )
    
    parser.add_argument(
        "--split-request-rates",
        action="store_true",
        help="Create separate plots for low (1,2,4) and high (8,16) request rates"
    )
    
    # Legacy options for auto-generating summary CSV path
    parser.add_argument(
        "--base-path",
        type=str,
        default="/mnt/raid/michael/sgl_benchmark_ci/online/GROK1",
        help="Base path for summary CSV (used if --summary-csv not provided)"
    )
    
    parser.add_argument(
        "--base-prefix",
        type=str,
        default="GROK1_MOE-I4F8_online",
        help="Base prefix for summary CSV (used if --summary-csv not provided)"
    )
    
    parser.add_argument(
        "--csv-mode-filter",
        type=str,
        default="aiter",
        help="Mode filter used in CSV filename (used if --summary-csv not provided)"
    )
    
    # Parse arguments
    args = parser.parse_args()
    
    # Determine summary CSV path
    if args.summary_csv:
        summary_csv_path = args.summary_csv
    else:
        # Auto-generate path based on csv-mode-filter
        csv_mode_filter = parse_mode_filter(args.csv_mode_filter)
        if csv_mode_filter == "all" or csv_mode_filter is None:
            summary_csv_path = f"{args.base_path}/{args.base_prefix}_all_summary.csv"
        elif isinstance(csv_mode_filter, str):
            summary_csv_path = f"{args.base_path}/{args.base_prefix}_{csv_mode_filter}_summary.csv"
        elif isinstance(csv_mode_filter, list):
            mode_suffix = "_".join(sorted(csv_mode_filter))
            summary_csv_path = f"{args.base_path}/{args.base_prefix}_{mode_suffix}_summary.csv"
        else:
            summary_csv_path = f"{args.base_path}/{args.base_prefix}_aiter_summary.csv"
    
    # Parse mode filter for plotting
    mode_filter = parse_mode_filter(args.mode_filter)
    
    # Print configuration
    print(f"Configuration:")
    print(f"  Summary CSV: {summary_csv_path}")
    print(f"  Plot directory: {args.plot_dir}")
    print(f"  Model name: {args.model_name}")
    print(f"  Mode filter: {mode_filter if mode_filter is not None else 'all modes from CSV'}")
    print(f"  Split request rates: {args.split_request_rates}")
    print()
    
    # Create plotter and generate plots
    plotter = OnlineGraphPlotter(summary_csv_path, args.plot_dir, args.model_name, 
                                mode_filter=mode_filter, split_request_rates=args.split_request_rates)
    plotter.generate_and_save_plots() 