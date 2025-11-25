#!/usr/bin/env python3
"""
Standalone version of compare_suites.py that works without SGLang dependencies.
Parses run_suite.py as text instead of executing it.

USAGE:
    # Default: Generate CSV summary report with date-stamped filename (outputs to both terminal and file)
    python3 compare_suites.py

    # Generate detailed markdown report with date-stamped filename (includes full test lists)
    python3 compare_suites.py --details

    # Generate report with custom filename
    python3 compare_suites.py --output "my_report.csv"
    python3 compare_suites.py --details --output "detailed_report.md"

    # Output only to terminal (no file)
    python3 compare_suites.py --stdout

    # Generate detailed markdown report to terminal only
    python3 compare_suites.py --details --stdout

    # Compare suites from specific URL
    python3 compare_suites.py https://github.com/sgl-project/sglang/blob/main/test/srt/run_suite.py

REQUIREMENTS:
    - Internet connection (to fetch workflow and nightly test files)
    - Python requests library
"""

import argparse
import csv
import os
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
    ("per-commit-1-gpu", "per-commit-amd", "unit-test-backend-1-gpu"),
    ("per-commit-2-gpu", "per-commit-2-gpu-amd", "unit-test-backend-2-gpu"),
    ("per-commit-4-gpu", "per-commit-4-gpu-amd", "unit-test-backend-4-gpu"),
    ("per-commit-8-gpu-h200", "per-commit-8-gpu-amd", "unit-test-backend-8-gpu"),
]

# URLs for workflow and test files
# Per-PR CI workflows
NVIDIA_WORKFLOW_URL = "https://raw.githubusercontent.com/sgl-project/sglang/main/.github/workflows/pr-test.yml"
AMD_WORKFLOW_URL = "https://raw.githubusercontent.com/sgl-project/sglang/main/.github/workflows/pr-test-amd.yml"
# Nightly CI workflows
NVIDIA_NIGHTLY_WORKFLOW_URL = "https://raw.githubusercontent.com/sgl-project/sglang/main/.github/workflows/nightly-test-nvidia.yml"
AMD_NIGHTLY_WORKFLOW_URL = "https://raw.githubusercontent.com/sgl-project/sglang/main/.github/workflows/nightly-test-amd.yml"
# Nightly test suite definitions
NVIDIA_NIGHTLY_SUITE_URL = "https://raw.githubusercontent.com/sgl-project/sglang/main/test/run_suite_nightly.py"
AMD_NIGHTLY_SUITE_URL = "https://raw.githubusercontent.com/sgl-project/sglang/main/test/srt/run_suite.py"  # AMD uses run_suite.py with nightly-amd suite
# Try multiple possible NVIDIA nightly file names (file may have been moved/renamed) - DEPRECATED, kept for backwards compatibility
NVIDIA_NIGHTLY_URLS = [
    "https://raw.githubusercontent.com/sgl-project/sglang/main/test/srt/nightly/test_text_models_gsm8k_eval.py",
    "https://raw.githubusercontent.com/sgl-project/sglang/main/test/srt/test_nightly_text_models_gsm8k_eval.py",
    "https://raw.githubusercontent.com/sgl-project/sglang/main/test/srt/test_nightly_gsm8k_eval.py",
]
AMD_NIGHTLY_URL = "https://raw.githubusercontent.com/sgl-project/sglang/main/test/srt/nightly/test_gsm8k_eval_amd.py"


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


