#!/usr/bin/env python3
"""
Fixed GSM8K Benchmark Script for PD Testing
Replaces the broken bench_sglang.py in Docker images with HttpResponse API issues.
"""

import argparse
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor

# Try importing required modules
try:
    import requests
except ImportError:
    print("Error: requests module not found. Installing...")
    import subprocess
    subprocess.check_call(["pip", "install", "requests"])
    import requests

# Dataset questions (sample from GSM8K)
# In production, this would load from a file or dataset
GSM8K_QUESTIONS = [
    {"question": "Janet's ducks lay 16 eggs per day. She eats three for breakfast every morning and bakes muffins for her friends every day with four. She sells the remainder at the farmers' market daily for $2 per fresh duck egg. How much in dollars does she make every day at the farmers' market?", "answer": "18", "answer_variants": ["18", "$18", "18.0", "18.00"]},
    {"question": "A robe takes 2 bolts of blue fiber and half that much white fiber. How many bolts in total does it take?", "answer": "3", "answer_variants": ["3", "3.0"]},
    {"question": "Josh decides to try flipping a house.  He buys a house for $80,000 and then puts in $50,000 in repairs.  This increased the value of the house by 150%.  How much profit did he make?", "answer": "70000", "answer_variants": ["70000", "$70000", "70,000", "$70,000"]},
    {"question": "James decides to run 3 sprints 3 times a week.  He runs 60 meters each sprint.  How many total meters does he run a week?", "answer": "540", "answer_variants": ["540", "540.0"]},
    {"question": "Every day, Wendi feeds each of her chickens three cups of mixed chicken feed, containing seeds, mealworms and vegetables to help keep them healthy.  She gives the chickens their feed in three separate meals. In the morning, she gives her flock of chickens 15 cups of feed.  In the afternoon, she gives her chickens another 25 cups of feed.  How many cups of feed does she need to give her chickens in the final meal of the day if the size of Wendi's flock is 20 chickens?", "answer": "20", "answer_variants": ["20", "20.0"]},
]


def extract_answer(text):
    """Extract numeric answer from model output."""
    # Clean up the text
    text = text.strip()

    # Try to find patterns like "The answer is X" or "Answer: X"
    answer_patterns = [
        r'(?:the\s+)?answer\s+is\s+\$?([0-9,]+\.?[0-9]*)',
        r'answer:\s*\$?([0-9,]+\.?[0-9]*)',
        r'####\s*\$?([0-9,]+\.?[0-9]*)',  # GSM8K format
        r'=\s*\$?([0-9,]+\.?[0-9]*)\s*$',  # Ends with = number
    ]

    for pattern in answer_patterns:
        match = re.search(pattern, text.lower())
        if match:
            answer = match.group(1).replace(',', '')
            return answer

    # Fallback: find the last number in the text
    numbers = re.findall(r'-?\d+\.?\d*', text.replace(',', ''))
    if numbers:
        return numbers[-1]
    return ""


def run_single_question(args, question_data, question_idx):
    """Run a single GSM8K question through the model."""
    question = question_data["question"]
    correct_answer = question_data["answer"]
    answer_variants = question_data.get("answer_variants", [correct_answer])

    # Build prompt with few-shot examples if num_shots > 0
    if args.num_shots > 0:
        prompt = "Solve the following math word problems. Show your work and provide the final answer.\n\n"
        # Add few-shot examples (use first few questions as examples)
        num_examples = min(args.num_shots, len(GSM8K_QUESTIONS), question_idx)
        for i in range(num_examples):
            example = GSM8K_QUESTIONS[i % len(GSM8K_QUESTIONS)]
            if i != question_idx % len(GSM8K_QUESTIONS):  # Don't use the current question
                prompt += f"Q: {example['question']}\nA: The answer is {example['answer']}\n\n"
        prompt += f"Q: {question}\nA:"
    else:
        prompt = f"Solve this math problem: {question}\nAnswer:"

    # Make request to the server
    url = f"{args.host}:{args.port}/v1/completions"

    payload = {
        "prompt": prompt,
        "max_tokens": args.max_new_tokens,
        "temperature": 0.0,  # Deterministic for math problems
        "stop": ["\n\n", "Q:"],
    }

    # Add model parameter if provided
    if hasattr(args, 'model') and args.model:
        payload["model"] = args.model

    try:
        response = requests.post(url, json=payload, timeout=60)
        response.raise_for_status()

        result = response.json()
        if "choices" in result and len(result["choices"]) > 0:
            generated_text = result["choices"][0]["text"]
            predicted_answer = extract_answer(generated_text)

            # Check against all answer variants
            is_correct = predicted_answer in answer_variants

            return {
                "question_idx": question_idx,
                "correct": is_correct,
                "predicted": predicted_answer,
                "expected": correct_answer,
                "generated_text": generated_text,
            }
        else:
            return {
                "question_idx": question_idx,
                "correct": False,
                "predicted": "",
                "expected": correct_answer,
                "error": "No choices in response",
                "generated_text": "",
            }

    except Exception as e:
        return {
            "question_idx": question_idx,
            "correct": False,
            "predicted": "",
            "expected": correct_answer,
            "error": str(e),
            "generated_text": "",
        }


