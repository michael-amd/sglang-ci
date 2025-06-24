#!/usr/bin/env python3
"""
Compare CSV results from SGLang benchmarks and generate markdown reports.

This script handles both offline and online benchmark CSV formats.
"""

import argparse
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd


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


def compare_offline_results(main_df: pd.DataFrame, pr_df: pd.DataFrame) -> str:
    """Compare offline benchmark results and generate markdown."""
    output = []

    # Merge dataframes on common columns
    merge_cols = ["TP", "batch_size", "IL", "OL"]
    merged = pd.merge(main_df, pr_df, on=merge_cols, suffixes=("_main", "_pr"))

    if merged.empty:
        return "No common configurations found between main and PR results.\n"

    # Calculate performance differences
    metrics = [
        ("E2E_Throughput(token/s)", "higher"),
        ("Prefill_Throughput(token/s)", "higher"),
        ("Median_Decode_Throughput(token/s)", "higher"),
        ("E2E_Latency(s)", "lower"),
        ("Prefill_latency(s)", "lower"),
        ("Median_decode_latency(s)", "lower"),
    ]

    output.append("| Configuration | Metric | Main | PR | Change |")
    output.append("|---------------|--------|------|----|---------| ")

    for _, row in merged.iterrows():
        config = (
            f"TP={row['TP']}, BS={row['batch_size']}, IL={row['IL']}, OL={row['OL']}"
        )

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
                            f"| {config} | {metric_name} | {main_val:.2f} | {pr_val:.2f} | {change_str} |"
                        )
                except:
                    pass

    return "\n".join(output) + "\n"


def compare_online_results(
    main_data: Dict[str, pd.DataFrame], pr_data: Dict[str, pd.DataFrame]
) -> str:
    """Compare online benchmark results and generate markdown."""
    output = []

    for metric_type in ["E2E", "TTFT", "ITL"]:
        if metric_type not in main_data or metric_type not in pr_data:
            continue

        output.append(f"\n#### {metric_type} Latency Comparison\n")

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
            output.append("| Request Rate | Main (ms) | PR (ms) | Change |")
            output.append("|--------------|-----------|---------|---------|")

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
                        f"| {rate} | {main_val:.1f} | {pr_val:.1f} | {change_str} |"
                    )
                except:
                    pass

    return "\n".join(output) + "\n"


def main():
    parser = argparse.ArgumentParser(description="Compare SGLang benchmark CSV results")
    parser.add_argument(
        "--main-csv", required=True, help="Path to main branch CSV file"
    )
    parser.add_argument("--pr-csv", required=True, help="Path to PR CSV file")
    parser.add_argument(
        "--output-md", required=True, help="Path to output markdown file"
    )
    parser.add_argument("--append", action="store_true", help="Append to existing file")

    args = parser.parse_args()

    # Determine if this is offline or online benchmark based on filename
    is_offline = "offline" in args.main_csv.lower()

    output_lines = []

    if is_offline:
        # Parse offline CSVs
        main_df = parse_offline_csv(args.main_csv)
        pr_df = parse_offline_csv(args.pr_csv)

        if main_df is not None and pr_df is not None:
            comparison = compare_offline_results(main_df, pr_df)
            output_lines.append(comparison)
        else:
            output_lines.append("Failed to parse CSV files.\n")
    else:
        # Parse online CSVs
        main_data = parse_online_csv(args.main_csv)
        pr_data = parse_online_csv(args.pr_csv)

        if main_data and pr_data:
            comparison = compare_online_results(main_data, pr_data)
            output_lines.append(comparison)
        else:
            output_lines.append("Failed to parse CSV files.\n")

    # Write output
    mode = "a" if args.append else "w"
    with open(args.output_md, mode) as f:
        f.write("\n".join(output_lines))

    print(f"Comparison written to {args.output_md}")


if __name__ == "__main__":
    main()