def count_nightly_suite_tests_from_file(suite_file_content: str) -> int:
    """Count total tests across all nightly suites in run_suite_nightly.py or similar.

    Excludes hardware-specific suites (B200, H200, H20) for fair comparison with AMD.
    """
    # Find the suites dictionary
    suite_pattern = r"suites\s*=\s*\{([^}]+(?:\}[^}]*)*)\}"
    match = re.search(suite_pattern, suite_file_content, re.DOTALL)

    if not match:
        return 0

    suite_content = match.group(1)

    # Hardware-specific suites to exclude (no AMD equivalent)
    excluded_suites = ["b200", "h200", "h20", "gb200"]

    # Parse suite by suite to exclude specific hardware variants
    total_count = 0
    current_suite_name = None
    in_excluded_suite = False

    lines = suite_content.split("\n")
    for line in lines:
        stripped_line = line.strip()

        # Skip commented lines
        if stripped_line.startswith("#"):
            continue

        # Check if this is a suite name definition
        suite_name_match = re.match(r'"([^"]+)"\s*:\s*\[', stripped_line)
        if suite_name_match:
            current_suite_name = suite_name_match.group(1).lower()
            # Check if this suite should be excluded
            in_excluded_suite = any(hw in current_suite_name for hw in excluded_suites)

        # If we're not in an excluded suite, count TestFile entries
        if not in_excluded_suite:
            total_count += len(re.findall(r"TestFile\s*\(", line))

    return total_count


def count_nightly_suite_tests(suites_map: Dict[str, List[str]], suite_name: str) -> int:
    """Count tests in a nightly suite, excluding AMD-incompatible tests."""
    tests = suites_map.get(suite_name, [])
    # For NVIDIA suites, exclude AMD-incompatible tests for fair comparison
    if "amd" not in suite_name.lower():
        tests = [test for test in tests if not is_amd_incompatible(test)]
    return len(tests)


def get_dynamic_additional_categories() -> List[Tuple[str, int, int]]:
    """Dynamically fetch test counts from workflow and nightly files."""
    try:
        # Fetch workflow files
        nvidia_workflow = fetch_text(NVIDIA_WORKFLOW_URL)
        amd_workflow = fetch_text(AMD_WORKFLOW_URL)

        # Fetch nightly suite files
        # NVIDIA: count test files from run_suite_nightly.py (excludes hardware-specific variants)
        # AMD: count models from test_gsm8k_eval_amd.py (one file tests multiple models)
        try:
            nvidia_nightly_suite = fetch_text(NVIDIA_NIGHTLY_SUITE_URL)
            nvidia_nightly_count = count_nightly_suite_tests_from_file(
                nvidia_nightly_suite
            )
        except Exception as e:
            print(
                f"⚠️  Warning: Could not fetch NVIDIA nightly suite file from {NVIDIA_NIGHTLY_SUITE_URL}",
                file=sys.stderr,
            )
            print(f"   Error: {e}", file=sys.stderr)
            print(
                "   Continuing with NVIDIA nightly count = 0",
                file=sys.stderr,
            )
            nvidia_nightly_count = 0

        # For AMD, count models from the nightly test file
        # AMD's nightly suite has 1 test file that tests multiple models
        # We count models for fair comparison with NVIDIA's multiple test files
        try:
            amd_nightly_file = fetch_text(AMD_NIGHTLY_URL)
            amd_nightly_count = count_nightly_models(amd_nightly_file)
            if amd_nightly_count == 0:
                print(
                    f"⚠️  Warning: No models found in AMD nightly test file",
                    file=sys.stderr,
                )
        except Exception as e:
            print(
                f"⚠️  Warning: Could not fetch AMD nightly test file from {AMD_NIGHTLY_URL}",
                file=sys.stderr,
            )
            print(f"   Error: {e}", file=sys.stderr)
            amd_nightly_count = 0

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

        # Nightly test counts are already calculated above from suite files

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
            f"Required:\n"
            f"- {NVIDIA_WORKFLOW_URL}\n"
            f"- {AMD_WORKFLOW_URL}\n"
            f"Optional (for nightly test counts):\n"
            f"- {NVIDIA_NIGHTLY_SUITE_URL}\n"
            f"- {AMD_NIGHTLY_SUITE_URL}"
        ) from e


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


