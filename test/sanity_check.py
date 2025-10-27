#!/usr/bin/env python3
# ------------------------------------------------------------------------------
# sanity_check.py
#   SGLang Sanity Check with Docker Support
#
# USAGE:
#   # Run with Docker image (all default models)
#   python3 test/sanity_check.py --docker-image=rocm/sgl-dev:v0.5.3rc0-rocm700-mi30x-20250925 --hardware=mi30x
#
#   # Run specific model with custom path
#   python3 test/sanity_check.py --docker-image=rocm/sgl-dev:v0.5.3rc0-rocm700-mi30x-20250925 --hardware=mi30x \
#           --model-path=/mnt/raid/models/huggingface/amd--grok-1-W4A8KV8 --model-type=GROK1-IN4
#
#   # Run without Docker (direct execution)
#   python3 test/sanity_check.py --hardware=mi30x --models GROK1-IN4 GROK2.5
#
#   # Run with custom models directory and log directory
#   python3 test/sanity_check.py --hardware=mi30x --models-dir=/data/models --log-dir=/tmp/my_sanity_logs --trials=1
#
#   # NOTE: Log uploads are now handled by cron/github_log_upload.sh (called from crontab)
#   # See cron/github_log_upload.sh for upload usage
#
# Available model types:
#   GPT-OSS-120B, GPT-OSS-20B, QWEN-30B, DeepSeek-V3, GROK1-IN4, GROK1-FP8, GROK2.5, llama4
#
# Available hardware platforms:
#   mi30x, mi35x
# ------------------------------------------------------------------------------

import argparse
import atexit
import os
import re
import signal
import subprocess
import sys
import time

# Set timezone to PST/PDT
os.environ["TZ"] = "America/Los_Angeles"

