# Caveman + Headroom v0.5.2 — remediation (2026-09-28)

> **Status: fixed and verified (v0.5.3).** Every "FIXED" row below was
> implemented, probed functionally (concurrency probes for the clarity store,
> live compress_text probes for the threshold and dry_run changes) and pinned
> by new regression tests — `tests/test_caveman.py` 73/73,
> `tests/test_headroom_integration.py` 78/78. The deliberately-open items at
> the bottom remain open.

Follow-up to `AUDIT.md` (2026-09-27). That audit fixed F1–F4 and K1–K4; this
remediation was triggered by a deeper review that found the audit's own safety
mechanism dead at the source, plus two dead WebUI surfaces and a systemic
message-envelope mismatch the test fakes had been masking. Everything here was
verified against framework code (`agent.py`, `helpers/history.py`,
`helpers/api.py`, `helpers/llm_result.py`) or measured, not inferred.

## Root cause behind the Tier 0 findings

`agent.py::hist_add_user_message` stores every user message as the dict
envelope rendered from `prompts/fw.user_message.md`
(`{"user_message": ..., "system_message": ..., "attachments": ...}`), and
`helpers/history.py::_stringify_output` prefixes `"user: "` and JSON-dumps
dict content. Three plugin code paths assumed a plain string and therefore
never see real user text:

1. `_30_caveman_command.py` reads `Message.output_text()` and classifies with
   anchored patterns → `/caveman ultra` arrives as
   `user: {"user_message":"/caveman ultra"}` and never matches. **The entire
   slash-command feature was dead.**
2. `_05_auto_clarity.py::_extract_text` checks the keys
   `content`/`message`/`text` — the real key is `user_message` → the
   destructive-command skip flag was **never set in production**. The AUDIT
   F1 fix (peek/consume ordering in `_07`/`_10`) was protecting a flag that
   could not exist.
3. `_10_compress_history.py` / `hist_add_before` used the same wrong key
   list → user-message compression was inert.

The unit tests passed because `tests/test_caveman.py`'s `FakeMessage`
returns raw strings without the label prefix or the envelope — the fake
diverged from the real `Message` contract in exactly the dimension these
bugs live in.

## Findings and fix status

