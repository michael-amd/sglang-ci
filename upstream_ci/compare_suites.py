#!/usr/bin/env python3
import argparse
import os
import re
import runpy
import sys
import tempfile
from typing import Any, Dict, List, Optional, Tuple

try:
    import requests
except ImportError:
    requests = None

SUITE_PAIRS = [
    ("per-commit", "per-commit-amd"),
    ("per-commit-2-gpu", "per-commit-2-gpu-amd"),
    ("per-commit-4-gpu", "per-commit-4-gpu-amd"),
    ("per-commit-8-gpu", "per-commit-8-gpu-amd"),
]

LIKELY_SUITE_VARNAMES = [
    "suites",
    "SUITES",
    "SUITE_MAP",
    "SUITE_DEFS",
    "SUITE_DEFINITIONS",
    "TEST_SUITES",
    "SRT_SUITES",
]


def fetch_text(path_or_url: str) -> str:
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


def write_temp_py(text: str) -> str:
    fd, path = tempfile.mkstemp(suffix=".py", prefix="run_suite_")
    os.close(fd)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


def normalize_suite_value(val: Any) -> Optional[List[str]]:
    if isinstance(val, (list, tuple)):
        out: List[str] = []
        for item in val:
            if isinstance(item, str):
                out.append(item)
            else:
                name = getattr(item, "name", None)
                if isinstance(name, str):
                    out.append(name)
                else:
                    return None
        return out
    return None