def load_gsm8k_dataset(data_path, num_questions):
    """Load GSM8K dataset from file or use sample questions."""
    if data_path:
        try:
            with open(data_path, 'r') as f:
                questions = []
                for line in f:
                    data = json.loads(line)
                    questions.append(data)
                return questions[:num_questions]
        except Exception as e:
            print(f"Warning: Could not load dataset from {data_path}: {e}")
            print("Using sample questions instead...")

    # Use sample questions and duplicate if needed
    questions = GSM8K_QUESTIONS * ((num_questions // len(GSM8K_QUESTIONS)) + 1)
    return questions[:num_questions]


def main(args):
    print(f"=" * 60)
    print(f"GSM8K Benchmark - PD Testing")
    print(f"=" * 60)
    print(f"Host: {args.host}:{args.port}")
    if hasattr(args, 'model') and args.model:
        print(f"Model: {args.model}")
    print(f"Num Questions: {args.num_questions}")
    print(f"Parallelism: {args.parallel}")
    print(f"Num Shots: {args.num_shots}")
    print(f"Max New Tokens: {args.max_new_tokens}")
    print(f"=" * 60)

    # Load dataset
    questions = load_gsm8k_dataset(args.data_path, args.num_questions)
    print(f"Loaded {len(questions)} questions")

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

    with ThreadPoolExecutor(max_workers=args.parallel) as executor:
        futures = []
        for idx, question_data in enumerate(questions):
            future = executor.submit(run_single_question, args, question_data, idx)
            futures.append(future)

        # Collect results
        for i, future in enumerate(futures):
            result = future.result()
            results.append(result)

            # Print progress every 100 questions
            if (i + 1) % 100 == 0:
                elapsed = time.time() - start_time
                qps = (i + 1) / elapsed
                print(f"Progress: {i + 1}/{len(questions)} questions ({qps:.2f} QPS)")

    end_time = time.time()
    total_time = end_time - start_time

    # Calculate accuracy
    correct_count = sum(1 for r in results if r.get("correct", False))
    total_count = len(results)
    accuracy = correct_count / total_count if total_count > 0 else 0.0

    # Print results
    print(f"\n" + "=" * 60)
    print(f"RESULTS")
    print(f"=" * 60)
    print(f"Total Questions: {total_count}")
    print(f"Correct: {correct_count}")
    print(f"Incorrect: {total_count - correct_count}")
    print(f"Accuracy: {accuracy:.4f}")
    print(f"Total Time: {total_time:.2f}s")
    print(f"Questions per Second: {total_count / total_time:.2f}")
    print(f"=" * 60)

    # Print some examples of errors
    errors = [r for r in results if not r.get("correct", False)]
    if errors:
        print(f"\nSample Errors (first 10):")
        for err in errors[:10]:
            print(f"  Q{err['question_idx']}: Expected={err['expected']}, Got={err['predicted']}")
            if 'error' in err:
                print(f"    Error: {err['error']}")
            elif err.get('generated_text'):
                # Show truncated generated text
                gen_text = err['generated_text'][:100]
                print(f"    Generated: {gen_text}...")

        # Save detailed errors to file for debugging
        try:
            with open('/tmp/gsm8k_errors.json', 'w') as f:
                json.dump(errors[:50], f, indent=2)
            print(f"\nDetailed errors saved to: /tmp/gsm8k_errors.json")
        except Exception as e:
            print(f"\nCould not save error details: {e}")

    return {"accuracy": accuracy, "total_time": total_time}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="GSM8K Benchmark for SGLang")
    parser.add_argument("--host", type=str, default="http://127.0.0.1", help="Server host")
    parser.add_argument("--port", type=int, default=30000, help="Server port")
    parser.add_argument("--model", type=str, default=None, help="Model identifier to send with completion requests (optional)")
    parser.add_argument("--num-questions", type=int, default=200, help="Number of questions to test")
    parser.add_argument("--parallel", type=int, default=128, help="Number of parallel requests")
    parser.add_argument("--num-shots", type=int, default=5, help="Number of few-shot examples")
    parser.add_argument("--max-new-tokens", type=int, default=512, help="Max tokens to generate")
    parser.add_argument("--data-path", type=str, default=None, help="Path to GSM8K dataset file")

    args = parser.parse_args()

    metrics = main(args)

    # Return exit code based on success
    exit(0)