# =======================
# Configuration Table
# =======================
# Default model configurations - can be overridden by command line arguments
DEFAULT_MODELS = {
    "GPT-OSS-120B": {
        "model_path": {
            "mi30x": "lmsys/gpt-oss-120b-bf16",
            "mi35x": "openai/gpt-oss-120b",
        },
        "tokenizer_path": {
            "mi30x": "lmsys/gpt-oss-120b-bf16",
            "mi35x": "openai/gpt-oss-120b",
        },
        "launch_cmd_template": {
            "mi30x": "SGLANG_USE_AITER=0 python3 -m sglang.launch_server --model-path {model_path} --tp 8 --trust-remote-code --chunked-prefill-size 130172 --max-running-requests 128 --mem-fraction-static 0.85 --attention-backend triton",
            "mi35x": "SGLANG_USE_AITER=1 python3 -m sglang.launch_server --model-path {model_path} --tp 8 --trust-remote-code --chunked-prefill-size 130172 --max-running-requests 128 --mem-fraction-static 0.85 --attention-backend triton",
        },
        "bench_cmd": "python3 /sgl-workspace/sglang/benchmark/gsm8k/bench_sglang.py --num-questions 2000 --parallel 2000",
        "criteria": {"accuracy": 0.820},
    },
    "GPT-OSS-20B": {
        "model_path": {
            "mi30x": "lmsys/gpt-oss-20b-bf16",
            "mi35x": "openai/gpt-oss-20b",
        },
        "tokenizer_path": {
            "mi30x": "lmsys/gpt-oss-20b-bf16",
            "mi35x": "openai/gpt-oss-20b",
        },
        "launch_cmd_template": {
            "mi30x": "SGLANG_USE_AITER=0 python3 -m sglang.launch_server --model-path {model_path} --tp 8 --trust-remote-code --chunked-prefill-size 130172 --max-running-requests 128 --mem-fraction-static 0.85 --attention-backend triton",
            "mi35x": "SGLANG_USE_AITER=1 python3 -m sglang.launch_server --model-path {model_path} --tp 8 --trust-remote-code --chunked-prefill-size 130172 --max-running-requests 128 --mem-fraction-static 0.85 --attention-backend triton",
        },
        "bench_cmd": "python3 /sgl-workspace/sglang/benchmark/gsm8k/bench_sglang.py --num-questions 2000 --parallel 2000",
        "criteria": {"accuracy": 0.520},
    },
    "QWEN-30B": {
        "model_path": {
            "mi30x": "Qwen/Qwen3-30B-A3B-Thinking-2507",
            "mi35x": "Qwen/Qwen3-30B-A3B-Thinking-2507",
        },
        "tokenizer_path": {
            "mi30x": "Qwen/Qwen3-30B-A3B-Thinking-2507",
            "mi35x": "Qwen/Qwen3-30B-A3B-Thinking-2507",
        },
        "launch_cmd_template": {
            "mi30x": "SGLANG_USE_AITER=0 python3 -m sglang.launch_server --model-path {model_path} --tp 8 --trust-remote-code --chunked-prefill-size 130172 --max-running-requests 128 --mem-fraction-static 0.85 --attention-backend aiter",
            "mi35x": "SGLANG_USE_AITER=0 python3 -m sglang.launch_server --model-path {model_path} --tp 8 --trust-remote-code --chunked-prefill-size 130172 --max-running-requests 128 --mem-fraction-static 0.85 --attention-backend aiter",
        },
        "bench_cmd": "python3 /sgl-workspace/sglang/benchmark/gsm8k/bench_sglang.py --num-questions 2000 --parallel 2000",
        "criteria": {"accuracy": 0.840},
    },
    # "DeepSeek-V3": {
    #     "model_path": {
    #         "mi30x": "deepseek-ai/DeepSeek-V3-0324",
    #     },
    #     "tokenizer_path": {
    #         "mi30x": "deepseek-ai/DeepSeek-V3-0324",
    #     },
    #     "launch_cmd_template": {
    #         "mi30x": "SGLANG_USE_ROCM700A=1 SGLANG_USE_AITER=1 python3 -m sglang.launch_server --model-path {model_path} --tp 8 --trust-remote-code --chunked-prefill-size 131072 --dp-size 8 --enable-dp-attention --mem-fraction-static 0.85",
    #     },
    #     "bench_cmd": "python3 /sgl-workspace/sglang/benchmark/gsm8k/bench_sglang.py --num-questions 2000 --parallel 2000",
    #     "criteria": {"accuracy": 0.930},
    # },
    # "DeepSeek-R1": {
    #     "model_path": {
    #         "mi30x": "/mnt/raid/models/huggingface/deepseek-ai/DeepSeek-R1-0528/",
    #         "mi35x": "/mnt/raid/models/huggingface/deepseek-ai/DeepSeek-R1-0528/",
    #     },
    #     "tokenizer_path": {
    #         "mi30x": "/mnt/raid/models/huggingface/deepseek-ai/DeepSeek-R1-0528/",
    #         "mi35x": "/mnt/raid/models/huggingface/deepseek-ai/DeepSeek-R1-0528/",
    #     },
    #     "launch_cmd_template": {
    #         "mi30x": "SGLANG_USE_AITER=1 python3 -m sglang.launch_server --model-path {model_path} --tp 8 --attention-backend aiter --chunked-prefill-size 131072 --disable-radix-cache",
    #         "mi35x": "SGLANG_USE_AITER=1 python3 -m sglang.launch_server --model-path {model_path} --tp 8 --attention-backend aiter --chunked-prefill-size 131072 --disable-radix-cache",
    #     },
    #     "bench_cmd": "python3 /sgl-workspace/sglang/benchmark/gsm8k/bench_sglang.py --num-questions 2000 --parallel 2000",
    #     "criteria": {"accuracy": 0.930},
    # },
    # "DeepSeek-R1-MXFP4": {
    #     "model_path": {
    #         "mi35x": "/data2/models/amd-DeepSeek-R1-MXFP4-Preview",
    #     },
    #     "tokenizer_path": {
    #         "mi35x": "/data2/models/amd-DeepSeek-R1-MXFP4-Preview",
    #     },
    #     "launch_cmd_template": {
    #         "mi35x": "python3 -m sglang.launch_server --model-path {model_path} --tensor-parallel-size 8 --trust-remote-code --chunked-prefill-size 131072 --host 0.0.0.0 --port 8000 --log-requests --disable-radix-cache --mem-fraction-static 0.95 --dp-size 8",
    #     },
    #     "bench_cmd": "python3 /sgl-workspace/sglang/benchmark/gsm8k/bench_sglang.py --num-questions 2000 --parallel 2000",
    #     "criteria": {"accuracy": 0.930},
    # },
    "GROK1-IN4": {
        "model_path": {
            "mi30x": "amd--grok-1-W4A8KV8",
            "mi35x": "amd--grok-1-W4A8KV8",
        },
        "tokenizer_path": {
            "mi30x": "Xenova--grok-1-tokenizer",
            "mi35x": "Xenova--grok-1-tokenizer",
        },
        "launch_cmd_template": {
            "mi30x": "RCCL_MSCCL_ENABLE=0 SGLANG_USE_AITER=1 SGLANG_INT4_WEIGHT=1 python3 -m sglang.launch_server --model-path {model_path} --tokenizer-path {tokenizer_path} --tp 8 --quantization fp8 --trust-remote-code --attention-backend aiter --mem-fraction-static 0.85",
            "mi35x": "RCCL_MSCCL_ENABLE=0 SGLANG_USE_AITER=1 SGLANG_INT4_WEIGHT=1 python3 -m sglang.launch_server --model-path {model_path} --tokenizer-path {tokenizer_path} --tp 8 --quantization fp8 --trust-remote-code --attention-backend aiter --mem-fraction-static 0.85",
        },
        "bench_cmd": "python3 /sgl-workspace/sglang/benchmark/gsm8k/bench_sglang.py --num-questions 2000 --parallel 2000",
        "criteria": {"accuracy": 0.800},
    },
    "GROK1-FP8": {
        "model_path": {
            "mi30x": "lmzheng-grok-1",
            "mi35x": "lmzheng-grok-1",
        },
        "tokenizer_path": {
            "mi30x": "Xenova--grok-1-tokenizer",
            "mi35x": "Xenova--grok-1-tokenizer",
        },
        "launch_cmd_template": {
            "mi30x": "RCCL_MSCCL_ENABLE=0 SGLANG_USE_AITER=1 SGLANG_INT4_WEIGHT=0 python3 -m sglang.launch_server --model-path {model_path} --tokenizer-path {tokenizer_path} --tp 8 --quantization fp8 --trust-remote-code --attention-backend aiter --mem-fraction-static 0.85",
            "mi35x": "RCCL_MSCCL_ENABLE=0 SGLANG_USE_AITER=1 SGLANG_INT4_WEIGHT=0 python3 -m sglang.launch_server --model-path {model_path} --tokenizer-path {tokenizer_path} --tp 8 --quantization fp8 --trust-remote-code --attention-backend aiter --mem-fraction-static 0.85",
        },
        "bench_cmd": "python3 /sgl-workspace/sglang/benchmark/gsm8k/bench_sglang.py --num-questions 2000 --parallel 2000",
        "criteria": {"accuracy": 0.8},
    },
    "GROK2.5": {
        "model_path": {
            "mi30x": "grok-2",
            "mi35x": "grok-2",
        },
        "tokenizer_path": {
            "mi30x": "alvarobartt--grok-2-tokenizer",
            "mi35x": "alvarobartt--grok-2-tokenizer",
        },
        "launch_cmd_template": {
            "mi30x": "RCCL_MSCCL_ENABLE=0 SGLANG_USE_AITER=1 SGLANG_INT4_WEIGHT=0 python3 -m sglang.launch_server --model-path {model_path} --tokenizer-path {tokenizer_path} --tp 8 --quantization fp8 --trust-remote-code --attention-backend aiter --mem-fraction-static 0.85",
            "mi35x": "RCCL_MSCCL_ENABLE=0 SGLANG_USE_AITER=1 SGLANG_INT4_WEIGHT=0 python3 -m sglang.launch_server --model-path {model_path} --tokenizer-path {tokenizer_path} --tp 8 --quantization fp8 --trust-remote-code --attention-backend aiter --mem-fraction-static 0.85",
        },
        "bench_cmd": "python3 /sgl-workspace/sglang/benchmark/gsm8k/bench_sglang.py --num-questions 2000 --parallel 2000",
        "criteria": {"accuracy": 0.915},
    },
    "llama4": {
        "model_path": {
            "mi30x": "meta-llama/Llama-4-Maverick-17B-128E-Instruct-FP8",
            "mi35x": "/data2/models/meta-llama/Llama-4-Maverick-17B-128E-Instruct-FP8",
        },
        "tokenizer_path": {
            "mi30x": "meta-llama/Llama-4-Maverick-17B-128E-Instruct-FP8",
            "mi35x": "/data2/models/meta-llama/Llama-4-Maverick-17B-128E-Instruct-FP8",
        },
        "launch_cmd_template": {
            "mi30x": "SGLANG_USE_AITER=1 python3 -m sglang.launch_server --model-path {model_path} --tp 8 --attention-backend aiter --trust-remote-code",
            "mi35x": "SGLANG_USE_AITER=1 python3 -m sglang.launch_server --model-path {model_path} --tp 8 --attention-backend aiter --trust-remote-code",
        },
        "bench_cmd": "python3 /sgl-workspace/sglang/benchmark/gsm8k/bench_sglang.py --num-questions 2000 --parallel 2000",
        "criteria": {"accuracy": 0.900},
    },
}

# =======================
# Constants and Configuration
# =======================
PASS_MARK = "PASS [OK]"
FAIL_MARK = "FAIL [X]"
DEFAULT_LOG_DIR = os.path.join("test", "sanity_check_log", "mi30x")
DEFAULT_WORK_DIR = "/mnt/raid/michael/sglang-ci"
DEFAULT_DOCKER_IMAGE = "lmsysorg/sglang:v0.4.7-rocm630"
DEFAULT_MODELS_DIR = "/mnt/raid/models/huggingface"

