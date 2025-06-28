#!/usr/bin/env python3
import glob
import json
import os
import re
import sys
from collections import defaultdict

# --------- Configuration ---------
# Set date and node values:
RUN_TAG = "20250626"  # Example: 20250511 (Update this for different runs)
NODE = "dell300x-pla-t10-23"

# Model name
MODEL_NAME = "GROK1"

# Request rates to consider (as strings)
req_rates = ["1", "2", "4", "8", "16"]

# Hard-coded H100 reference arrays for each metric (online mode)
H100_E2E = ["13209", "13874", "16613", "44918", "85049"]
H100_TTFT = ["99.1", "102.0", "113.4", "170.7", "520.9"]
H100_ITL = ["23.0", "24.4", "25.9", "63.9", "108.6"]

# --------- Helper functions ---------
def setup_and_validate_folder(run_tag, model_name):
    """
    Set up folder path and validate that it exists with proper permissions.
    Returns (folder_path, output_csv_path) or exits on error.
    """
    # New naming convention: online/<MODEL_NAME>/<RUN_TAG>_<MODEL_NAME>_MOE-I4F8_online
    folder_base_name = f"{run_tag}_{model_name}_MOE-I4F8_online"
    folder = f"./online/{model_name}/{folder_base_name}"  # Assumes script is run from sgl_benchmark_ci directory

    # Check if folder exists
    if not os.path.exists(folder):
        print(f"Error: Folder does not exist: {folder}")
        print(f"Please make sure the benchmark has been run for date {run_tag}")
        sys.exit(1)

    # Output CSV file name:
    output_csv = os.path.join(folder, f"{folder_base_name}.csv")

    # Check write permissions
    try:
        # Try to create a temp file to test write permissions
        test_file = os.path.join(folder, ".test_write_permission")
        with open(test_file, "w") as f:
            f.write("test")
        os.remove(test_file)
    except PermissionError:
        print(f"Error: No write permission in folder: {folder}")
        print("Please check folder permissions or run with appropriate privileges")
        sys.exit(1)
    except Exception as e:
        print(f"Error checking permissions: {e}")
        sys.exit(1)

    return folder, output_csv


def parse_metrics_from_file(filepath):
    """
    Parse the three metrics from a log file.
    Expected log lines:
      Median E2E Latency (ms): <value>
      Median TTFT (ms): <value>
      Median ITL (ms): <value>
    Returns a tuple of (e2e, ttft, itl) as floats if found; otherwise None.
    """
    try:
        with open(filepath, "r") as f:
            content = f.read()
    except Exception as e:
        print(f"Warning: Could not read file {filepath}: {e}")
        return None

    # Use regex to extract numbers
    e2e_match = re.search(r"Median E2E Latency \(ms\):\s*([\d\.]+)", content)
    ttft_match = re.search(r"Median TTFT \(ms\):\s*([\d\.]+)", content)
    itl_match = re.search(r"Median ITL \(ms\):\s*([\d\.]+)", content)
    if e2e_match:
        try:
            e2e = float(e2e_match.group(1))
        except ValueError:
            e2e = None
    else:
        e2e = None
    if ttft_match:
        try:
            ttft = float(ttft_match.group(1))
        except ValueError:
            ttft = None
    else:
        ttft = None
    if itl_match:
        try:
            itl = float(itl_match.group(1))
        except ValueError:
            itl = None
    else:
        itl = None

    if e2e is None and ttft is None and itl is None:
        return None
    return (e2e, ttft, itl)


