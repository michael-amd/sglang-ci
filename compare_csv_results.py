#!/usr/bin/env python3
"""
Compare CSV results from SGLang benchmarks and generate markdown reports.

This script handles both offline and online benchmark CSV formats.

Usage Examples:
1. Offline GROK1 comparison:
   python3 compare_csv_results.py --csv1 offline/GROK1/20250624_GROK1_MOE-I4F8_offline --csv2 offline/GROK1/20250626_GROK1_MOE-I4F8_offline --mode offline --model grok1

2. Online GROK1 comparison:
   python3 compare_csv_results.py --csv1 online/GROK1/20250624_GROK1_MOE-I4F8_online --csv2 online/GROK1/20250626_GROK1_MOE-I4F8_online --mode online --model grok1

3. Offline DeepSeek-V3 comparison:
   python3 compare_csv_results.py --csv1 offline/DeepSeek-V3-0324/20250515_DeepSeek-V3-0324_FP8_offline --csv2 offline/DeepSeek-V3-0324/20250516_DeepSeek-V3-0324_FP8_offline --mode offline --model DeepSeek-V3-0324

Output:
- Creates a folder in /mnt/raid/michael/sgl_benchmark_ci/comparison_results/
- Folder name format: {date}_{csv1_dirname}_vs_{csv2_dirname}
- Contains a markdown file with the same name showing E2E performance comparisons
"""

import argparse
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from datetime import datetime

import numpy as np
import pandas as pd


def find_csv_files(directory: str, model: Optional[str] = None) -> List[Path]:
    """Find CSV files in the given directory, optionally filtering by model name."""
    dir_path = Path(directory)
    if not dir_path.exists():
        print(f"Directory {directory} does not exist", file=sys.stderr)
        return []

    csv_files = list(dir_path.glob("*.csv"))

    if model:
        # Filter by model name (case-insensitive)
        csv_files = [f for f in csv_files if model.lower() in f.name.lower()]

    return csv_files


def parse_offline_csv(filepath: str) -> pd.DataFrame:
    """Parse offline benchmark CSV file."""
    try:
        df = pd.read_csv(filepath)
        return df
    except Exception as e:
        print(f"Error parsing offline CSV {filepath}: {e}", file=sys.stderr)
        return None


def parse_online_csv(filepath: str) -> Dict[str, pd.DataFrame]:
    """Parse online benchmark CSV file with multiple sections."""
    try:
        with open(filepath, "r") as f:
            lines = f.readlines()

        # Find section boundaries
        sections = {}
        current_section = None
        current_data = []

        for line in lines:
            line = line.strip()
            if not line:
                continue

            if "E2E Latency" in line and "lower better" in line:
                if current_section and current_data:
                    sections[current_section] = current_data
                current_section = "E2E"
                current_data = []
            elif "TTFT" in line and "lower better" in line:
                if current_section and current_data:
                    sections[current_section] = current_data
                current_section = "TTFT"
                current_data = []
            elif "ITL" in line and "lower better" in line:
                if current_section and current_data:
                    sections[current_section] = current_data
                current_section = "ITL"
                current_data = []
            elif current_section:
                current_data.append(line)

        if current_section and current_data:
            sections[current_section] = current_data

        # Parse each section into DataFrame
        dataframes = {}
        for section_name, data in sections.items():
            # Find header line (contains "request rate")
            header_idx = None
            for i, line in enumerate(data):
                if "request rate" in line:
                    header_idx = i
                    break

            if header_idx is None:
                continue

            # Parse the data rows after header
            rows = []
            for line in data[header_idx + 1 :]:
                if line and not line.startswith("Online mode"):
                    parts = line.split("\t")
                    if len(parts) > 1:
                        rows.append(parts)

            if rows:
                # Create DataFrame
                headers = data[header_idx].split("\t")
                df = pd.DataFrame(rows, columns=headers)
                dataframes[section_name] = df

        return dataframes
    except Exception as e:
        print(f"Error parsing online CSV {filepath}: {e}", file=sys.stderr)
        return {}


