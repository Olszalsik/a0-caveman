#!/usr/bin/env python3
"""
Caveman plugin - benchmark harness (real A/B, Agent Zero port).

Runs the same prompt set through two reference arms and one arm per
intensity level, then reports what caveman adds on top of a plain
"Answer concisely." instruction.

    baseline  no system prompt at all
    terse     control: "Answer concisely."   <- the comparison that matters
    <level>   the plugin's real injected prompt at that level

Why the terse control
---------------------
Measuring caveman against a verbose baseline conflates two different things:
the style prompt, and the fact that the user asked for brevity. Upstream
`evals/measure.py` reports savings *on top of the terse arm* for exactly this
reason, and that is what this harness does too. A level that only matches the
terse control has not earned its input-token cost.

What this does and does not establish
-------------------------------------
It measures output length under one model on one prompt set. It does not
prove semantic or technical equivalence, and it does not measure input-token
cost, cache behaviour, latency, or billing. See benchmarks/README.md and
upstream `docs/technical/accounting-and-evidence.md`.

There is no `--dry-run` number. The previous harness had one that fed the
prompt text back in as a synthetic "response" and then reported a reduction
that was algebraically equal to a hardcoded constant, so `--level full`
printed 65% forever whether or not a model was ever called.

Usage:
    python benchmarks/run.py --model gpt-4o-mini --output results.json
    python benchmarks/run.py --model gpt-4o-mini --levels full,ultra
    python benchmarks/run.py --repeats 3 --temperature 0.0
    python benchmarks/run.py --list
    python benchmarks/run.py --validate
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import platform
import statistics
import sys
import time
import types
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
PLUGIN_ROOT = HERE.parent
PROMPTS_FILE = HERE / "prompts.json"
PROMPTS_DIR = PLUGIN_ROOT / "prompts"

VALID_LEVELS = (
    "lite",
    "full",
    "ultra",
    "wenyan-lite",
    "wenyan-full",
    "wenyan-ultra",
)

# The control arm. Deliberately minimal: it is the "just be brief" baseline
# that a style prompt has to beat to be worth anything.
TERSE_CONTROL = "Answer concisely."


# ---------------------------------------------------------------------------
# Token counting
# ---------------------------------------------------------------------------


def _load_tokenizer():
    """Return (encode_fn, basis, detail) or (None, ...) when unavailable.

    `encode_fn` must itself return a token *count*: for tiktoken that means a
    wrapper around `Encoding.encode`, which returns a list.
    """
    try:
        import tiktoken
    except ImportError:
        return None, "inferred", "tiktoken not installed; char/4 estimate"
    try:
        encoding = tiktoken.get_encoding("o200k_base")
    except Exception as exc:  # pragma: no cover - network/cache issues
        return None, "inferred", f"tiktoken unavailable ({exc}); char/4 estimate"
    return (
        (lambda text: len(encoding.encode(text))),
        "measured",
        "tiktoken o200k_base (approximation of provider BPE)",
    )


class Counter:
    def __init__(self) -> None:
        self._encode, self.basis, self.detail = _load_tokenizer()
        self.uses_tiktoken = self._encode is not None

    def tokens(self, text: str) -> int:
        if not text:
            return 0
        if self._encode is not None:
            return self._encode(text)
        # Fallback. CJK is roughly one token per character for most modern
        # tokenizers, so a flat /4 badly undercounts the wenyan arms.
        cjk = sum(1 for ch in text if "㐀" <= ch <= "鿿")
        other = len(text) - cjk
        return cjk + (other + 3) // 4


# ---------------------------------------------------------------------------
# Prompts: the same fragments the system_prompt extension injects
# ---------------------------------------------------------------------------


def _agent_zero_root() -> str | None:
    """Walk up from this file until a directory containing `usr/` is found."""
    current = HERE
    while True:
        parent = current.parent
        if parent == current:
            return None
        if (parent / "usr").is_dir():
            return str(parent)
        current = parent


def _register_package(name: str, path: str | None) -> None:
    if name in sys.modules:
        return
    module = types.ModuleType(name)
    module.__path__ = [path] if path is not None else []
    sys.modules[name] = module


def _bootstrap_imports() -> None:
    """Make `usr.plugins.caveman.*` importable from either layout.

    Installed, the plugin sits at <a0>/usr/plugins/caveman/. In a standalone
    clone it is the repo root and there is no `usr/` package, so the same
    import fails. This file is documented to be runnable from a clone, so both
    layouts have to work.
    """
    root = _agent_zero_root()
    if root is not None:
        if root not in sys.path:
            sys.path.insert(0, root)
        return
    _register_package("usr", None)
    _register_package("usr.plugins", None)
    _register_package("usr.plugins.caveman", str(PLUGIN_ROOT))


_bootstrap_imports()


def build_system_prompt(level: str, auto_clarity: bool = True) -> str:
    """The exact text the system_prompt extension injects for this level.

    Delegates to `helpers.prompts`, the same module the extension uses, so the
    benchmark cannot drift from what is actually sent. The previous harness
    never called a model with any system prompt at all.
    """
    from usr.plugins.caveman.helpers import prompts as caveman_prompts

    return caveman_prompts.build_system_prompt(level, auto_clarity=auto_clarity)


# ---------------------------------------------------------------------------
# Model call
# ---------------------------------------------------------------------------


async def call_model(prompt: str, system: str | None, model: str, temperature: float,
                     max_tokens: int) -> tuple[str, dict | None, str | None]:
    """Return (text, provider_usage_or_None, error_or_None)."""
    try:
        import litellm
    except ImportError:
        return "", None, "litellm not installed"
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    try:
        response = await asyncio.to_thread(
            litellm.completion,
            model=model,
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
        )
    except Exception as exc:
        return "", None, f"{type(exc).__name__}: {exc}"
    try:
        text = response.choices[0].message.content or ""
    except Exception as exc:
        return "", None, f"unreadable response: {exc}"
    usage = None
    try:
        raw = getattr(response, "usage", None)
        if raw is not None:
            usage = raw.model_dump() if hasattr(raw, "model_dump") else dict(raw)
    except Exception:
        usage = None
    return text, usage, None


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def describe(values: list[float]) -> dict:
    if not values:
        return {"n": 0}
    return {
        "n": len(values),
        "median": statistics.median(values),
        "mean": statistics.fmean(values),
        "min": min(values),
        "max": max(values),
        "stdev": statistics.stdev(values) if len(values) > 1 else 0.0,
    }


def complete_prompt_ids(
    outputs: dict[str, dict[str, list[str]]],
    arms: tuple[str, ...] | list[str],
    expected_repeats: int,
) -> list[str]:
    """Return prompts with the same full sample count in every arm."""
    if not arms or expected_repeats < 1:
        return []
    candidates = set.intersection(
        *(set(outputs.get(arm, {})) for arm in arms)
    )
    return sorted(
        pid
        for pid in candidates
        if all(
            len(outputs.get(arm, {}).get(pid, [])) == expected_repeats
            for arm in arms
        )
    )


def pct(x: float) -> str:
    """Format a reduction ratio. Positive means shorter than the reference."""
    sign = "-" if x < 0 else "+"
    return f"{sign}{abs(x) * 100:.0f}%"


async def main_async() -> int:
    parser = argparse.ArgumentParser(description="Caveman A/B benchmark")
    parser.add_argument("--model", default="gpt-4o-mini", help="litellm model id")
    parser.add_argument("--levels", default=",".join(VALID_LEVELS))
    parser.add_argument("--prompt", type=int, default=None, help="run only the Nth prompt")
    parser.add_argument("--repeats", type=int, default=1, help="runs per arm per prompt")
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--max-tokens", type=int, default=800)
    parser.add_argument("--output", default=None, help="write raw results JSON here")
    parser.add_argument("--list", action="store_true", help="list prompts and exit")
    parser.add_argument(
        "--validate",
        action="store_true",
        help="check fixtures and prompt fragments only; no model call",
    )
    args = parser.parse_args()

    if args.repeats < 1:
        print("ERROR: --repeats must be at least 1", file=sys.stderr)
        return 1
    if args.max_tokens < 1:
        print("ERROR: --max-tokens must be at least 1", file=sys.stderr)
        return 1

    if not PROMPTS_FILE.is_file():
        print(f"ERROR: missing {PROMPTS_FILE}", file=sys.stderr)
        return 1
    fixture = json.loads(PROMPTS_FILE.read_text(encoding="utf-8"))
    prompts = fixture.get("prompts", [])

    if args.prompt is not None:
        if not 0 <= args.prompt < len(prompts):
            print(f"ERROR: --prompt out of range (0..{len(prompts) - 1})", file=sys.stderr)
            return 1
        prompts = [prompts[args.prompt]]

    if args.list:
        for i, p in enumerate(prompts):
            print(f"[{i}] {p.get('id')}  ({p.get('category')})")
        return 0

    levels = [lvl.strip() for lvl in args.levels.split(",") if lvl.strip()]
    bad = [lvl for lvl in levels if lvl not in VALID_LEVELS]
    if bad:
        print(f"ERROR: unknown level(s): {bad}. Valid: {list(VALID_LEVELS)}", file=sys.stderr)
        return 1

    # Arms: two references plus one per level.
    arms: dict[str, str | None] = {"__baseline__": None, "__terse__": TERSE_CONTROL}
    for lvl in levels:
        arms[lvl] = build_system_prompt(lvl)
    missing_fragments = [lvl for lvl in levels if not build_system_prompt(lvl)]
    if missing_fragments:
        print(
            f"ERROR: no prompt fragment for level(s) {missing_fragments}. "
            f"Expected {PROMPTS_DIR}/caveman.intensity.<level>.md",
            file=sys.stderr,
        )
        return 1

    if args.validate:
        print(f"fixtures: {len(prompts)} prompts (version {fixture.get('version')})")
        for name, system in arms.items():
            label = "none" if system is None else f"{len(system)} chars"
            print(f"  arm {name:>16}: system prompt {label}")
        print("OK: fixtures and prompt fragments are usable. No model was called.")
        return 0

    counter = Counter()
    print(f"model      : {args.model}")
    print(f"prompts    : {len(prompts)}   arms: {list(arms)}   repeats: {args.repeats}")
    print(f"tokenizer  : {counter.detail}  [basis: {counter.basis}]")
    print(f"temperature: {args.temperature}   max_tokens: {args.max_tokens}")
    print()

    # outputs[arm][prompt_id] -> list of texts (one per repeat)
    outputs: dict[str, dict[str, list[str]]] = {a: {} for a in arms}
    errors: list[str] = []
    provider_usage: dict[str, dict] = {a: {} for a in arms}
    started = time.time()

    for index, entry in enumerate(prompts):
        pid = entry.get("id") or f"prompt-{index}"
        text = entry.get("prompt", "")
        print(f"[{index + 1}/{len(prompts)}] {pid}")
        for arm, system in arms.items():
            for repeat in range(args.repeats):
                out, usage, error = await call_model(
                    text, system, args.model, args.temperature, args.max_tokens
                )
                if error:
                    errors.append(f"{pid}/{arm}#{repeat}: {error}")
                    print(f"    {arm:>16}: ERROR {error[:80]}")
                    continue
                outputs[arm].setdefault(pid, []).append(out)
                if usage:
                    provider_usage[arm].setdefault(pid, []).append(usage)  # type: ignore[arg-type]
                print(f"    {arm:>16}: {counter.tokens(out):>5} tok")

    elapsed = round(time.time() - started, 2)

    # ---- aggregate -------------------------------------------------------
    # Bail out before any division or fmean: with nothing comparable there is
    # no result to report, and statistics.fmean([]) raises.
    def arm_tokens(arm: str) -> dict[str, int]:
        return {
            pid: sum(counter.tokens(t) for t in texts)
            for pid, texts in outputs[arm].items()
        }

    token_map = {arm: arm_tokens(arm) for arm in arms}
    common = complete_prompt_ids(
        outputs, list(arms), expected_repeats=args.repeats
    )
    if not common:
        print()
        print("=" * 78)
        print("No prompt completed in every arm, so there is nothing to compare.")
        print(f"{len(errors)} call(s) failed:")
        for line in errors[:20]:
            print(f"  - {line}")
        if len(errors) > 20:
            print(f"  ... and {len(errors) - 20} more")
        print("=" * 78)
        return 1

    report: dict = {
        "metadata": {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "model": args.model,
            "temperature": args.temperature,
            "max_tokens": args.max_tokens,
            "repeats": args.repeats,
            "n_prompts": len(prompts),
            "fixture_version": fixture.get("version"),
            "tokenizer": counter.detail,
            "token_basis": counter.basis,
            "savings_basis": "benchmark_counterfactual",
            "platform": platform.platform(),
            "python": platform.python_version(),
            "elapsed_seconds": elapsed,
        },
        "reference_arms": {},
        "levels": {},
        "errors": errors,
        "raw": outputs,
        "provider_usage": provider_usage,
    }

    baseline_total = sum(token_map["__baseline__"].get(p, 0) for p in common)
    terse_total = sum(token_map["__terse__"].get(p, 0) for p in common)
    report["reference_arms"] = {
        "baseline": {
            "system_prompt": None,
            "total_tokens": baseline_total,
        },
        "terse": {
            "system_prompt": TERSE_CONTROL,
            "total_tokens": terse_total,
            "vs_baseline": 1 - (terse_total / baseline_total) if baseline_total else 0.0,
        },
    }

    for lvl in levels:
        per_prompt = []
        for pid in common:
            skill = token_map[lvl].get(pid, 0)
            terse = token_map["__terse__"].get(pid, 0)
            base = token_map["__baseline__"].get(pid, 0)
            per_prompt.append(
                {
                    "prompt_id": pid,
                    "level_tokens": skill,
                    "terse_tokens": terse,
                    "baseline_tokens": base,
                    "vs_terse": 1 - (skill / terse) if terse else 0.0,
                    "vs_baseline": 1 - (skill / base) if base else 0.0,
                }
            )
        input_tokens = counter.tokens(arms[lvl] or "")
        saved_mean = statistics.fmean(
            [r["vs_terse"] for r in per_prompt]
        ) * (terse_total / len(common) if common and terse_total else 0)
        report["levels"][lvl] = {
            "system_prompt_chars": len(arms[lvl] or ""),
            "input_tokens_per_turn": input_tokens,
            "total_tokens": sum(token_map[lvl].get(p, 0) for p in common),
            "vs_terse": describe([r["vs_terse"] for r in per_prompt]),
            "vs_baseline": describe([r["vs_baseline"] for r in per_prompt]),
            "input_cost": {
                "input_tokens_per_turn": input_tokens,
                "output_tokens_saved_per_turn": saved_mean,
                "net_tokens_per_turn": saved_mean - input_tokens,
                "break_even_output_tokens_per_turn": (
                    input_tokens / (terse_total / len(common))
                    if common and terse_total
                    else None
                ),
            },
            "per_prompt": per_prompt,
        }

    # ---- print -----------------------------------------------------------
    print()
    print("=" * 78)
    print("Reference arms (no caveman)")
    print("=" * 78)
    print(f"  baseline (no system prompt)          {baseline_total:>7} tok")
    print(
        f"  terse   ('{TERSE_CONTROL}'){'':<10} {terse_total:>7} tok  "
        f"({pct(1 - terse_total / baseline_total) if baseline_total else 'n/a'} shorter)"
    )
    print()
    print("Caveman levels - additional shortening on top of the terse control")
    print()
    print(f"  {'level':<16}{'median':>9}{'mean':>8}{'min':>8}{'max':>8}{'sd':>7}   tokens")
    for lvl, data in sorted(
        report["levels"].items(), key=lambda kv: -kv[1]["vs_terse"].get("median", 0)
    ):
        s = data["vs_terse"]
        print(
            f"  {lvl:<16}{pct(s.get('median', 0)):>9}{pct(s.get('mean', 0)):>8}"
            f"{pct(s.get('min', 0)):>8}{pct(s.get('max', 0)):>8}"
            f"{s.get('stdev', 0) * 100:>6.0f}%   {data['total_tokens']}"
        )

    # ---- the number that decides whether this is worth it ----------------
    # The old harness never measured the input side. On a chatty workload a
    # fixed prompt cost per turn can exceed the output reduction, which is
    # exactly the net-loss report in upstream issue #145.
    print()
    print("Input cost of the style prompt (measured on the exact text injected)")
    print()
    print(f"  {'level':<16}{'input tok/turn':>17}{'break-even out/turn':>21}{'net vs terse':>14}")
    per_prompt_mean = (terse_total / len(common)) if common else 0
    for lvl, data in sorted(report["levels"].items()):
        input_tokens = counter.tokens(arms[lvl] or "")
        saved_mean = data["vs_terse"].get("mean", 0.0) * per_prompt_mean
        net = saved_mean - input_tokens
        break_even = (
            (input_tokens / per_prompt_mean) if per_prompt_mean else float("inf")
        )
        print(
            f"  {lvl:<16}{input_tokens:>17}"
            f"{break_even:>20.0f}{pct(net / per_prompt_mean) if per_prompt_mean else 'n/a':>14}"
        )
    print()
    print(f"  break-even = output tokens that must be saved per turn to offset the")
    print(f"  input the style prompt adds. baseline arm averages {per_prompt_mean:.0f} tok/turn.")
    print("  [basis: measured on prompt text; provider cache behaviour not modelled]")

    print()
    print(f"  n = {len(common)} prompts x {args.repeats} repeat(s), tokens summed per repeat")
    print(f"  basis: {counter.basis} counting; savings = benchmark_counterfactual")
    print()
    print("  This measures output length only. It does not establish semantic or")
    print("  technical equivalence, cache behaviour, latency, or billing. Compare")
    print("  provider-billed totals on the same task with and without caveman.")

    if errors:
        print()
        print(f"  {len(errors)} call(s) failed and are EXCLUDED from the numbers above:")
        for line in errors[:10]:
            print(f"    - {line}")
        if len(errors) > 10:
            print(f"    ... and {len(errors) - 10} more")
        if not common:
            print()
            print("  No prompt completed in every arm, so there is nothing to compare.")
            return 1

    print("=" * 78)

    if args.output:
        Path(args.output).write_text(
            json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        print(f"raw results written to {args.output}")
        print("  (commit this file alongside the numbers you quote; upstream's bar is")
        print("   committed raw pairs plus separate review, not a headline percentage)")

    return 0


def main() -> int:
    try:
        return asyncio.run(main_async())
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
