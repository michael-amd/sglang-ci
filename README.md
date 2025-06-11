# SGLang Benchmark CI

This repository contains a collection of benchmarking scripts designed for SGL performance testing of GROK models. The benchmarks are split into two main modes: **offline** and **online**. Each mode is implemented in separate scripts, which can be run independently to evaluate different configurations, input lengths, and performance metrics.

---

## Table of Contents

- [Overview](#overview)
- [Supported Docker Images](#supported-docker-images)
- [Benchmark Modes](#benchmark-modes)
  - [Offline Mode](#offline-mode)
    - [grok_perf_offline_csv.sh](#grok_perf_offline_csvsh)
    - [grok_perf_offline_csv_dummy.sh](#grok_perf_offline_csv_dummysh)
    - [grok_perf_offline_csv_long_context.sh](#grok_perf_offline_csv_long_contextsh)
    - [deepseek_perf_offline_csv.sh](#deepseek_perf_offline_csvsh)
    - [Viewing Offline Plots](#viewing-offline-plots)
  - [Online Mode](#online-mode)
    - [grok_perf_online_csv.sh](#grok_perf_online_csvsh)
- [Requirements](#requirements)
- [Additional Notes](#additional-notes)

---

## Overview

The SGL Benchmark CI repository is intended to evaluate the performance of GROK models on various configurations and use cases. The scripts provided in this repository capture critical metrics such as latency and throughput for both offline and online benchmarking. Results are output as CSV files for easy analysis and archival.

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
- **Purpose:** Benchmarks the standard (production) GROK model.
- **Configuration:**
  - **TP:** Fixed at 8.
  - **Batch Sizes:** Iterates over multiple sizes.
  - **Input / Output Lengths:** Input length (IL) set to 1024 and output length (OL) set to 128.
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
  # Using ROCm SGLang development images
  bash grok_perf_offline_csv.sh --docker_image=rocm/sgl-dev:20250331rc   # release-candidate image
  bash grok_perf_offline_csv.sh --docker_image=rocm/sgl-dev:20250429     # nightly image
  
  # Using LMSYS SGLang images
  bash grok_perf_offline_csv.sh --docker_image=lmsysorg/sglang:v0.4.6.post3-rocm630
  bash grok_perf_offline_csv.sh --docker_image=lmsysorg/sglang:v0.4.7-rocm630
  
  # Using custom built images from source
  bash grok_perf_offline_csv.sh --docker_image=my-sglang:custom-rocm630
  bash grok_perf_offline_csv.sh --docker_image=my-sglang:dev-abc1234-rocm630
  ```

#### Viewing Offline Plots

The `generate_offline_plots.py` script (invoked by `python3 generate_offline_plots.py` after `process_offline_csv.py` has run) generates PNG plot files in the `/mnt/raid/michael/sgl_benchmark_ci/offline/GROK1/plots` directory.

To view these plots via a web browser, a simple HTTP server can be started using the provided `plots_server.sh` script.

1.  **Ensure no other process is using port 8000.** If it is, stop that process first.
2.  **Start the server:**
    ```bash
    bash /mnt/raid/michael/sgl_benchmark_ci/plots_server.sh
    ```
    This script runs `custom_http_server.py` which serves files from the `/mnt/raid/michael/sgl_benchmark_ci/offline/GROK1/plots` directory on port 8000. The directory listing page will be titled "GROK1 offline plots".

3.  **Access the plots:**
    Open a web browser and navigate to the server's address. If the server is running on a machine with IP address `10.194.129.138` (as an example), the URL would be:
    `http://10.194.129.138:8000/`

    You can then click on the individual `.png` files to view them.

#### grok_perf_offline_csv_dummy.sh
- **Purpose:** Benchmarks a small, dummy production GROK model using a minimal configuration.
- **Configuration:**
  - **TP:** Fixed at 8.
  - **Batch Size:** Runs only a single batch size (batch size = 2).
  - **Input / Output Lengths:** IL is set to 256 and OL is set to 4096.
  - Uses a dummy model format with a simplified configuration.
- **Output:**
  - A folder specifically named for dummy offline tests.
  - A `config.json` file containing the Docker image details.
  - A CSV file capturing performance metrics similar to the production benchmark.
  - An optional `result.jsonl` file with detailed results.
- **Usage:**
  ```bash
  bash grok_perf_offline_csv_dummy.sh
  ```  

#### grok_perf_offline_csv_long_context.sh
- **Purpose:** Tests model performance with long context inputs using the FP8 GROK model configuration with INT4 weight support.
- **Configuration:**
  - **Input Lengths:** Varies among 8K, 16K, and 32K tokens.
  - **TP:** Fixed at 8.
  - **Batch Size:** Fixed at 1 to isolate input length effects.
  - **Output Length:** Set to a small fixed value (OL = 10) for these tests.
- **Metrics Captured:** Same as the other offline benchmarks (latency and throughput metrics).
- **Output:**
  - A folder named with the current date and "LONGCONTEXT" in the title.
  - A `config.json` file with the Docker image information.
  - A CSV file with benchmark results for long-context experiments.
  - An optional `result.jsonl` file with detailed results.
- **Usage:**
  ```bash
  bash grok_perf_offline_csv_long_context.sh
  ```  

#### deepseek_perf_offline_csv.sh
- **Purpose:** Benchmarks the DeepSeek V3 model with FP8 quantization.
- **Configuration:**
  - **TP:** Fixed at 8.
  - **Batch Size:** Fixed at 32.
  - **Input / Output Lengths:** Input length (IL) set to 128 and output length (OL) set to 32.
  - **GSM8K Warm-up:** Performs GSM8K accuracy testing before benchmarking (threshold: 0.93).
- **Metrics Captured:** 
  - Same as grok_perf_offline_csv.sh (latency and throughput metrics)
- **Output:**
  - A folder named with the date and model configuration.
  - A CSV file with benchmark results.
  - Log files for server output and GSM8K testing.
- **Usage:**  
  ```bash
  # Using ROCm SGLang development images
  bash deepseek_perf_offline_csv.sh --docker_image=rocm/sgl-dev:20250430
  
  # Using LMSYS SGLang images
  bash deepseek_perf_offline_csv.sh --docker_image=lmsysorg/sglang:v0.4.6.post3-rocm630
  
  # Using custom built images from source
  bash deepseek_perf_offline_csv.sh --docker_image=my-sglang:custom-rocm630
  ```

### Online Mode

Online mode benchmarks measure the real-time serving performance of GROK1. This mode is designed to simulate interactive use and assess latency under different request rates.

#### grok_perf_online_csv.sh
- **Purpose:** Benchmarks the online serving performance, capturing both server startup and response latencies.
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
       - Otherwise it launches a single **Triton** back-end with environment variables `SGLANG_USE_AITER=1 SGLANG_INT4_WEIGHT=1 SGLANG_MOE_PADDING=0`.  
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
  # Using ROCm SGLang development images
  bash grok_perf_online_csv.sh --docker_image=rocm/sgl-dev:20250331rc    # RC build (keeps AITer pipelines)
  bash grok_perf_online_csv.sh --docker_image=rocm/sgl-dev:20250429      # Nightly build (Triton backend)

  # Using LMSYS SGLang images
  bash grok_perf_online_csv.sh --docker_image=lmsysorg/sglang:v0.4.6.post3-rocm630
  bash grok_perf_online_csv.sh --docker_image=lmsysorg/sglang:v0.4.7-rocm630
  
  # Using custom built images from source
  bash grok_perf_online_csv.sh --docker_image=my-sglang:custom-rocm630
  bash grok_perf_online_csv.sh --docker_image=my-sglang:dev-abc1234-rocm630
  ```

---

## Requirements

- **Operating System:** Unix-like shell environment.
- **Dependencies:**
  - **Bash:** Scripts are written in Bash.
  - **Python3:** Required to execute benchmark modules (e.g., `sglang.bench_one_batch`, `sglang.launch_server`, etc.).
  - **Docker:** Required for running the online benchmark if executed outside a container.
  - **Model Files:** Ensure the model and tokenizer files are available at the paths specified in the scripts.
- **Additional:** The SGL benchmark Python package should be installed and configured in your environment.

---

## Additional Notes

- **Output Organization:**  
  All scripts automatically create output folders named with the current date and a description of the benchmark run (e.g., `_offline`, `_dummy_offline`, `_LONGCONTEXT_offline`, or `_online`).
- **Script Customization:**  
  Modify model paths, tokenizer paths, or benchmark parameters directly in the scripts if your configuration differs.
- **Resource Management:**  
  Ensure that no other processes are consuming critical GPU resources to avoid memory capacity errors.

---

## Cron Schedule

The benchmarks are scheduled to run daily via cron jobs. The schedule is defined in `crontab_rules.txt`.

- **Offline Benchmark:** Runs daily at 7 PM PT.
- **Online Benchmark:** Runs daily at 8 PM PT.

To apply these rules, run: `crontab /mnt/raid/michael/sgl_benchmark_ci/crontab_rules.txt`