def compare_offline_results(main_df: pd.DataFrame, pr_df: pd.DataFrame) -> List[str]:
    """Compare offline benchmark results and generate markdown."""
    output = []

    # Check if either dataframe has empty values
    main_has_data = not main_df.empty and main_df.iloc[:, 5:].notna().any().any()
    pr_has_data = not pr_df.empty and pr_df.iloc[:, 5:].notna().any().any()

    if not main_has_data and not pr_has_data:
        return ["❌ Both CSV files contain no benchmark data.\n", "\n"]
    elif not main_has_data:
        return ["❌ Main CSV contains no benchmark data. Cannot perform comparison.\n", "\n",
                "**PR CSV Summary:**\n", pr_df.to_string(index=False) + "\n", "\n"]
    elif not pr_has_data:
        return ["❌ PR CSV contains no benchmark data. Cannot perform comparison.\n", "\n",
                "**Main CSV Summary:**\n", main_df.to_string(index=False) + "\n", "\n"]

    # Merge dataframes on common columns
    merge_cols = ["TP", "batch_size", "IL", "OL"]
    merged = pd.merge(main_df, pr_df, on=merge_cols, suffixes=("_main", "_pr"))

    if merged.empty:
        return ["No common configurations found between main and PR results.\n", "\n"]

    # Only compare E2E metrics
    metrics = [
        ("E2E_Throughput(token/s)", "higher"),
        ("E2E_Latency(s)", "lower"),
    ]

    output.append("| Batch Size | Metric | Main | PR | Change |\n")
    output.append("|------------|--------|------|----|---------|\n")

    # Group by batch size
    for batch_size in sorted(merged['batch_size'].unique()):
        batch_rows = merged[merged['batch_size'] == batch_size]

        for _, row in batch_rows.iterrows():
            for metric, better_direction in metrics:
                main_col = f"{metric}_main"
                pr_col = f"{metric}_pr"

                if main_col in row and pr_col in row:
                    try:
                        main_val = float(row[main_col])
                        pr_val = float(row[pr_col])

                        if main_val > 0:
                            if better_direction == "higher":
                                change_pct = ((pr_val - main_val) / main_val) * 100
                            else:  # lower is better
                                change_pct = ((main_val - pr_val) / main_val) * 100

                            # Format change with color
                            if change_pct > 5:
                                change_str = f"**+{change_pct:.1f}%** 🟢"
                            elif change_pct < -5:
                                change_str = f"**{change_pct:.1f}%** 🔴"
                            else:
                                change_str = f"{change_pct:+.1f}%"

                            metric_name = (
                                metric.replace("(token/s)", "")
                                .replace("(s)", "")
                                .replace("_", " ")
                            )
                            output.append(
                                f"| {batch_size} | {metric_name} | {main_val:.2f} | {pr_val:.2f} | {change_str} |\n"
                            )
                    except (ValueError, KeyError, TypeError, ZeroDivisionError) as e:
                        print(f"Warning: Failed to process {metric} for batch_size {batch_size}: {e}", file=sys.stderr)
                        # Add a row indicating missing data
                        metric_name = metric.replace("(token/s)", "").replace("(s)", "").replace("_", " ")
                        output.append(f"| {batch_size} | {metric_name} | N/A | N/A | Error |\n")

    output.append("\n")  # Add empty line at the end
    return output


def compare_online_results(
    main_data: Dict[str, pd.DataFrame], pr_data: Dict[str, pd.DataFrame]
) -> List[str]:
    """Compare online benchmark results and generate markdown."""
    output = []

    for metric_type in ["E2E", "TTFT", "ITL"]:
        if metric_type not in main_data or metric_type not in pr_data:
            continue

        output.append(f"\n#### {metric_type} Latency Comparison\n\n")

        main_df = main_data[metric_type]
        pr_df = pr_data[metric_type]

        # Find MI300x rows
        main_mi300x_row = None
        pr_mi300x_row = None

        for idx, row in main_df.iterrows():
            if "MI300x" in str(row.iloc[0]):
                main_mi300x_row = row
                break

        for idx, row in pr_df.iterrows():
            if "MI300x" in str(row.iloc[0]):
                pr_mi300x_row = row
                break

        if main_mi300x_row is not None and pr_mi300x_row is not None:
            output.append("| Request Rate | Main (ms) | PR (ms) | Change |\n")
            output.append("|--------------|-----------|---------|---------|\n")

            # Compare each request rate
            for i in range(1, len(main_mi300x_row)):
                try:
                    rate = main_df.columns[i]
                    main_val = float(main_mi300x_row.iloc[i])
                    pr_val = float(pr_mi300x_row.iloc[i])

                    # Calculate percentage change (lower is better for latency)
                    change_pct = ((main_val - pr_val) / main_val) * 100

                    if change_pct > 5:
                        change_str = f"**+{change_pct:.1f}%** 🟢"
                    elif change_pct < -5:
                        change_str = f"**{change_pct:.1f}%** 🔴"
                    else:
                        change_str = f"{change_pct:+.1f}%"

                    output.append(
                        f"| {rate} | {main_val:.1f} | {pr_val:.1f} | {change_str} |\n"
                    )
                except (ValueError, KeyError, TypeError, IndexError, ZeroDivisionError) as e:
                    print(f"Warning: Failed to process request rate column {i} for {metric_type}: {e}", file=sys.stderr)
                    # Skip this column and continue

    output.append("\n")  # Add empty line at the end
    return output