# AMD-incompatible test patterns (NVIDIA-only features and specific hardware variants)
# Excludes tests from specialized hardware suites: H200, H20, B200, DeepEP, DeepSeek v3.2, vLLM deps
AMD_INCOMPATIBLE_PATTERNS = [
    # FlashInfer - NVIDIA-only attention backend
    "flashinfer",
    # Mamba - NVIDIA-only state-space model architecture
    "mamba",
    # FlashAttention 3/4 - NVIDIA H100+/B200+ only
    "test_fa3.py",
    "flash_attention_4",
    # NVIDIA-specific models
    "nvidia_nemotron",
    # NVIDIA ModelOpt quantization tooling
    "modelopt",
    # TorchAO - primarily NVIDIA-focused quantization
    "torchao",
    # NVIDIA-specific FP8/INT8 kernels for MLA
    "test_mla_fp8.py",
    "test_mla_int8",
    # DeepEP (Expert Parallelism) - specific NVIDIA feature
    "deepep",
    "ep/test_deepep",
    "ep/test_mooncake_ep",
    # H200-specific tests
    "lora/test_lora_llama4",
    "test_deepseek_v3_basic",
    "test_deepseek_v3_mtp",
    "test_disaggregation_hybrid_attention",
    # H20-specific tests
    "test_w4a8_deepseek_v3",
    "test_disaggregation_different_tp",
    "test_disaggregation_pp",
    # B200-specific tests
    "test_deepseek_v3_fp4_4gpu",
    "test_gpt_oss_4gpu",  # Also appears in B200 suite (hardware-specific variant)
    # DeepSeek v3.2 tests (new architecture)
    "test_deepseek_v32",
    # vLLM dependency tests (not core SGLang)
    "test_vllm_dependency",
    "quant/test_awq.py",
    "test_bnb.py",
    "test_gptqmodel_dynamic",
    "test_gguf.py",
]


def is_amd_incompatible(test_name: str) -> bool:
    """Check if a test is AMD-incompatible based on known patterns."""
    test_lower = test_name.lower()
    return any(pattern.lower() in test_lower for pattern in AMD_INCOMPATIBLE_PATTERNS)


def process_suite_tests(
    nv_suite: str, amd_suite: str, suites_map: Dict[str, List[str]]
) -> Tuple[List[str], List[str]]:
    """Process and deduplicate test lists for a suite pair.

    Excludes AMD-incompatible tests from NVIDIA count to get accurate coverage.
    """
    nv_tests = suites_map.get(nv_suite, [])
    amd_tests = suites_map.get(amd_suite, [])

    # Exclude AMD-incompatible tests from NVIDIA denominator
    # (only count tests that are feasible on AMD hardware)
    nv_tests = [test for test in nv_tests if not is_amd_incompatible(test)]

    # For unit-test-backend-1-gpu, include per-commit-amd-mi35x and deduplicate
    if nv_suite == "per-commit-1-gpu":
        # Add mi35x suite tests and deduplicate
        mi35x_tests = suites_map.get("per-commit-amd-mi35x", [])
        amd_tests_set = set(amd_tests + mi35x_tests)
        amd_tests = list(amd_tests_set)

    return nv_tests, amd_tests


