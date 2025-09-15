#!/usr/bin/env python3
"""
Standalone version of compare_suites.py that works without SGLang dependencies.
Parses run_suite.py as text instead of executing it.

USAGE:
    # Generate CSV report with date-stamped filename (RECOMMENDED)
    python3 compare_suites_standalone.py --format csv --output "sglang_ci_report_$(date +%Y%m%d).csv"

    # Compare suites with detailed breakdown
    python3 compare_suites_standalone.py https://github.com/sgl-project/sglang/blob/main/test/srt/run_suite.py

    # Generate summary only (no detailed test lists)
    python3 compare_suites_standalone.py --format csv --no-details
    python3 compare_suites_standalone.py --format markdown --no-details

REQUIREMENTS:
    - Internet connection (to fetch workflow and nightly test files)
    - Python requests library
"""

import argparse
import csv
import re
import sys
from datetime import datetime
from typing import Dict, List, Optional, Tuple

try:
    import requests
except ImportError:
    requests = None

# Suite pairs mapping from internal names to display names
SUITE_PAIRS = [
    ("per-commit", "per-commit-amd", "unit-test-backend-1-gpu"),
    ("per-commit-2-gpu", "per-commit-2-gpu-amd", "unit-test-backend-2-gpu"),
    ("per-commit-4-gpu", "per-commit-4-gpu-amd", "unit-test-backend-4-gpu"),
    ("per-commit-8-gpu", "per-commit-8-gpu-amd", "unit-test-backend-8-gpu"),
]

# URLs for workflow and test files
NVIDIA_WORKFLOW_URL = "https://raw.githubusercontent.com/sgl-project/sglang/main/.github/workflows/pr-test.yml"
AMD_WORKFLOW_URL = "https://raw.githubusercontent.com/sgl-project/sglang/main/.github/workflows/pr-test-amd.yml"
NVIDIA_NIGHTLY_URL = "https://raw.githubusercontent.com/sgl-project/sglang/main/test/srt/test_nightly_gsm8k_eval.py"
AMD_NIGHTLY_URL = "https://raw.githubusercontent.com/sgl-project/sglang/main/test/srt/test_nightly_gsm8k_eval_amd.py"


def count_unittest_executions(workflow_content: str, job_pattern: str) -> int:
    """Count unittest executions in a workflow job."""
    # Find the specific job section more precisely
    job_match = re.search(
        rf"  {job_pattern}:(.*?)(?=\n  [a-zA-Z]|$)", workflow_content, re.DOTALL
    )
    if not job_match:
        return 0

    job_content = job_match.group(1)

    # Only count unittest executions in run steps
    run_sections = re.findall(
        r"run:\s*\|(.*?)(?=\n      - name:|$)", job_content, re.DOTALL
    )

    total_count = 0
    for run_section in run_sections:
        # Count unittest executions
        unittest_count = len(re.findall(r"python3 -m unittest", run_section))

        # Count direct test file executions (but be more specific)
        direct_test_count = len(
            re.findall(r"python3 (?!-m)[^\s]*test[^\s]*\.py", run_section)
        )

        # Count AMD CI script executions with test files (for bench-test-2-gpu-amd)
        amd_ci_test_count = len(
            re.findall(r"bash.*amd_ci_exec\.sh.*python3.*test.*?\.py", run_section)
        )

        total_count += unittest_count + direct_test_count + amd_ci_test_count

    return total_count


def count_nightly_models(nightly_content: str) -> int:
    """Count models in MODEL_SCORE_THRESHOLDS dictionary."""
    # Find the MODEL_SCORE_THRESHOLDS section
    threshold_match = re.search(
        r"MODEL_SCORE_THRESHOLDS\s*=\s*\{([^}]+)\}", nightly_content, re.DOTALL
    )
    if not threshold_match:
        return 0

    threshold_content = threshold_match.group(1)

    # Count entries with threshold values (": 0.XX")
    model_count = len(re.findall(r'": 0\.\d+', threshold_content))

    return model_count