def detect_attention_backend(folder):
    """
    Detect which attention backend was used by examining log files or server output.
    Returns "aiter", "triton", or "unknown" if detection fails.
    """
    # First try to check server output log
    server_log = os.path.join(folder, "server_output_aiter.log")
    if os.path.exists(server_log):
        try:
            with open(server_log, "r") as f:
                content = f.read()
                if "--attention-backend aiter" in content:
                    return "aiter"
                elif "--attention-backend triton" in content:
                    return "triton"
        except Exception as e:
            print(f"Warning: Could not read server log: {e}")

    # If not found in server log, check client log filenames
    log_files = glob.glob(os.path.join(folder, "sglang_client_log_*.log"))
    for filepath in log_files:
        basename = os.path.basename(filepath)
        # Look for patterns like grok1_aiter_1_run or grok1_triton_1_run
        if "_aiter_" in basename:
            return "aiter"
        elif "_triton_" in basename:
            return "triton"

    # If no backend is detected, return unknown and warn user
    print("Warning: Could not detect attention backend from logs. Returning 'unknown'.")
    print("This may indicate missing or incorrectly named log files.")
    return "unknown"


def get_best_metrics_for_backend(backend, folder, model_name):
    """
    For a given backend (either "aiter" or "triton"), find all log files,
    group them by request rate (extracted from filename), and choose the one
    with the minimum Median E2E Latency for each request rate.
    Returns a dict mapping request rate (string) -> (e2e, ttft, itl) (as floats or None)
    """
    # Pattern: sglang_client_log_<MODEL_NAME>_<backend>_<rate>_run*.log
    pattern = os.path.join(
        folder, f"sglang_client_log_{model_name}_{backend}_*_run*.log"
    )
    files = glob.glob(pattern)
    print(f"Found {len(files)} log files for backend {backend}")

    # Group by request rate
    groups = defaultdict(list)
    for filepath in files:
        basename = os.path.basename(filepath)
        # Example filename: sglang_client_log_GROK1_aiter_1_run1_20250401_231818.log
        m = re.search(
            r"sglang_client_log_"
            + model_name
            + "_"
            + re.escape(backend)
            + r"_(\d+)_run",
            basename,
        )
        if m:
            rate = m.group(1)
            groups[rate].append(filepath)

    best = {}
    for rate in req_rates:
        if rate not in groups:
            best[rate] = None
            print(f"No logs found for rate {rate}")
        else:
            best_val = None
            for filepath in groups[rate]:
                metrics = parse_metrics_from_file(filepath)
                if metrics is None or metrics[0] is None:
                    continue
                if best_val is None or metrics[0] < best_val[0]:
                    best_val = metrics
            best[rate] = best_val
            if best_val:
                print(
                    f"Rate {rate}: Best E2E={best_val[0]:.2f}ms from {len(groups[rate])} runs"
                )
    return best


def compute_ratio(ref_str, meas_val):
    """Compute ratio as integer percentage: round(ref/meas*100). If meas is None or zero, return 'N/A'."""
    try:
        ref = float(ref_str)
    except:
        return "N/A"
    if meas_val is None or meas_val == 0:
        return "N/A"
    ratio = round(ref / meas_val * 100)
    return f"{ratio}%"


def format_metric(value):
    """Format a numeric value as string; if None, return 'N/A'."""
    if value is None:
        return "N/A"
    # Remove trailing zeros if possible
    if int(value) == value:
        return str(int(value))
    return f"{value:.2f}"


def read_docker_image_name(folder_path):
    """
    Read docker image name from config.json in the specified folder.
    Returns docker image name string or "N/A" if not found/readable.
    """
    config_path = os.path.join(folder_path, "config.json")
    docker_name = "N/A"
    if os.path.exists(config_path):
        try:
            with open(config_path, "r") as f:
                config = json.load(f)
                docker_name = config.get("docker", "N/A")
        except Exception as e:
            print(f"Warning: Could not read config.json: {e}")
            docker_name = "N/A"
    return docker_name


