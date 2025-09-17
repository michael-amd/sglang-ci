# SGLang CI and toolkit

This repository provides a comprehensive benchmarking and continuous integration toolkit for SGLang, designed for rigorous performance evaluation and accuracy testing of large language models, specifically GROK and DeepSeek models. The toolkit offers a complete end-to-end solution for model performance analysis, from automated benchmark execution to data visualization and comparison.

## Key Features

**🚀 Comprehensive Benchmarking Modes:**

- **Offline Mode:** Batch processing benchmarks measuring throughput and latency across various configurations, batch sizes, and context lengths
- **Online Mode:** Real-time serving performance evaluation with concurrent request handling and interactive latency metrics

**🔧 Automated Infrastructure:**

- **Docker Integration:** Full support for ROCm SGLang development images and LMSYS SGLang images with automatic container management
- **Nightly Automation:** Scheduled benchmarking with `perf_nightly.sh` for continuous performance monitoring
- **Resource Management:** Intelligent GPU utilization checks and container lifecycle management

**📊 Advanced Analytics & Visualization:**

- **Performance Metrics:** Captures prefill/decode latency, end-to-end throughput, TTFT (Time-To-First-Token), and ITL (Inter-Token Latency)
- **GSM8K Accuracy Testing:** Integrated mathematical reasoning benchmarks with configurable accuracy thresholds
- **Data Processing Pipeline:** Automated consolidation of results across multiple benchmark runs with trend analysis
- **Interactive Plot Server:** Web-based visualization server for exploring performance trends and comparisons

**⚡ Flexible Configuration:**

- **Command-Line Interface:** All parameters configurable via CLI arguments, eliminating manual script editing
- **Multi-Model Support:** Specialized configurations for GROK1, GROK2 (Grok 2.5), and DeepSeek models with FP8/INT4 quantization
- **Backend Selection:** Automatic detection and configuration of optimal backends (aiter/triton) based on image versions

**🔍 Advanced Comparison Tools:**

- **Performance Regression Detection:** Automated comparison between benchmark runs with configurable thresholds
- **Statistical Analysis:** GSM8K accuracy significance testing and performance change quantification
- **Report Generation:** Markdown reports with color-coded performance indicators and detailed metrics breakdown

The toolkit is designed for both development teams conducting regular performance validation and researchers requiring detailed model performance analysis across different configurations and deployment scenarios.

---

## Table of Contents