def get_dynamic_additional_categories() -> List[Tuple[str, int, int]]:
    """Dynamically fetch test counts from workflow and nightly files."""
    try:
        # Fetch workflow files
        nvidia_workflow = fetch_text(NVIDIA_WORKFLOW_URL)
        amd_workflow = fetch_text(AMD_WORKFLOW_URL)

        # Fetch nightly test files
        nvidia_nightly = fetch_text(NVIDIA_NIGHTLY_URL)
        amd_nightly = fetch_text(AMD_NIGHTLY_URL)

        # Count performance tests - ONLY from specific sections
        # performance-test-1-gpu = part-1 + part-2 for both NVIDIA and AMD
        nvidia_perf_1_part1 = count_unittest_executions(
            nvidia_workflow, "performance-test-1-gpu-part-1"
        )
        nvidia_perf_1_part2 = count_unittest_executions(
            nvidia_workflow, "performance-test-1-gpu-part-2"
        )
        nvidia_perf_1_total = nvidia_perf_1_part1 + nvidia_perf_1_part2

        amd_perf_1_part1 = count_unittest_executions(
            amd_workflow, "performance-test-1-gpu-part-1-amd"
        )
        amd_perf_1_part2 = count_unittest_executions(
            amd_workflow, "performance-test-1-gpu-part-2-amd"
        )
        amd_perf_1_total = amd_perf_1_part1 + amd_perf_1_part2

        # performance-test-2-gpu = ONLY from performance-test-2-gpu section (NVIDIA) and bench-test-2-gpu-amd (AMD)
        nvidia_perf_2 = count_unittest_executions(
            nvidia_workflow, "performance-test-2-gpu"
        )
        amd_perf_2 = count_unittest_executions(amd_workflow, "bench-test-2-gpu-amd")

        # Count accuracy tests
        nvidia_acc_1 = 1 if "accuracy-test-1-gpu:" in nvidia_workflow else 0
        nvidia_acc_2 = 1 if "accuracy-test-2-gpu:" in nvidia_workflow else 0
        amd_acc_1 = 1 if "accuracy-test-1-gpu-amd:" in amd_workflow else 0
        amd_acc_2 = 1 if "accuracy-test-2-gpu-amd:" in amd_workflow else 0

        # Count nightly models
        nvidia_nightly_count = count_nightly_models(nvidia_nightly)
        amd_nightly_count = count_nightly_models(amd_nightly)

        # Count frontend tests
        nvidia_frontend = 1 if "unit-test-frontend:" in nvidia_workflow else 0
        amd_frontend = 1 if "unit-test-frontend-amd:" in amd_workflow else 0

        return [
            ("nightly-models-test", amd_nightly_count, nvidia_nightly_count),
            ("unit-test-frontend", amd_frontend, nvidia_frontend),
            ("performance-test-1-gpu", amd_perf_1_total, nvidia_perf_1_total),
            ("performance-test-2-gpu", amd_perf_2, nvidia_perf_2),
            ("accuracy-test-1-gpu", amd_acc_1, nvidia_acc_1),
            ("accuracy-test-2-gpu", amd_acc_2, nvidia_acc_2),
        ]

    except Exception as e:
        raise RuntimeError(
            f"Failed to fetch dynamic test counts from workflow and nightly files.\n"
            f"Error: {e}\n"
            f"Please check your internet connection and ensure the following URLs are accessible:\n"
            f"- {NVIDIA_WORKFLOW_URL}\n"
            f"- {AMD_WORKFLOW_URL}\n"
            f"- {NVIDIA_NIGHTLY_URL}\n"
            f"- {AMD_NIGHTLY_URL}"
        )


def fetch_text(path_or_url: str) -> str:
    """Fetch text content from file or URL."""
    if re.match(r"^https?://", path_or_url):
        if requests is None:
            raise RuntimeError(
                "requests not installed. Install via: pip install requests"
            )
        url = path_or_url
        if "github.com" in url and "/blob/" in url:
            url = url.replace(
                "https://github.com/", "https://raw.githubusercontent.com/"
            ).replace("/blob/", "/")
        resp = requests.get(url, timeout=30)
        resp.raise_for_status()
        return resp.text
    with open(path_or_url, "r", encoding="utf-8") as f:
        return f.read()


def parse_suites_from_text(source_code: str) -> Optional[Dict[str, List[str]]]:
    """Parse test suites from run_suite.py source code using regex."""

    all_suites = {}

    # Look for main suites = { ... }
    suite_pattern = r"suites\s*=\s*\{([^}]+(?:\}[^}]*)*)\}"
    match = re.search(suite_pattern, source_code, re.DOTALL)

    if match:
        suite_content = match.group(1)
        parsed_suites = parse_suite_dict(suite_content)
        all_suites.update(parsed_suites)

    # Look for AMD suites: suite_amd = { ... }
    amd_suite_pattern = r"suite_amd\s*=\s*\{([^}]+(?:\}[^}]*)*)\}"
    amd_match = re.search(amd_suite_pattern, source_code, re.DOTALL)

    if amd_match:
        amd_suite_content = amd_match.group(1)
        amd_parsed_suites = parse_suite_dict(amd_suite_content)
        all_suites.update(amd_parsed_suites)

    # Look for suites.update(suite_amd) pattern
    if "suites.update(suite_amd)" in source_code and amd_match:
        amd_suite_content = amd_match.group(1)
        amd_parsed_suites = parse_suite_dict(amd_suite_content)
        all_suites.update(amd_parsed_suites)

    return all_suites if all_suites else None


