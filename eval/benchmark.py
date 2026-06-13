"""
eval/benchmark.py

Automated benchmark that measures how effectively the humanizer
reduces AI detection scores across 20 test cases.

What it does:
1. Loads 20 known AI-generated text samples from test_cases.json
2. Scores each sample with GPTZero BEFORE humanization
3. Runs each sample through the humanizer backend
4. Scores each output with GPTZero AFTER humanization
5. Computes aggregate statistics
6. Writes a formatted results.md for the README

Usage:
    cd eval
    pip install requests python-dotenv tabulate
    python benchmark.py

Required environment variables (in ../.env or set in shell):
    GEMINI_API_KEY     — your Gemini key (used by the backend)
    GPTZERO_API_KEY    — get free at https://gptzero.me/api
    BACKEND_URL        — e.g. http://localhost:8000
    BENCHMARK_JWT      — a valid user JWT for auth (see note below)

Getting BENCHMARK_JWT:
    Sign into the app, open DevTools → Application → Cookies,
    find the Supabase access_token, paste it here.
    Or add a /api/dev/token endpoint temporarily during testing.

GPTZero API:
    Free tier: 150 requests/day — enough for this benchmark (40 calls: 20 before + 20 after).
    Sign up at https://gptzero.me and get your key under API Access.

Output:
    Prints a summary table to stdout.
    Writes full results to eval/results.md.
"""

import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

import requests
from dotenv import load_dotenv

# Load .env from project root
load_dotenv(Path(__file__).parent.parent / ".env")


# ── Config ─────────────────────────────────────────────────────────────────

BACKEND_URL     = os.getenv("BACKEND_URL",     "http://localhost:8000")
GEMINI_API_KEY  = os.getenv("GEMINI_API_KEY",  "")
GPTZERO_API_KEY = os.getenv("GPTZERO_API_KEY", "")
BENCHMARK_JWT   = os.getenv("BENCHMARK_JWT",   "")

GPTZERO_URL  = "https://api.gptzero.me/v2/predict/text"
HUMANIZE_URL = f"{BACKEND_URL}/api/humanize"

STRENGTH     = "medium"   # benchmark at medium strength
DELAY_SEC    = 1.5        # pause between API calls to respect rate limits

TEST_CASES_PATH = Path(__file__).parent / "test_cases.json"
RESULTS_PATH    = Path(__file__).parent / "results.md"


# ── Validation ─────────────────────────────────────────────────────────────

def validate_config():
    missing = []
    if not GPTZERO_API_KEY: missing.append("GPTZERO_API_KEY")
    if not BENCHMARK_JWT:   missing.append("BENCHMARK_JWT")

    if missing:
        print(f"\n[ERROR] Missing environment variables: {', '.join(missing)}")
        print("Set them in ../.env or export them in your shell.")
        print("\nSee the docstring at the top of this file for instructions.")
        sys.exit(1)

    if not TEST_CASES_PATH.exists():
        print(f"\n[ERROR] test_cases.json not found at {TEST_CASES_PATH}")
        sys.exit(1)

    print("✓ Config validated")


# ── GPTZero scoring ────────────────────────────────────────────────────────

def score_with_gptzero(text: str) -> dict:
    """
    Submits text to GPTZero and returns the prediction result.

    Returns:
        {
          "completely_generated_prob": float,  # 0.0–1.0 (key metric)
          "average_generated_prob":    float,
          "overall_burstiness":        float,
        }
    """
    headers = {
        "Accept":        "application/json",
        "Content-Type":  "application/json",
        "X-Api-Key":     GPTZERO_API_KEY,
    }
    payload = {
        "document": text,
        "version":  "2024-01-09",
    }

    response = requests.post(GPTZERO_URL, json=payload, headers=headers, timeout=30)
    response.raise_for_status()

    data     = response.json()
    document = data.get("documents", [{}])[0]

    return {
        "completely_generated_prob": document.get("completely_generated_prob", 0.0),
        "average_generated_prob":    document.get("average_generated_prob",    0.0),
        "overall_burstiness":        document.get("overall_burstiness",        0.0),
    }


# ── Humanize ───────────────────────────────────────────────────────────────

def humanize_text(text: str) -> str:
    """
    Sends text to the humanizer backend and collects the full streamed response.
    Uses the BENCHMARK_JWT for authentication.
    """
    headers = {
        "Authorization": f"Bearer {BENCHMARK_JWT}",
        "Content-Type":  "application/json",
        "Accept":        "text/event-stream",
    }
    payload = {
        "text":     text,
        "strength": STRENGTH,
        "profile":  None,  # generic humanization for benchmark consistency
    }

    response = requests.post(
        HUMANIZE_URL,
        json=payload,
        headers=headers,
        stream=True,
        timeout=60,
    )
    response.raise_for_status()

    # Collect SSE stream
    full_output = ""
    for line in response.iter_lines():
        if not line:
            continue
        decoded = line.decode("utf-8") if isinstance(line, bytes) else line
        if not decoded.startswith("data: "):
            continue
        raw = decoded[6:]
        try:
            chunk = json.loads(raw)
            if chunk.get("text"):
                full_output += chunk["text"]
            if chunk.get("done"):
                break
            if chunk.get("error"):
                raise RuntimeError(f"Stream error: {chunk['error']}")
        except json.JSONDecodeError:
            continue

    return full_output.strip()


