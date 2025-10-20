#!/usr/bin/env python3
"""
GSM8K Benchmark Script for PD Testing
Based on sglang/python/sglang/test/few_shot_gsm8k.py implementation
"""

import argparse
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
from typing import List, Dict

# Try importing required modules
try:
    import requests
except ImportError:
    print("Error: requests module not found. Installing...")
    import subprocess
    subprocess.check_call(["pip", "install", "requests"])
    import requests


def read_jsonl(file_path):
    """Read JSONL file."""
    examples = []
    with open(file_path, 'r', encoding='utf-8') as f:
        for line in f:
            examples.append(json.loads(line))
    return examples


def get_answer_value(answer_str):
    """
    Extract numeric answer from answer string.
    Based on sglang's implementation.
    """
    # Remove commas from numbers
    answer_str = answer_str.replace(',', '')
    # Find all numbers
    numbers = re.findall(r'\d+', answer_str)
    if numbers:
        # Return the last number (this is the final answer)
        return numbers[-1]
    return "INVALID"


def get_one_example(question, answer=None, simplified=False):
    """
    Format a single example in GSM8K format.

    Args:
        question: The question text
        answer: The full answer (with reasoning) or None
        simplified: If True, extract only the final number (for PD with token limits)
    """
    if answer is None:
        return f"Question: {question}\nAnswer:"
    else:
        # For few-shot examples, optionally use simplified answer
        if simplified:
            # Extract just the final number after ####
            final_answer = get_answer_value(answer)
            return f"Question: {question}\nAnswer: {final_answer}"
        else:
            return f"Question: {question}\nAnswer: {answer}"


def build_few_shot_prompt(examples: List[Dict], num_shots: int, test_question: str, simplified: bool = True) -> str:
    """
    Build few-shot prompt with examples.

    Args:
        examples: List of example questions/answers
        num_shots: Number of few-shot examples to include
        test_question: The question to answer
        simplified: If True, use only final numbers in examples (for PD token limits)
    """
    prompt_parts = []

    # Add few-shot examples (simplified to avoid token limits in PD)
    for i in range(num_shots):
        example = examples[i]
        prompt_parts.append(get_one_example(example['question'], example['answer'], simplified=simplified))

    # Add the test question
    prompt_parts.append(get_one_example(test_question))

    return "\n\n".join(prompt_parts)


def download_gsm8k_dataset():
    """Download GSM8K dataset from GitHub."""
    url = "https://raw.githubusercontent.com/openai/grade-school-math/master/grade_school_math/data/test.jsonl"

    print(f"Downloading GSM8K dataset from {url}...")
    try:
        response = requests.get(url, timeout=30)
        response.raise_for_status()

        # Parse JSONL
        examples = []
        for line in response.text.strip().split('\n'):
            if line:
                examples.append(json.loads(line))

        print(f"✓ Downloaded {len(examples)} questions")
        return examples
    except Exception as e:
        print(f"✗ Failed to download dataset: {e}")
        return None