def build_section(section_title, h100_ref, backend_metrics, backend_name):
    """
    Helper function to build a section given a header and metrics dictionary and H100 reference.
    """
    sec_lines = []
    sec_lines.append(section_title)
    # Header row: "request rate" then req_rates
    header = "request rate\t" + "\t".join(req_rates)
    sec_lines.append(header)
    # H100 row
    h100_line = "H100\t" + "\t".join(h100_ref)
    sec_lines.append(h100_line)
    # Row for MI300x with backend
    mi300x_values = []
    for r in req_rates:
        m = backend_metrics.get(r)
        if m is None:
            mi300x_values.append("N/A")
        else:
            # Depending on section, choose index: 0 for E2E, 1 for TTFT, 2 for ITL
            if section_title.startswith("Median E2E"):
                mi300x_values.append(format_metric(m[0]))
            elif section_title.startswith("Median TTFT"):
                mi300x_values.append(format_metric(m[1]))
            elif section_title.startswith("Median ITL"):
                mi300x_values.append(format_metric(m[2]))
    mi300x_line = f"MI300x-{backend_name}, {NODE}\t" + "\t".join(mi300x_values)
    sec_lines.append(mi300x_line)
    # Ratio row: compute ratio row as: H100/MI300x-backend
    ratio_values = []
    for idx, r in enumerate(req_rates):
        m_val = backend_metrics.get(r)
        if m_val is None:
            ratio_values.append("N/A")
        else:
            if section_title.startswith("Median E2E"):
                ratio_values.append(compute_ratio(h100_ref[idx], m_val[0]))
            elif section_title.startswith("Median TTFT"):
                ratio_values.append(compute_ratio(h100_ref[idx], m_val[1]))
            elif section_title.startswith("Median ITL"):
                ratio_values.append(compute_ratio(h100_ref[idx], m_val[2]))
    ratio_line = f"H100/MI300x-{backend_name}\t" + "\t".join(ratio_values)
    sec_lines.append(ratio_line)

    return sec_lines


def generate_csv_content(model_name, docker_name, best_metrics, backend):
    """
    Generate CSV content lines as a list of strings.
    """
    lines = []
    lines.append(f"Online mode - {model_name} ({docker_name})")
    lines.append("")

    # Build sections using the detected backend
    section_e2e = build_section(
        "Median E2E Latency (ms, lower better)", H100_E2E, best_metrics, backend
    )
    section_ttft = build_section(
        "Median TTFT (ms, lower better)", H100_TTFT, best_metrics, backend
    )
    section_itl = build_section(
        "Median ITL (ms, lower better)", H100_ITL, best_metrics, backend
    )

    # Append sections to lines (with blank lines between)
    lines.extend(section_e2e)
    lines.append("")
    lines.extend(section_ttft)
    lines.append("")
    lines.extend(section_itl)

    return lines


def write_csv_file(output_csv_path, lines):
    """
    Write CSV content to file with error handling and fallback to stdout.
    """
    try:
        # Add newlines to each line for writelines()
        lines_with_newlines = [line + "\n" for line in lines]
        with open(output_csv_path, "w") as f:
            f.writelines(lines_with_newlines)
        print(f"CSV summary saved to {output_csv_path}")
    except PermissionError:
        print(f"Error: Permission denied when writing to {output_csv_path}")
        print("Possible solutions:")
        print("1. Check if the file is open in another program")
        print("2. Check file/folder permissions")
        print("3. Try running with sudo if appropriate")
        # Try to output to stdout as fallback
        print("\n--- CSV Output ---")
        for line in lines:
            print(line)
    except Exception as e:
        print(f"Error writing CSV: {e}")
        sys.exit(1)


# --------- Main Parsing ---------
def main():
    """Main function to orchestrate the parsing and CSV generation."""
    # Setup folder and validate permissions
    folder, output_csv = setup_and_validate_folder(RUN_TAG, MODEL_NAME)

    # Read docker image name from config.json
    docker_name = read_docker_image_name(folder)

    # Detect which attention backend was used
    backend = detect_attention_backend(folder)
    print(f"Detected attention backend: {backend}")

    # Get best metrics for the detected backend
    best_metrics = get_best_metrics_for_backend(backend, folder, MODEL_NAME)

    # Generate CSV content
    lines = generate_csv_content(MODEL_NAME, docker_name, best_metrics, backend)

    # Write final CSV (tab-delimited text file)
    write_csv_file(output_csv, lines)


if __name__ == "__main__":
    main()
