# Contributing

Thanks for looking at this. A caveman plugin is a small, self-contained Python
package with a health check and a test suite, so the loop is short.

## Setup

The plugin lives in an Agent Zero install at `usr/plugins/caveman/`. Everything
below assumes that directory.

```bash
cd usr/plugins/caveman

python execute.py                  # health check, non-zero on failure
python tests/test_caveman.py       # unit tests
python tests/test_healthcheck.py   # proves the health check can fail
```

All three run without a full Agent Zero runtime and without pytest: the few
framework modules the tests touch are stubbed at the top of the file.

## Before you open a pull request

1. `python execute.py` must pass. It is not a smoke test — it executes the state
   round-trip, the API action contract, slash command classification, and the
   level ruleset, and it fails on the defect classes this plugin has actually
   shipped.
2. `python tests/test_caveman.py` must pass, and must stay rerun-safe.
3. If you touched anything `execute.py` inspects, run
   `python tests/test_healthcheck.py` and confirm it still catches every
   mutation case. If you introduced a new class of defect the check should
   catch, add a mutation case for it.
4. `python benchmarks/run.py --validate` must pass if you touched `prompts/`.

## Things that will get a change rejected

- **A percentage savings claim.** Upstream retracted its headline figure
  (`docs/HONEST-NUMBERS.md`: output reduction "Not published"). Measured, cited
  results are fine; a ratio applied to an observed length is not. The health
  check enforces this on user-facing files, with an explicit
  `claim-guard: allow` marker for a line that quotes the number in order to
  explain the retraction.
- **A new setting that nothing reads.** If you add a key to `config.json` or
  `default_config.yaml`, it must be in `helpers/plugins_config.DEFAULTS` and
  read by real code. The health check fails otherwise, and the same check
  rejects a `config.html` binding for a key that no backend reads.
- **"Simplifying" the compressor.** The `(?<![\w-])` boundaries and the
  position-matched `sure` are deliberate. Replacing either with `\b` or a
  collocation list reintroduces upstream #1055 and #1073, which shipped as
  corruption in the model's tool list. `tests/test_caveman.py` pins both.
- **An extension point that carries no data.** `response_stream_end` receives
  only `loop_data` and builds a fresh `kwargs` dict per extension;
  `message_loop_prompts_before` fires before any tool payload exists. Both hosted
  code here that could never run. If you need the response text, use
  `message_loop_result`; if you need the tool payload, use
  `chat_model_call_before`.
- **Reading `AGENT_WORKDIR` or `A0_WORKDIR`.** The framework never sets them.
  Use `get_settings()["workdir_path"]`, which is already dockerized.
- **Writing files from an extension directly.** `helpers/state.py` owns
  `<workdir>/.caveman/` and holds the single lock. A second writer means a
  second lock means lost updates.
- **Editing a per-level prompt file.** There is one ruleset,
  `prompts/caveman.intensity.md`, filtered per level. Adding
  `caveman.intensity.<level>.md` reintroduces the copy drift this replaced.

## Reporting a bug

The most useful report includes the output of `python execute.py`, the level in
force, and the actual prompt text if the style prompt is involved. A
discrepancy between what the dropdown shows and what the chat does is worth
reporting even if the model output looks fine — that class of mismatch is how
this plugin shipped an inert feature.

## License

MIT. See `LICENSE`: upstream copyright is retained and the Agent Zero port is
attributed separately.