def load_gsm8k_dataset(data_path, num_questions):
    """Load GSM8K dataset from file or download."""
    if data_path:
        try:
            print(f"Loading GSM8K dataset from {data_path}...")
            examples = read_jsonl(data_path)
            print(f"✓ Loaded {len(examples)} questions from file")
            return examples[:num_questions]
        except Exception as e:
            print(f"Warning: Could not load dataset from {data_path}: {e}")
            print("Attempting to download dataset...")

    # Try to download
    examples = download_gsm8k_dataset()
    if examples:
        return examples[:num_questions]

    # Fallback to sample data
    print("Warning: Using minimal sample dataset")
    return [
        {
            "question": "Janet's ducks lay 16 eggs per day. She eats three for breakfast every morning and bakes muffins for her friends every day with four. She sells the remainder at the farmers' market daily for $2 per fresh duck egg. How much in dollars does she make every day at the farmers' market?",
            "answer": "Let's calculate step by step:\n- Janet has 16 eggs\n- She eats 3 for breakfast\n- She uses 4 for muffins\n- Remaining eggs: 16 - 3 - 4 = 9\n- She sells them for $2 each\n- Total: 9 × $2 = $18\n#### 18"
        },
        {
            "question": "A robe takes 2 bolts of blue fiber and half that much white fiber. How many bolts in total does it take?",
            "answer": "Blue fiber: 2 bolts\nWhite fiber: 2/2 = 1 bolt\nTotal: 2 + 1 = 3 bolts\n#### 3"
        }
    ] * (num_questions // 2 + 1)


def run_single_question(args, examples, question_idx):
    """Run a single GSM8K question through the model."""
    test_example = examples[question_idx]
    question = test_example["question"]
    ground_truth_answer = test_example["answer"]

    # Extract the numeric answer from ground truth
    expected_answer = get_answer_value(ground_truth_answer)

    # Build prompt with few-shot examples
    # Use examples before the current question
    few_shot_examples = examples[:args.num_shots]
    # Use simplified examples (just final numbers) to avoid token limits in PD disaggregation
    prompt = build_few_shot_prompt(few_shot_examples, args.num_shots, question, simplified=True)

    # Make request to the server
    url = f"{args.host}:{args.port}/v1/completions"

    payload = {
        "model": args.model,
        "prompt": prompt,
        "max_tokens": args.max_new_tokens,
        "temperature": 0.0,
        "stop": ["\n\n", "Question:"],
    }

    try:
        response = requests.post(url, json=payload, timeout=120)
        response.raise_for_status()

        result = response.json()
        if "choices" in result and len(result["choices"]) > 0:
            generated_text = result["choices"][0]["text"]
            predicted_answer = get_answer_value(generated_text)

            is_correct = predicted_answer == expected_answer
            is_invalid = predicted_answer == "INVALID"

            return {
                "question_idx": question_idx,
                "correct": is_correct,
                "invalid": is_invalid,
                "predicted": predicted_answer,
                "expected": expected_answer,
                "generated_text": generated_text[:200],  # Truncate for storage
                "question": question[:100],  # Truncate
            }
        else:
            return {
                "question_idx": question_idx,
                "correct": False,
                "invalid": True,
                "predicted": "INVALID",
                "expected": expected_answer,
                "error": "No choices in response",
                "question": question[:100],
            }

    except Exception as e:
        return {
            "question_idx": question_idx,
            "correct": False,
            "invalid": True,
            "predicted": "INVALID",
            "expected": expected_answer,
            "error": str(e),
            "question": question[:100],
        }


def main(args):
    print(f"=" * 70)
    print(f"GSM8K Benchmark - PD Testing (Simplified for PD)")
    print(f"=" * 70)
    print(f"Host: {args.host}:{args.port}")
    print(f"Model: {args.model}")
    print(f"Num Questions: {args.num_questions}")
    print(f"Parallelism: {args.parallel}")
    print(f"Num Shots: {args.num_shots}")
    print(f"Max New Tokens: {args.max_new_tokens}")
    print(f"Note: Using simplified few-shot (final numbers only) for PD token limits")
    print(f"=" * 70)

    # Load dataset
    examples = load_gsm8k_dataset(args.data_path, args.num_questions + args.num_shots)

    if not examples:
        print("Error: Could not load dataset")
        return {"accuracy": 0.0, "error": "No dataset"}

    print(f"Loaded {len(examples)} examples")

    # Check server health
    try:
        health_url = f"{args.host}:{args.port}/health"
        response = requests.get(health_url, timeout=5)
        if response.status_code == 200:
            print(f"✓ Server is healthy")
        else:
            print(f"Warning: Server health check returned status {response.status_code}")
    except Exception as e:
        print(f"Warning: Could not check server health: {e}")
        print("Continuing anyway...")

    # Run benchmark
    start_time = time.time()
    results = []

    print(f"\nRunning benchmark with {args.parallel} parallel requests...")
    print(f"Testing questions {args.num_shots} to {args.num_shots + args.num_questions - 1}")

    with ThreadPoolExecutor(max_workers=args.parallel) as executor:
        futures = []
        # Start from num_shots to avoid using test questions as examples
        for idx in range(args.num_shots, args.num_shots + args.num_questions):
            future = executor.submit(run_single_question, args, examples, idx)
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
                print(f"Progress: {i + 1}/{len(futures)} questions ({qps:.2f} QPS, Acc: {acc_so_far:.4f})")

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
            print(f"\n{i+1}. Q{err['question_idx']}: {err['question']}")
            print(f"   Expected: {err['expected']}, Got: {err['predicted']}")
            if err.get('error'):
                print(f"   Error: {err['error']}")
            elif err.get('generated_text'):
                print(f"   Generated: {err['generated_text']}")

        # Save detailed errors
        try:
            error_file = "/tmp/gsm8k_errors_detailed.json"
            with open(error_file, 'w') as f:
                json.dump(errors[:50], f, indent=2)
            print(f"\n✓ Detailed errors saved to: {error_file}")
        except Exception as e:
            print(f"\n✗ Could not save error details: {e}")

    # Print some correct examples too
    correct = [r for r in results if r.get("correct", False)]
    if correct:
        print(f"\nSample Correct Answers (first 3):")
        for i, corr in enumerate(correct[:3]):
            print(f"\n{i+1}. Q{corr['question_idx']}: {corr['question']}")
            print(f"   Expected: {corr['expected']}, Got: {corr['predicted']} ✓")

    return {"accuracy": accuracy, "total_time": total_time, "invalid_rate": invalid_rate}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="GSM8K Benchmark for SGLang PD")
    parser.add_argument("--host", type=str, default="http://127.0.0.1", help="Server host")
    parser.add_argument("--port", type=int, default=30000, help="Server port")
    parser.add_argument("--model", type=str, required=True, help="Model identifier to send with completion requests")
    parser.add_argument("--num-questions", type=int, default=200, help="Number of questions to test")
    parser.add_argument("--parallel", type=int, default=128, help="Number of parallel requests")
    parser.add_argument("--num-shots", type=int, default=5, help="Number of few-shot examples")
    parser.add_argument("--max-new-tokens", type=int, default=512, help="Max tokens to generate")
    parser.add_argument("--data-path", type=str, default=None, help="Path to GSM8K dataset JSONL file")

    args = parser.parse_args()

    metrics = main(args)

    # Exit with success
    exit(0)
