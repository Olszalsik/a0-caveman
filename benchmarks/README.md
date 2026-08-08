# Caveman benchmarks (Agent Zero port)

Reproducible token-savings measurements for the caveman plugin running on
Agent Zero v2.2.

## Quick start

```bash
# Dry run (no LLM call, estimates from prompt length only):
python /a0/usr/plugins/caveman/benchmarks/run.py --dry-run

# Real run with a specific model:
python /a0/usr/plugins/caveman/benchmarks/run.py --model gpt-4o-mini --output out.json

# One prompt only:
python /a0/usr/plugins/caveman/benchmarks/run.py --prompt 3 --model gpt-4o-mini
```

## Method

- 10 fixed prompts ported from the upstream caveman benchmark suite
  (`prompts.json`). Categories: debugging, bugfix, setup, explanation,
  refactor, architecture, code-review, devops, implementation.
- For each prompt, we run the prompt through the configured litellm model
  with caveman level applied (the level is passed via the plugin config).
- We measure `chars_out` per response, estimate tokens as `chars / 4`
  (matches upstream's order of magnitude; not exact Claude-API telemetry).
- Reduction fractions per level (calibrated to upstream's published numbers):

| Level | Reduction |
|---|---|
| lite | 30% |
| full | 65% |
| ultra | 80% |
| wenyan-lite | 55% |
| wenyan-full | 70% |
| wenyan-ultra | 80% |

## Honest numbers

The numbers are ESTIMATES, not exact Claude-API usage. They are useful for
relative comparison (which level saves more) and for tracking the plugin's
overall behavior over time, not for billing reconciliation. See the
upstream [HONEST-NUMBERS.md](https://github.com/juliusbrussee/caveman/blob/main/docs/HONEST-NUMBERS.md)
for the full caveat.

## Files

- `run.py` - harness, async litellm calls, JSON output
- `prompts.json` - the 10 benchmark prompts
- `README.md` - this file

## Reproducing upstream numbers

The upstream caveman repo has a different evaluation harness (`evals/`).
The port here uses the same prompt set but a simpler measurement method
(chars/4 estimate instead of real Claude API usage). The relative ordering
of levels should match; the absolute percentages may differ by 5-15%.
