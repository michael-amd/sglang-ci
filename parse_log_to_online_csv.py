#!/usr/bin/env python3
import os
import glob
import re
import json
import sys
from collections import defaultdict

# --------- Configuration ---------
# Set date, image name, and node values:
DATE = "20250401"
IMAGE_NAME = "20250331rc"
NODE = "dell300x-pla-t10-23"

# Folder path (adjust if needed or take as a command-line argument)
folder = f"./{DATE}_{IMAGE_NAME}_GROK1_CK-MOE-I4F8-AITER-DECODE-ATTN_online"
# Output CSV file name:
output_csv = os.path.join(folder, f"{DATE}_{IMAGE_NAME}_GROK1_CK-MOE-I4F8-AITER-DECODE-ATTN_online_parsed.csv")

# Request rates to consider (as strings)
req_rates = ["1", "2", "4", "8", "16"]
# We also include an "inf" column (set to blank if not available)
inf_col = "N/A"

# Hard-coded H100 reference arrays for each metric (online mode)
# (These values can be adjusted as needed.)
H100_E2E = ["13209", "13874", "16613", "44918", "85049", ""]
H100_TTFT = ["99.1", "102.0", "113.4", "170.7", "520.9", ""]
H100_ITL = ["23.0", "24.4", "25.9", "63.9", "108.6", ""]

# Row labels for the two modes (using the NODE variable)
label_aiter = f"MI300x-aiter (prefill+decode), {NODE}"
label_aiter_decode = f"MI300x-aiter_decode (decode only), {NODE}"

# --------- Helper functions ---------
def parse_metrics_from_file(filepath):
    """
    Parse the three metrics from a log file.
    Expected log lines:
      Median E2E Latency (ms): <value>
      Median TTFT (ms): <value>
      Median ITL (ms): <value>
    Returns a tuple of (e2e, ttft, itl) as floats if found; otherwise None.
    """
    with open(filepath, "r") as f:
        content = f.read()
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

def get_best_metrics_for_mode(mode):
    """
    For a given mode (either "aiter" or "aiter_decode"), find all log files,
    group them by request rate (extracted from filename), and choose the one
    with the minimum Median E2E Latency for each request rate.
    Returns a dict mapping request rate (string) -> (e2e, ttft, itl) (as floats or None)
    """
    # Pattern: sglang_client_log_grok1_<mode>_<rate>_run*.log
    pattern = os.path.join(folder, f"sglang_client_log_grok1_{mode}_*_run*.log")
    files = glob.glob(pattern)
    # Group by request rate
    groups = defaultdict(list)
    for filepath in files:
        basename = os.path.basename(filepath)
        # Example filename: sglang_client_log_grok1_aiter_16_run1_20250321_011929.log
        m = re.search(r"sglang_client_log_grok1_" + re.escape(mode) + r"_(\d+)_run", basename)
        if m:
            rate = m.group(1)
            groups[rate].append(filepath)
    best = {}
    for rate in req_rates:
        if rate not in groups:
            best[rate] = None
        else:
            best_val = None
            for filepath in groups[rate]:
                metrics = parse_metrics_from_file(filepath)
                if metrics is None or metrics[0] is None:
                    continue
                if best_val is None or metrics[0] < best_val[0]:
                    best_val = metrics
            best[rate] = best_val
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

# --------- Main Parsing ---------
# Read DOCKER_NAME from config.json
config_path = os.path.join(folder, "config.json")
docker_name = "N/A"
if os.path.exists(config_path):
    with open(config_path, "r") as f:
        try:
            config = json.load(f)
            docker_name = config.get("docker", "N/A")
        except:
            docker_name = "N/A"

# Get best metrics for both modes
best_aiter = get_best_metrics_for_mode("aiter")
best_decode = get_best_metrics_for_mode("aiter_decode")

# Now, build CSV content lines (as list of strings)
lines = []
lines.append(f"Online mode - GROK1 ({docker_name})")
lines.append("")

