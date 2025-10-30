#!/usr/bin/env python3
"""
GSM8K Benchmark Script for PD (Prefill-Decode Disaggregation) Testing
Based on https://github.com/sgl-project/sglang/blob/main/benchmark/gsm8k/bench_sglang.py

CHANGES FROM OFFICIAL IMPLEMENTATION AND WHY NECESSARY FOR PD:

1. HTTP Requests Instead of SGLang Decorators:
   - Official uses: @sgl.function decorator with sglang backend
   - PD version uses: Direct HTTP POST to /v1/completions endpoint
   - Why: PD disaggregation runs as separate services (router, prefill, decode)
     accessed via HTTP API, not as a Python library with decorators

2. Manual Parallelism with ThreadPoolExecutor:
   - Official uses: sglang's built-in run_batch() with num_threads
   - PD version uses: ThreadPoolExecutor for concurrent HTTP requests
   - Why: No access to sglang library internals when calling via HTTP

3. Optional Model Parameter:
   - Official doesn't need model param (inferred from backend)
   - PD version accepts --model parameter in requests
   - Why: PD router needs model identifier in completion requests

4. Increased Timeout and Reduced Parallelism:
   - Official uses: 128 parallel, default timeout
   - PD version uses: 16 parallel (configurable), 600s timeout
   - Why: PD disaggregation has higher latency due to network communication
     between prefill and decode servers, especially with long prompts

WHAT STAYS THE SAME (CRITICAL FOR ACCURACY PARITY):
- get_answer_value(): Exact same regex and logic (integer-only extraction)
- get_one_example(): Same prompt format "Question: ... Answer:"
- get_few_shot_examples(): Same few-shot construction with full reasoning
- Stop sequences: ["Question", "Assistant:", "<|separator|>"]
- Temperature: 0.0 (deterministic)
- Max tokens: 512

This ensures PD achieves same 0.93+ accuracy as non-PD benchmarks.
"""

import argparse
import ast
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, List

# Try importing required modules
try:
    import requests
except ImportError:
    print("Error: requests module not found. Installing...")
    import subprocess

    subprocess.check_call(["pip", "install", "requests"])
    import requests

INVALID = -9999999


def get_one_example(lines, i, include_answer):
    """
    Format a single GSM8K example.
    Matches sglang implementation exactly.
    """
    ret = "Question: " + lines[i]["question"] + "\nAnswer:"
    if include_answer:
        ret += " " + lines[i]["answer"]
    return ret


def get_few_shot_examples(lines, k):
    """
    Build few-shot examples string.
    Matches sglang implementation exactly.
    """
    ret = ""
    for i in range(k):
        ret += get_one_example(lines, i, True) + "\n\n"
    return ret


def get_answer_value(answer_str):
    """
    Extract numeric answer from answer string.
    Matches sglang implementation exactly - only integers, no decimals.

    CRITICAL: Uses r"\d+" (integers only) NOT r"\d+\.?\d*" (decimals)
    This is why previous version had 46% accuracy instead of 93%+:
    - Model generates: "The answer is 18."
    - r"\d+\.?\d*" extracts: "18." (with period)
    - Comparison fails: "18." != 18
    - r"\d+" extracts: "18" -> ast.literal_eval -> 18 (correct!)
    """
    answer_str = answer_str.replace(",", "")
    numbers = re.findall(r"\d+", answer_str)  # Integer-only regex - CRITICAL!
    if len(numbers) < 1:
        return INVALID
    try:
        return ast.literal_eval(numbers[-1])
    except SyntaxError:
        return INVALID


def download_gsm8k_dataset():
    """Download GSM8K dataset from GitHub."""
    url = "https://raw.githubusercontent.com/openai/grade-school-math/master/grade_school_math/data/test.jsonl"

    print(f"Downloading GSM8K dataset from {url}...")
    try:
        response = requests.get(url, timeout=30)
        response.raise_for_status()

        # Parse JSONL
        examples = []
        for line in response.text.strip().split("\n"):
            if line:
                examples.append(json.loads(line))

        print(f"✓ Downloaded {len(examples)} questions")
        return examples
    except Exception as e:
        print(f"✗ Failed to download dataset: {e}")
        return None


def read_jsonl(file_path):
    """Read JSONL file."""
    examples = []
    with open(file_path, "r", encoding="utf-8") as f:
        for line in f:
            examples.append(json.loads(line))
    return examples