def is_suite_mapping(obj: Any) -> bool:
    if not isinstance(obj, dict) or not obj:
        return False
    if not all(isinstance(k, str) for k in obj.keys()):
        return False
    values = list(obj.values())
    good = 0
    for v in values:
        norm = normalize_suite_value(v)
        if isinstance(norm, list):
            good += 1
    return good >= max(1, len(values) // 2)


def load_suites_by_executing(source_code: str) -> Optional[Dict[str, List[str]]]:
    path = write_temp_py(source_code)
    prev_argv = sys.argv[:]
    try:
        globs = {"__name__": "sglang_srt_run_suite_loaded"}
        sys.argv = [path]
        os.environ.setdefault("CI", "1")
        module_globals = runpy.run_path(path, init_globals=globs)
    except Exception:
        return None
    finally:
        sys.argv = prev_argv
        try:
            os.remove(path)
        except Exception:
            pass

    for name in LIKELY_SUITE_VARNAMES:
        raw = module_globals.get(name)
        if is_suite_mapping(raw):
            norm: Dict[str, List[str]] = {}
            for k, v in raw.items():
                nv = normalize_suite_value(v)
                if nv is not None:
                    norm[k] = nv
            return norm

    candidates = [v for v in module_globals.values() if is_suite_mapping(v)]
    if candidates:
        targets = {
            "per-commit",
            "per-commit-amd",
            "per-commit-2-gpu",
            "per-commit-2-gpu-amd",
            "per-commit-4-gpu",
            "per-commit-4-gpu-amd",
            "per-commit-8-gpu",
            "per-commit-8-gpu-amd",
        }
        for cand in candidates:
            if any(k in cand for k in targets):
                norm: Dict[str, List[str]] = {}
                for k, v in cand.items():
                    nv = normalize_suite_value(v)
                    if nv is not None:
                        norm[k] = nv
                return norm
    return None


def to_markdown_table(headers: List[str], rows: List[List[str]]) -> str:
    # Basic Markdown table with header separator
    line = "| " + " | ".join(headers) + " |"
    sep = "| " + " | ".join("---" for _ in headers) + " |"
    body = "\n".join("| " + " | ".join(row) + " |" for row in rows)
    return "\n".join([line, sep, body]) if rows else "\n".join([line, sep])


def to_csv(headers: List[str], rows: List[List[str]]) -> str:
    def esc(cell: str) -> str:
        if any(c in cell for c in [",", "\n", '"']):
            return '"' + cell.replace('"', '""') + '"'
        return cell

    out = [",".join(esc(h) for h in headers)]
    out += [",".join(esc(c) for c in row) for row in rows]
    return "\n".join(out)


def list_to_multiline_cell(items: List[str], limit: Optional[int]) -> str:
    if limit is not None and len(items) > limit:
        shown = items[:limit]
        rest = len(items) - limit
        return "<br>".join(shown) + f"<br>... and {rest} more"
    return "<br>".join(items)


def compare_pair(nv_list: List[str], amd_list: List[str]):
    nv_set = set(nv_list)
    amd_set = set(amd_list)
    only_nv = sorted(nv_set - amd_set)
    only_amd = sorted(amd_set - nv_set)
    common = sorted(nv_set & amd_set)
    return only_nv, only_amd, common


def main():
    parser = argparse.ArgumentParser(
        description="Compare NVIDIA vs AMD test suites and render as Markdown/CSV"
    )
    parser.add_argument(
        "path_or_url",
        nargs="?",
        default="https://raw.githubusercontent.com/sgl-project/sglang/main/test/srt/run_suite.py",
        help="Path or URL to run_suite.py (default: GitHub raw URL)",
    )
    parser.add_argument(
        "--pairs",
        nargs="*",
        default=[],
        help="Pairs like 'per-commit,per-commit-amd' 'per-commit-2-gpu,per-commit-2-gpu-amd'",
    )
    parser.add_argument(
        "--format",
        choices=["markdown", "csv", "text"],
        default="markdown",
        help="Output format (default: markdown)",
    )
    parser.add_argument(
        "--no-details",
        action="store_true",
        help="Only print the summary (omit the long Common/Only-in tables)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Limit the number of entries shown per section; shows a '... and N more' suffix",
    )
    args = parser.parse_args()

    source = fetch_text(args.path_or_url)
    suites_map = load_suites_by_executing(source)
    if suites_map is None:
        print(
            "Error: could not evaluate suites from run_suite.py. Run inside the sglang repo so imports resolve, or pass a local path.",
            file=sys.stderr,
        )
        sys.exit(2)

    if args.pairs:
        pairs: List[Tuple[str, str]] = []
        for token in args.pairs:
            if "," in token:
                nv, amd = [x.strip() for x in token.split(",", 1)]
                pairs.append((nv, amd))
            else:
                print(f"Unrecognized pair token: {token}", file=sys.stderr)
                sys.exit(3)
    else:
        pairs = SUITE_PAIRS

    # Build outputs
    for nv, amd in pairs:
        nv_list = suites_map.get(nv)
        amd_list = suites_map.get(amd)

        header = f"{nv} (NVIDIA) vs {amd} (AMD)"
        if nv_list is None or amd_list is None:
            print(f"# {header}" if args.format == "markdown" else header)
            missing = []
            if nv_list is None:
                missing.append(nv)
            if amd_list is None:
                missing.append(amd)
            print(f"Missing in suites: {', '.join(missing)}")
            print()
            continue

        only_nv, only_amd, common = compare_pair(nv_list, amd_list)

        # Summary table
        if args.format == "markdown":
            print(f"## {header}")
            headers = ["Suite", "Total", "Common", "Only in NVIDIA", "Only in AMD"]
            rows = [
                [
                    f"{nv} vs {amd}",
                    str(len(nv_list)) + " vs " + str(len(amd_list)),
                    str(len(common)),
                    str(len(only_nv)),
                    str(len(only_amd)),
                ]
            ]
            print(to_markdown_table(headers, rows))
            print()
        elif args.format == "csv":
            headers = ["pair", "nv_total", "amd_total", "common", "only_nv", "only_amd"]
            rows = [
                [
                    f"{nv} vs {amd}",
                    str(len(nv_list)),
                    str(len(amd_list)),
                    str(len(common)),
                    str(len(only_nv)),
                    str(len(only_amd)),
                ]
            ]
            print(to_csv(headers, rows))
        else:
            # text
            print(f"{header}")
            print(
                f"- Total: {len(nv_list)} vs {len(amd_list)}; Common={len(common)}; Only NV={len(only_nv)}; Only AMD={len(only_amd)}"
            )

        if not args.no_details:
            # Detailed table with multi-line cells using <br> (renders well in GitHub/Markdown)
            if args.format == "markdown":
                headers = ["Common", "Only in NVIDIA", "Only in AMD"]
                rows = [
                    [
                        list_to_multiline_cell(common, args.limit),
                        list_to_multiline_cell(only_nv, args.limit),
                        list_to_multiline_cell(only_amd, args.limit),
                    ]
                ]
                print(to_markdown_table(headers, rows))
                print()
            elif args.format == "csv":
                headers = ["pair", "common", "only_nv", "only_amd"]
                rows = [
                    [
                        f"{nv} vs {amd}",
                        (
                            " | ".join(common[: args.limit])
                            if args.limit
                            else " | ".join(common)
                        ),
                        (
                            " | ".join(only_nv[: args.limit])
                            if args.limit
                            else " | ".join(only_nv)
                        ),
                        (
                            " | ".join(only_amd[: args.limit])
                            if args.limit
                            else " | ".join(only_amd)
                        ),
                    ]
                ]
                print(to_csv(headers, rows))
            else:
                print("Common:")
                for t in common[: args.limit] if args.limit else common:
                    print(f"  - {t}")
                if args.limit and len(common) > args.limit:
                    print(f"  ... and {len(common) - args.limit} more")
                print("Only in NVIDIA:")
                for t in only_nv[: args.limit] if args.limit else only_nv:
                    print(f"  - {t}")
                if args.limit and len(only_nv) > args.limit:
                    print(f"  ... and {len(only_nv) - args.limit} more")
                print("Only in AMD:")
                for t in only_amd[: args.limit] if args.limit else only_amd:
                    print(f"  - {t}")
                if args.limit and len(only_amd) > args.limit:
                    print(f"  ... and {len(only_amd) - args.limit} more")
                print()


if __name__ == "__main__":
    main()