# Helper function to build a section given a header and two best dictionaries and H100 reference.
def build_section(section_title, h100_ref, label_aiter, label_decode):
    sec_lines = []
    sec_lines.append(section_title)
    # Header row: "request rate" then req_rates + "inf"
    header = "request rate\t" + "\t".join(req_rates) + "\tinf"
    sec_lines.append(header)
    # H100 row
    h100_line = "H100\t" + "\t".join(h100_ref) + "\t"
    sec_lines.append(h100_line)
    # Row for aiter
    aiter_values = []
    for r in req_rates:
        m = best_aiter.get(r)
        if m is None:
            aiter_values.append("N/A")
        else:
            # Depending on section, choose index: 0 for E2E, 1 for TTFT, 2 for ITL
            if section_title.startswith("Median E2E"):
                aiter_values.append(format_metric(m[0]))
            elif section_title.startswith("Median TTFT"):
                aiter_values.append(format_metric(m[1]))
            elif section_title.startswith("Median ITL"):
                aiter_values.append(format_metric(m[2]))
    aiter_line = f"{label_aiter}\t" + "\t".join(aiter_values) + "\t"
    sec_lines.append(aiter_line)
    # Row for aiter_decode
    decode_values = []
    for r in req_rates:
        m = best_decode.get(r)
        if m is None:
            decode_values.append("N/A")
        else:
            if section_title.startswith("Median E2E"):
                decode_values.append(format_metric(m[0]))
            elif section_title.startswith("Median TTFT"):
                decode_values.append(format_metric(m[1]))
            elif section_title.startswith("Median ITL"):
                decode_values.append(format_metric(m[2]))
    decode_line = f"{label_decode}\t" + "\t".join(decode_values) + "\t"
    sec_lines.append(decode_line)
    # Ratio rows for each mode: compute ratio row as: H100/MI300x-aiter and H100/MI300x-aiter_decode.
    ratio_aiter = []
    for idx, r in enumerate(req_rates):
        m_val = best_aiter.get(r)
        if m_val is None:
            ratio_aiter.append("N/A")
        else:
            if section_title.startswith("Median E2E"):
                ratio_aiter.append(compute_ratio(h100_ref[idx], m_val[0]))
            elif section_title.startswith("Median TTFT"):
                ratio_aiter.append(compute_ratio(h100_ref[idx], m_val[1]))
            elif section_title.startswith("Median ITL"):
                ratio_aiter.append(compute_ratio(h100_ref[idx], m_val[2]))
    ratio_line_aiter = f"H100/MI300x-aiter\t" + "\t".join(ratio_aiter)
    sec_lines.append(ratio_line_aiter)
    
    ratio_decode = []
    for idx, r in enumerate(req_rates):
        m_val = best_decode.get(r)
        if m_val is None:
            ratio_decode.append("N/A")
        else:
            if section_title.startswith("Median E2E"):
                ratio_decode.append(compute_ratio(h100_ref[idx], m_val[0]))
            elif section_title.startswith("Median TTFT"):
                ratio_decode.append(compute_ratio(h100_ref[idx], m_val[1]))
            elif section_title.startswith("Median ITL"):
                ratio_decode.append(compute_ratio(h100_ref[idx], m_val[2]))
    ratio_line_decode = f"H100/MI300x-aiter_decode\t" + "\t".join(ratio_decode)
    sec_lines.append(ratio_line_decode)
    
    return sec_lines

# Build sections using the updated labels
section_e2e = build_section("Median E2E Latency (ms, lower better)", H100_E2E[:5], 
                            label_aiter, label_aiter_decode)
section_ttft = build_section("Median TTFT (ms, lower better)", H100_TTFT[:5], 
                             label_aiter, label_aiter_decode)
section_itl = build_section("Median ITL (ms, lower better)", H100_ITL[:5], 
                            label_aiter, label_aiter_decode)

# Append sections to lines (with blank lines between)
lines.extend(section_e2e)
lines.append("")
lines.extend(section_ttft)
lines.append("")
lines.extend(section_itl)

# Write final CSV (tab-delimited text file)
with open(output_csv, "w") as f:
    for line in lines:
        f.write(line + "\n")

print(f"CSV summary saved to {output_csv}")