- [Supported Docker Images](#supported-docker-images)
- [Benchmark CI](#benchmark-ci)
  - [Offline Mode](#offline-mode)
    - [grok_perf_offline_csv.sh](#grok_perf_offline_csvsh)
    - [deepseek_perf_offline_csv.sh](#deepseek_perf_offline_csvsh)
  - [Online Mode](#online-mode)
    - [grok_perf_online_csv.sh](#grok_perf_online_csvsh)
    - [deepseek_perf_online_csv.sh](#deepseek_perf_online_csvsh)
  - [Data Processing and Visualization](#data-processing-and-visualization)
    - [Processing and Plotting Scripts](#processing-and-plotting-scripts)
    - [Plot Server](#plot-server)
- [Nightly CI](#nightly-ci)
  - [Nightly Docker Image Monitoring](#nightly-docker-image-monitoring)
    - [nightly_image_check.sh](#nightly_image_checksh)
  - [Nightly Unit Test](#nightly-unit-test)
    - [test_nightly.sh](#test_nightlysh)
  - [Nightly Benchmarking](#nightly-benchmarking)
    - [perf_nightly.sh](#perf_nightlysh)
- [Upstream SGLang Tool](#upstream-sglang-tool)
  - [Compare CI Suites](#compare-ci-suites)
- [Benchmark Comparison](#benchmark-comparison)
- [Requirements](#requirements)
- [Additional Notes](#additional-notes)
- [Cron Schedule](#cron-schedule)
- [Contribution Guide](#contribution-guide)

---

## Supported Docker Images

The benchmark scripts support Docker images from multiple sources:

1. **ROCm SGLang Development Images:** Available at <https://hub.docker.com/r/rocm/sgl-dev>
   - Example: `rocm/sgl-dev:v0.4.9.post2-rocm630-mi30x-20250716` (nightly build)

2. **LMSYS SGLang Images:** Available at <https://hub.docker.com/r/lmsysorg/sglang/tags>
   - Example: `lmsysorg/sglang:v0.4.9.post2-rocm630-mi30x`

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
   If you need to build from source (e.g., for PRs with version changes), please refer to the official SGLang repository for the latest build instructions:

   **📋 Latest Build Instructions:** <https://github.com/sgl-project/sglang/blob/main/docker/Dockerfile.rocm>

   **Note:** When building from source in the future, remember to rebuild sgl_kernel inside the Docker image after PR checkout.

**Important:** You must provide the full Docker image name including the registry/organization prefix.

---

## Benchmark CI

### Offline Mode

Offline mode benchmarks are executed without real-time interaction, measuring model performance through batch processing. The following scripts are available for offline benchmarking:

#### grok_perf_offline_csv.sh

- **Purpose:** Benchmarks GROK models (GROK1, GROK2) with multiple test modes (normal, long_context, dummy).
- **Parameters:**
  - `--docker_image=IMAGE`: Docker image to use
  - `--mode=MODE`: Test mode - normal, long_context, or dummy (default: normal)
  - `--model=PATH`: Model path (configurable via environment variables)
  - `--model-type=TYPE`: Model type - grok1 or grok2 (default: grok1)
  - `--tokenizer=NAME`: Tokenizer name (default: Xenova/grok-1-tokenizer)
  - `--dummy-model=PATH`: Dummy model path for dummy mode (configurable via environment variables)
  - `--work-dir=PATH`: Working directory (configurable via environment variables)
  - `--output-dir=PATH`: Output directory (default: same as work-dir)
  - `--help`: Show help message
- **Automatic Backend Selection:** The script automatically uses the `aiter` backend for all supported images (`rocm/sgl-dev` and `lmsysorg/sglang`).
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
  bash grok_perf_offline_csv.sh --docker_image=rocm/sgl-dev:v0.4.9.post2-rocm630-mi30x-20250716
  bash grok_perf_offline_csv.sh --docker_image=lmsysorg/sglang:v0.4.9.post2-rocm630-mi30x

  # Custom model and tokenizer
  bash grok_perf_offline_csv.sh \
    --model=$MODEL_PATH \
    --tokenizer=$TOKENIZER_NAME

  # Grok 2 model
  bash grok_perf_offline_csv.sh --model-type=grok2

  # Long context mode
  bash grok_perf_offline_csv.sh --mode=long_context

  # Dummy mode with custom model
  bash grok_perf_offline_csv.sh --mode=dummy --dummy-model=$DUMMY_MODEL_PATH

  # Custom directories
  bash grok_perf_offline_csv.sh \
    --work-dir=$WORK_DIR \
    --output-dir=$OUTPUT_DIR
  ```

#### deepseek_perf_offline_csv.sh

- **Purpose:** Benchmarks the DeepSeek model with FP8 quantization.
- **Parameters:**
  - `--docker_image=IMAGE`: Docker image to use
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

Online mode benchmarks measure the real-time serving performance of GROK models (GROK1, GROK2). This mode is designed to simulate interactive use and assess latency under different request rates.

#### grok_perf_online_csv.sh

- **Purpose:** Benchmarks the online serving performance, capturing both server startup and response latencies.
- **Parameters:**
  - `--docker_image=IMAGE`: Docker image to use
  - `--model=PATH`: Model path (configurable via environment variables)
  - `--model-type=TYPE`: Model type - grok1 or grok2 (default: grok1)
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
     - **Automatic Backend Selection:** The script uses the `aiter` backend for all supported images.
     - Sets appropriate environment variables for the `aiter` backend:
       - `SGLANG_USE_AITER=1`
       - `SGLANG_INT4_WEIGHT=1`
     - Runs client benchmarks at multiple request rates.
     - Captures logs and parses median end-to-end latency (E2E), time-to-first-token (TTFT), and inter-token latency (ITL).
  4. **Results Aggregation:**
     - Aggregates metrics for performance analysis.
     - Generates a CSV summary with dynamic backend labeling (e.g., MI300x-aiter or MI300x-triton).
- **Output:**
  A run folder containing:
  - Server logs
  - Client logs
  - A CSV summary of online benchmark metrics with backend-specific labels (MI300x-aiter or MI300x-triton)
- **Usage:**

  ```bash
  # Basic usage (GROK1 default)
  bash grok_perf_online_csv.sh

  # GROK2 model
  bash grok_perf_online_csv.sh --model-type=grok2

  # Custom configuration
  bash grok_perf_online_csv.sh \
    --docker_image=rocm/sgl-dev:v0.4.9.post2-rocm630-mi30x-20250716 \
    --model=$MODEL_PATH \
    --model-type=grok2 \
    --tokenizer=$TOKENIZER_NAME \
    --node=$NODE_NAME \
    --threshold=0.85

  # Custom paths
  bash grok_perf_online_csv.sh \
    --work-dir=$WORK_DIR \
    --output-dir=$OUTPUT_DIR \
    --gsm8k-script=$GSM8K_SCRIPT_PATH
  ```

#### deepseek_perf_online_csv.sh

- **Purpose:** Benchmarks the online serving performance of the DeepSeek model. The script first runs a GSM8K accuracy test, then proceeds with a load test using various concurrency levels to measure latency and throughput.
- **Parameters:**
  - `--docker_image=IMAGE`: Docker image to use
  - `--model=PATH`: Path to the DeepSeek model (configurable via environment variables).
  - `--model-name=NAME`: A specific name for the model, used in output file and folder names (default: `DeepSeek-V3-0324`).
  - `--hf-model-id=ID`: The HuggingFace model ID for automated downloading (default: `deepseek-ai/DeepSeek-V3-0324`).
  - `--work-dir=PATH`: The working directory for the benchmark (configurable via environment variables).
  - `--output-dir=PATH`: Directory to save benchmark results (defaults to the working directory).
  - `--gsm8k-script=PATH`: Path to the GSM8K benchmark script (configurable via environment variables).
  - `--threshold=VALUE`: The minimum GSM8K accuracy required for the warm-up test to pass (default: `0.93`).
  - `--download-model`: A flag to enable automatic model download if it's not found locally.
  - `--check-dp-attention`: Enable Data Parallel attention mode - runs GSM8K test only and checks for server errors (skips serving benchmarks).
  - `--help`: Displays the help message with all available options.
- **Workflow:**
  1. **Container Management:** Automatically manages the Docker container lifecycle, re-invoking itself inside a container if run on a host machine.
  2. **Model Download:** If the `--download-model` flag is present, it uses `huggingface-cli` to download the model.
  3. **Server Launch:** Starts the SGLang server with the specified DeepSeek model and a tensor-parallel size of 8.
     - **Standard Mode:** Uses standard server configuration for both GSM8K and serving benchmarks.
     - **DP Attention Mode:** Uses Data Parallel attention settings with `--dp-size 8` and `--enable-dp-attention` flags.
  4. **GSM8K Warm-up Test:** Runs an initial benchmark using the GSM8K script to validate model accuracy against the threshold.
  5. **Serving Benchmark:** Executes a series of load tests using `sglang.bench_serving` across multiple concurrency levels (e.g., 128, 64, 16, 4, 1).
     - **Note:** In DP attention mode (`--check-dp-attention`), serving benchmarks are skipped and only GSM8K testing is performed with error checking.
- **Metrics Captured:**
  - **GSM8K Test:** Average accuracy, throughput, and latency.
  - **Serving Test:** For each concurrency level, it captures the best results from multiple runs for Median End-to-End (E2E) Latency, Median Time-To-First-Token (TTFT), and Median Inter-Token Latency (ITL).
- **Output:**
  A dedicated run folder is created, which includes:
  - **Standard Mode:** Server logs (`sglang_server.log`) and client logs for both GSM8K and serving benchmarks.
    - A CSV file summarizing the initial GSM8K test results.
    - A separate, structured CSV file for the serving benchmark, detailing E2E, TTFT, and ITL metrics against concurrency levels.
  - **DP Attention Mode:** Folder name includes `_dp_attention` suffix.
    - Server logs (`sglang_server.log`) with error checking for RuntimeError and critical issues.
    - GSM8K test results only (serving benchmarks are skipped).
- **Usage:**

  ```bash
  # Basic usage with default parameters
  bash deepseek_perf_online_csv.sh

  # Using a specific Docker image
  bash deepseek_perf_online_csv.sh \
    --docker_image=rocm/sgl-dev:v0.4.9.post2-rocm630-mi30x-20250716

  # Custom model and a different accuracy threshold
  bash deepseek_perf_online_csv.sh \
    --model=$MODEL_PATH \
    --model-name=DeepSeek-Custom \
    --threshold=0.90

  # DP attention mode - GSM8K test only with error checking
  bash deepseek_perf_online_csv.sh \
    --docker_image=rocm/sgl-dev:v0.5.2rc1-rocm630-mi30x-20250904 \
    --check-dp-attention \
    --model-path=/path-to-deepseek
  ```

**DP Attention Mode Features:**

The Data Parallel attention mode (`--check-dp-attention`) is a specialized testing mode designed for validation of DP attention functionality:

- **Purpose:** Validates Data Parallel attention implementation with error detection
- **Server Configuration:**
  - Uses `SGLANG_USE_AITER=1` environment variable
  - Launches server with `--dp-size 8`, `--enable-dp-attention`, and `--chunked-prefill-size 131072` flags
- **Testing Scope:** Runs only GSM8K accuracy tests (serving benchmarks are skipped for focused testing)
- **Error Detection:** Automatically monitors server logs for RuntimeError and critical errors during execution
- **Output Naming:** Creates output folders with `_dp_attention` suffix for easy identification
- **Use Cases:** Ideal for validating DP attention functionality, debugging server issues, or running quick accuracy-only tests

### Data Processing and Visualization

The benchmark CI includes unified scripts that handle both data processing and plot generation. These scripts consolidate results from multiple benchmark runs and generate performance trend plots, supporting both `grok` and `deepseek` models via a `--model` flag.

#### Processing and Plotting Scripts

These scripts process raw benchmark outputs, create consolidated summary CSV files, and generate plots.

- **`process_and_generate_offline_plots.py`**
  - **Purpose:** Process raw offline data and create visual representations of performance trends.
  - **Functionality:**
    - Consolidates data from multiple dates into a single summary CSV.
    - Generates plots for latency and throughput vs. date, with backend information.
  - **Usage:**

    ```bash
    # For GROK
    python3 process_and_generate_offline_plots.py --model grok

    # For DeepSeek
    python3 process_and_generate_offline_plots.py --model deepseek
    ```

- **`process_and_generate_online_plots.py`**
  - **Purpose:** Process raw online data and create visual representations of performance trends for both GROK and DeepSeek.
  - **Functionality:**
    - Consolidates data from multiple dates into a single summary CSV.
    - Supports model-specific configurations for `grok` and `deepseek`.
    - Handles different load metrics (`request_rate` for grok, `concurrency` for deepseek).
    - Generates a comprehensive plot with subplots for E2E Latency, TTFT, ITL, and, if available, Token/KV Cache data.
  - **Usage:**

    ```bash
    cd $WORK_DIR
    # For GROK
    python3 process_and_generate_online_plots.py --model grok

    # For DeepSeek
    python3 process_and_generate_online_plots.py --model deepseek
    ```

#### Plot Server

A simple HTTP server is provided to view generated plots:

```bash
# Start the plot server
bash server/plots_server.sh

# Access plots at http://<server-ip>:8000/
```

The server uses `server/custom_http_server.py` to serve files with proper directory listings.

---

## Nightly CI

This section covers all automated nightly continuous integration processes, including benchmarking, unit testing, and Docker image monitoring.

### Nightly Docker Image Monitoring

Automated Docker image availability monitoring for mi30x and mi35x hardware types with optional Teams alerts.

#### nightly_image_check.sh

Checks nightly Docker images (`rocm/sgl-dev`) for both hardware types using Docker Hub API.

**Key Options:**

- `--date=YYYYMMDD`: Check specific date (default: today PST)
- `--days=N`: Check last N days (default: 1)
- `--teams-webhook=URL`: Teams webhook URL for alerts (or set `TEAMS_WEBHOOK_URL`)

**Usage:**

```bash
# Basic check
./nightly_image_check.sh

# Check with Teams alerts
./nightly_image_check.sh --teams-webhook="https://your-webhook-url"

# Check specific date with Teams alerts
./nightly_image_check.sh --date=20250108 --teams-webhook="https://your-webhook-url"
```

**Teams Alert Features:**

- ✅ **Success**: All images available (shows specific image tags)
- ⚠️ **Warning**: Some images missing
- ❌ **Error**: All images missing or critical issues
- Includes links to GitHub workflow and Docker Hub for troubleshooting
- Test mode: `python3 team_alert/send_docker_image_alert.py --test-mode`

### Nightly Unit Test

The automated nightly unit test system provides continuous validation of SGL functionality through automated unit test execution on the latest Docker images. This system runs unit tests from the [SGLang repository test directory](https://github.com/sgl-project/sglang/tree/main/test) to ensure code quality and compatibility across different hardware configurations (mi30x and mi35x) with intelligent resource management and Teams integration for immediate failure notifications.

#### test_nightly.sh

- **Purpose:** Automated nightly unit test runner that discovers, pulls, and runs unit tests from the [SGLang repository](https://github.com/sgl-project/sglang/tree/main/test) on the latest Docker images for SGL with support for mi30x and mi35x hardware variants.
- **Key Features:**
  - **Automatic Image Discovery:** Uses Docker Hub API with pagination to find latest non-SRT images from today, then yesterday as fallback
  - **Hardware Support:** Supports both mi30x and mi35x hardware variants
  - **Intelligent Resource Management:** Checks GPU idle status and stops existing containers before launching tests
  - **Teams Integration:** Sends detailed Teams notifications with test results, including success/failure counts and runtime information
  - **Comprehensive Logging:** Creates detailed logs for debugging and analysis
  - **Test Discovery:** Automatically discovers and runs all test files from the SGLang repository test directory
  - **Error Handling:** Robust error detection and reporting for container issues, test failures, and resource conflicts

**Parameters:**

- `--hardware`: Hardware type (`mi30x` or `mi35x`, default: `mi30x`)
- `--teams-webhook`: Teams webhook URL for notifications (or set `TEAMS_WEBHOOK_URL` environment variable)
- `--dry-run`: Show what would be done without executing
- `--verbose`: Enable detailed logging output
- `--container-timeout`: Container startup timeout in seconds (default: 300)
- `--test-timeout`: Individual test timeout in seconds (default: 1800)

**Usage:**

```bash
# Basic run with mi30x hardware
bash test_nightly.sh

# Run with mi35x hardware and Teams notifications
bash test_nightly.sh --hardware=mi35x --teams-webhook="https://your-webhook-url"

# Dry run to see what would be executed
bash test_nightly.sh --dry-run --verbose

# Custom timeouts for slower tests
bash test_nightly.sh --container-timeout=600 --test-timeout=3600
```

**Teams Notifications:**

- 🟢 **Success**: All tests passed (includes test count, runtime, and Docker image info)
- 🟡 **Partial Success**: Some tests failed (detailed breakdown of pass/fail counts)
- 🔴 **Failure**: Critical errors or all tests failed
- **Rich Information**: Container details, test execution summary, and troubleshooting links
- **Test Mode**: Use `python3 team_alert/send_unit_test_alert.py --test-mode` for testing

**Output Structure:**

```
unit_test_results/
├── YYYYMMDD_unit_test_{hardware}/
│   ├── container_logs.txt          # Docker container startup logs
│   ├── test_execution_summary.txt  # High-level test results summary
│   ├── test_results/               # Individual test outputs
│   │   ├── test_file1.py.log      # Individual test logs
│   │   └── test_file2.py.log
│   └── teams_notification.json     # Teams notification payload (if sent)
```

**Troubleshooting:**

- **"No suitable Docker images found"**: Check Docker Hub for available images or try different date
- **"Teams notifications disabled"**: No webhook URL configured - use `--teams-webhook-url` or set `TEAMS_WEBHOOK_URL` environment variable
- **HTTP 202 responses**: Normal for Power Automate flows (asynchronous processing)
- **Plot server not accessible**: Check `PLOT_SERVER_HOST`/`PLOT_SERVER_PORT` and ensure plot server is running, or use `--check-server` to test connectivity
- **Permission issues**: Ensure webhook URL has proper Teams permissions
- **Missing analysis data**: Check `BENCHMARK_BASE_DIR` environment variable or use `--benchmark-dir` to specify custom benchmark directory
- **Plot files not found**: Verify `--plot-dir` path to ensure plots exist with expected naming pattern

### Nightly Benchmarking

The `perf_nightly.sh` script provides automated orchestration for running nightly benchmarks for both GROK and DeepSeek. This script is designed to be run via cron jobs and handles the complete workflow from Docker image management to benchmark execution and data processing.

#### perf_nightly.sh

- **Purpose:** Automated nightly benchmark orchestration that pulls the latest `rocm/sgl-dev` Docker image and runs benchmarks with proper resource management.
- **Key Features:**
  - **Automatic Image Selection:** Pulls the latest `rocm/sgl-dev` image for the current PST date (with fallback to previous day)
  - **GPU Resource Management:** Checks GPU idle status and stops running containers if needed
  - **Container Management:** Creates/manages persistent containers with proper mounts and configuration
  - **Comprehensive Workflow:** Runs benchmarks and then immediately processes CSV data and generates plots.
  - **Flexible Model and Mode Selection:** Supports `grok` and `deepseek` models, and can run `offline`-only, `online`-only, or `all` benchmarks.
- **Parameters:**
  - `--model=MODEL`: The model to run. Options: `grok` (default), `deepseek`.
  - `--mode=MODE`: Which benchmarks to run. Options: `all` (default), `offline`, `online`.
- **Environment Variables:** All configuration can be customized via environment variables:
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
  5. **Data Processing & Plotting:** Automatically runs the combined script to process data and generate plots.
  6. **Logging:** Saves processing logs to benchmark output folders
- **Output:**
  - Benchmark results in standard output folders
  - Processing logs: `process_and_generate_offline_plots.log` or `process_and_generate_online_plots.log`
- **Usage:**

  ```bash
  # Run GROK online and offline benchmarks (default)
  bash perf_nightly.sh

  # Run GROK offline only
  bash perf_nightly.sh --model=grok --mode=offline

  # Run DeepSeek online only
  bash perf_nightly.sh --model=deepseek --mode=online
  ```

**Note:** This script is designed for automated execution via cron jobs and handles all aspects of the benchmarking pipeline, making it ideal for unattended nightly performance monitoring.

---

The nightly benchmark script includes built-in Microsoft Teams integration to automatically send plot notifications to Teams channels and group chats when benchmarks complete. **Teams notifications are disabled by default** and require explicit configuration to enable.

All Teams-related components are organized in the `team_alert/` folder.

#### Features

- **🔕 Disabled by Default**: Teams notifications require explicit webhook URL configuration
- **📱 Multi-Target Support**: Send to Teams channels (Incoming Webhooks) or group chats (Power Automate)
- **🎯 Intelligent Analysis**: Automated GSM8K accuracy checking and performance regression detection
- **📝 Text-Only Notifications**: Optimized adaptive cards with plot links
- **🔗 Direct Plot Access**: Includes links to view/download generated plots and browse dashboard
- **⚙️ Flexible Configuration**: Command-line options, environment variables, or config files
- **🧪 Comprehensive Testing**: Built-in test suite and validation tools

#### Quick Start

```bash
# Enable Teams notifications with webhook URL
bash perf_nightly.sh --teams-webhook-url="https://your-webhook-url"

# Run specific model/mode with Teams notifications
bash perf_nightly.sh --model=grok --mode=online --teams-webhook-url="https://..."

# Run with intelligent analysis (GSM8K accuracy + performance regression detection)
bash perf_nightly.sh --teams-webhook-url="..." --teams-analysis-days=7

# Run with plots only (skip analysis for faster notifications)
bash perf_nightly.sh --teams-webhook-url="..." --teams-skip-analysis

# Direct script usage with custom directories
python3 team_alert/send_teams_notification.py --model grok --mode online \
  --webhook-url "https://your-webhook-url" \
  --plot-dir "/custom/plot/directory" \
  --benchmark-dir "/custom/benchmark/directory"
```

#### Intelligent Analysis & Alerts

The Teams integration includes **automatic benchmark health monitoring** with intelligent alerts:

**🎯 GSM8K Accuracy Monitoring**

- **GROK**: Alerts if accuracy falls below 80%
- **DeepSeek**: Alerts if accuracy falls below 93%
- Automatically parses GSM8K results from benchmark logs

**📈 Performance Regression Detection**

- Monitors online benchmark metrics: **E2E Latency**, **TTFT**, **ITL**
- Compares current results with recent history (configurable lookback period)
- Alerts on **>5% performance degradation** (latency increases)
- Supports historical comparison up to 7 days back (configurable)

**🚨 Alert Levels**

- ✅ **Good**: No accuracy or performance issues detected
- ⚠️ **Warning**: Performance regression detected
- ❌ **Error**: GSM8K accuracy failure or critical issues

#### Setting Up Teams Webhook URLs

**For Teams Channels (Incoming Webhook)**

1. Go to your Teams channel
2. Click "..." menu → "Connectors" or "Workflows"
3. Add "Incoming Webhook" connector
4. Configure name and optional image
5. Copy the generated webhook URL: `https://outlook.office.com/webhook/...`

**For Teams Group Chats (Power Automate)**

1. Go to [flow.microsoft.com](https://flow.microsoft.com)
2. Create flow with "When a HTTP request is received" trigger
3. Add "Post message in a chat or channel" action
4. Configure to post to your group chat
5. Copy the HTTP POST URL: `https://prod-XX.westus.logic.azure.com:443/workflows/...`

#### Configuration Options

| Variable | Description | Default |
|----------|-------------|---------|
| `TEAMS_WEBHOOK_URL` | Teams webhook URL (**required to enable**) | Empty (disabled) |
| `TEAMS_SKIP_ANALYSIS` | Skip GSM8K accuracy and performance analysis | `false` |
| `TEAMS_ANALYSIS_DAYS` | Days to look back for performance comparison | `7` |
| `PLOT_SERVER_HOST` | Plot server hostname | Auto-detected via `hostname -I` |
| `PLOT_SERVER_PORT` | Plot server port | `8000` |
| `PLOT_SERVER_BASE_URL` | Full server URL override | - |
| `BENCHMARK_BASE_DIR` | Base directory for benchmark data | `~/sglang-ci` |

#### Command Line Options

The Teams notification script supports these additional command line options:

| Option | Description | Default |
|--------|-------------|---------|
| `--model` | Model name (`grok`, `deepseek`) | **Required** |
| `--mode` | Benchmark mode (`online`, `offline`) | **Required** |
| `--webhook-url` | Teams webhook URL (overrides `TEAMS_WEBHOOK_URL`) | - |
| `--plot-dir` | Base directory where plots are stored | `~/sglang-ci/plots_server` |
| `--benchmark-dir` | Base benchmark directory (overrides `BENCHMARK_BASE_DIR`) | `~/sglang-ci` |
| `--check-server` | Check plot server accessibility before sending | `false` |
| `--skip-analysis` | Skip GSM8K accuracy and performance analysis | `false` |
| `--analysis-days` | Days to look back for performance comparison | `7` |

#### Teams Message Content

When benchmarks complete, Teams receives text-only adaptive cards containing:

- 🎯 **Intelligent Summary Alert**: GSM8K accuracy status and performance regression detection
  - ✅ **Good**: No accuracy or performance issues detected
  - ⚠️ **Warning**: Performance regression detected (>5% increase in E2E/TTFT/ITL latency)
  - ❌ **Error**: GSM8K accuracy failure or significant performance issues
- 🚀 **Benchmark Info**: Model name, mode, and generation timestamp (San Francisco time)
- 📊 **Plot Summary**: Number of plots found and clickable file names
- 🔗 **Direct Plot Links**: Text links to view/download individual plots at full resolution
- 🌐 **Dashboard Access**: Browse all plots via web interface
- 📅 **Status Updates**: Success/failure notifications with context

#### Disabling Teams Notifications

```bash
# Run without Teams (default behavior - no webhook URL configured)
bash perf_nightly.sh

# Temporarily disable when webhook is configured via environment
unset TEAMS_WEBHOOK_URL
bash perf_nightly.sh


```

#### Troubleshooting

- **"Teams notifications disabled"**: No webhook URL configured - use `--teams-webhook-url` or set `TEAMS_WEBHOOK_URL` environment variable
- **HTTP 202 responses**: Normal for Power Automate flows (asynchronous processing)
- **Plot server not accessible**: Check `PLOT_SERVER_HOST`/`PLOT_SERVER_PORT` and ensure plot server is running, or use `--check-server` to test connectivity
- **Permission issues**: Ensure webhook URL has proper Teams permissions
- **Missing analysis data**: Check `BENCHMARK_BASE_DIR` environment variable or use `--benchmark-dir` to specify custom benchmark directory
- **Plot files not found**: Verify `--plot-dir` path to ensure plots exist with expected naming pattern

## Upstream SGLang Tool

This section contains tools for analyzing the upstream SGLang project's continuous integration (CI) system.

### Compare CI Suites

**Purpose:** Compare NVIDIA vs AMD test suites from SGLang's CI system to analyze test coverage and identify differences between GPU platforms.

**Script:** `upstream_ci/compare_suites_standalone.py`

**Key Features:**

- **Automatic Suite Analysis:** Fetches and parses SGLang's test suites from GitHub
- **Coverage Comparison:** Generates detailed coverage analysis between AMD and NVIDIA test suites
- **Dual Output Formats:**
  - CSV format with coverage percentages and test counts
  - Markdown format with detailed test breakdowns
- **Dynamic Category Detection:** Automatically detects performance, accuracy, and nightly test categories
- **Standalone Operation:** Works without SGLang dependencies, parsing test files as text

**Usage:**

```bash
# Default: Generate CSV coverage report with date-stamped filename
python3 compare_suites_standalone.py

# Generate detailed markdown report with full test lists
python3 compare_suites_standalone.py --details

# Output to terminal only (no file creation)
python3 compare_suites_standalone.py --stdout

# Custom output filename
python3 compare_suites_standalone.py --output "my_report.csv"
```

**Output Examples:**

- **CSV Format:** Test categories with AMD/NVIDIA test counts and coverage percentages
- **Markdown Format:** Detailed breakdowns showing common tests, NVIDIA-only tests, and AMD-only tests

### Benchmark Comparison

The benchmark CI includes a powerful comparison tool that allows you to compare CSV results between different benchmark runs, automatically extracting GSM8K accuracy information and generating detailed performance comparison reports.

**Purpose:** Compare SGLang benchmark CSV results from different runs and generate comprehensive markdown reports with performance analysis.

**Script:** `compare_csv_results.py`

**Key Features:**

- **Automatic Mode Detection:** Detects whether CSV files are from offline or online benchmarks
- **GSM8K Accuracy Extraction:** Automatically extracts GSM8K accuracy from associated log files
- **Performance Threshold Analysis:** Configurable thresholds for highlighting significant changes
- **Flexible Output:** Generates timestamped comparison folders with markdown reports
- **Multi-Model Support:** Handles different model comparisons (GROK1, GROK2, DeepSeek-V3, etc.)

**Parameters:**

- `--csv1`: Path to first CSV directory (required)
- `--csv2`: Path to second CSV directory (required)
- `--mode`: Benchmark mode (`offline`, `online`, or `auto` for auto-detection)
- `--model`: Model name to filter CSV files (for same model comparisons)
- `--model1`: Model name for first directory (overrides `--model`)
- `--model2`: Model name for second directory (overrides `--model`)
- `--output-md`: Custom path for output markdown file (optional)
- `--output-dir`: Output directory (default: `~/sglang-ci/comparison_results`)
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

# Compare GROK2 offline results
python3 compare_csv_results.py \
  --csv1 offline/GROK2/20250624_GROK2_FP8_offline \
  --csv2 offline/GROK2/20250626_GROK2_FP8_offline \
  --mode offline --model grok2

# Compare GROK2 online results
python3 compare_csv_results.py \
  --csv1 online/GROK2/20250624_GROK2_FP8_online \
  --csv2 online/GROK2/20250626_GROK2_FP8_online \
  --mode online --model grok2

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

## Model Configuration Details

### Torch.Compile Settings

The benchmark scripts use different torch.compile configurations that affect performance results:

- **Dummy Mode (grok_perf_offline_csv.sh):**
  - Uses `--enable-torch-compile` with `--torch-compile-max-bs 4`
  - **Note:** Torch.compile introduces compilation overhead on first run, which may affect timing measurements
  - Performance plots should account for this configuration when comparing results

- **Normal and Long Context Modes:**
  - Torch.compile is not enabled by default
  - Uses standard CUDA graphs with `--cuda-graph-max-bs 1024`

### GSM8K Accuracy Thresholds

The benchmark scripts use GSM8K accuracy testing as a warm-up validation step with the following default thresholds:

- **GROK-1 Model:**
  - **Online Mode:** 0.8 (80% accuracy threshold)

- **GROK-2 Model:**
  - **Online Mode:** 0.8 (80% accuracy threshold)

- **DeepSeek V3-0324 Model:**
  - **Online Mode:** 0.93 (93% accuracy threshold)

These thresholds can be customized using the `--threshold` parameter in online modes. If GSM8K accuracy falls below the threshold, the benchmark will skip performance testing to avoid reporting results from a potentially misconfigured model.

---

## Cron Schedule

The benchmarks and CI processes are scheduled to run daily via cron jobs. Hardware-specific schedules are defined in separate files:

- **mi30x hardware:** `cron/crontab_rules_mi30x.txt`
- **mi35x hardware:** `cron/crontab_rules_mi35x.txt`

**Currently Scheduled Tests:**

1. **Docker image availability check** - Verifies nightly Docker images are available
2. **Nightly Unit test** - Runs automated unit tests on latest Docker images
3. **Grok 2 online benchmark** - Performance benchmarking for Grok 2 model
4. **Grok online benchmark** - Performance benchmarking for Grok model
5. **DeepSeek online with DP attention checking** - DeepSeek benchmarking with data parallel attention validation
6. **DeepSeek online** - Standard DeepSeek performance benchmarking

**Usage:**

```bash
# Check specific hardware schedule file for exact timing
cat cron/crontab_rules_mi30x.txt  # For mi30x hardware
cat cron/crontab_rules_mi35x.txt  # For mi35x hardware

# Apply cron rules for your hardware type
crontab cron/crontab_rules_mi30x.txt  # For mi30x
crontab cron/crontab_rules_mi35x.txt  # For mi35x

# Check currently deployed cron jobs
crontab -l
```

---

## Contribution Guide

We follow standard code quality practices to maintain consistency across the codebase. Please adhere to these guidelines when contributing.

### Format code with pre-commit

We use pre-commit to maintain consistent code style checks. Before pushing your changes, please run:

```bash
pip3 install pre-commit
pre-commit install
pre-commit run --all-files
```

- **`pre-commit run --all-files`** manually runs all configured checks, applying fixes if possible. If it fails the first time, re-run it to ensure lint errors are fully resolved. Make sure your code passes all checks **before** creating a Pull Request.

- **Do not commit** directly to the `main` branch. Always create a new branch (e.g., `feature/my-new-feature`), push your changes, and open a PR from that branch.

**Reference:** [SGLang Contribution Guide - Format code with pre-commit](https://docs.sglang.ai/developer_guide/contribution_guide.html#format-code-with-pre-commit)