# Container configuration
CONTAINER_SHM_SIZE = "32g"
MOUNT_DIR = "/mnt/raid/"
WORK_DIR_CONTAINER = "/sgl-workspace"

# Global server process tracking
ACTIVE_SERVER_PROCESSES = []


def cleanup_servers():
    """Clean up any active server processes."""
    global ACTIVE_SERVER_PROCESSES
    for server_proc in ACTIVE_SERVER_PROCESSES:
        try:
            if server_proc.poll() is None:  # Process is still running
                print(f"🛑 Cleaning up server process (PID: {server_proc.pid})")
                os.killpg(os.getpgid(server_proc.pid), signal.SIGTERM)
                # Wait a bit for graceful shutdown
                time.sleep(2)
                if server_proc.poll() is None:
                    print(f"🔥 Force killing server process (PID: {server_proc.pid})")
                    os.killpg(os.getpgid(server_proc.pid), signal.SIGKILL)
        except (ProcessLookupError, OSError):
            pass  # Process already terminated
    ACTIVE_SERVER_PROCESSES.clear()


def signal_handler(signum, frame):
    """Handle interrupt signals to clean up servers."""
    print(f"\n🚨 Received signal {signum}, cleaning up...")
    cleanup_servers()
    sys.exit(1)


# Register cleanup handlers
atexit.register(cleanup_servers)
signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)

# =======================
# GPU and Docker Cleanup Functions
# =======================

# GPU idle check thresholds
GPU_USAGE_THRESHOLD = int(
    os.environ.get("GPU_USAGE_THRESHOLD", "5")
)  # GPU usage threshold in %
VRAM_USAGE_THRESHOLD = int(
    os.environ.get("VRAM_USAGE_THRESHOLD", "5")
)  # VRAM usage threshold in %


def check_gpu_idle():
    """Check if GPU is idle based on usage thresholds."""
    try:
        result = subprocess.run(
            ["rocm-smi"], capture_output=True, text=True, timeout=10
        )
        if result.returncode != 0:
            print(
                "[sanity] WARN: rocm-smi not found or failed. Skipping GPU idle check."
            )
            return True

        lines = result.stdout.strip().split("\n")
        for line in lines[2:]:  # Skip header lines
            fields = line.split()
            if len(fields) >= 2:
                try:
                    # Extract GPU and VRAM usage percentages (typically last two columns)
                    gpu_usage = float(fields[-2].rstrip("%"))
                    vram_usage = float(fields[-1].rstrip("%"))
                    if (
                        gpu_usage > GPU_USAGE_THRESHOLD
                        or vram_usage > VRAM_USAGE_THRESHOLD
                    ):
                        return False
                except (ValueError, IndexError):
                    continue
        return True
    except (subprocess.TimeoutExpired, FileNotFoundError):
        print("[sanity] WARN: rocm-smi not available. Skipping GPU idle check.")
        return True


