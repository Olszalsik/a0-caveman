#!/usr/bin/env python3
"""
Caveman plugin - reproducible benchmarks for the Agent Zero port.

Port of the upstream caveman benchmark harness. Runs a fixed set of prompts
through the local LLM and measures:
 - chars_out (per response)
 - est_tokens (chars/4)
 - est_tokens_saved (per caveman level)
 - average reduction percentage

Usage:
 python /a0/usr/plugins/caveman/benchmarks/run.py [--prompt N] [--model litellm/...] [--output out.json]

Requires a live Agent Zero framework + litellm configured. Use --dry-run to
estimate savings without calling the LLM (useful for CI).
"""

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROMPTS_FILE = HERE / "prompts.json"

REDUCTION_FRACTION = {
    "lite": 0.30,
    "full": 0.65,
    "ultra": 0.80,
    "wenyan-lite": 0.55,
    "wenyan-full": 0.70,
    "wenyan-ultra": 0.80,
}


def load_prompts():
    with open(PROMPTS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def estimate(text: str, level: str) -> int:
    if not text:
        return 0
    return int((len(text) // 4) * REDUCTION_FRACTION.get(level, 0.65))


async def call_litellm(prompt: str, model: str) -> str:
    try:
        import litellm
    except ImportError:
        return ""
    try:
        r = await asyncio.to_thread(
            litellm.completion,
            model=model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=800,
        )
        return r.choices[0].message.content or ""
    except Exception as e:
        print(f"WARN: litellm call failed: {e}", file=sys.stderr)
        return ""


async def run_benchmark(prompts, model, level, dry_run):
    rows = []
    for p in prompts:
        prompt = p["prompt"]
        if dry_run:
            response = prompt[:600]  # synthetic
        else:
            response = await call_litellm(prompt, model)
        chars_out = len(response)
        est_tokens = chars_out // 4
        est_saved = estimate(response, level)
        rows.append({
            "id": p.get("id"),
            "task": p.get("task"),
            "chars_out": chars_out,
            "est_tokens": est_tokens,
            "est_tokens_saved": est_saved,
            "level": level,
        })
    return rows


def summarize(rows):
    if not rows:
        return {}
    total_chars = sum(r["chars_out"] for r in rows)
    total_saved = sum(r["est_tokens_saved"] for r in rows)
    return {
        "prompts": len(rows),
        "total_chars": total_chars,
        "total_est_tokens": total_chars // 4,
        "total_est_saved": total_saved,
        "avg_reduction_pct": (100.0 * total_saved / max(1, total_chars // 4)),
    }


async def main_async():
    parser = argparse.ArgumentParser(description="Run caveman benchmarks")
    parser.add_argument("--level", default="full", choices=list(REDUCTION_FRACTION.keys()))
    parser.add_argument("--model", default="gpt-4o-mini", help="litellm model id")
    parser.add_argument("--prompt", type=int, default=None, help="run only the Nth prompt")
    parser.add_argument("--output", default=None, help="write JSON results here")
    parser.add_argument("--dry-run", action="store_true", help="estimate without calling the LLM")
    args = parser.parse_args()

    prompts = load_prompts()["prompts"]
    if args.prompt is not None:
        if 0 <= args.prompt < len(prompts):
            prompts = [prompts[args.prompt]]
        else:
            print(f"ERROR: --prompt {args.prompt} out of range (0..{len(prompts)-1})")
            return 1

    print(f"Running {len(prompts)} prompts at level={args.level} model={args.model} dry_run={args.dry_run}")
    t0 = time.time()
    rows = await run_benchmark(prompts, args.model, args.level, args.dry_run)
    summary = summarize(rows)
    summary["elapsed_seconds"] = round(time.time() - t0, 2)
    summary["level"] = args.level
    summary["dry_run"] = args.dry_run

    print()
    print(f"Prompts:    {summary['prompts']}")
    print(f"Total chars: {summary['total_chars']}")
    print(f"Total est tokens: {summary['total_est_tokens']}")
    print(f"Total est saved: {summary['total_est_saved']}")
    print(f"Avg reduction: {summary['avg_reduction_pct']:.1f}%")
    print(f"Elapsed:     {summary['elapsed_seconds']}s")

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump({"summary": summary, "rows": rows}, f, indent=2)
        print(f"Wrote {args.output}")
    return 0


def main():
    try:
        sys.exit(asyncio.run(main_async()))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    main()
