# SGLang Benchmark CI

This repository contains a collection of benchmarking scripts designed for SGL performance testing of GROK and DeepSeek models. The benchmarks are split into two main modes: **offline** and **online**. Each mode is implemented in separate scripts, which can be run independently to evaluate different configurations, input lengths, and performance metrics.

The scripts have been updated to support command-line parameters, eliminating the need to manually edit scripts for different configurations. All paths, model names, and other settings can now be specified via command-line arguments.

---

## Table of Contents

- [Overview](#overview)
- [Supported Docker Images](#supported-docker-images)
- [Benchmark Modes](#benchmark-modes)
  - [Offline Mode](#offline-mode)
    - [grok_perf_offline_csv.sh](#grok_perf_offline_csvsh)
    - [deepseek_perf_offline_csv.sh](#deepseek_perf_offline_csvsh)
    - [Offline Data Processing](#offline-data-processing)
    - [Viewing Offline Plots](#viewing-offline-plots)
  - [Online Mode](#online-mode)
    - [grok_perf_online_csv.sh](#grok_perf_online_csvsh)
    - [Online Data Processing](#online-data-processing)
    - [Viewing Online Plots](#viewing-online-plots)
- [Automated Nightly Benchmarking](#automated-nightly-benchmarking)
    - [grok_perf_nightly.sh](#grok_perf_nightlysh)
- [Data Processing and Visualization](#data-processing-and-visualization)
  - [Processing Scripts](#processing-scripts)
  - [Plotting Scripts](#plotting-scripts)
- [Benchmark Comparison](#benchmark-comparison)
  - [compare_csv_results.py](#compare_csv_resultspy)
- [Requirements](#requirements)
- [Additional Notes](#additional-notes)
- [Cron Schedule](#cron-schedule)

---

## Overview

The SGL Benchmark CI repository is intended to evaluate the performance of GROK and DeepSeek models on various configurations and use cases. The scripts provided in this repository capture critical metrics such as latency and throughput for both offline and online benchmarking. Results are output as CSV files for easy analysis and archival.

---

## Supported Docker Images

The benchmark scripts support Docker images from multiple sources:

1. **ROCm SGLang Development Images:** Available at <https://hub.docker.com/r/rocm/sgl-dev>
   - Example: `rocm/sgl-dev:20250623` (nightly build)

2. **LMSYS SGLang Images:** Available at <https://hub.docker.com/r/lmsysorg/sglang/tags>
   - Example: `lmsysorg/sglang:v0.4.7-rocm630`

3. **Pre-built Images via Helper Script:**
   **Recommended approach:** Use the provided helper script to pull pre-built SGLang images from DockerHub:

   ```bash
   # Pull latest image (main branch)
   bash ./build_sglang_docker.sh

   # Pull specific version
   bash ./build_sglang_docker.sh --branch=v0.4.9
   ```

   **Important Limitations:**
   - This approach pulls pre-built images and will **NOT work for PRs that change aiter version**
   - Pre-built images are based on main/released versions, not specific PR changes
   - This is a workaround until aiter build becomes faster in the future

   **Manual Build Alternative:**
   If you need to build from source (e.g., for PRs with version changes), you can manually build:

   ```bash
   # Clone the sglang repository
   git clone https://github.com/sgl-project/sglang.git
   cd sglang

   # Build the Docker image (adjust ROCm version as needed)
   docker build -t my-sglang:custom-rocm630 -f docker/Dockerfile.rocm \
     --build-arg ROCM_VERSION=6.3.0 \
     --build-arg PYTHON_VERSION=3.10 .

   # Or build with specific commit/branch
   git checkout <specific-branch-or-commit>
   docker build -t my-sglang:dev-$(git rev-parse --short HEAD)-rocm630 \
     -f docker/Dockerfile.rocm .
   ```

   **Note:** When building from source in the future, remember to rebuild sgl_kernel inside the Docker image after PR checkout.

   Then use your image with the benchmark scripts:

   ```bash
   bash grok_perf_offline_csv.sh --docker_image=my-sglang:custom-rocm630
   bash grok_perf_online_csv.sh --docker_image=my-sglang:dev-abc1234-rocm630
   ```

**Important:** You must provide the full Docker image name including the registry/organization prefix.

---

## Benchmark Modes

### Offline Mode

Offline mode benchmarks are executed without real-time interaction, measuring model performance through batch processing. The following scripts are available for offline benchmarking:

#### grok_perf_offline_csv.sh

- **Purpose:** Benchmarks the GROK model with multiple test modes (normal, long_context, dummy).
- **Parameters:**
  - `--docker_image=IMAGE`: Docker image to use (default: lmsysorg/sglang:v0.4.7-rocm630)
  - `--mode=MODE`: Test mode - normal, long_context, or dummy (default: normal)
  - `--model=PATH`: Model path (configurable via environment variables)
  - `--tokenizer=NAME`: Tokenizer name (default: Xenova/grok-1-tokenizer)
  - `--dummy-model=PATH`: Dummy model path for dummy mode (configurable via environment variables)
  - `--work-dir=PATH`: Working directory (configurable via environment variables)
  - `--output-dir=PATH`: Output directory (default: same as work-dir)
  - `--help`: Show help message
- **Automatic Backend Selection:** The script automatically determines the attention backend based on the Docker image:
  - For `rocm/sgl-dev` images:
    - Dates >= 20250521 use `aiter` backend
    - Dates < 20250521 use `triton` backend
  - For `lmsysorg/sglang` images:
    - Versions >= v0.4.7 use `aiter` backend
    - Versions < v0.4.7 use `triton` backend
  - Other images default to `aiter` backend
- **Configuration by Mode:**
  - **Normal Mode:**
    - TP: 8, Batch Sizes: 1, 2, 4, 8, 16, 32, 64, 128, 256
    - Input Length: 1024, Output Length: 128
  - **Long Context Mode:**
    - TP: 8, Batch Size: 1
    - Input Lengths: 8192, 16384, 32768, Output Length: 10
  - **Dummy Mode:**
    - TP: 8, Batch Size: 2
    - Input Length: 256, Output Length: 4096
- **Metrics Captured:**
  - Prefill latency (s)
  - Median decode latency (s)
  - End-to-end (E2E) latency (s)
  - Prefill throughput (token/s)
  - Median decode throughput (token/s)
  - E2E throughput (token/s)
- **Output:**
  - A folder named with the current date and configuration information.
  - A CSV file containing a row for each benchmark run with backend information.
  - A `config.json` file with Docker image and backend details.
  - An optional `result.jsonl` file with detailed result data.
- **Usage:**

  ```bash
  # Basic usage with default parameters
  bash grok_perf_offline_csv.sh

  # Using specific Docker images
  bash grok_perf_offline_csv.sh --docker_image=rocm/sgl-dev:20250520  # Uses triton
  bash grok_perf_offline_csv.sh --docker_image=rocm/sgl-dev:20250521  # Uses aiter
  bash grok_perf_offline_csv.sh --docker_image=lmsysorg/sglang:v0.4.7-rocm630  # Uses aiter

  # Custom model and tokenizer
  bash grok_perf_offline_csv.sh \
    --model=$MODEL_PATH \
    --tokenizer=$TOKENIZER_NAME

  # Long context mode
  bash grok_perf_offline_csv.sh --mode=long_context

  # Dummy mode with custom model
  bash grok_perf_offline_csv.sh --mode=dummy --dummy-model=$DUMMY_MODEL_PATH

  # Custom directories
  bash grok_perf_offline_csv.sh \
    --work-dir=$WORK_DIR \
    --output-dir=$OUTPUT_DIR
  ```

#### Offline Data Processing

- **Purpose:** Process raw offline benchmark CSV files from multiple dates and consolidate them into a single summary CSV.
- **Script:** `process_offline_csv.py`
- **Functionality:**
  - Scans the last 30 days of benchmark results
  - Aggregates data from all batch sizes and dates
  - Extracts backend information from CSV files (if Backend column exists) or config.json
  - Handles both old format (without Backend column) and new format (with Backend column)
  - Creates a single summary CSV sorted by date, batch_size, and backend
  - Automatically cleans up old individual batch size CSV files
- **Output:** `GROK1_MOE-I4F8_offline_summary.csv` containing all benchmark data with backend information
- **Usage:**

  ```bash
  python3 process_offline_csv.py
  ```

#### Viewing Offline Plots

The `generate_offline_plots.py` script generates visualization plots from the consolidated summary CSV created by `process_offline_csv.py`.

- **Purpose:** Create visual representations of offline benchmark performance trends
- **Script:** `generate_offline_plots.py`
- **Functionality:**
  - Reads from the consolidated summary CSV
  - Displays backend information (aiter/triton) in plot titles when available
  - Generates three types of plots:
    - **Latency vs Date:** Shows E2E latency trends for each batch size with backend info
    - **Throughput vs Date:** Shows E2E throughput trends for each batch size with backend info
    - **Combined Metrics:** Shows both latency and throughput trends on a single plot
- **Output:** PNG files saved to the configured plots directory
- **Usage:**

  ```bash
  cd $WORK_DIR
  python3 generate_offline_plots.py
  ```

To view these plots via a web browser, use the provided plot server:

1. **Start the server:**

   ```bash
   bash $WORK_DIR/plots_server.sh
   ```

   This serves files from the plots directory on port 8000.

2. **Access the plots:**
   Open a web browser and navigate to `http://<server-ip>:8000/`
   Navigate to the `GROK1/offline/` directory to view the plots.

#### deepseek_perf_offline_csv.sh

- **Purpose:** Benchmarks the DeepSeek V3 model with FP8 quantization.
- **Parameters:**
  - `--docker_image=IMAGE`: Docker image to use (default: rocm/sgl-dev:20250430)
  - `--model=PATH`: Model path (configurable via environment variables)
  - `--model-name=NAME`: Model name for output files (default: DeepSeek-V3-0324)
  - `--hf-model-id=ID`: HuggingFace model ID for download (default: deepseek-ai/DeepSeek-V3-0324)
  - `--work-dir=PATH`: Working directory (configurable via environment variables)
  - `--output-dir=PATH`: Output directory (default: same as work-dir)
  - `--gsm8k-script=PATH`: Path to GSM8K benchmark script (configurable via environment variables)
  - `--threshold=VALUE`: GSM8K accuracy threshold (default: 0.93)
  - `--download-model`: Download model if not present (default: false)
  - `--help`: Show help message
- **Configuration:**
  - **TP:** Fixed at 8.
  - **Batch Size:** Fixed at 32.
  - **Input / Output Lengths:** Input length (IL) set to 128 and output length (OL) set to 32.
  - **GSM8K Warm-up:** Performs GSM8K accuracy testing before benchmarking.
- **Metrics Captured:**
  - Same as grok_perf_offline_csv.sh (latency and throughput metrics)
- **Output:**
  - A folder named with the date and model configuration.
  - A CSV file with benchmark results.
  - Log files for server output and GSM8K testing.
- **Usage:**

  ```bash
  # Basic usage
  bash deepseek_perf_offline_csv.sh

  # Custom model configuration
  bash deepseek_perf_offline_csv.sh \
    --model=$MODEL_PATH \
    --model-name=DeepSeek-V3-Custom \
    --hf-model-id=deepseek-ai/DeepSeek-V3

  # Download model if not present
  bash deepseek_perf_offline_csv.sh \
    --model=$MODEL_PATH \
    --hf-model-id=deepseek-ai/DeepSeek-V3 \
    --download-model

  # Custom paths and threshold
  bash deepseek_perf_offline_csv.sh \
    --work-dir=$WORK_DIR \
    --output-dir=$OUTPUT_DIR \
    --gsm8k-script=$GSM8K_SCRIPT_PATH \
    --threshold=0.95
  ```

### Online Mode

Online mode benchmarks measure the real-time serving performance of GROK1. This mode is designed to simulate interactive use and assess latency under different request rates.

#### grok_perf_online_csv.sh

- **Purpose:** Benchmarks the online serving performance, capturing both server startup and response latencies.
- **Parameters:**
  - `--docker_image=IMAGE`: Docker image to use (default: lmsysorg/sglang:v0.4.7-rocm630)
  - `--model=PATH`: Model path (configurable via environment variables)
  - `--tokenizer=NAME`: Tokenizer name (default: Xenova/grok-1-tokenizer)
  - `--work-dir=PATH`: Working directory (configurable via environment variables)
  - `--output-dir=PATH`: Output directory (default: same as work-dir)
  - `--gsm8k-script=PATH`: Path to GSM8K benchmark script (configurable via environment variables)
  - `--node=NAME`: Node name for reporting (configurable via environment variables)
  - `--threshold=VALUE`: GSM8K accuracy threshold (default: 0.8)
  - `--help`: Show help message
- **Workflow:**
  1. **Container Management:**
     - Detects whether the script is running inside a container.
     - If executed outside a container and Docker is available, the script manages the container lifecycle: checks for an existing container, starts one if necessary, or pulls a new image and then re-invokes itself inside the container.
  2. **Run Folder Setup:**
     - Creates a folder named
       `<TAG>_GROK1_MOE-I4F8_online`.
  3. **Server Launch & Client Benchmark:**
     - **Automatic Backend Selection:** The script automatically determines the attention backend based on the Docker image:
       - For `rocm/sgl-dev` images:
         - Dates >= 20250521 use `aiter` backend
         - Dates < 20250521 use `triton` backend
       - For `lmsysorg/sglang` images:
         - Versions >= v0.4.7 use `aiter` backend
         - Versions < v0.4.7 use `triton` backend
       - Other images default to `aiter` backend
     - Sets appropriate environment variables based on image version:
       - For SGLang v0.4.7+: `SGLANG_USE_AITER=1` (when using aiter)
       - For SGLang v0.4.6 and earlier: `SGLANG_AITER_MOE=1` (when using aiter)
       - Always sets: `SGLANG_INT4_WEIGHT=1 SGLANG_MOE_PADDING=0`
     - Runs client benchmarks at multiple request rates.
     - Captures logs and parses median end-to-end latency (E2E), time-to-first-token (TTFT), and inter-token latency (ITL).
  4. **Results Aggregation:**
     - Aggregates metrics and compares them with reference H100 values.
     - Generates a CSV summary with dynamic backend labeling (e.g., MI300x-aiter or MI300x-triton).
- **Output:**
  A run folder containing:
  - Server logs
  - Client logs
  - A CSV summary of online benchmark metrics with backend-specific labels (MI300x-aiter or MI300x-triton)
- **Usage:**

  ```bash
  # Basic usage
  bash grok_perf_online_csv.sh

  # Custom configuration
  bash grok_perf_online_csv.sh \
    --docker_image=lmsysorg/sglang:v0.4.7-rocm630 \
    --model=$MODEL_PATH \
    --tokenizer=$TOKENIZER_NAME \
    --node=$NODE_NAME \
    --threshold=0.85

  # Custom paths
  bash grok_perf_online_csv.sh \
    --work-dir=$WORK_DIR \
    --output-dir=$OUTPUT_DIR \
    --gsm8k-script=$GSM8K_SCRIPT_PATH
  ```

#### Online Data Processing

- **Purpose:** Process raw online benchmark CSV files from multiple dates and consolidate them into a single summary CSV.
- **Script:** `process_online_csv.py`
- **Functionality:**
  - Scans the last 30 days of benchmark results
  - Extracts metrics from three sections: E2E Latency, TTFT, and ITL
  - Dynamically extracts backend modes (aiter, triton) from CSV row labels
  - Parses KV cache information from server log files
  - Key features:
    - Compiled regex patterns for efficient log parsing
    - Handles multiple backend modes dynamically
    - Supports both legacy (e.g., MI300x-aiter (prefill+decode)) and new formats (e.g., MI300x-aiter, MI300x-triton)
    - Robust parsing with fallbacks for missing data
  - Creates a single summary CSV sorted by date, mode, and request_rate
- **Output:** `GROK1_MOE-I4F8_online_summary.csv` containing all benchmark data
- **Usage:**

  ```bash
  cd $WORK_DIR
  python3 process_online_csv.py
  ```

#### Viewing Online Plots

The `generate_online_plots.py` script generates visualization plots from the consolidated summary CSV created by `process_online_csv.py`.

- **Purpose:** Create visual representations of online benchmark performance trends
- **Script:** `generate_online_plots.py`
- **Functionality:**
  - Reads from the consolidated summary CSV
  - Generates a comprehensive plot with 5 subplots:
    - **E2E Latency:** Shows trends for different request rates and backend modes (aiter, triton)
    - **TTFT (Time to First Token):** First token generation latency across modes
    - **ITL (Inter-Token Latency):** Latency between tokens for each backend
    - **Number of Tokens:** KV cache allocation at server startup per backend mode
    - **KV Cache Usage:** Memory usage in GB (bar chart) for each backend
  - Automatically handles multiple backend modes in the data
  - Differentiates modes with distinct colors and labels in plots
- **Output:** PNG file saved to the configured plots directory
- **Usage:**

  ```bash
  cd $WORK_DIR
  python3 generate_online_plots.py
  ```

To view the plots, use the same plot server as for offline plots.

---

## Automated Nightly Benchmarking

The `grok_perf_nightly.sh` script provides automated orchestration for running nightly benchmarks with the latest Docker images. This script is designed to be run via cron jobs and handles the complete workflow from Docker image management to benchmark execution and data processing.

### grok_perf_nightly.sh

- **Purpose:** Automated nightly benchmark orchestration that pulls the latest `rocm/sgl-dev` Docker image and runs benchmarks with proper resource management.
- **Key Features:**
  - **Automatic Image Selection:** Pulls the latest `rocm/sgl-dev` image for the current PST date (with fallback to previous day)
  - **GPU Resource Management:** Checks GPU idle status and stops running containers if needed
  - **Container Management:** Creates/manages persistent containers with proper mounts and configuration
  - **Comprehensive Workflow:** Runs benchmarks, processes CSV data, and generates plots automatically
  - **Flexible Mode Selection:** Supports running offline-only, online-only, or both benchmarks
- **Parameters:**
  - `--mode=MODE`: Specify which benchmarks to run:
    - `offline`: Run offline benchmarks only
    - `online`: Run online benchmarks only
    - `all`: Run both offline and online benchmarks (default)
- **Environment Variables:** All configuration can be customized via environment variables:
  - `BENCHMARK_CI_DIR`: Base directory for benchmark scripts (default: `/mnt/raid/michael/sgl_benchmark_ci`)
  - `MOUNT_DIR`: Directory to mount in container (default: `/mnt/raid/`)
  - `WORK_DIR`: Working directory inside container (default: `/sgl-workspace`)
  - `IMAGE_REPO`: Docker image repository (default: `rocm/sgl-dev`)
  - `GPU_USAGE_THRESHOLD`: GPU usage threshold for idle check (default: 20%)
  - `VRAM_USAGE_THRESHOLD`: VRAM usage threshold for idle check (default: 20%)
  - `TIME_ZONE`: Timezone for date calculations (default: `America/Los_Angeles`)
- **Workflow:**
  1. **GPU Idle Check:** Ensures GPU is not busy; stops running containers if needed
  2. **Image Management:** Pulls latest `rocm/sgl-dev:YYYYMMDD` image for current PST date
  3. **Container Setup:** Creates/starts container with proper mounts and privileges
  4. **Benchmark Execution:** Runs selected benchmark scripts inside container
  5. **Data Processing:** Automatically processes CSV data and generates plots
  6. **Logging:** Saves processing logs to benchmark output folders
- **Output:**
  - Benchmark results in standard output folders
  - Processing logs: `process_offline_csv.log`, `generate_offline_plots.log` (for offline mode)
  - Processing logs: `process_online_csv.log`, `generate_online_plots.log` (for online mode)
- **Usage:**

  ```bash
  # Run both offline and online benchmarks (default)
  bash grok_perf_nightly.sh

  # Run offline benchmarks only
  bash grok_perf_nightly.sh --mode=offline

  # Run online benchmarks only
  bash grok_perf_nightly.sh --mode=online

  # Run all benchmarks (explicit)
  bash grok_perf_nightly.sh --mode=all
  ```

**Note:** This script is designed for automated execution via cron jobs and handles all aspects of the benchmarking pipeline, making it ideal for unattended nightly performance monitoring.

---

## Data Processing and Visualization

The benchmark CI includes automated data processing and visualization scripts that consolidate results from multiple benchmark runs and generate performance trend plots.

### Processing Scripts

These scripts process raw benchmark outputs and create consolidated summary CSV files:

- **process_offline_csv.py**
  - Processes offline benchmark results from dated folders
  - Consolidates data from all batch sizes into a single summary CSV
  - Handles multiple date formats (YYYYMMDD and YYYYMMDDrc)
  - Automatically cleans up old per-batch CSV files
  - Key features:
    - Robust error handling for missing or corrupted files
    - Aggregates using mean values when multiple data points exist
    - Maintains ILEN/OLEN configuration data

- **process_online_csv.py**
  - Processes online benchmark results with complex multi-table CSV format
  - Extracts metrics from three sections: E2E Latency, TTFT, and ITL
  - Dynamically extracts backend modes (aiter, triton) from CSV row labels
  - Parses KV cache information from server log files
  - Key features:
    - Compiled regex patterns for efficient log parsing
    - Handles multiple backend modes dynamically
    - Supports both legacy (e.g., MI300x-aiter (prefill+decode)) and new formats (e.g., MI300x-aiter, MI300x-triton)
    - Robust parsing with fallbacks for missing data

### Plotting Scripts

These scripts generate visualization plots from the consolidated summary CSVs:

- **generate_offline_plots.py**
  - Creates three types of visualizations:
    - Individual subplots for each batch size showing latency trends
    - Individual subplots for each batch size showing throughput trends
    - Combined plot showing all batch sizes on single latency/throughput charts
  - Features:
    - Automatic date formatting on x-axis
    - Value annotations on data points
    - Handles missing data gracefully

- **generate_online_plots.py**
  - Creates a comprehensive multi-subplot figure showing:
    - E2E Latency trends for different request rates and backend modes (aiter, triton)
    - TTFT (Time to First Token) performance
    - ITL (Inter-Token Latency) metrics
    - Number of tokens allocated for KV cache
    - KV Cache memory usage (as bar chart)
  - Features:
    - Intelligent annotation overlap detection
    - Separate handling for performance metrics vs. resource metrics
    - Explanatory notes for technical metrics

### Plot Server

A simple HTTP server is provided to view generated plots:

```bash
# Start the plot server
bash plots_server.sh

# Access plots at http://<server-ip>:8000/
# Navigate to GROK1/offline/ or GROK1/online/ directories
```

The server uses `custom_http_server.py` to serve files with proper directory listings.

---

## Benchmark Comparison

The benchmark CI includes a powerful comparison tool that allows you to compare CSV results between different benchmark runs, automatically extracting GSM8K accuracy information and generating detailed performance comparison reports.

### compare_csv_results.py

**Purpose:** Compare SGLang benchmark CSV results from different runs and generate comprehensive markdown reports with performance analysis.

**Key Features:**
- **Automatic Mode Detection:** Detects whether CSV files are from offline or online benchmarks
- **GSM8K Accuracy Extraction:** Automatically extracts GSM8K accuracy from associated log files
- **Performance Threshold Analysis:** Configurable thresholds for highlighting significant changes
- **Flexible Output:** Generates timestamped comparison folders with markdown reports
- **Multi-Model Support:** Handles different model comparisons (GROK1, DeepSeek-V3, etc.)

**Parameters:**
- `--csv1`: Path to first CSV directory (required)
- `--csv2`: Path to second CSV directory (required)
- `--mode`: Benchmark mode (`offline`, `online`, or `auto` for auto-detection)
- `--model`: Model name to filter CSV files (for same model comparisons)
- `--model1`: Model name for first directory (overrides `--model`)
- `--model2`: Model name for second directory (overrides `--model`)
- `--output-md`: Custom path for output markdown file (optional)
- `--output-dir`: Output directory (default: `/mnt/raid/michael/sgl_benchmark_ci/comparison_results`)
- `--append`: Append to existing file instead of overwriting
- `--gsm8k-threshold`: GSM8K accuracy threshold for significance detection (default: 0.001)
- `--performance-threshold`: Performance change threshold for highlighting (default: 5.0%)

**Configuration via Environment Variables:**
- `COMPARISON_OUTPUT_DIR`: Default output directory
- `GSM8K_ACCURACY_THRESHOLD`: Default GSM8K accuracy threshold
- `PERFORMANCE_THRESHOLD`: Default performance improvement threshold
- `GSM8K_LOG_PATTERNS`: Patterns for GSM8K log files (semicolon-separated)

**Offline Mode Comparison:**
- Compares E2E throughput and latency across different batch sizes
- Automatically merges results on common configurations (TP, batch_size, IL, OL)
- Generates performance change percentages with color-coded improvements/regressions
- Includes comprehensive GSM8K accuracy comparison

**Online Mode Comparison:**
- Compares E2E Latency, TTFT, and ITL metrics across different request rates
- Focuses on MI300x performance data
- Calculates percentage improvements for latency metrics (lower is better)
- Provides detailed breakdown by request rate and metric type

**Output Structure:**
- Creates timestamped folders: `{date}_{csv1_dirname}_vs_{csv2_dirname}/`
- Contains markdown report with same name as folder
- Includes performance tables with color-coded change indicators:
  - 🟢 Green: Improvements above threshold
  - 🔴 Red: Regressions above threshold
  - Standard: Changes within threshold range

**Usage Examples:**

```bash
# Compare offline GROK1 results
python3 compare_csv_results.py \
  --csv1 offline/GROK1/20250624_GROK1_MOE-I4F8_offline \
  --csv2 offline/GROK1/20250626_GROK1_MOE-I4F8_offline \
  --mode offline --model grok1

# Compare online GROK1 results
python3 compare_csv_results.py \
  --csv1 online/GROK1/20250624_GROK1_MOE-I4F8_online \
  --csv2 online/GROK1/20250626_GROK1_MOE-I4F8_online \
  --mode online --model grok1

# Compare DeepSeek-V3 offline results
python3 compare_csv_results.py \
  --csv1 offline/DeepSeek-V3-0324/20250515_DeepSeek-V3-0324_FP8_offline \
  --csv2 offline/DeepSeek-V3-0324/20250516_DeepSeek-V3-0324_FP8_offline \
  --mode offline --model DeepSeek-V3-0324
```

**Sample Output:**
The generated markdown reports include:
- Header with comparison details and generation timestamp
- GSM8K accuracy comparison with significance testing
- Performance comparison tables organized by batch size/request rate
- Color-coded change indicators for easy identification of improvements/regressions
- Detailed metrics for E2E latency, throughput, TTFT, and ITL

---

## Requirements

- **Operating System:** Unix-like shell environment.
- **Dependencies:**
  - **Bash:** Scripts are written in Bash.
  - **Python3:** Required to execute benchmark modules (e.g., `sglang.bench_one_batch`, `sglang.launch_server`, etc.).
  - **Docker:** Required for running the online benchmark if executed outside a container.
  - **Model Files:** Ensure the model and tokenizer files are available at the paths specified or use the command-line parameters to specify custom paths.
- **Additional:** The SGL benchmark Python package should be installed and configured in your environment.

---

## Additional Notes

- **Output Organization:**
  All scripts automatically create output folders named with the current date and a description of the benchmark run. Output location can be customized using the `--output-dir` parameter.
- **Script Customization:**
  Use command-line parameters to specify model paths, tokenizer paths, or benchmark parameters instead of modifying scripts directly. Run any script with `--help` to see available options.
- **Resource Management:**
  Ensure that no other processes are consuming critical GPU resources to avoid memory capacity errors.

---

## Cron Schedule

The benchmarks are scheduled to run daily via cron jobs. The schedule is defined in `crontab_rules.txt`.

- **Online and Offline Benchmark:** Runs daily at 12 AM PT.

To apply these rules, run: `crontab $WORK_DIR/crontab_rules.txt`