def cleanup_sglang_processes():
    """Clean up SGLang processes and free up ports, similar to perf_nightly.sh cleanup."""
    print("[sanity] Checking for existing SGLang processes and port conflicts...")

    # Kill processes using port 30000 (default SGLang port)
    try:
        result = subprocess.run(
            ["lsof", "-ti:30000"], capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0 and result.stdout.strip():
            pids = result.stdout.strip().split("\n")
            print(f"[sanity] Found processes using port 30000: {' '.join(pids)}")
            for pid in pids:
                if pid:
                    try:
                        subprocess.run(
                            ["kill", "-9", pid], capture_output=True, timeout=5
                        )
                        print(f"[sanity] Killed process {pid} using port 30000")
                    except (subprocess.SubprocessError, subprocess.TimeoutExpired):
                        pass
        else:
            print("[sanity] No processes found using port 30000")
    except (FileNotFoundError, subprocess.TimeoutExpired):
        print("[sanity] lsof not available or timed out, skipping port check")

    # Kill any sglang.launch_server processes
    try:
        result = subprocess.run(
            ["pgrep", "-f", "sglang.launch_server"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0 and result.stdout.strip():
            pids = result.stdout.strip().split("\n")
            print(f"[sanity] Found SGLang server processes: {' '.join(pids)}")
            for pid in pids:
                if pid:
                    try:
                        # Try graceful termination first
                        subprocess.run(
                            ["kill", "-15", pid], capture_output=True, timeout=5
                        )  # SIGTERM
                        time.sleep(2)
                        # Check if still running, then force kill
                        check_result = subprocess.run(
                            ["kill", "-0", pid], capture_output=True, timeout=5
                        )
                        if check_result.returncode == 0:
                            subprocess.run(
                                ["kill", "-9", pid], capture_output=True, timeout=5
                            )  # SIGKILL
                        print(f"[sanity] Killed SGLang server process {pid}")
                    except (subprocess.SubprocessError, subprocess.TimeoutExpired):
                        pass
        else:
            print("[sanity] No SGLang server processes found")
    except (FileNotFoundError, subprocess.TimeoutExpired):
        print("[sanity] pgrep not available or timed out, skipping process check")

    # Additional cleanup for any python processes running sglang modules (but not this script)
    try:
        current_pid = str(os.getpid())
        result = subprocess.run(
            ["pgrep", "-f", "python.*sglang"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0 and result.stdout.strip():
            pids = result.stdout.strip().split("\n")
            # Filter out the current script's PID to avoid self-termination
            filtered_pids = [pid for pid in pids if pid and pid != current_pid]
            if filtered_pids:
                print(
                    f"[sanity] Found Python SGLang processes: {' '.join(filtered_pids)}"
                )
                for pid in filtered_pids:
                    try:
                        subprocess.run(
                            ["kill", "-15", pid], capture_output=True, timeout=5
                        )
                        print(f"[sanity] Terminated Python SGLang process {pid}")
                    except (subprocess.SubprocessError, subprocess.TimeoutExpired):
                        pass
            else:
                print(
                    "[sanity] Found Python SGLang processes, but they are this script - skipping self-termination"
                )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass  # Optional cleanup, don't warn if not available


def ensure_gpu_idle():
    """Ensure GPU is idle by stopping running Docker containers, similar to perf_nightly.sh."""
    # GPU idle wait time
    GPU_IDLE_WAIT_TIME = 15

    # First, clean up any existing SGLang processes and port conflicts
    cleanup_sglang_processes()

    if not check_gpu_idle():
        print("[sanity] GPU is busy. Attempting to stop running Docker containers...")
        # Stop all running containers, ignoring errors if some are already stopped.
        if subprocess.run(["which", "docker"], capture_output=True).returncode == 0:
            try:
                # Check if docker daemon is accessible
                result = subprocess.run(
                    ["docker", "info"], capture_output=True, text=True, timeout=30
                )
                if result.returncode == 0:
                    # Get running container IDs
                    result = subprocess.run(
                        ["docker", "ps", "-q"],
                        capture_output=True,
                        text=True,
                        timeout=15,
                    )
                    if result.returncode == 0:
                        running_ids = result.stdout.strip().split("\n")
                        running_ids = [
                            cid for cid in running_ids if cid
                        ]  # Filter empty strings

                        if running_ids:
                            print(
                                f"[sanity] Stopping running containers: {' '.join(running_ids)}"
                            )
                            subprocess.run(
                                ["docker", "stop"] + running_ids,
                                capture_output=True,
                                timeout=60,
                            )
                        else:
                            print("[sanity] No running containers to stop.")
                    else:
                        print("[sanity] WARN: Failed to get running container list.")
                else:
                    print(
                        "[sanity] WARN: Docker not accessible; skipping container stop."
                    )
                    print("[sanity] Docker error details:")
                    for line in result.stderr.split("\n"):
                        if line.strip():
                            print(f"[sanity]   {line}")
            except (subprocess.SubprocessError, subprocess.TimeoutExpired) as e:
                print(f"[sanity] WARN: Docker command failed or timed out: {e}")
        else:
            print("[sanity] WARN: Docker not available; skipping container stop.")

        print(f"[sanity] Waiting {GPU_IDLE_WAIT_TIME}s for GPU to become idle...")
        time.sleep(GPU_IDLE_WAIT_TIME)

    # Final check and report status
    if check_gpu_idle():
        print("[sanity] GPU is idle. Proceeding with tests.")
    else:
        print("[sanity] WARN: GPU may still be busy, but proceeding as requested.")


# =======================
# Docker Container Management Functions
# =======================
def manage_docker_container(
    docker_image, work_dir, model_path, script_path, models_dir=DEFAULT_MODELS_DIR
):
    """Manage Docker container creation and execution."""
    if os.environ.get("INSIDE_CONTAINER"):
        return None  # Already inside container

    if not subprocess.run(["which", "docker"], capture_output=True).returncode == 0:
        os.environ["INSIDE_CONTAINER"] = "1"
        return (
            None  # No docker available, assume we're already in the right environment
        )

    # Extract image components
    image_with_tag = docker_image.split("/")[-1]  # sgl-dev:20250429
    repo = image_with_tag.split(":")[0]  # sgl-dev
    latest_tag = image_with_tag.split(":")[1] if ":" in image_with_tag else "latest"

    container_name = f"{repo}_{latest_tag}"
    print(f"[sanity] Using container {container_name}")
    print(f"[sanity] Docker image    {docker_image}")

    # Check if container exists and is accessible
    container_exists = (
        subprocess.run(
            ["docker", "ps", "-a", "--format", "{{.Names}}"],
            capture_output=True,
            text=True,
        )
        .stdout.strip()
        .split("\n")
    )

    if container_name in container_exists:
        # Check if container is running
        running_containers = (
            subprocess.run(
                ["docker", "ps", "--format", "{{.Names}}"],
                capture_output=True,
                text=True,
            )
            .stdout.strip()
            .split("\n")
        )

        if container_name not in running_containers:
            print("[sanity] Starting existing container...")
            subprocess.run(["docker", "start", container_name], check=True)

        # Validate accessibility
        script_accessible = (
            subprocess.run(
                ["docker", "exec", container_name, "test", "-f", script_path],
                capture_output=True,
            ).returncode
            == 0
        )

        model_accessible = (
            subprocess.run(
                ["docker", "exec", container_name, "test", "-d", model_path],
                capture_output=True,
            ).returncode
            == 0
        )

        if not script_accessible or not model_accessible:
            print("[sanity] Recreating container due to accessibility issues...")
            subprocess.run(["docker", "stop", container_name], capture_output=True)
            subprocess.run(["docker", "rm", container_name], capture_output=True)
            container_name = None
    else:
        container_name = None

    # Create container if needed
    if not container_name:
        container_name = f"{repo}_{latest_tag}"
        print("[sanity] Creating container...")

        # Build mount arguments
        script_dir = os.path.dirname(script_path)
        mount_args = ["-v", f"{MOUNT_DIR}:{MOUNT_DIR}"]

        # Always mount the models directory explicitly to ensure accessibility
        mount_args.extend(["-v", f"{models_dir}:{models_dir}"])

        # Mount script directory if not under MOUNT_DIR
        if not script_dir.startswith(MOUNT_DIR):
            mount_args.extend(["-v", f"{script_dir}:{script_dir}"])

        # Mount work directory if not under MOUNT_DIR
        if not work_dir.startswith(MOUNT_DIR) and not work_dir.startswith(script_dir):
            mount_args.extend(["-v", f"{work_dir}:{work_dir}"])

        # Mount model directory if needed
        model_dir = os.path.dirname(model_path)
        if not model_dir.startswith(MOUNT_DIR) and not model_dir.startswith(script_dir):
            # Determine mount root
            if model_path.startswith("/data2/"):
                mount_root = "/data2"
            elif model_path.startswith("/mnt/"):
                mount_root = "/mnt"
            elif model_path.startswith("/home/"):
                mount_root = "/home"
            else:
                mount_root = model_dir

            if mount_root != MOUNT_DIR.rstrip("/"):
                mount_args.extend(["-v", f"{mount_root}:{mount_root}"])

        # Create container
        cmd = (
            [
                "docker",
                "run",
                "-d",
                "--name",
                container_name,
                "--shm-size",
                CONTAINER_SHM_SIZE,
                "--ipc=host",
                "--cap-add=SYS_PTRACE",
                "--network=host",
                "--device=/dev/kfd",
                "--device=/dev/dri",
                "--security-opt",
                "seccomp=unconfined",
                "--group-add",
                "video",
                "--privileged",
                "-w",
                WORK_DIR_CONTAINER,
            ]
            + mount_args
            + [docker_image, "tail", "-f", "/dev/null"]
        )

        subprocess.run(cmd, check=True)

    return container_name


def execute_in_container(container_name, script_path, args):
    """Execute the script inside the Docker container."""
    cmd = [
        "docker",
        "exec",
        "-e",
        "INSIDE_CONTAINER=1",
        "-e",
        f"TZ=America/Los_Angeles",
        container_name,
        "python3",
        script_path,
    ] + args

    return subprocess.run(cmd)


# =======================
# Core Functions
# =======================
def resolve_model_path(path, models_dir=DEFAULT_MODELS_DIR):
    """Resolve relative model path to absolute path."""
    if not path:
        return path
    # If already absolute, return as-is
    if os.path.isabs(path):
        return path
    # If it's a HuggingFace repo identifier (not a local path), return as-is.
    # "alvarobartt--grok-2-tokenizer" **looks** like a repo id but is actually a
    # *local* directory in our models cache.  Trying to fetch it from the Hub
    # triggers `HFValidationError` because the naming convention (double dash)
    # is disallowed.  Treat such paths as local *if* the directory exists
    # locally; otherwise fall back to remote lookup.
    if path.startswith("alvarobartt--"):
        candidate = os.path.join(models_dir, path)
        if os.path.isdir(candidate):
            return candidate

    if path.startswith(("Xenova/", "hf-internal-testing/")):
        return path
    # Otherwise, prepend the models directory
    return os.path.join(models_dir, path)


def wait_for_server_ready(server_log_path, timeout=300):
    """Wait until server outputs readiness message. Returns (success, error_type, error_details)"""
    ready_msg = "The server is fired up and ready to roll!"
    start_time = time.time()
    last_error_logged = 0
    last_content = ""

    while True:
        time.sleep(0.5)
        elapsed = time.time() - start_time

        if elapsed > timeout:
            # Check for specific error patterns before returning
            try:
                with open(server_log_path, "r") as f:
                    content = f.read()

                # Check for memory errors
                if "Not enough memory" in content or "mem_fraction_static" in content:
                    mem_match = re.search(r"mem_fraction_static=([-\d.]+)", content)
                    current_mem = mem_match.group(1) if mem_match else "unknown"
                    return False, "memory_error", current_mem

                # Check for timeout during compilation
                if "start build" in content and "finish build" not in content:
                    return False, "compilation_timeout", None

            except:
                pass

            return False, "timeout", None

        try:
            with open(server_log_path, "r") as f:
                content = f.read()
                if ready_msg in content:
                    return True, None, None

                # Check for memory errors early
                if "Not enough memory" in content:
                    mem_match = re.search(r"mem_fraction_static=([-\d.]+)", content)
                    current_mem = mem_match.group(1) if mem_match else "unknown"
                    print(
                        f"❌ Memory error detected! Current mem_fraction_static={current_mem}"
                    )
                    print(f"   Suggestion: Increase --mem-fraction-static value")
                    return False, "memory_error", current_mem

                # Log errors periodically to help with debugging
                if elapsed - last_error_logged > 30:  # Every 30 seconds
                    if (
                        "Error" in content
                        or "Exception" in content
                        or "Traceback" in content
                    ):
                        print(
                            f"⚠️  Server errors detected at {elapsed:.0f}s - check {server_log_path}"
                        )
                        last_error_logged = elapsed

                # Log compilation progress for slow models
                if "start build" in content and content != last_content:
                    if "finish build" in content:
                        build_matches = re.findall(
                            r"finish build.*?cost\s+([\d.]+)s", content
                        )
                        if build_matches and elapsed > 60:
                            print(
                                f"   📦 Kernel compilation completed ({len(build_matches)} kernels)"
                            )
                    elif elapsed > 60 and elapsed - last_error_logged > 30:
                        print(
                            f"   ⏳ Kernel compilation in progress... ({elapsed:.0f}s elapsed)"
                        )
                        last_error_logged = elapsed
                    last_content = content

        except FileNotFoundError:
            # Log file doesn't exist yet, continue waiting
            pass
        except Exception as e:
            print(f"⚠️  Error reading server log: {e}")
            pass


def parse_accuracy(log_file):
    """Extract accuracy from client log."""
    with open(log_file, "r") as f:
        content = f.read()
    match = re.search(r"Accuracy:\s*([0-9.]+)", content)
    if match:
        return float(match.group(1))
    else:
        raise ValueError(f"No accuracy found in {log_file}")


def validate_model_paths(
    config,
    platform,
    model_path=None,
    tokenizer_path=None,
    models_dir=DEFAULT_MODELS_DIR,
):
    """Validate that model and tokenizer paths exist and contain required files.

    Returns:
        tuple: (is_valid, message, final_model_path, final_tokenizer_path)
    """
    # Use custom paths if provided, otherwise use defaults from config
    relative_model_path = model_path if model_path else config["model_path"][platform]
    relative_tokenizer_path = (
        tokenizer_path if tokenizer_path else config["tokenizer_path"][platform]
    )

    # Resolve to absolute paths
    final_model_path = resolve_model_path(relative_model_path, models_dir)
    final_tokenizer_path = resolve_model_path(relative_tokenizer_path, models_dir)

    # Check if model path exists and contains config.json
    if not os.path.exists(final_model_path):
        return (
            False,
            f"Model path does not exist: {final_model_path}",
            final_model_path,
            final_tokenizer_path,
        )

    config_json_path = os.path.join(final_model_path, "config.json")
    if not os.path.exists(config_json_path):
        return (
            False,
            f"config.json not found in model path: {final_model_path}",
            final_model_path,
            final_tokenizer_path,
        )

    # For tokenizer path, check if it's a HuggingFace repo name or local path
    if final_tokenizer_path and not final_tokenizer_path.startswith(
        ("Xenova/", "alvarobartt--", "hf-internal-testing/")
    ):
        if not os.path.exists(final_tokenizer_path):
            return (
                False,
                f"Tokenizer path does not exist: {final_tokenizer_path}",
                final_model_path,
                final_tokenizer_path,
            )

    return True, "Paths validated successfully", final_model_path, final_tokenizer_path


def build_launch_command(
    config,
    platform,
    model_path=None,
    tokenizer_path=None,
    models_dir=DEFAULT_MODELS_DIR,
):
    """Build the launch command from config template and custom paths.

    Returns:
        tuple: (launch_cmd, error_msg) where error_msg is set if paths are invalid
    """
    if platform not in config["launch_cmd_template"]:
        return None, None

    # Validate paths first
    valid, error_msg, final_model_path, final_tokenizer_path = validate_model_paths(
        config, platform, model_path, tokenizer_path, models_dir
    )
    if not valid:
        return None, error_msg

    template = config["launch_cmd_template"][platform]

    # Format the template with the paths
    try:
        launch_cmd = template.format(
            model_path=final_model_path, tokenizer_path=final_tokenizer_path
        )
    except KeyError:
        # If template doesn't use tokenizer_path, just use model_path
        launch_cmd = template.format(model_path=final_model_path)

    return launch_cmd, None


def sanity_check(
    model_name,
    config,
    platform,
    trials=3,
    log_dir=None,
    model_path=None,
    tokenizer_path=None,
    timing_log=None,
    docker_image=None,
    models_dir=DEFAULT_MODELS_DIR,
):
    """Run server + multiple client trials and save logs.

    Returns:
        str or bool: "SKIPPED" if model/tokenizer not available, True if passed, False if failed
    """
    launch_cmd, error_msg = build_launch_command(
        config, platform, model_path, tokenizer_path, models_dir
    )
    if not launch_cmd:
        if error_msg:
            # Model or tokenizer files not available - this is a skip, not a failure
            print(f"⏭️  {model_name}: SKIPPED ({error_msg})")
            if timing_log:
                timing_log.write(f"{model_name}: SKIPPED - {error_msg}\n")
                timing_log.write(
                    f"  Note: Model/tokenizer files not available, not counted as failure\n"
                )
                timing_log.write("=" * 50 + "\n")
                timing_log.flush()
            return "SKIPPED"
        else:
            # No launch command for platform - also skip
            print(
                f"⏭️  {model_name}: SKIPPED (no launch command for platform {platform})"
            )
            if timing_log:
                timing_log.write(
                    f"{model_name}: SKIPPED (no launch command for {platform})\n"
                )
                timing_log.write("=" * 50 + "\n")
                timing_log.flush()
            return "SKIPPED"

    bench_cmd = config["bench_cmd"]
    criteria = config["criteria"]

    # Use custom log directory if provided
    if log_dir is None:
        log_dir = os.path.join("test", "sanity_check_log", platform)

    os.makedirs(log_dir, mode=0o755, exist_ok=True)

    print(f"\n=== Testing {model_name} on {platform} ===")
    if timing_log:
        timing_log.write(f"\n=== {model_name} on {platform} ===\n")
        timing_log.write(f"Start time: {time.strftime('%Y-%m-%d %H:%M:%S %Z')}\n")
        timing_log.flush()

    overall_start = time.time()

    # 1. Start server
    server_log = os.path.join(log_dir, f"{model_name}_{platform}_server.log")
    with open(server_log, "w") as f:
        server_proc = subprocess.Popen(
            launch_cmd,
            shell=True,
            stdout=f,
            stderr=subprocess.STDOUT,
            preexec_fn=os.setsid,
            text=True,
            bufsize=1,
        )

    # Track the server process for cleanup
    global ACTIVE_SERVER_PROCESSES
    ACTIVE_SERVER_PROCESSES.append(server_proc)

    # 2. Wait for server ready
    print(f"🚀 Starting server for {model_name}...")
    server_start = time.time()

    # Set timeout based on model type - GROK1-FP8 needs more time for kernel compilation
    if model_name == "GROK1-FP8":
        timeout = 600  # 10 minutes for GROK1-FP8
        print(
            f"   Using extended timeout ({timeout}s) for {model_name} kernel compilation"
        )
    else:
        timeout = 300  # 5 minutes for other models

    # Wait for server with improved error detection
    ready, error_type, error_details = wait_for_server_ready(
        server_log, timeout=timeout
    )
    server_ready_time = time.time() - server_start

    if not ready:
        # Generate detailed error message based on error type
        if error_type == "memory_error":
            error_msg = f"Out of memory (current mem_fraction_static={error_details})"
            suggestion = (
                f"💡 Suggestion: Increase --mem-fraction-static in launch command"
            )
            print(f"{model_name}: {FAIL_MARK} ({error_msg})")
            print(f"   {suggestion}")
            if timing_log:
                timing_log.write(
                    f"Server startup: FAILED after {server_ready_time:.2f}s\n"
                )
                timing_log.write(f"Error: {error_msg}\n")
                timing_log.write(f"{suggestion}\n")
                timing_log.flush()
        elif error_type == "compilation_timeout":
            error_msg = "Timeout during kernel compilation"
            suggestion = f"💡 Suggestion: Consider increasing timeout or checking compilation environment"
            print(f"{model_name}: {FAIL_MARK} ({error_msg})")
            print(f"   {suggestion}")
            if timing_log:
                timing_log.write(
                    f"Server startup: FAILED after {server_ready_time:.2f}s\n"
                )
                timing_log.write(f"Error: {error_msg}\n")
                timing_log.write(f"{suggestion}\n")
                timing_log.flush()
        else:
            print(
                f"{model_name}: {FAIL_MARK} (server not ready after {server_ready_time:.2f}s)"
            )
            if timing_log:
                timing_log.write(
                    f"Server startup: FAILED after {server_ready_time:.2f}s\n"
                )
                timing_log.flush()

        # Clean up the failed server process
        try:
            os.killpg(os.getpgid(server_proc.pid), signal.SIGTERM)
            time.sleep(1)
            if server_proc.poll() is None:
                os.killpg(os.getpgid(server_proc.pid), signal.SIGKILL)
        except (ProcessLookupError, OSError):
            pass
        # Remove from active processes list
        if server_proc in ACTIVE_SERVER_PROCESSES:
            ACTIVE_SERVER_PROCESSES.remove(server_proc)
        return False

    print(f"✅ Server ready in {server_ready_time:.2f}s")
    if timing_log:
        timing_log.write(f"Server startup: {server_ready_time:.2f}s\n")
        timing_log.flush()

    # 3. Run multiple client trials
    print(f"🧪 Running {trials} benchmark trials...")
    accuracies = []
    for i in range(1, trials + 1):
        client_log = os.path.join(log_dir, f"{model_name}_{platform}_client_try{i}.log")
        trial_start = time.time()
        print(f"  📊 Trial {i}/{trials} starting...")

        # Check if server is still running before starting trial
        if server_proc.poll() is not None:
            print(f"  ❌ Server process died before trial {i}")
            if timing_log:
                timing_log.write(f"Trial {i}: FAILED - Server process died\n")
                timing_log.flush()
            accuracies.append(0.0)
            continue

        with open(client_log, "w") as f:
            proc = subprocess.Popen(
                bench_cmd, shell=True, stdout=f, stderr=subprocess.STDOUT, text=True
            )

            # Set benchmark timeout (10 minutes should be enough for most models)
            benchmark_timeout = 600

            # Show progress while benchmark is running
            benchmark_timed_out = False
            while proc.poll() is None:
                elapsed_so_far = time.time() - trial_start

                # Check for timeout
                if elapsed_so_far > benchmark_timeout:
                    print(
                        f"    ⚠️  Trial {i} timeout after {benchmark_timeout}s - killing benchmark process"
                    )
                    try:
                        proc.kill()
                        proc.wait(timeout=5)
                    except:
                        pass
                    benchmark_timed_out = True
                    break

                if (
                    elapsed_so_far > 30 and int(elapsed_so_far) % 15 == 0
                ):  # Show progress every 15s after 30s
                    print(
                        f"    ⏳ Trial {i} still running... ({elapsed_so_far:.0f}s elapsed)"
                    )
                time.sleep(1)

            if not benchmark_timed_out:
                proc.wait()

        trial_end = time.time()
        elapsed = trial_end - trial_start

        if benchmark_timed_out:
            print(
                f"  ❌ Trial {i}: {FAIL_MARK} (Benchmark timeout after {benchmark_timeout}s, Time: {elapsed:.2f}s)"
            )
            if timing_log:
                timing_log.write(
                    f"Trial {i}: {FAIL_MARK} (Benchmark timeout after {benchmark_timeout}s, Time: {elapsed:.2f}s)\n"
                )
                timing_log.flush()
            accuracies.append(0.0)
        else:
            try:
                acc = parse_accuracy(client_log)
                accuracies.append(acc)
                status = PASS_MARK if acc >= criteria["accuracy"] else FAIL_MARK
                print(
                    f"  ✅ Trial {i}: {status} (Accuracy: {acc:.3f}, Time: {elapsed:.2f}s)"
                )
                if timing_log:
                    timing_log.write(
                        f"Trial {i}: {status} (Accuracy: {acc:.3f}, Time: {elapsed:.2f}s)\n"
                    )
                    timing_log.flush()
            except ValueError as e:
                print(f"  ❌ Trial {i}: {FAIL_MARK} ({e}, Time: {elapsed:.2f}s)")
                if timing_log:
                    timing_log.write(
                        f"Trial {i}: {FAIL_MARK} ({e}, Time: {elapsed:.2f}s)\n"
                    )
                    timing_log.flush()
                accuracies.append(0.0)

    # 4. Kill server after all trials
    print(f"🛑 Stopping server...")
    shutdown_start = time.time()

    try:
        # Send SIGTERM first for graceful shutdown
        os.killpg(os.getpgid(server_proc.pid), signal.SIGTERM)

        # Wait up to 10 seconds for graceful shutdown
        timeout = 10
        elapsed = 0
        while server_proc.poll() is None and elapsed < timeout:
            time.sleep(0.5)
            elapsed += 0.5

        # If still running, force kill
        if server_proc.poll() is None:
            print(f"🔥 Server didn't stop gracefully, force killing...")
            os.killpg(os.getpgid(server_proc.pid), signal.SIGKILL)
            time.sleep(1)

    except (ProcessLookupError, OSError) as e:
        print(f"⚠️  Server process cleanup: {e}")

    # Remove from active processes list
    if server_proc in ACTIVE_SERVER_PROCESSES:
        ACTIVE_SERVER_PROCESSES.remove(server_proc)

    shutdown_time = time.time() - shutdown_start

    # 5. Determine final result based on highest accuracy
    max_accuracy = max(accuracies) if accuracies else 0.0

    required_accuracy = criteria["accuracy"]
    any_pass = max_accuracy >= required_accuracy
    final_status = PASS_MARK if any_pass else FAIL_MARK
    total_time = time.time() - overall_start

    print(f"📋 Result for {model_name} on {platform}: {final_status}")
    print(f"   Accuracies: {accuracies}")
    print(
        f"   Highest Accuracy: {max_accuracy:.3f} (Required: {required_accuracy:.3f})"
    )
    print(f"   Total Time: {total_time:.2f}s")

    if timing_log:
        timing_log.write(f"Server shutdown: {shutdown_time:.2f}s\n")
        timing_log.write(f"Final result: {final_status}\n")
        timing_log.write(f"Accuracies: {accuracies}\n")
        timing_log.write(
            f"Highest accuracy: {max_accuracy:.3f} (Required: {required_accuracy:.3f})\n"
        )
        timing_log.write(f"Total time: {total_time:.2f}s\n")
        timing_log.write(f"End time: {time.strftime('%Y-%m-%d %H:%M:%S %Z')}\n")
        timing_log.write("=" * 50 + "\n")
        timing_log.flush()

    return any_pass


# =======================
# Main Execution
# =======================
if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="SGLang Sanity Check with Docker Support"
    )
    parser.add_argument("--docker-image", "--docker_image", help="Docker image to use")
    parser.add_argument("--model-path", help="Custom model path")
    parser.add_argument("--model-type", help="Model type (if using custom model path)")
    parser.add_argument(
        "--hardware", choices=["mi30x", "mi35x"], help="Hardware platform"
    )
    parser.add_argument("--tokenizer-path", help="Custom tokenizer path")
    parser.add_argument(
        "--work-dir", default=DEFAULT_WORK_DIR, help="Working directory"
    )
    parser.add_argument(
        "--models-dir",
        default=DEFAULT_MODELS_DIR,
        help=f"Models directory (default: {DEFAULT_MODELS_DIR})",
    )
    parser.add_argument("--log-dir", help="Log output directory")
    parser.add_argument(
        "-t", "--trials", type=int, default=3, help="Number of client trials per model"
    )
    parser.add_argument(
        "--models", nargs="+", help="Specific models to test (default: all)"
    )

    # Legacy arguments for backward compatibility
    parser.add_argument(
        "-p", "--platform", choices=["mi30x", "mi35x"], help="Target platform (legacy)"
    )

    args = parser.parse_args()

    # Handle Docker container management
    if not os.environ.get("INSIDE_CONTAINER") and args.docker_image:
        script_path = os.path.abspath(__file__)
        model_path_for_mount = (
            args.model_path or args.models_dir
        )  # Use models_dir as default for mounting

        container_name = manage_docker_container(
            args.docker_image,
            args.work_dir,
            model_path_for_mount,
            script_path,
            args.models_dir,
        )

        if container_name:
            # Build arguments for container execution
            container_args = []
            if args.model_path:
                container_args.extend(["--model-path", args.model_path])
            if args.model_type:
                container_args.extend(["--model-type", args.model_type])
            if args.hardware:
                container_args.extend(["--hardware", args.hardware])
            elif args.platform:
                container_args.extend(["--hardware", args.platform])
            if args.tokenizer_path:
                container_args.extend(["--tokenizer-path", args.tokenizer_path])
            if args.work_dir != DEFAULT_WORK_DIR:
                container_args.extend(["--work-dir", args.work_dir])
            if args.models_dir != DEFAULT_MODELS_DIR:
                container_args.extend(["--models-dir", args.models_dir])
            if args.log_dir:
                container_args.extend(["--log-dir", args.log_dir])
            if args.trials != 3:
                container_args.extend(["--trials", str(args.trials)])
            if args.models:
                container_args.extend(["--models"] + args.models)
            # Pass the docker image info to the container execution
            container_args.extend(["--docker-image", args.docker_image])

            # Execute inside container
            result = execute_in_container(container_name, script_path, container_args)
            sys.exit(result.returncode)

    # Determine platform
    platform = args.hardware or args.platform
    if not platform:
        print("Error: Must specify --hardware (mi30x or mi35x)")
        sys.exit(1)

    # Change to work directory
    if args.work_dir and os.path.exists(args.work_dir):
        os.chdir(args.work_dir)

    # Determine which models to test
    models_to_test = DEFAULT_MODELS
    if args.models:
        models_to_test = {k: v for k, v in DEFAULT_MODELS.items() if k in args.models}
        missing_models = set(args.models) - set(DEFAULT_MODELS.keys())
        if missing_models:
            print(f"Warning: Unknown models specified: {', '.join(missing_models)}")

    # Skip GROK2.5 on rocm630 images (requires rocm700)
    if (
        args.docker_image
        and "rocm630" in args.docker_image
        and "GROK2.5" in models_to_test
    ):
        print(
            "⚠️  Skipping GROK2.5: This model requires ROCm 7.00+ (current image uses ROCm 6.30)"
        )
        models_to_test = {k: v for k, v in models_to_test.items() if k != "GROK2.5"}

    # Handle custom model
    if args.model_path and args.model_type:
        # Add custom model configuration
        models_to_test = {
            args.model_type: {
                "model_path": {platform: args.model_path},
                "tokenizer_path": {platform: args.tokenizer_path or args.model_path},
                "launch_cmd_template": DEFAULT_MODELS.get(args.model_type, {}).get(
                    "launch_cmd_template", {}
                ),
                "bench_cmd": "python3 /sgl-workspace/sglang/benchmark/gsm8k/bench_sglang.py --num-questions 2000 --parallel 2000",
                "criteria": DEFAULT_MODELS.get(args.model_type, {}).get(
                    "criteria", {"accuracy": 0.5}
                ),
            }
        }
    elif args.model_path:
        print("Error: --model-type must be specified when using --model-path")
        sys.exit(1)

    print(f"🔧 Testing on platform: {platform}")
    print(f"📋 Models to test: {', '.join(models_to_test.keys())}")
    print(f"🎯 Trials per model: {args.trials}")

    # Ensure GPU is idle before starting tests
    print("\n🔍 Checking GPU status and cleaning up running containers...")
    ensure_gpu_idle()

    # Create timing log with Docker image subfolder
    if args.log_dir:
        base_log_dir = os.path.abspath(args.log_dir)
    else:
        base_log_dir = os.path.abspath(
            os.path.join("test", "sanity_check_log", platform)
        )

    # Create subfolder based on Docker image tag if provided
    if args.docker_image:
        image_tag = (
            args.docker_image.split(":")[-1]
            if ":" in args.docker_image
            else args.docker_image.split("/")[-1]
        )
        log_dir_path = os.path.join(base_log_dir, image_tag)
        print(f"📁 Using image-specific log directory: {log_dir_path}")
    else:
        log_dir_path = base_log_dir

    timing_log_path = os.path.join(
        log_dir_path, f"timing_summary_{time.strftime('%Y%m%d_%H%M%S')}.log"
    )

    # Create log directory with proper permissions
    try:
        os.makedirs(log_dir_path, mode=0o755, exist_ok=True)
    except PermissionError:
        print(f"❌ Permission denied creating log directory: {log_dir_path}")
        print(
            f"💡 Try running with sudo or use a different log directory with --log-dir"
        )
        sys.exit(1)

    script_start_time = time.time()
    results = {}

    with open(timing_log_path, "w") as timing_log:
        timing_log.write("SGLang Sanity Check Timing Summary\n")
        timing_log.write("=" * 50 + "\n")
        timing_log.write(f"Start time: {time.strftime('%Y-%m-%d %H:%M:%S %Z')}\n")
        timing_log.write(f"Platform: {platform}\n")
        timing_log.write(f"Models: {', '.join(models_to_test.keys())}\n")
        timing_log.write(f"Trials per model: {args.trials}\n")
        timing_log.write(f"Docker image: {args.docker_image or 'Not specified'}\n")
        timing_log.write("=" * 50 + "\n")
        timing_log.flush()

        total_models = len(models_to_test)
        current_model = 0

        for model_name, config in models_to_test.items():
            current_model += 1
            model_start_time = time.time()
            print(f"\n🚀 [{current_model}/{total_models}] Starting {model_name}...")
            print("=" * 60)

            # Progress bar
            progress = int((current_model - 1) / total_models * 40)
            remaining = 40 - progress
            progress_bar = "█" * progress + "░" * remaining
            print(
                f"Progress: [{progress_bar}] {current_model}/{total_models} ({(current_model-1)/total_models*100:.1f}%)"
            )

            results[model_name] = sanity_check(
                model_name,
                config,
                platform,
                trials=args.trials,
                log_dir=log_dir_path,  # Use the image-specific log directory
                model_path=args.model_path,
                tokenizer_path=args.tokenizer_path,
                timing_log=timing_log,
                docker_image=args.docker_image,
                models_dir=args.models_dir,
            )

            # Update progress bar after completion
            model_end_time = time.time()
            model_duration = model_end_time - model_start_time
            progress = int(current_model / total_models * 40)
            remaining = 40 - progress
            progress_bar = "█" * progress + "░" * remaining
            if results[model_name] == "SKIPPED":
                status_emoji = "⏭️"
                status_text = "skipped"
            else:
                status_emoji = "✅" if results[model_name] else "❌"
                status_text = "completed"
            print(
                f"Progress: [{progress_bar}] {current_model}/{total_models} ({current_model/total_models*100:.1f}%) {status_emoji} {model_name} {status_text} in {model_duration:.1f}s"
            )

        # Final timing summary
        script_end_time = time.time()
        total_script_time = script_end_time - script_start_time

        timing_log.write(f"\nOVERALL SUMMARY\n")
        timing_log.write("=" * 50 + "\n")
        timing_log.write(f"End time: {time.strftime('%Y-%m-%d %H:%M:%S %Z')}\n")
        timing_log.write(
            f"Total execution time: {total_script_time:.2f}s ({total_script_time/60:.1f} minutes)\n"
        )
        timing_log.write(
            f"Average time per model: {total_script_time/total_models:.2f}s\n"
        )

        passed_count = sum(1 for result in results.values() if result is True)
        failed_count = sum(1 for result in results.values() if result is False)
        skipped_count = sum(1 for result in results.values() if result == "SKIPPED")
        tested_count = total_models - skipped_count

        timing_log.write(f"Models tested: {tested_count}/{total_models}\n")
        if tested_count > 0:
            timing_log.write(f"Models passed: {passed_count}/{tested_count}\n")
            timing_log.write(f"Models failed: {failed_count}/{tested_count}\n")
        else:
            timing_log.write(f"Models passed: 0 (no models tested)\n")
            timing_log.write(f"Models failed: 0 (no models tested)\n")
        timing_log.write(f"Models skipped: {skipped_count}/{total_models}\n")

        for model, result in results.items():
            if result == "SKIPPED":
                timing_log.write(f"  {model}: SKIPPED\n")
            else:
                timing_log.write(f"  {model}: {'PASS' if result else 'FAIL'}\n")

    print(f"\n🎯 All tests completed!")
    print(
        f"📊 Total execution time: {total_script_time:.2f}s ({total_script_time/60:.1f} minutes)"
    )
    print(f"📝 Timing log saved to: {timing_log_path}")

    print("\n=== Final Summary ===")
    passed_count = 0
    failed_count = 0
    skipped_count = 0
    for model, result in results.items():
        if result == "SKIPPED":
            status_emoji = "⏭️"
            print(f"{status_emoji} {model}: SKIPPED (model/tokenizer not available)")
            skipped_count += 1
        else:
            status_emoji = "✅" if result else "❌"
            print(f"{status_emoji} {model}: {PASS_MARK if result else FAIL_MARK}")
            if result:
                passed_count += 1
            else:
                failed_count += 1

    tested_count = len(results) - skipped_count
    if tested_count > 0:
        print(
            f"\n📈 Overall: {passed_count}/{tested_count} models passed ({passed_count/tested_count*100:.1f}%)"
        )
    else:
        print(f"\n⚠️  No models were tested (all {skipped_count} models skipped)")

    if skipped_count > 0:
        print(
            f"⏭️  Skipped: {skipped_count}/{len(results)} models (not counted in pass/fail statistics)"
        )

    # Final cleanup to ensure all servers are stopped
    print(f"\n🧹 Final cleanup...")
    cleanup_servers()
    print(f"✅ All processes cleaned up successfully!")