# ── Main benchmark loop ────────────────────────────────────────────────────

def run_benchmark():
    print(f"\n{'═' * 60}")
    print(f"  Humanizer Benchmark  —  {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"{'═' * 60}")
    print(f"  Strength : {STRENGTH}")
    print(f"  Backend  : {BACKEND_URL}")
    print(f"{'═' * 60}\n")

    # Load test cases
    with open(TEST_CASES_PATH) as f:
        test_cases = json.load(f)

    results = []
    errors  = []

    for i, case in enumerate(test_cases, 1):
        case_id = case["id"]
        domain  = case["domain"]
        topic   = case["topic"]
        text    = case["text"]

        print(f"[{i:02d}/{len(test_cases)}] {case_id} ({domain})")

        # ── Score BEFORE ──────────────────────────────────────
        print(f"         → Scoring original with GPTZero…", end=" ", flush=True)
        try:
            before = score_with_gptzero(text)
            print(f"{before['completely_generated_prob']:.1%}")
        except Exception as e:
            print(f"FAILED: {e}")
            errors.append({"id": case_id, "stage": "score_before", "error": str(e)})
            continue

        time.sleep(DELAY_SEC)

        # ── Humanize ──────────────────────────────────────────
        print(f"         → Humanizing…", end=" ", flush=True)
        try:
            output = humanize_text(text)
            word_count = len(output.split())
            print(f"done ({word_count} words)")
        except Exception as e:
            print(f"FAILED: {e}")
            errors.append({"id": case_id, "stage": "humanize", "error": str(e)})
            continue

        time.sleep(DELAY_SEC)

        # ── Score AFTER ───────────────────────────────────────
        print(f"         → Scoring output with GPTZero…", end=" ", flush=True)
        try:
            after = score_with_gptzero(output)
            print(f"{after['completely_generated_prob']:.1%}")
        except Exception as e:
            print(f"FAILED: {e}")
            errors.append({"id": case_id, "stage": "score_after", "error": str(e)})
            continue

        time.sleep(DELAY_SEC)

        # ── Store result ──────────────────────────────────────
        reduction = before["completely_generated_prob"] - after["completely_generated_prob"]
        passed    = after["completely_generated_prob"] < 0.20

        results.append({
            "id":               case_id,
            "domain":           domain,
            "topic":            topic,
            "before":           before["completely_generated_prob"],
            "after":            after["completely_generated_prob"],
            "reduction":        reduction,
            "burstiness_after": after["overall_burstiness"],
            "passed":           passed,
            "output_preview":   output[:120] + "…" if len(output) > 120 else output,
        })

        status = "✓ PASS" if passed else "  fail"
        print(f"         {status}  {before['completely_generated_prob']:.1%} → {after['completely_generated_prob']:.1%}  (↓ {reduction:.1%})\n")

    return results, errors


# ── Statistics ─────────────────────────────────────────────────────────────

def compute_stats(results: list) -> dict:
    if not results:
        return {}

    befores    = [r["before"]    for r in results]
    afters     = [r["after"]     for r in results]
    reductions = [r["reduction"] for r in results]
    passed     = [r for r in results if r["passed"]]

    def avg(lst): return sum(lst) / len(lst) if lst else 0

    # By domain
    domains = {}
    for r in results:
        d = r["domain"]
        if d not in domains:
            domains[d] = []
        domains[d].append(r)

    domain_stats = {
        d: {
            "count":       len(items),
            "avg_before":  avg([i["before"]    for i in items]),
            "avg_after":   avg([i["after"]     for i in items]),
            "avg_reduction": avg([i["reduction"] for i in items]),
            "pass_rate":   sum(1 for i in items if i["passed"]) / len(items),
        }
        for d, items in domains.items()
    }

    return {
        "n":                  len(results),
        "avg_before":         avg(befores),
        "avg_after":          avg(afters),
        "avg_reduction":      avg(reductions),
        "max_reduction":      max(reductions),
        "min_reduction":      min(reductions),
        "pass_count":         len(passed),
        "pass_rate":          len(passed) / len(results),
        "domain_stats":       domain_stats,
    }


# ── Write results.md ───────────────────────────────────────────────────────

def write_results_md(results: list, stats: dict, errors: list):
    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    lines = [
        "# Benchmark Results",
        "",
        f"**Run date:** {now}  ",
        f"**Strength:** {STRENGTH}  ",
        f"**Total cases:** {stats['n']}  ",
        f"**Backend:** {BACKEND_URL}",
        "",
        "---",
        "",
        "## Summary",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| Avg GPTZero score — before | {stats['avg_before']:.1%} |",
        f"| Avg GPTZero score — after  | {stats['avg_after']:.1%} |",
        f"| Avg score reduction        | {stats['avg_reduction']:.1%} |",
        f"| Max reduction (single case)| {stats['max_reduction']:.1%} |",
        f"| Cases below 20% AI prob    | {stats['pass_count']} / {stats['n']} |",
        f"| Pass rate                  | {stats['pass_rate']:.1%} |",
        "",
        "---",
        "",
        "## Results by domain",
        "",
        "| Domain | Cases | Avg before | Avg after | Avg reduction | Pass rate |",
        "|---|---|---|---|---|---|",
    ]

    for domain, ds in stats["domain_stats"].items():
        lines.append(
            f"| {domain} | {ds['count']} "
            f"| {ds['avg_before']:.1%} "
            f"| {ds['avg_after']:.1%} "
            f"| {ds['avg_reduction']:.1%} "
            f"| {ds['pass_rate']:.0%} |"
        )

    lines += [
        "",
        "---",
        "",
        "## Per-case results",
        "",
        "| # | ID | Domain | Before | After | Reduction | Pass |",
        "|---|---|---|---|---|---|---|",
    ]

    for i, r in enumerate(results, 1):
        tick = "✓" if r["passed"] else "✗"
        lines.append(
            f"| {i} | {r['id']} | {r['domain']} "
            f"| {r['before']:.1%} | {r['after']:.1%} "
            f"| {r['reduction']:.1%} | {tick} |"
        )

    lines += [
        "",
        "---",
        "",
        "## Output previews",
        "",
    ]

    for r in results:
        lines += [
            f"### {r['id']} — {r['topic']}",
            "",
            f"**Before:** {r['before']:.1%} AI probability  ",
            f"**After:** {r['after']:.1%} AI probability",
            "",
            f"> {r['output_preview']}",
            "",
        ]

    if errors:
        lines += [
            "---",
            "",
            "## Errors",
            "",
            f"{len(errors)} case(s) encountered errors:",
            "",
        ]
        for e in errors:
            lines.append(f"- `{e['id']}` at stage `{e['stage']}`: {e['error']}")
        lines.append("")

    lines += [
        "---",
        "",
        "*Generated by `eval/benchmark.py`*",
    ]

    RESULTS_PATH.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n✓ Results written to {RESULTS_PATH}")


# ── Print summary to stdout ────────────────────────────────────────────────

def print_summary(stats: dict, errors: list):
    print(f"\n{'═' * 60}")
    print(f"  BENCHMARK COMPLETE")
    print(f"{'═' * 60}")
    print(f"  Cases run        : {stats.get('n', 0)}")
    print(f"  Errors           : {len(errors)}")
    print(f"  Avg score before : {stats.get('avg_before', 0):.1%}")
    print(f"  Avg score after  : {stats.get('avg_after', 0):.1%}")
    print(f"  Avg reduction    : {stats.get('avg_reduction', 0):.1%}")
    print(f"  Pass rate (<20%) : {stats.get('pass_count', 0)}/{stats.get('n', 0)}  ({stats.get('pass_rate', 0):.1%})")
    print(f"{'═' * 60}\n")

    # README bullet for copy-paste
    print("  README bullet point:")
    print(f"  \"Achieves {stats.get('avg_reduction', 0):.0%} average GPTZero score reduction")
    print(f"   across {stats.get('n', 0)} test cases; {stats.get('pass_count', 0)}/{stats.get('n', 0)} samples")
    print(f"   score below the 20% AI-probability threshold.\"")
    print()


# ── Entry point ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    validate_config()

    results, errors = run_benchmark()

    if not results:
        print("\n[ERROR] No results collected. Check errors above.")
        sys.exit(1)

    stats = compute_stats(results)
    print_summary(stats, errors)
    write_results_md(results, stats, errors)

    # Update README benchmark table with real numbers
    readme_path = Path(__file__).parent.parent / "README.md"
    if readme_path.exists():
        content = readme_path.read_text()
        # Replace the blank table values with real ones
        replacements = {
            "| Avg GPTZero score (before) | — |":
                f"| Avg GPTZero score (before) | {stats['avg_before']:.1%} |",
            "| Avg GPTZero score (after) | — |":
                f"| Avg GPTZero score (after) | {stats['avg_after']:.1%} |",
            "| Avg score reduction | — |":
                f"| Avg score reduction | {stats['avg_reduction']:.1%} |",
            "| Cases below 20% AI probability | — / 20 |":
                f"| Cases below 20% AI probability | {stats['pass_count']} / {stats['n']} |",
        }
        for old, new in replacements.items():
            content = content.replace(old, new)
        readme_path.write_text(content)
        print(f"✓ README.md benchmark table updated with real results")