| # | Severity | Finding | Where | Status |
|---|---|---|---|---|
| 1 | HIGH | Slash commands dead (label prefix + envelope) | `_30_caveman_command.py` | FIXED |
| 2 | HIGH | Auto-clarity detection dead (wrong envelope keys) | `_05_auto_clarity.py` | FIXED |
| 3 | HIGH | User-message compression inert (same keys) | `_10_compress_history.py`, `_10_compress_user_message.py` | FIXED |
| 4 | HIGH | Dashboard modal 404s (`dashboard-store.js` vs `headroom-dashboard-store.js`) | `webui/headroom-dashboard.html` | FIXED |
| 5 | HIGH | `headroom-config.html` binds `config.*` out of scope; unsavable standalone | `webui/headroom-config.html` | FIXED |
| 6 | MED | clarity.py flag store: 100% loss/corruption under concurrent chats (measured 266 corrupt + 134 lost / 400) | `helpers/headroom/clarity.py` | FIXED |
| 7 | MED | compress_text couples the tool threshold to history/user sources (verified: tool_min=0 kills history compression) | `helpers/headroom/compressor.py` | FIXED |
| 8 | MED | `dry_run` phantom setting: read, reported, never enforced, in no defaults | `compressor.py`, `plugins_config.py`, `default_config.yaml` | FIXED (enforced) |
| 9 | MED | migration `_scoped_sources` UnboundLocalError in its own fallback path → API 500 | `helpers/headroom/migration.py` | FIXED |
| 10 | HIGH→doc | `api/caveman_state.py` claims `self.agent` exists; ApiHandler never sets it → WebUI shows global scope while chat resolves agent-scoped | `api/caveman_state.py` | FIXED (honest docstring + section read) |
| 11 | MED | `_30_caveman_command` calls `set_state` without `compat.state_api()` guard; execute.py guard list omits `"command"` | `_30_caveman_command.py`, `execute.py` | FIXED |
| 12 | MED | `caveman_stats.py` zero compat guards → 500s on partial upgrade | `api/caveman_stats.py` | FIXED |
| 13 | MED | Tool-call turns: observe records envelope JSON as output; validate can mangle envelope | `_50_caveman_validate.py`, `_60_caveman_observe.py` | FIXED |
| 14 | MED | `_strip_filler` has no code-block protection (collapses code indentation) | `_50_caveman_validate.py` | FIXED |
| 15 | MED | Dropdown MutationObserver refreshes on every DOM mutation (request flood) | `webui/caveman-dropdown.js` | FIXED |
| 16 | MED | proxy API blocks the shared event loop up to ~15s (sync start/stop/restart) | `api/headroom_proxy.py` | FIXED |
| 17 | MED | `window_hours=0` (lifetime) unreachable; lifetime shows last-24h | `api/headroom_stats.py` | FIXED |
| 18 | MED | On-demand compress_text tool no-ops with auto threshold 0 (contradicts UI text); consumes auto-clarity flag | `tools/compress_text.py` | FIXED (force=True) |
| 19 | MED | toggle_info always "unknown" (`get_toggle_state` returns a str, not an enum) | `tools/compress_text.py` | FIXED |
| 20 | MED | headroom_retrieve truncates restores at 4000 chars — partial recovery contradicts the CCR contract | `tools/headroom_retrieve.py` | FIXED (offset/max_chars) |
| 21 | MED | proxy auto-start documented, never wired | `hooks.py` | FIXED |
| 22 | HIGH | execute.py import scan misses `from usr.plugins import headroom_compress` + dynamic imports | `execute.py` | FIXED |
| 23 | HIGH | "Live" migration preview / coexistence report always run against a stub | `execute.py` | FIXED (stub only as fallback, honestly labelled) |
| 24 | MED | Claim guard scans only .md/.yaml/.yml/.json; HTML/JS/banner text unscanned | `execute.py` | FIXED |
| 25 | MED | Banner-id check sees 2 of 3 banners (imported constant invisible) | `execute.py` | FIXED |
| 26 | MED | Config parity enforced only for headroom section, not caveman top level | `execute.py` | FIXED |
| 27 | MED | Bare-fetch CSRF guard passes vacuously once fetchApi appears anywhere | `execute.py` | FIXED |
| 28 | MED | Headroom WebUI surface unvalidated (bindings, script srcs) | `execute.py` | FIXED |
| 29 | LOW | REQUIRED_FILES demands gitignored `config.json` → fresh install fails | `execute.py` | FIXED |
| 30 | LOW | install.py EXPECTED_VERSION 0.5.1 vs 0.5.2 | `install.py` | FIXED |
| 31 | LOW | plugins_config docstring contradicts plugin.yaml per_*_config: true | `helpers/plugins_config.py` | FIXED |
| 32 | LOW | execute.py timeout path leaks zombie subprocess | `api/headroom_execute.py` | FIXED |
| 33 | LOW | migration backup path 1-second collision | `helpers/headroom/migration.py` | FIXED |
| 34 | LOW | repo: entire Headroom half untracked, v0.5.2 uncommitted | nested repo | FIXED (committed+pushed) |

## Missing test coverage added

`tests/test_caveman.py`: real-contract command classification (label + JSON
envelope, the anti-fake-drift test), clarity-store concurrency (lost-update +
corruption), clarity corrupt-file tolerance, compressor threshold decoupling,
dry_run enforcement, auto-clarity envelope detection.

`tests/test_headroom_integration.py`: WebUI asset wiring (every script src in
`webui/*.html` exists), headroom-config bindings vs DEFAULTS, compress_text
tool on-demand-with-auto-off, toggle_info reporting, retrieve pagination,
stats window semantics, migration exception path, proxy async dispatch.

`tests/test_healthcheck.py`: mutations for the new/changed checks.