def compare_suites(
    suites_map: Dict[str, List[str]],
    format_type: str = "markdown",
):
    """Compare NVIDIA vs AMD test suites and output results."""

    if format_type == "csv":
        # CSV format with coverage analysis
        print("Test Category,AMD # of Tests,Nvidia # of Tests,AMD Coverage (%)")

        # Calculate CI category totals first
        per_pr_amd = 0
        per_pr_nvidia = 0

        # Process suite pairs for per-PR CI
        suite_details = []
        for nv_suite, amd_suite, display_name in SUITE_PAIRS:
            nv_tests, amd_tests = process_suite_tests(nv_suite, amd_suite, suites_map)
            amd_count = len(amd_tests)
            nvidia_count = len(nv_tests)
            coverage = calculate_coverage(amd_count, nvidia_count)
            suite_details.append((display_name, amd_count, nvidia_count, coverage))
            per_pr_amd += amd_count
            per_pr_nvidia += nvidia_count

        # Get additional categories from dynamic analysis
        additional_categories = get_dynamic_additional_categories()

        # Separate nightly and other categories
        nightly_amd = 0
        nightly_nvidia = 0
        other_details = []

        for category, amd_count, nvidia_count in additional_categories:
            coverage = calculate_coverage(amd_count, nvidia_count)
            if "nightly" in category.lower():
                nightly_amd += amd_count
                nightly_nvidia += nvidia_count
            else:
                per_pr_amd += amd_count
                per_pr_nvidia += nvidia_count
            other_details.append((category, amd_count, nvidia_count, coverage))

        # Print CI category summaries
        print("")
        print("# CI Category Summary")
        per_pr_coverage = calculate_coverage(per_pr_amd, per_pr_nvidia)
        print(
            f"Per-PR CI (total test count),{per_pr_amd},{per_pr_nvidia},{per_pr_coverage}"
        )

        nightly_coverage = calculate_coverage(nightly_amd, nightly_nvidia)
        print(
            f"Nightly/Periodic CI (total test count),{nightly_amd},{nightly_nvidia},{nightly_coverage}"
        )

        # Per-commit is same as Per-PR in SGLang
        print(
            f"Per-Commit CI (total test count),{per_pr_amd},{per_pr_nvidia},{per_pr_coverage}"
        )

        # Print detailed breakdown
        print("")
        print("# Detailed Breakdown")
        print("## Per-PR CI Tests")
        for display_name, amd_count, nvidia_count, coverage in suite_details:
            print(f"{display_name},{amd_count},{nvidia_count},{coverage}")

        print("")
        print("## Additional Test Categories")
        for category, amd_count, nvidia_count, coverage in other_details:
            print(f"{category},{amd_count},{nvidia_count},{coverage}")

        # Total
        total_amd = per_pr_amd + nightly_amd
        total_nvidia = per_pr_nvidia + nightly_nvidia
        total_coverage = calculate_coverage(total_amd, total_nvidia)
        print("")
        print(f"Total,{total_amd},{total_nvidia},{total_coverage}")

    else:  # markdown format
        for nv_suite, amd_suite, display_name in SUITE_PAIRS:
            nv_tests, amd_tests = process_suite_tests(nv_suite, amd_suite, suites_map)

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
        "--details",
        action="store_true",
        help="Show detailed test lists (default: summary only)",
    )
    parser.add_argument(
        "--output",
        "-o",
        help="Output file (default: date-stamped file based on format)",
    )
    parser.add_argument(
        "--stdout",
        action="store_true",
        help="Output to stdout instead of file",
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

    # Determine output behavior
    if args.stdout:
        # Output only to stdout
        # Details flag determines format: details=markdown, no-details=csv
        format_type = "markdown" if args.details else "csv"
        compare_suites(suites_map, format_type)
    else:
        # Default: output to both terminal and file
        import io
        from datetime import datetime

        # Determine output filename
        if args.output:
            output_file = args.output
        else:
            # Default date-stamped filename based on details flag
            # Save to ci_report directory relative to script location
            script_dir = os.path.dirname(os.path.abspath(__file__))
            ci_report_dir = os.path.join(script_dir, "ci_report")
            os.makedirs(ci_report_dir, exist_ok=True)

            date_stamp = datetime.now().strftime("%Y%m%d")
            if args.details:
                output_file = os.path.join(
                    ci_report_dir, f"sglang_ci_report_{date_stamp}.md"
                )
            else:
                output_file = os.path.join(
                    ci_report_dir, f"sglang_ci_report_{date_stamp}.csv"
                )

        # Capture output to string first
        output_buffer = io.StringIO()
        original_stdout = sys.stdout
        sys.stdout = output_buffer

        try:
            # Details flag determines format: details=markdown, no-details=csv
            format_type = "markdown" if args.details else "csv"
            compare_suites(suites_map, format_type)
        finally:
            sys.stdout = original_stdout

        # Get the captured output
        output_content = output_buffer.getvalue()

        # Write to file
        with open(output_file, "w", encoding="utf-8") as f:
            f.write(output_content)

        # Also print to terminal
        print(output_content, end="")
        print(f"Output written to: {output_file}")


if __name__ == "__main__":
    main()
