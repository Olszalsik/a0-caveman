# Caveman benchmarks (Agent Zero port)

Reproducible measurement of what the caveman style prompt actually does, run
against a real model.

## Quick start

```bash
# Check fixtures and prompt fragments. No model call, no invented numbers.
python benchmarks/run.py --validate

python benchmarks/run.py --list
python benchmarks/run.py --model gpt-4o-mini --output results.json
python benchmarks/run.py --model gpt-4o-mini --levels full,ultra --repeats 3
python benchmarks/run.py --model gpt-4o-mini --repeats 3 --lean
```

There is no `--dry-run` output number. The previous harness had one that fed
the prompt text back in as a synthetic "response" and then reported a
reduction algebraically equal to a hardcoded constant, so `--level full`
printed 65% forever whether or not a model was ever called.

## What was wrong with the previous harness

- It never used the plugin's prompts. It sent a bare user message with no
  system prompt at all, so it was not measuring caveman.
- It had no control arm. There was nothing to compare the treatment against.
- `avg_reduction_pct = 100 * total_saved / total_est_tokens`, where
  `est_saved = est_tokens * REDUCTION_FRACTION[level]` and
  `est_tokens = chars // 4`. That expression simplifies to
  `REDUCTION_FRACTION[level] * 100` for any input, so the reported figure was
  the constant it started from.
- The `REDUCTION_FRACTION` table was upstream's original 65% headline, which
  upstream has since retracted (`docs/HONEST-NUMBERS.md`: output reduction
  "Not published"; "earlier stats releases applied a fixed 65% output ratio
  without a committed reviewed result").

## Method

**Arms.** For every prompt, the same model is called once per arm:

| Arm | System prompt |
|---|---|
| `__baseline__` | none |
| `__terse__` | `Answer concisely.` |
| `<level>` | the plugin's real fragments for that level |
| `<level>-lean` *(with `--lean`)* | the compact style variant (`lean_style_prompt: true`) |

The level arms are built by `build_system_prompt()`, which reads the same
files the `system_prompt` extension injects at runtime. A missing fragment
fails the run rather than silently measuring nothing.

**The terse control is the comparison that matters.** Measuring against a
verbose baseline conflates the style prompt with the fact that the user asked
for brevity. Upstream `evals/measure.py` reports savings *on top of the terse
arm* for this reason, and so does this harness. A level that merely matches
`Answer concisely.` has not justified its input-token cost.

**Counting.** `tiktoken` `o200k_base` when installed, otherwise a character
estimate that weights CJK at roughly one token per character (a flat `chars/4`
badly undercounts the three `wenyan-*` arms). The report prints which basis
was used.

**Statistics.** Median, mean, min, max and stdev per level, so a number can be
seen to be solid or noisy. Single runs have stdev 0 by construction; use
`--repeats` for a real spread.

**Input cost.** The style prompt is re-sent on every turn, so the harness also
counts its tokens and reports a break-even: how many output tokens per turn
must be saved to offset the input the prompt adds. On the current prompt
fragments that is roughly 750-850 input tokens per turn (measured on the shipped text, 713 for wenyan-ultra through 821 for ultra). On terse Q&A that is a
real cost, and it is the number that decides whether a level is worth
enabling for a given workload.

**Billed usage.** The 1:1 content-token tables above treat input and output
tokens as equally priced. Real pricing weights output at roughly 3-5x input,
so the harness re-scores the provider's own usage counters at a configurable
ratio: `--io-price 4.0` (default) means one output token costs four input
tokens. The billed table reports, per arm, `cost = prompt_tokens +
completion_tokens * io_price` and the percentage against the terse control,
from the provider's billed counters rather than local counts. Two things the
content tables miss:

- **Reasoning tokens.** Reasoning models bill thinking as completion tokens.
  `reasoning_tokens_est` (billed completion minus locally counted content)
  makes that budget visible; a style prompt can make a reasoning model think
  *longer* on some prompts, which the content tables read as a saving.
- **Reasoning-model caveat.** On a reasoning model, trust the billed table;
  the content-token table overstates savings. A non-reasoning model with the
  same prompt set is the cleaner comparison.

## What a result does and does not establish

Per upstream `docs/technical/accounting-and-evidence.md`, every number here
carries a basis:

- token counts: `measured` (tiktoken) or `inferred` (char estimate)
- savings: `benchmark_counterfactual`

It establishes output length under one model on one prompt set. It does
**not** establish semantic or technical equivalence, input-token cost under
real cache behaviour, latency, or billing. One fixture result supports only
that fixture and method; an average reduction does not prove equal task
quality.

Quote a result only alongside the raw file it came from:

```bash
python benchmarks/run.py --model gpt-4o-mini --repeats 3 --output results.json
```

`results.json` records the fixture version, model, temperature, tokenizer
basis, platform, and every raw output per arm, so the printed table can be
recomputed rather than trusted.

## Files

- `run.py` - harness
- `prompts.json` - 10 prompts, categories: debugging, bugfix, setup,
  explanation, refactor, architecture, code-review, devops, implementation
- `README.md` - this file

## Upstream

Ported from [juliusbrussee/caveman](https://github.com/juliusbrussee/caveman)
(MIT). Upstream's own harness lives in `evals/` and `benchmarks/`; read
`docs/HONEST-NUMBERS.md` there before quoting any percentage, including one
produced here.