def main():
    parser = argparse.ArgumentParser(description="Compare SGLang benchmark CSV results")
    parser.add_argument(
        "--csv1", required=True, help="Path to first CSV directory"
    )
    parser.add_argument("--csv2", required=True, help="Path to second CSV directory")
    parser.add_argument(
        "--mode", required=True, choices=["offline", "online"], help="Benchmark mode"
    )
    parser.add_argument(
        "--model", help="Model name to filter CSV files (optional)"
    )
    parser.add_argument(
        "--output-md", help="Path to output markdown file (optional, auto-generated if not provided)"
    )
    parser.add_argument(
        "--output-dir", help="Output directory (default: /mnt/raid/michael/sgl_benchmark_ci/comparison_results)"
    )
    parser.add_argument(
        "--append", action="store_true", help="Append to existing file"
    )

    args = parser.parse_args()

    # Find CSV files in both directories
    csv1_files = find_csv_files(args.csv1, args.model)

    if not csv1_files:
        print(f"No CSV files found in {args.csv1}", file=sys.stderr)
        sys.exit(1)

    csv2_files = find_csv_files(args.csv2, args.model)

    if not csv2_files:
        print(f"No CSV files found in {args.csv2}", file=sys.stderr)
        sys.exit(1)

    # For now, take the first CSV file from each directory
    # In the future, you might want to match files by date or other criteria
    main_csv = csv1_files[0]
    pr_csv = csv2_files[0]

    print(f"Comparing:\n  Main: {main_csv}\n  PR: {pr_csv}")

    # Generate output directory and filename if not provided
    if not args.output_md:
        base_output_dir = Path(args.output_dir) if args.output_dir else Path("/mnt/raid/michael/sgl_benchmark_ci/comparison_results")

        # Extract directory names from CSV paths
        csv1_dirname = Path(args.csv1).name
        csv2_dirname = Path(args.csv2).name

        # Generate folder name with date and CSV directory names
        date_str = datetime.now().strftime("%Y%m%d_%H%M%S")
        folder_name = f"{date_str}_{csv1_dirname}_vs_{csv2_dirname}"

        # Create the specific output folder
        output_folder = base_output_dir / folder_name
        output_folder.mkdir(parents=True, exist_ok=True)

        # Put markdown file inside the folder
        output_path = output_folder / f"{folder_name}.md"
    else:
        output_path = Path(args.output_md)
        output_path.parent.mkdir(parents=True, exist_ok=True)

    output_lines = []
    output_lines.append(f"## {args.model.upper() if args.model else 'Model'} Benchmark Comparison\n\n")
    output_lines.append(f"**Mode**: {args.mode}\n\n")
    output_lines.append(f"**Main CSV**: `{main_csv.name}`\n\n")
    output_lines.append(f"**PR CSV**: `{pr_csv.name}`\n\n")
    output_lines.append(f"**Generated**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")
    output_lines.append("### Results\n\n")

    if args.mode == "offline":
        # Parse offline CSVs
        main_df = parse_offline_csv(str(main_csv))
        pr_df = parse_offline_csv(str(pr_csv))

        if main_df is not None and pr_df is not None:
            comparison = compare_offline_results(main_df, pr_df)
            output_lines.extend(comparison)
        else:
            output_lines.append("Failed to parse CSV files.\n")
    else:
        # Parse online CSVs
        main_data = parse_online_csv(str(main_csv))
        pr_data = parse_online_csv(str(pr_csv))

        if main_data and pr_data:
            comparison = compare_online_results(main_data, pr_data)
            output_lines.extend(comparison)
        else:
            output_lines.append("Failed to parse CSV files.\n")

    # Write output
    mode = "a" if args.append else "w"
    with open(output_path, mode) as f:
        f.writelines(output_lines)

    print(f"Comparison written to {output_path}")


if __name__ == "__main__":
    main()