## P4.4 closed: live smoke + provider benchmark (2026-09-28)

**Live turn smoke (PASSED, 217 s).** Driver: `tmp/p44_smoke.py` on the host,
run in the container with the A0 venv from a container-local copy (9p-flap
safety). It backs up `config.json`, enables both switches (caveman `full` +
headroom with tool threshold 200), drives one real turn via the
`initialize_agent` / `AgentContext.communicate` pattern in an isolated
workdir, restores `config.json` in `finally`, then verifies against the
plugin's sqlite DBs, attributing rows by timestamp because the CCR/stats DBs
are shared with concurrently running production chats.

Observed on the turn's own `code_execution_tool` output (~2000 tokens of
log-shaped text): hook fired live and compressed (verbose line
`1917 -> 1877 tokens (40 saved, ratio 0.98)`; stats row `1999 -> 1936, 63
saved`), a CCR entry was written (`tool:code_execution_tool`), a second,
older tool result was cleared from history with its original stored
(`clear:code_execution_tool`, `1042 -> 44` stub), and
`compressor.retrieve_original` returned the full 2224-char original. Config
backup/restore clean. Two further facts worth knowing:

- The plugin DBs are shared production state. Rows from other chats appear
  alongside the smoke's rows; the driver attributes by timestamp window, and
  docs should never quote the shared totals as one chat's numbers.
- In safe mode the extractive compressor saves little on repetitive prose
  (a 900-token repeated-sentence output saved 0-1 tokens) and declines
  JSON-envelope tool results; real savings come from structural/log content
  (the same window showed 14-15% on production `tool:parallel` outputs, and
  ~2-3% on synthetic log/code shapes offline). Mode `safe` is conservative
  by design; measure before enabling broadly.

**Provider A/B benchmark (3 prompts x 1 repeat, honest numbers).**
`benchmarks/run.py --model openai/glm-5.3-flash --levels full,ultra
--repeats 1 --max-tokens 4000` via the ollama cloud OpenAI-compatible
endpoint. n=1 per prompt; directional only.

Output content tokens vs the `Answer concisely.` control: caveman cuts a
<!-- quotes the benchmark's own measured per-prompt output deltas; claim-guard: allow -->
lot (ultra 55%/71%/48% shorter, full 55%/38%/-3% on the three prompts). But
what gets *billed* differs: glm-5.3-flash is a reasoning model and the style
prompt makes it reason longer. Provider-billed totals (input+completion):

| prompt | terse | full | ultra |
|---|---|---|---|
| react-rerender | 1473 | 1511 (+3%) | 1935 (+31%) |
| postgres-pool | 2563 | 2250 (**-12%**) | 2289 (**-11%**) |
| auth-middleware-fix | 1640 | 3076 (+88%) | 3859 (+135%) |

Plus ~780-850 extra input tokens/turn for the style prompt (already in the
totals above). Verdict for this model/workload: 1 of 3 prompts a modest
billed saving, 2 a net loss; the reasoning-model behaviour (style prompt
makes the model reason longer) dominates. This matches the README's
documented net-loss case; no savings claim goes into the repo docs. Raw
results in `tmp/p44_benchmark_results*.json` (not committed). **Superseded
by the n=3 run below: the n=1 numbers were noise, not signal.**

Harness bug found and fixed while running this: `benchmarks/run.py`
raised `ZeroDivisionError` when a control arm returned 0 tokens (the
no-system-prompt baseline arm burned its whole completion budget on
reasoning with glm-5.3-flash). Guarded `terse_total` divisions.

## Benchmark v2: cost-weighted, n=3, lean arms (2026-09-28)