def parse_suite_dict(suite_content: str) -> Dict[str, List[str]]:
    """Parse a single suite dictionary content."""
    suites = {}

    # Parse each suite entry like: "per-commit": [ ... ]
    # Handle multi-line arrays with proper bracket matching
    # This pattern matches quoted keys followed by arrays, handling nested brackets carefully
    suite_entry_pattern = r'"([^"]+)"\s*:\s*\[((?:[^\[\]]+|\[[^\]]*\])*)\]'

    for suite_match in re.finditer(suite_entry_pattern, suite_content, re.DOTALL):
        suite_name = suite_match.group(1)
        suite_tests_str = suite_match.group(2)

        # Extract test file names from TestFile objects and quoted strings
        test_files = []
        # Look for TestFile("filename.py") or TestFile("filename.py", time)
        # But exclude commented out lines
        lines = suite_tests_str.split("\n")
        for line in lines:
            # Skip commented out lines
            stripped_line = line.strip()
            if stripped_line.startswith("#"):
                continue

            test_pattern = r'TestFile\("([^"]+\.py)"(?:,\s*\d+)?\)'
            for test_match in re.finditer(test_pattern, line):
                test_file = test_match.group(1)
                test_files.append(test_file)

        # Also look for direct quoted strings (fallback)
        if not test_files:
            direct_pattern = r'"([^"]+\.py)"'
            for test_match in re.finditer(direct_pattern, suite_tests_str):
                test_file = test_match.group(1)
                test_files.append(test_file)

        suites[suite_name] = test_files

    return suites


def calculate_coverage(amd_count: int, nvidia_count: int) -> str:
    """Calculate AMD coverage percentage."""
    if nvidia_count == 0:
        return "N/A"
    return f"{(amd_count / nvidia_count) * 100:.0f}%"