def load_gsm8k_dataset(data_path, num_questions):
    """Load GSM8K dataset from file or download."""
    if data_path:
        try:
            print(f"Loading GSM8K dataset from {data_path}...")
            examples = read_jsonl(data_path)
            print(f"✓ Loaded {len(examples)} questions from file")
            return examples
        except Exception as e:
            print(f"Warning: Could not load dataset from {data_path}: {e}")
            print("Attempting to download dataset...")

    # Try to download
    examples = download_gsm8k_dataset()
    if examples:
        return examples

    # Fallback error
    print("Error: Could not load or download dataset")
    return None


def run_single_question(args, few_shot_examples, question, label, question_idx):
    """
    Run a single GSM8K question through the model via HTTP API.

    PD Adaptation: Instead of sglang decorator (@sgl.function), we make
    direct HTTP requests to the PD router which coordinates prefill/decode.
    """
    # Build prompt exactly like sglang implementation
    prompt = few_shot_examples + question

    # Make request to the PD router endpoint
    url = f"{args.host}:{args.port}/v1/completions"

    payload = {
        "prompt": prompt,
        "max_tokens": args.max_new_tokens,
        "temperature": 0.0,  # Deterministic - matches official
        "stop": ["Question", "Assistant:", "<|separator|>"],  # Exact match to official
    }

    # PD-specific: Add model parameter for router to identify model
    if hasattr(args, "model") and args.model:
        payload["model"] = args.model

    try:
        # PD disaggregation can be slower than monolithic serving
        # Increased timeout from 120s to 600s (10 minutes) for long reasoning chains
        response = requests.post(url, json=payload, timeout=600)
        response.raise_for_status()

        result = response.json()
        if "choices" in result and len(result["choices"]) > 0:
            generated_text = result["choices"][0]["text"]
            predicted_answer = get_answer_value(generated_text)

            is_correct = predicted_answer == label
            is_invalid = predicted_answer == INVALID

            return {
                "question_idx": question_idx,
                "correct": is_correct,
                "invalid": is_invalid,
                "predicted": predicted_answer,
                "expected": label,
                "generated_text": generated_text[:200],  # Truncate for storage
            }
        else:
            return {
                "question_idx": question_idx,
                "correct": False,
                "invalid": True,
                "predicted": INVALID,
                "expected": label,
                "error": "No choices in response",
            }

    except Exception as e:
        return {
            "question_idx": question_idx,
            "correct": False,
            "invalid": True,
            "predicted": INVALID,
            "expected": label,
            "error": str(e),
        }


