# SGLang Benchmark CI

This repository contains a collection of benchmarking scripts designed for SGL performance testing of GROK models. The benchmarks are split into two main modes: **offline** and **online**. Each mode is implemented in separate scripts, which can be run independently to evaluate different configurations, input lengths, and performance metrics.

---

## Table of Contents

- [Overview](#overview)
- [Benchmark Modes](#benchmark-modes)
  - [Offline Mode](#offline-mode)
    - [grok_perf_offline_csv.sh](#grok_perf_offline_csvsh)
    - [grok_perf_offline_csv_dummy.sh](#grok_perf_offline_csv_dummysh)
    - [grok_perf_offline_csv_long_context.sh](#grok_perf_offline_csv_long_contextsh)
  - [Online Mode](#online-mode)
    - [grok_perf_online_csv.sh](#grok_perf_online_csvsh)
- [Requirements](#requirements)
- [Additional Notes](#additional-notes)

---

## Overview

The SGL Benchmark CI repository is intended to evaluate the performance of GROK models on various configurations and use cases. The scripts provided in this repository capture critical metrics such as latency and throughput for both offline and online benchmarking. Results are output as CSV files for easy analysis and archival.

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
  bash grok_perf_offline_csv.sh --docker_image=sgl-dev:20250331rc   # release-candidate image
  bash grok_perf_offline_csv.sh --docker_image=sgl-dev:20250429     # nightly image
  ```



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

---

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
       - Otherwise it launches a single **Triton** back-end with environment variables `SGLANG_AITER_MOE=1 SGLANG_INT4_WEIGHT=1 MOE_PADDING=0`.  
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
  # RC build (keeps AITer pipelines)
  bash grok_perf_online_csv.sh --docker_image=sgl-dev:20250331rc

  # Nightly build (Triton backend)
  bash grok_perf_online_csv.sh --docker_image=sgl-dev:20250429
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