def compare_suites(
    suites_map: Dict[str, List[str]],
    format_type: str = "markdown",
    no_details: bool = False,
):
    """Compare NVIDIA vs AMD test suites and output results."""

    if format_type == "csv":
        if no_details:
            # CSV header
            print("pair,nv_total,amd_total,common,only_nv,only_amd")

            # Process suite pairs
            for nv_suite, amd_suite, display_name in SUITE_PAIRS:
                nv_tests = suites_map.get(nv_suite, [])
                amd_tests = suites_map.get(amd_suite, [])

                # Exclude test_mla_flashinfer.py from per-commit suite
                if nv_suite == "per-commit":
                    nv_tests = [
                        test
                        for test in nv_tests
                        if "test_mla_flashinfer.py" not in test
                    ]

                nv_set = set(nv_tests)
                amd_set = set(amd_tests)
                common = len(nv_set & amd_set)
                only_nv = len(nv_set - amd_set)
                only_amd = len(amd_set - nv_set)

                print(
                    f"{display_name},{len(nv_tests)},{len(amd_tests)},{common},{only_nv},{only_amd}"
                )
        else:
            # Full CSV with additional categories
            print("Test Category,AMD # of Tests,Nvidia # of Tests,AMD Coverage (%)")

            total_amd = 0
            total_nvidia = 0

            # Process suite pairs
            for nv_suite, amd_suite, display_name in SUITE_PAIRS:
                nv_tests = suites_map.get(nv_suite, [])
                amd_tests = suites_map.get(amd_suite, [])

                # For unit-test-backend-1-gpu, include per-commit-amd-mi35x and deduplicate
                if nv_suite == "per-commit":
                    # Exclude test_mla_flashinfer.py from per-commit suite
                    nv_tests = [
                        test
                        for test in nv_tests
                        if "test_mla_flashinfer.py" not in test
                    ]

                    # Add mi35x suite tests and deduplicate
                    mi35x_tests = suites_map.get("per-commit-amd-mi35x", [])
                    amd_tests_set = set(amd_tests + mi35x_tests)
                    amd_tests = list(amd_tests_set)

                amd_count = len(amd_tests)
                nvidia_count = len(nv_tests)
                coverage = calculate_coverage(amd_count, nvidia_count)

                print(f"{display_name},{amd_count},{nvidia_count},{coverage}")
                total_amd += amd_count
                total_nvidia += nvidia_count

            # Add additional categories from dynamic analysis
            additional_categories = get_dynamic_additional_categories()
            for category, amd_count, nvidia_count in additional_categories:
                coverage = calculate_coverage(amd_count, nvidia_count)
                print(f"{category},{amd_count},{nvidia_count},{coverage}")
                total_amd += amd_count
                total_nvidia += nvidia_count

            # Total
            total_coverage = calculate_coverage(total_amd, total_nvidia)
            print(f"Total,{total_amd},{total_nvidia},{total_coverage}")

    else:  # markdown format
        for nv_suite, amd_suite, display_name in SUITE_PAIRS:
            nv_tests = suites_map.get(nv_suite, [])
            amd_tests = suites_map.get(amd_suite, [])

            # For unit-test-backend-1-gpu, include per-commit-amd-mi35x and deduplicate
            if nv_suite == "per-commit":
                # Exclude test_mla_flashinfer.py from per-commit suite
                nv_tests = [
                    test for test in nv_tests if "test_mla_flashinfer.py" not in test
                ]

                # Add mi35x suite tests and deduplicate
                mi35x_tests = suites_map.get("per-commit-amd-mi35x", [])
                amd_tests_set = set(amd_tests + mi35x_tests)
                amd_tests = list(amd_tests_set)

            print(f"## {nv_suite} (NVIDIA) vs {amd_suite} (AMD)")

            if not nv_tests and not amd_tests:
                print(f"Missing in suites: {nv_suite}, {amd_suite}")
                print()
                continue
            elif not amd_tests:
                print(f"Missing in suites: {amd_suite}")
                print()
                continue
            elif not nv_tests:
                print(f"Missing in suites: {nv_suite}")
                print()
                continue

            nv_set = set(nv_tests)
            amd_set = set(amd_tests)
            common = sorted(nv_set & amd_set)
            only_nv = sorted(nv_set - amd_set)
            only_amd = sorted(amd_set - nv_set)

            if no_details:
                print(f"| Suite | Total | Common | Only in NVIDIA | Only in AMD |")
                print(f"| --- | --- | --- | --- | --- |")
                print(
                    f"| {display_name} | {len(nv_tests)} vs {len(amd_tests)} | {len(common)} | {len(only_nv)} | {len(only_amd)} |"
                )
            else:
                print(f"| Suite | Total | Common | Only in NVIDIA | Only in AMD |")
                print(f"| --- | --- | --- | --- | --- |")
                print(
                    f"| {display_name} | {len(nv_tests)} vs {len(amd_tests)} | {len(common)} | {len(only_nv)} | {len(only_amd)} |"
                )
                print()
                print(f"| Common | Only in NVIDIA | Only in AMD |")
                print(f"| --- | --- | --- |")
                common_str = "<br>".join(common) if common else ""
                only_nv_str = "<br>".join(only_nv) if only_nv else ""
                only_amd_str = "<br>".join(only_amd) if only_amd else ""
                print(f"| {common_str} | {only_nv_str} | {only_amd_str} |")

            print()


def main():
    parser = argparse.ArgumentParser(
        description="Compare NVIDIA vs AMD test suites from SGLang (standalone version)"
    )
    parser.add_argument(
        "path_or_url",
        nargs="?",
        default="https://raw.githubusercontent.com/sgl-project/sglang/main/test/srt/run_suite.py",
        help="Path or URL to run_suite.py (default: GitHub raw URL)",
    )
    parser.add_argument(
        "--format",
        choices=["markdown", "csv"],
        default="markdown",
        help="Output format (default: markdown)",
    )
    parser.add_argument(
        "--no-details",
        action="store_true",
        help="Show summary only without detailed test lists",
    )
    parser.add_argument(
        "--output",
        "-o",
        help="Output file (default: stdout)",
    )
    args = parser.parse_args()

    # Fetch and parse the run_suite.py
    try:
        source = fetch_text(args.path_or_url)
        suites_map = parse_suites_from_text(source)
        if suites_map is None:
            print(
                "Error: could not parse suites from run_suite.py. The file format may have changed.",
                file=sys.stderr,
            )
            sys.exit(2)
    except Exception as e:
        print(f"Error fetching or parsing run_suite.py: {e}", file=sys.stderr)
        sys.exit(1)

    # Handle output to both terminal and file if needed
    if args.output:
        # Import io for StringIO
        import io

        # Capture output to string first
        output_buffer = io.StringIO()
        original_stdout = sys.stdout
        sys.stdout = output_buffer

        try:
            compare_suites(suites_map, args.format, args.no_details)
        finally:
            sys.stdout = original_stdout

        # Get the captured output
        output_content = output_buffer.getvalue()

        # Write to file
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(output_content)

        # Also print to terminal
        print(output_content, end="")
        print(f"Output written to: {args.output}")
    else:
        compare_suites(suites_map, args.format, args.no_details)


if __name__ == "__main__":
    main()
