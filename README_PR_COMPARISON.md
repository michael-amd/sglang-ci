# PR Performance Comparison Tool

This tool compares the performance impact of a pull request (PR) against the SGLang main branch by running benchmarks on both versions.

## Features

- Automatically builds Docker images for both main branch and PR
- Runs offline and online benchmarks for Grok-1 and DeepSeek-V3
- Generates detailed comparison reports with performance metrics
- Highlights significant performance improvements or regressions

## Prerequisites

- Docker installed and running
- Access to the benchmark models (Grok-1 and DeepSeek-V3)
- Sufficient GPU resources (8x MI300X for full benchmarks)
- Python 3.8+ with pandas installed

## Installation

```bash
# Install Python dependencies
pip install pandas numpy

# Make scripts executable (already done)
chmod +x compare_pr_performance.sh compare_csv_results.py
```

## Usage

### Basic Usage

Compare a PR against the main branch:

```bash
./compare_pr_performance.sh --pr=1234
```

### Advanced Options

```bash
# Specify a different repository
./compare_pr_performance.sh --pr=1234 --repo=your-fork/sglang

# Run only specific models
./compare_pr_performance.sh --pr=1234 --models=grok
./compare_pr_performance.sh --pr=1234 --models=deepseek
./compare_pr_performance.sh --pr=1234 --models=grok,deepseek

# Run only specific benchmark types
./compare_pr_performance.sh --pr=6838 --benchmark-types=offline
./compare_pr_performance.sh --pr=1234 --benchmark-types=online
./compare_pr_performance.sh --pr=1234 --benchmark-types=offline,online

# Use a different base image for building
./compare_pr_performance.sh --pr=1234 --base-image=rocm/sgl-dev:vllm20250114

# Skip building and use existing images
./compare_pr_performance.sh --pr=1234 --skip-build \
    --main-image=main-abc123-rocm630 \
    --pr-image=pull-1234-merge-def456-rocm630

# Specify custom output directory
./compare_pr_performance.sh --pr=1234 --output-dir=/path/to/results
```

## Output

The tool creates a timestamped directory with all results:

```
comparison_results/
└── pr1234_20250120_143022/
    ├── comparison.log          # Main log file
    ├── comparison_report.md    # Markdown comparison report
    ├── main/                   # Results from main branch
    │   ├── offline/
    │   │   ├── GROK1/
    │   │   └── DeepSeek-V3-0324/
    │   └── online/
    │       └── GROK1/
    └── pr/                     # Results from PR
        ├── offline/
        │   ├── GROK1/
        │   └── DeepSeek-V3-0324/
        └── online/
            └── GROK1/
```

## Comparison Report

The comparison report (`comparison_report.md`) includes:

- **Summary**: Overview of the PR being tested
- **Performance Tables**: Side-by-side comparison of metrics
- **Change Indicators**:
  - 🟢 Performance improvement > 5%
  - 🔴 Performance regression > 5%
  - Small changes (±5%) shown without indicators

### Example Report Section

```markdown
### Grok Offline Benchmark

| Configuration | Metric | Main | PR | Change |
|---------------|--------|------|----|---------|
| TP=8, BS=32, IL=1024, OL=128 | E2E Throughput | 1032.13 | 1085.74 | **+5.2%** 🟢 |
| TP=8, BS=32, IL=1024, OL=128 | E2E Latency | 0.132 | 0.125 | **+5.6%** 🟢 |
```

## Integration with CI/CD

You can integrate this tool into your CI/CD pipeline:

```yaml
# Example GitHub Actions workflow
name: PR Performance Test
on:
  pull_request:
    types: [opened, synchronize]

jobs:
  benchmark:
    runs-on: [self-hosted, gpu]
    steps:
      - uses: actions/checkout@v3
      - name: Run performance comparison
        run: |
          ./compare_pr_performance.sh --pr=${{ github.event.pull_request.number }}
      - name: Upload results
        uses: actions/upload-artifact@v3
        with:
          name: benchmark-results
          path: comparison_results/
```

## Troubleshooting

### Common Issues

1. **Docker build fails**: Check that the PR branch exists and is accessible
2. **Benchmarks fail**: Ensure models are available at the expected paths
3. **Out of memory**: Reduce batch sizes or run fewer concurrent benchmarks
4. **CSV comparison fails**: Check that benchmark runs completed successfully

### Debug Mode

For detailed debugging, check the log files:

```bash
# Main comparison log
tail -f comparison_results/pr1234_*/comparison.log

# Individual benchmark logs
tail -f comparison_results/pr1234_*/main/grok_offline.log
tail -f comparison_results/pr1234_*/pr/grok_offline.log
```

## Performance Tips

- Run benchmarks on dedicated hardware to avoid interference
- Ensure consistent GPU states between runs
- Consider running multiple iterations for more reliable results
- Use `--skip-gsm8k=true` for faster online benchmarks during development
