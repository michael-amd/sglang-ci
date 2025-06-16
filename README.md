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
- [Data Processing and Visualization](#data-processing-and-visualization)
  - [Processing Scripts](#processing-scripts)
  - [Plotting Scripts](#plotting-scripts)
- [Requirements](#requirements)
- [Additional Notes](#additional-notes)
- [Cron Schedule](#cron-schedule)

---

## Overview

The SGL Benchmark CI repository is intended to evaluate the performance of GROK and DeepSeek models on various configurations and use cases. The scripts provided in this repository capture critical metrics such as latency and throughput for both offline and online benchmarking. Results are output as CSV files for easy analysis and archival.

---

## Supported Docker Images

The benchmark scripts support Docker images from multiple sources:

1. **ROCm SGLang Development Images:** Available at https://hub.docker.com/r/rocm/sgl-dev
   - Example: `rocm/sgl-dev:20250331rc` (release candidate)
   - Example: `rocm/sgl-dev:20250429` (nightly build)

2. **LMSYS SGLang Images:** Available at https://hub.docker.com/r/lmsysorg/sglang/tags
   - Example: `lmsysorg/sglang:v0.4.6.post3-rocm630`
   - Example: `lmsysorg/sglang:v0.4.7-rocm630`

3. **Custom Built Images from SGLang Source:**
   You can also build your own Docker images from the [sglang upstream source](https://github.com/sgl-project/sglang):
   
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
   
   **Alternative: Use the provided build helper script:**
   ```bash
   # Build from main branch (default)
   bash michael/sgl_benchmark_ci/build_sglang_docker.sh
   
   # Build from specific branch/tag/commit
   bash michael/sgl_benchmark_ci/build_sglang_docker.sh --branch=v0.4.7
   
   # Build from fork
   bash michael/sgl_benchmark_ci/build_sglang_docker.sh \
     --repo=https://github.com/yourusername/sglang.git \
     --branch=your-feature-branch
   ```
   
   Then use your custom image with the benchmark scripts:
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
  - `--docker_image=IMAGE`: Docker image to use (default: rocm/sgl-dev:20250331rc)
  - `--mode=MODE`: Test mode - normal, long_context, or dummy (default: normal)
  - `--model=PATH`: Model path (default: /mnt/raid/models/huggingface/amd--grok-1-W4A8KV8/)
  - `--tokenizer=NAME`: Tokenizer name (default: Xenova/grok-1-tokenizer)
  - `--dummy-model=PATH`: Dummy model path for dummy mode (default: /mnt/raid/models/dummy_prod1/)
  - `--work-dir=PATH`: Working directory (default: /mnt/raid/michael/sgl_benchmark_ci)
  - `--output-dir=PATH`: Output directory (default: same as work-dir)
  - `--help`: Show help message
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
  - A CSV file containing a row for each benchmark run.
  - An optional `result.jsonl` file with detailed result data.
- **Usage:**  
  ```bash
  # Basic usage with default parameters
  bash grok_perf_offline_csv.sh
  
  # Using specific Docker images
  bash grok_perf_offline_csv.sh --docker_image=rocm/sgl-dev:20250331rc
  bash grok_perf_offline_csv.sh --docker_image=lmsysorg/sglang:v0.4.7-rocm630
  
  # Custom model and tokenizer
  bash grok_perf_offline_csv.sh \
    --model=/path/to/your/grok/model \
    --tokenizer=your-tokenizer-name
  
  # Long context mode
  bash grok_perf_offline_csv.sh --mode=long_context
  
  # Dummy mode with custom model
  bash grok_perf_offline_csv.sh --mode=dummy --dummy-model=/path/to/dummy/model
  
  # Custom directories
  bash grok_perf_offline_csv.sh \
    --work-dir=/your/work/directory \
    --output-dir=/your/output/directory
  ```

#### Offline Data Processing
- **Purpose:** Process raw offline benchmark CSV files from multiple dates and consolidate them into a single summary CSV.
- **Script:** `process_offline_csv.py`
- **Functionality:**
  - Scans the last 30 days of benchmark results
  - Aggregates data from all batch sizes and dates
  - Creates a single summary CSV sorted by date and batch_size
  - Automatically cleans up old individual batch size CSV files
- **Output:** `GROK1_MOE-I4F8_offline_summary.csv` containing all benchmark data
- **Usage:**
  ```bash
  cd /mnt/raid/michael/sgl_benchmark_ci
  python3 process_offline_csv.py
  ```

#### Viewing Offline Plots

The `generate_offline_plots.py` script generates visualization plots from the consolidated summary CSV created by `process_offline_csv.py`.

- **Purpose:** Create visual representations of offline benchmark performance trends
- **Script:** `generate_offline_plots.py`
- **Functionality:**
  - Reads from the consolidated summary CSV
  - Generates three types of plots:
    - **Latency vs Date:** Shows E2E latency trends for each batch size
    - **Throughput vs Date:** Shows E2E throughput trends for each batch size
    - **Combined Metrics:** Shows both latency and throughput trends on a single plot
- **Output:** PNG files saved to `/mnt/raid/michael/sgl_benchmark_ci/plots_server/GROK1/offline/`
- **Usage:**
  ```bash
  cd /mnt/raid/michael/sgl_benchmark_ci
  python3 generate_offline_plots.py
  ```

To view these plots via a web browser, use the provided plot server:

1. **Start the server:**
   ```bash
   bash /mnt/raid/michael/sgl_benchmark_ci/plots_server.sh
   ```
   This serves files from the plots directory on port 8000.

2. **Access the plots:**
   Open a web browser and navigate to `http://<server-ip>:8000/`
   Navigate to the `GROK1/offline/` directory to view the plots.

#### deepseek_perf_offline_csv.sh
- **Purpose:** Benchmarks the DeepSeek V3 model with FP8 quantization.
- **Parameters:**
  - `--docker_image=IMAGE`: Docker image to use (default: rocm/sgl-dev:20250430)
  - `--model=PATH`: Model path (default: /mnt/raid/models/huggingface/deepseek-ai/DeepSeek-V3-0324)
  - `--model-name=NAME`: Model name for output files (default: DeepSeek-V3-0324)
  - `--hf-model-id=ID`: HuggingFace model ID for download (default: deepseek-ai/DeepSeek-V3-0324)
  - `--work-dir=PATH`: Working directory (default: /mnt/raid/michael/sgl_benchmark_ci)
  - `--output-dir=PATH`: Output directory (default: same as work-dir)
  - `--gsm8k-script=PATH`: Path to GSM8K benchmark script (default: /mnt/raid/michael/sglang/benchmark/gsm8k/bench_sglang.py)
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
    --model=/path/to/deepseek/model \
    --model-name=DeepSeek-V3-Custom \
    --hf-model-id=deepseek-ai/DeepSeek-V3
  
  # Download model if not present
  bash deepseek_perf_offline_csv.sh \
    --model=/new/path/for/model \
    --hf-model-id=deepseek-ai/DeepSeek-V3 \
    --download-model
  
  # Custom paths and threshold
  bash deepseek_perf_offline_csv.sh \
    --work-dir=/your/work/directory \
    --output-dir=/your/output/directory \
    --gsm8k-script=/path/to/gsm8k/bench_sglang.py \
    --threshold=0.95
  ```

### Online Mode

Online mode benchmarks measure the real-time serving performance of GROK1. This mode is designed to simulate interactive use and assess latency under different request rates.

#### grok_perf_online_csv.sh
- **Purpose:** Benchmarks the online serving performance, capturing both server startup and response latencies.
- **Parameters:**
  - `--docker_image=IMAGE`: Docker image to use (default: rocm/sgl-dev:20250331rc)
  - `--model=PATH`: Model path (default: /mnt/raid/models/huggingface/amd--grok-1-W4A8KV8/)
  - `--tokenizer=NAME`: Tokenizer name (default: Xenova/grok-1-tokenizer)
  - `--work-dir=PATH`: Working directory (default: /mnt/raid/michael/sgl_benchmark_ci)
  - `--output-dir=PATH`: Output directory (default: same as work-dir)
  - `--gsm8k-script=PATH`: Path to GSM8K benchmark script (default: /mnt/raid/michael/sglang/benchmark/gsm8k/bench_sglang.py)
  - `--node=NAME`: Node name for reporting (default: dell300x-pla-t10-23)
  - `--threshold=VALUE`: GSM8K accuracy threshold (default: 0.8)
  - `--help`: Show help message
- **Workflow:**
  1. **Container Management:**  
     - Detects whether the script is running inside a container.  
     - If executed outside a container and Docker is available, the script manages the container lifecycle: checks for an existing container, starts one if necessary, or pulls a new image and then re-invokes itself inside the container.
  2. **Run Folder Setup:**  
     - Creates a folder named  
       `YYYYMMDD_<TAG>_GROK1_MOE-I4F8_online`.  
  3. **Server Launch & Client Benchmark:**  
     - **Image selection:** pass `--docker_image=<image[:tag]>`.  
       - If the tag **ends with `rc`**, the script keeps the original **AITer** back-ends (`aiter` and `aiter_decode`).  
       - Otherwise it launches a single **Triton** back-end with environment variables:
         - For SGLang v0.4.7+: `SGLANG_USE_AITER=1 SGLANG_INT4_WEIGHT=1 SGLANG_MOE_PADDING=0`
         - For SGLang v0.4.6 and earlier: `SGLANG_AITER_MOE=1 SGLANG_INT4_WEIGHT=1 SGLANG_MOE_PADDING=0`
     - Runs client benchmarks at multiple request rates.  
     - Captures logs and parses median end-to-end latency (E2E), time-to-first-token (TTFT), and inter-token latency (ITL).
  4. **Results Aggregation:**  
     - Aggregates metrics and compares them with reference H100 values.  
     - Generates a CSV summary including metric ratios.
- **Output:**  
  A run folder containing:  
  - Server logs  
  - Client logs  
  - A CSV summary of online benchmark metrics  
- **Usage:**  
  ```bash
  # Basic usage
  bash grok_perf_online_csv.sh
  
  # Custom configuration
  bash grok_perf_online_csv.sh \
    --docker_image=lmsysorg/sglang:v0.4.7 \
    --model=/path/to/your/grok/model \
    --tokenizer=your-tokenizer-name \
    --node=your-node-name \
    --threshold=0.85
  
  # Custom paths
  bash grok_perf_online_csv.sh \
    --work-dir=/your/work/directory \
    --output-dir=/your/output/directory \
    --gsm8k-script=/path/to/gsm8k/bench_sglang.py
  ```

#### Online Data Processing
- **Purpose:** Process raw online benchmark CSV files from multiple dates and consolidate them into a single summary CSV.
- **Script:** `process_online_csv.py`
- **Functionality:**
  - Scans the last 30 days of benchmark results
  - Parses multiple metric tables (E2E Latency, TTFT, ITL) from each CSV
  - Extracts KV cache information from server log files
  - Creates a single summary CSV sorted by date, mode, and request_rate
- **Output:** `GROK1_MOE-I4F8_online_summary.csv` containing all benchmark data
- **Usage:**
  ```bash
  cd /mnt/raid/michael/sgl_benchmark_ci
  python3 process_online_csv.py
  ```

#### Viewing Online Plots

The `generate_online_plots.py` script generates visualization plots from the consolidated summary CSV created by `process_online_csv.py`.

- **Purpose:** Create visual representations of online benchmark performance trends
- **Script:** `generate_online_plots.py`
- **Functionality:**
  - Reads from the consolidated summary CSV
  - Generates a comprehensive plot with 5 subplots:
    - **E2E Latency:** Shows trends for different request rates and modes
    - **TTFT (Time to First Token):** First token generation latency
    - **ITL (Inter-Token Latency):** Latency between tokens
    - **Number of Tokens:** KV cache allocation at server startup
    - **KV Cache Usage:** Memory usage in GB (bar chart)
- **Output:** PNG file saved to `/mnt/raid/michael/sgl_benchmark_ci/plots_server/GROK1/online/`
- **Usage:**
  ```bash
  cd /mnt/raid/michael/sgl_benchmark_ci
  python3 generate_online_plots.py
  ```

To view the plots, use the same plot server as for offline plots.

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
  - Parses KV cache information from server log files
  - Key features:
    - Compiled regex patterns for efficient log parsing
    - Handles both 'aiter' and 'aiter_decode' modes
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
    - E2E Latency trends for different request rates and modes
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
bash /mnt/raid/michael/sgl_benchmark_ci/plots_server.sh

# Access plots at http://<server-ip>:8000/
# Navigate to GROK1/offline/ or GROK1/online/ directories
```

The server uses `custom_http_server.py` to serve files with proper directory listings.

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

- **Offline Benchmark:** Runs daily at 7 PM PT.
- **Online Benchmark:** Runs daily at 8 PM PT.

To apply these rules, run: `crontab /mnt/raid/michael/sgl_benchmark_ci/crontab_rules.txt`

