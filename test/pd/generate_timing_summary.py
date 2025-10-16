#!/usr/bin/env python3
"""
Generate timing summary for PD test runs.

This script reads test_summary.txt from a PD test run log directory
and generates a timing_summary.txt with timing information.
"""

import argparse
import os
import sys
from datetime import datetime
from pathlib import Path


def parse_log_for_timing(log_file):
    """Parse a log file to extract timing information."""
    if not os.path.exists(log_file):
        return None

    try:
        with open(log_file, "r") as f:
            content = f.read()
            # Look for timestamp patterns or other timing info
            # This is a simple placeholder - can be enhanced based on actual log format
            return {"file": os.path.basename(log_file), "exists": True}
    except Exception as e:
        return {"file": os.path.basename(log_file), "error": str(e)}


def generate_timing_summary(log_dir, test_summary_file, output_file):
    """Generate timing summary from test run logs."""

    # Read test_summary.txt
    if not os.path.exists(test_summary_file):
        print(
            f"ERROR: test_summary.txt not found at {test_summary_file}", file=sys.stderr
        )
        sys.exit(1)

    with open(test_summary_file, "r") as f:
        test_summary_content = f.read()

    # Extract key information from test_summary
    lines = test_summary_content.split("\n")
    test_date = ""
    docker_tag = ""
    hardware = ""
    model_name = ""
    docker_image = ""

    for line in lines:
        if line.startswith("Test Date:"):
            test_date = line.split(":", 1)[1].strip()
        elif line.startswith("Docker Tag:"):
            docker_tag = line.split(":", 1)[1].strip()
        elif line.startswith("Hardware:"):
            hardware = line.split(":", 1)[1].strip()
        elif line.startswith("Model:"):
            model_name = line.split(":", 1)[1].strip()
        elif line.startswith("Docker Image:"):
            docker_image = line.split(":", 1)[1].strip()

    # Get timing information from log files
    log_files = {
        "load_balance": os.path.join(log_dir, "load_balance.log"),
        "prefill": os.path.join(log_dir, "prefill.log"),
        "decode": os.path.join(log_dir, "decode.log"),
        "gsm8k": os.path.join(log_dir, "test_gsm8k.log"),
    }

    # Check if log files exist and get their sizes
    log_info = {}
    for name, path in log_files.items():
        if os.path.exists(path):
            stat = os.stat(path)
            log_info[name] = {
                "exists": True,
                "size": stat.st_size,
                "modified": datetime.fromtimestamp(stat.st_mtime).strftime(
                    "%Y-%m-%d %H:%M:%S"
                ),
            }
        else:
            log_info[name] = {"exists": False}

    # Count test result files
    test_result_files = []
    for f in os.listdir(log_dir):
        if f.startswith("test_") and f.endswith(".json"):
            test_result_files.append(f)

    # Get directory creation/modification time as test start time
    dir_stat = os.stat(log_dir)
    test_start_time = datetime.fromtimestamp(dir_stat.st_ctime).strftime(
        "%Y-%m-%d %H:%M:%S"
    )
    current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # Calculate elapsed time from directory name
    # Format: 20251015_030616_GPT-OSS-20B_v0.5.3.post1-rocm700-mi30x-20251014
    dir_name = os.path.basename(log_dir)
    if "_" in dir_name:
        date_time_part = "_".join(dir_name.split("_")[:2])
        try:
            start_dt = datetime.strptime(date_time_part, "%Y%m%d_%H%M%S")
            elapsed_seconds = (datetime.now() - start_dt).total_seconds()
            elapsed_time = f"{int(elapsed_seconds // 3600)}h {int((elapsed_seconds % 3600) // 60)}m {int(elapsed_seconds % 60)}s"
        except:
            elapsed_time = "Unknown"
    else:
        elapsed_time = "Unknown"

    # Generate timing summary
    summary_lines = []
    summary_lines.append("=" * 70)
    summary_lines.append("SGLang PD Disaggregation Test - Timing Summary")
    summary_lines.append("=" * 70)
    summary_lines.append("")
    summary_lines.append(f"Test Information:")
    summary_lines.append(f"  Docker Tag:       {docker_tag or docker_image}")
    summary_lines.append(f"  Hardware:         {hardware}")
    summary_lines.append(f"  Model Name:       {model_name}")
    summary_lines.append(
        f"  Test Date:        {test_date or os.path.basename(log_dir).split('_')[0] if '_' in os.path.basename(log_dir) else 'N/A'}"
    )
    summary_lines.append(f"  Test Start:       {test_start_time}")
    summary_lines.append(f"  Summary Created:  {current_time}")
    summary_lines.append(f"  Elapsed Time:     {elapsed_time}")
    summary_lines.append("")
    summary_lines.append(f"Log Directory:")
    summary_lines.append(f"  {log_dir}")
    summary_lines.append("")
    summary_lines.append("=" * 70)
    summary_lines.append("Component Log Files Status")
    summary_lines.append("=" * 70)
    summary_lines.append("")

    for name, info in log_info.items():
        if info["exists"]:
            size_kb = info["size"] / 1024
            summary_lines.append(
                f"  {name.upper():15} ✓ ({size_kb:.1f} KB, last modified: {info['modified']})"
            )
        else:
            summary_lines.append(f"  {name.upper():15} ✗ (not found)")

    # Extract GSM8K accuracy if available
    gsm8k_accuracy = "N/A"
    gsm8k_log = log_files.get("gsm8k")
    if gsm8k_log and os.path.exists(gsm8k_log):
        try:
            with open(gsm8k_log, "r") as f:
                content = f.read()
                # Look for "Accuracy: X.XXX" pattern
                import re

                match = re.search(r"Accuracy:\s*([\d.]+)", content)
                if match:
                    gsm8k_accuracy = match.group(1)
        except Exception:
            pass

    summary_lines.append("")
    summary_lines.append("=" * 70)
    summary_lines.append("Test Results Summary")
    summary_lines.append("=" * 70)
    summary_lines.append("")
    summary_lines.append(f"  Total test result files: {len(test_result_files)}")

    # Parse test results from test_summary
    test_results = []
    in_test_results = False
    for line in lines:
        if line.startswith("Test Results:"):
            in_test_results = True
            continue
        if in_test_results and line.startswith("-"):
            test_results.append(line)
        elif in_test_results and line.strip() == "":
            break

    if test_results:
        summary_lines.append("")
        for result in test_results:
            summary_lines.append(f"  {result.strip()}")

    # Add GSM8K accuracy if available
    if gsm8k_accuracy != "N/A":
        summary_lines.append(f"  - GSM8K Accuracy: {gsm8k_accuracy}")
        summary_lines.append(
            f"    (GSM8K log: {log_info.get('gsm8k', {}).get('size', 0) / 1024:.1f} KB)"
        )

    summary_lines.append("")
    summary_lines.append("=" * 70)
    summary_lines.append("Original Test Summary")
    summary_lines.append("=" * 70)
    summary_lines.append("")
    summary_lines.append(test_summary_content)
    summary_lines.append("")
    summary_lines.append("=" * 70)
    summary_lines.append(f"Timing summary generated at: {current_time}")
    summary_lines.append("=" * 70)

    # Write timing summary
    with open(output_file, "w") as f:
        f.write("\n".join(summary_lines))

    print(f"Timing summary generated: {output_file}")


def main():
    parser = argparse.ArgumentParser(
        description="Generate timing summary for PD test runs"
    )
    parser.add_argument(
        "--log-dir", required=True, help="Path to the PD test log directory"
    )
    parser.add_argument(
        "--output", help="Output file path (default: <log_dir>/timing_summary.txt)"
    )

    args = parser.parse_args()

    log_dir = os.path.abspath(args.log_dir)
    test_summary_file = os.path.join(log_dir, "test_summary.txt")

    if args.output:
        output_file = args.output
    else:
        output_file = os.path.join(log_dir, "timing_summary.txt")

    generate_timing_summary(log_dir, test_summary_file, output_file)


if __name__ == "__main__":
    main()
