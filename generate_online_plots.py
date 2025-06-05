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
        #Tokens is a line plot and KV Cache Usage is a bar plot, showing separate entries per mode for each date.
        All available dates are shown on the x-axis for each plot.
        """
        if self.df.empty:
            print("No data available to plot.")
            return

        metrics_to_plot = [
            ("E2E_Latency_ms", "E2E Latency (ms)", "line"),
            ("TTFT_ms", "TTFT (ms)", "line"),
            ("ITL_ms", "ITL (ms)", "line"),
            # "# Tokens": number of tokens for which the Key-Value (KV) Cache is allocated when the server starts up.
            ("num_tokens", "# Tokens*", "line"),
            ("KV_size_GB", "KV Cache Usage (GB)", "bar")
        ]

        unique_modes = self.df['mode'].unique()
        unique_request_rates = sorted(self.df['request_rate'].unique())
        all_dates_overall = sorted(self.df['date'].unique())

        fig, axes = plt.subplots(3, 2, figsize=(20, 18))
        axes = axes.flatten()

        for i, (metric_col, y_label, plot_type) in enumerate(metrics_to_plot):
            ax = axes[i]
            # Collect all dates that will have data plotted on this specific axis
            plotted_dates_for_this_axis = set()
            # Temp storage for data to be plotted, as we need all dates first for categorical mapping
            plot_data_collections = []

            if metric_col in ['num_tokens', 'KV_size_GB']:
                for mode_idx, mode in enumerate(unique_modes):
                    mode_metric_data = self.df[(self.df['mode'] == mode) & self.df[metric_col].notna()]
                    if not mode_metric_data.empty:
                        data_to_plot = mode_metric_data.groupby('date')[metric_col].first()
                        if not data_to_plot.empty:
                            plotted_dates_for_this_axis.update(data_to_plot.index)
                            # Add 'annotate': True for num_tokens to enable y-value annotations
                            annotate_flag = True if metric_col == 'num_tokens' else False
                            plot_data_collections.append({
                                'type': plot_type, 
                                'mode': mode, 
                                'mode_idx': mode_idx, 
                                'dates': data_to_plot.index, 
                                'values': data_to_plot.values, 
                                'label_suffix': y_label.split(' (')[0],
                                'annotate': annotate_flag
                            })
            else:
                for mode in unique_modes:
                    for rr in unique_request_rates:
                        subset = self.df[(self.df['mode'] == mode) & (self.df['request_rate'] == rr) & self.df[metric_col].notna()]
                        if not subset.empty:
                            plotted_dates_for_this_axis.update(subset['date'])
                            plot_data_collections.append({'type': 'line', 'mode': mode, 'rr': rr, 'dates': subset['date'], 'values': subset[metric_col], 'label_suffix': f"{mode} RR={rr}", 'annotate': True})
            
            if not plotted_dates_for_this_axis:
                # Handle case where this subplot has no data, remove it or skip
                ax.set_title(f"{y_label} vs. Date for {self.model_name_in_plot} (No Data)")
                ax.set_xticks([])
                ax.set_yticks([])
                continue # Skip to next subplot

            ordered_subplot_dates = sorted(list(plotted_dates_for_this_axis))
            subplot_date_to_local_idx = {date_obj: k for k, date_obj in enumerate(ordered_subplot_dates)}

            # Common settings for bar plots on categorical axis
            n_modes_for_bar = len([pdc for pdc in plot_data_collections if pdc['type'] == 'bar']) # Count distinct groups if plotting multiple things as bars
            # This assumes for num_tokens/KV_size_GB, n_modes refers to unique_modes if they are bars
            # If only one type of bar (KV_size_GB), n_modes is len(unique_modes)
            # For simplicity, assuming n_modes for bar plot spacing is based on unique_modes
            group_width_categorical = 0.8 # Total width for a bar group at one date category
            bar_width_categorical = group_width_categorical / len(unique_modes) if len(unique_modes) > 0 else group_width_categorical

            for data_item in plot_data_collections:
                x_indices = [subplot_date_to_local_idx[d] for d in data_item['dates']]
                values = data_item['values']
                label_text = f"{data_item['mode']} - {data_item['label_suffix']}" if metric_col in ['num_tokens', 'KV_size_GB'] else data_item['label_suffix']

                if data_item['type'] == "line":
                    ax.plot(x_indices, values, marker='o', linestyle='-', label=label_text)
                elif data_item['type'] == "bar":
                    # mode_idx is from the original loop when collecting num_tokens/KV_size_GB data
                    mode_idx = data_item['mode_idx'] 
                    offset_categorical = (mode_idx - (len(unique_modes) - 1) / 2.0) * bar_width_categorical
                    final_x_bar_indices = [x_idx + offset_categorical for x_idx in x_indices]
                    ax.bar(final_x_bar_indices, values, label=label_text, width=bar_width_categorical)

            # Collect all annotations for overlap detection (only for line plots with annotations)
            all_annotations = []
            for data_item in plot_data_collections:
                if data_item['type'] == "line" and data_item.get('annotate'):
                    x_indices = [subplot_date_to_local_idx[d] for d in data_item['dates']]
                    values = data_item['values']
                    for k_idx, x_val_idx in enumerate(x_indices):
                        y_val_actual = values.iloc[k_idx] if isinstance(values, pd.Series) else values[k_idx]
                        all_annotations.append({
                            'x': x_val_idx,
                            'y': y_val_actual,
                            'text': f'{y_val_actual:.1f}'
                        })
            
            # Sort annotations by x, then by y (descending for y to prioritize higher values)
            all_annotations.sort(key=lambda a: (a['x'], -a['y']))
            
            # Filter annotations to prevent overlap
            # We consider annotations overlapping if they're at the same x position or very close
            # and their y values are within a certain threshold
            filtered_annotations = []
            if all_annotations:
                # Estimate y-axis range for overlap threshold calculation
                y_values = [a['y'] for a in all_annotations]
                y_range = max(y_values) - min(y_values) if len(y_values) > 1 else 1
                y_overlap_threshold = y_range * 0.05  # 5% of y-range as threshold
                x_overlap_threshold = 0.15  # x positions within 0.15 units considered overlapping
                
                for ann in all_annotations:
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
            
            # Now add the filtered annotations to the plot
            for ann in filtered_annotations:
                ax.annotate(ann['text'],
                            (ann['x'], ann['y']),
                            textcoords="offset points", xytext=(0, 7),
                            ha='center', fontsize='x-small')

            if metric_col in ['num_tokens', 'KV_size_GB']:
                 ax.legend(loc='best', fontsize='small')
            else:
                 ax.legend(loc='upper left', bbox_to_anchor=(1, 1), fontsize='small')

            ax.set_title(f"{y_label} vs. Date for {self.model_name_in_plot}")
            ax.set_xlabel("Date")
            ax.set_ylabel(y_label)
            ax.grid(True)

            ax.set_xticks(range(len(ordered_subplot_dates)))
            ax.set_xticklabels([d.strftime('%Y-%m-%d') for d in ordered_subplot_dates], rotation=45, ha="right")
            
            # Removed: ax.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m-%d'))

            if metric_col == "num_tokens":
                explanation_text = 'Note: "# Tokens*" refers to the number of tokens for which the\nKey-Value (KV) Cache is allocated at server startup.'
                # Position text to the right of the plot area. transform=ax.transAxes means (0,0) is bottom-left, (1,1) is top-right of axes.
                ax.text(1.02, 0.5, explanation_text, transform=ax.transAxes, 
                        ha='left', va='center', fontsize='small', color='gray',
                        bbox=dict(boxstyle='round,pad=0.3', fc='white', alpha=0.5))

        # Remove the unused subplot if any
        if len(metrics_to_plot) < len(axes):
             for j in range(len(metrics_to_plot), len(axes)):
                 fig.delaxes(axes[j])

        # Adjust layout to make space for legends and potential side notes
        # The right padding (0.90) might need to be smaller if the note is wide, e.g., 0.85
        plt.tight_layout(rect=[0, 0, 0.88, 1]) # Adjusted right padding for side note

        # Remove the general explanation text from the bottom of the figure
        # fig.text(0.02, 0.01, explanation_text, ha='left', va='bottom', fontsize='small', color='gray')
        
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