The P4.4 benchmark scored tokens 1:1 and ran n=1; the plan that followed
(user: "if we have net loss, this plugin is pointless - find the settings
where we can create savings") produced four changes, all shipped in 0.5.4:

1. **Harness scoring fix.** `benchmarks/run.py --io-price` (default 4.0)
   re-scores the provider's own billed usage at an output:input price ratio
   (`cost = prompt_tokens + completion_tokens * ratio`) against the terse
   control, adds a `reasoning_tokens_est` column (billed completion minus
   locally counted content), and records `metadata.io_price` plus a `billed`
   block in the report JSON. The 1:1 content tables stay for comparability.
2. **Lean style prompt.** `prompts/caveman.system.style.lean.md` +
   `lean_style_prompt: false` (DEFAULTS, default_config.yaml, WebUI toggle,
   `--lean` harness flag). Measured injection: 802 tok/turn (full) and 820
   (ultra) standard, 355/373 lean - 56% cheaper, level ruleset unchanged.
3. **Reasoning-model warning.** The harness prints a caveat when the model id
   looks like a reasoning model: trust the billed table, not the content
   table.
4. **n=3 rerun.** 10 prompts x 6 arms (baseline / terse / full / full-lean /
   ultra / ultra-lean) x 3 repeats on glm-5.3-flash, max_tokens 4000, run as
   10 parallel single-prompt shards (the sequential run measured ~75 s/call;
   sharded, ~45 min wall).

<!-- quotes this repo's own measured benchmark output; claim-guard: allow -->
Pooled billed cost vs the terse control at 4:1: full -41%, full-lean -47%,
ultra -31%, ultra-lean -32%. At 1:1: -8% / -32% / +2% / -17%. Content tokens
vs terse: full -34%, full-lean -33%, ultra -47%, ultra-lean -40%.
`reasoning_tokens_est` shows ~16k of ~25k billed completion tokens for
`full` are thinking tokens - reasoning dominates billed output, which is why
the 1:1 content tables overstate the win and why the billed table is the one
to quote. Per prompt at 4:1, `async-refactor` and `pr-security-review` lose
on every arm (the terse control answered them very briefly) and `ultra`
additionally loses `auth-middleware-fix`.

The honest headline is in README "Honest numbers"; raw shard results in
`tmp/p55_bench_p*.json` + `tmp/p55merge.py` (not committed). The P4.4 n=1
verdict above ("net loss on 2 of 3") is kept for the record because the
flip between n=1 and n=3 on the same prompts is itself a finding: n=1
numbers are noise and must not be quoted.

## Adapter validation closed (2026-09-28)

The audit's "headroom-ai is not installed here" was stale: the A0 venv has
had `headroom-ai==0.38.0` (pip, 2026-09-24) all along. Validation was run
twice - once against an isolated `pip --target /tmp` copy of the same wheel,
once against the live venv install - with identical results, 12/12 checks:

- import + `__version__` (the `headroom_setup.py` check) OK; plugin
  `headroom_status()` reports available/0.38.0 with wired strategies
  `auto` + `smart_crusher`; both `ContentRouter.route_and_compress` and
  `SmartCrusher.smart_crush_tool_output` import as the adapter expects.
- Router content behaviour in this environment (pure-Python detection; the
  container's onnxruntime 1.19 is older than the ML extras' 1.24+ floor, so
  the kompress model is unusable and degrades with warnings - structural
  compression is unaffected): code passes through unchanged, traceback
  output is declined (protected by design, and the adapter's
  `len(routed) < len(text)` guard rejects the router's slightly larger
  rewrite, returning the original honestly), JSON job arrays compressed
  57.6%, a repetitive heartbeat log 26% via `compress_text` in `normal`
  mode with a clean CCR round trip. `smart_crusher` and the unsupported-
  strategy no-op behave as documented, and a router exception falls back to
  `_safe_transform` without crashing the caller.

No code change was needed - the adapter was written against this API and
matches it. The live venv install means `normal` mode is real in this
container (as of the next server restart, since `sys.modules` caches the
compressor); `safe` remains the default and unchanged.

## Deliberately left open

None.
- ApiHandler agent-scoping is a framework limitation: handlers have no agent.
  The WebUI reads global scope; per-project/per-agent configs are honoured on
  the model-facing path only. Documented rather than papered over.