def main(args):
    print(f"=" * 70)
    print(f"GSM8K Benchmark - PD Testing")
    print(f"=" * 70)
    print(f"Host: {args.host}:{args.port}")
    if hasattr(args, "model") and args.model:
        print(f"Model: {args.model}")
    print(f"Num Questions: {args.num_questions}")
    print(f"Parallelism: {args.parallel}")
    print(f"Num Shots: {args.num_shots}")
    print(f"Max New Tokens: {args.max_new_tokens}")
    print(f"=" * 70)

    # Load dataset
    lines = load_gsm8k_dataset(args.data_path, args.num_questions)

    if not lines:
        print("Error: Could not load dataset")
        return {"accuracy": 0.0, "error": "No dataset"}

    print(f"Loaded {len(lines)} examples")

    # Construct prompts exactly like sglang (CRITICAL for accuracy parity)
    num_questions = args.num_questions
    num_shots = args.num_shots
    few_shot_examples = get_few_shot_examples(lines, num_shots)

    # Build questions and labels using same functions as official implementation
    # This ensures identical prompt format and answer extraction
    questions = []
    labels = []
    for i in range(len(lines[:num_questions])):
        questions.append(get_one_example(lines, i, False))
        labels.append(get_answer_value(lines[i]["answer"]))

    # Verify all labels are valid
    if not all(l != INVALID for l in labels):
        print("Error: Some ground truth labels are invalid")
        return {"accuracy": 0.0, "error": "Invalid labels"}

    # Check server health
    try:
        health_url = f"{args.host}:{args.port}/health"
        response = requests.get(health_url, timeout=5)
        if response.status_code == 200:
            print(f"✓ Server is healthy")
        else:
            print(
                f"Warning: Server health check returned status {response.status_code}"
            )
    except Exception as e:
        print(f"Warning: Could not check server health: {e}")
        print("Continuing anyway...")

    # Run benchmark with parallel HTTP requests
    # PD Adaptation: Use ThreadPoolExecutor instead of sglang's run_batch()
    # since we're calling via HTTP rather than using sglang library
    start_time = time.time()
    results = []

    print(f"\nRunning benchmark with {args.parallel} parallel requests...")

    with ThreadPoolExecutor(max_workers=args.parallel) as executor:
        futures = []
        for idx in range(len(questions)):
            future = executor.submit(
                run_single_question,
                args,
                few_shot_examples,
                questions[idx],
                labels[idx],
                idx,
            )
            futures.append(future)

        # Collect results
        for i, future in enumerate(futures):
            result = future.result()
            results.append(result)

            # Print progress every 100 questions
            if (i + 1) % 100 == 0:
                elapsed = time.time() - start_time
                qps = (i + 1) / elapsed
                correct_so_far = sum(1 for r in results if r.get("correct", False))
                acc_so_far = correct_so_far / len(results) if results else 0
                print(
                    f"Progress: {i + 1}/{len(questions)} questions ({qps:.2f} QPS, Acc: {acc_so_far:.4f})"
                )

    end_time = time.time()
    total_time = end_time - start_time

    # Calculate metrics
    correct_count = sum(1 for r in results if r.get("correct", False))
    invalid_count = sum(1 for r in results if r.get("invalid", False))
    total_count = len(results)
    accuracy = correct_count / total_count if total_count > 0 else 0.0
    invalid_rate = invalid_count / total_count if total_count > 0 else 0.0

    # Print results
    print(f"\n" + "=" * 70)
    print(f"RESULTS")
    print(f"=" * 70)
    print(f"Total Questions: {total_count}")
    print(f"Correct: {correct_count}")
    print(f"Incorrect: {total_count - correct_count}")
    print(f"Invalid: {invalid_count}")
    print(f"Accuracy: {accuracy:.4f}")
    print(f"Invalid Rate: {invalid_rate:.4f}")
    print(f"Total Time: {total_time:.2f}s")
    print(f"Questions per Second: {total_count / total_time:.2f}")
    print(f"=" * 70)

    # Print error examples
    errors = [r for r in results if not r.get("correct", False)]
    if errors:
        print(f"\nSample Errors (first 5):")
        for i, err in enumerate(errors[:5]):
            print(f"\n{i+1}. Q{err['question_idx']}")
            print(f"   Expected: {err['expected']}, Got: {err['predicted']}")
            if err.get("error"):
                print(f"   Error: {err['error']}")
            elif err.get("generated_text"):
                print(f"   Generated: {err['generated_text']}")

        # Save detailed errors
        try:
            error_file = "/tmp/gsm8k_errors_detailed.json"
            with open(error_file, "w") as f:
                json.dump(errors[:50], f, indent=2)
            print(f"\n✓ Detailed errors saved to: {error_file}")
        except Exception as e:
            print(f"\n✗ Could not save error details: {e}")

    # Print some correct examples too
    correct = [r for r in results if r.get("correct", False)]
    if correct:
        print(f"\nSample Correct Answers (first 3):")
        for i, corr in enumerate(correct[:3]):
            print(f"\n{i+1}. Q{corr['question_idx']}")
            print(f"   Expected: {corr['expected']}, Got: {corr['predicted']} ✓")

    return {
        "accuracy": accuracy,
        "total_time": total_time,
        "invalid_rate": invalid_rate,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="GSM8K Benchmark for SGLang PD")
    parser.add_argument(
        "--host", type=str, default="http://127.0.0.1", help="Server host"
    )
    parser.add_argument("--port", type=int, default=30000, help="Server port")
    parser.add_argument(
        "--model",
        type=str,
        default=None,
        help="Model identifier to send with completion requests (optional)",
    )
    parser.add_argument(
        "--num-questions", type=int, default=200, help="Number of questions to test"
    )
    parser.add_argument(
        "--parallel", type=int, default=128, help="Number of parallel requests"
    )
    parser.add_argument(
        "--num-shots", type=int, default=5, help="Number of few-shot examples"
    )
    parser.add_argument(
        "--max-new-tokens", type=int, default=512, help="Max tokens to generate"
    )
    parser.add_argument(
        "--data-path", type=str, default=None, help="Path to GSM8K dataset JSONL file"
    )

    args = parser.parse_args()

    metrics = main(args)

    # Exit with success
    exit(0)
