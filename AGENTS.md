# caveman (Caveman + Headroom — Lean Context)

> Two independent features in one plugin: response-style controls (Caveman) and optional context compression with recoverable originals (Headroom). Prompt-based style, loss-aware input compression.

**Version:** 0.5.2 · **Plugin ID:** `caveman`

## Purpose

Reduce the length of the assistant's own prose by injecting a style prompt
into the system prompt, and — separately, off by default — compress eligible
*input* content (tool outputs, history, old tool results) before it reaches
the model, keeping the original recoverable in the CCR cache.

The two features share a plugin, not a purpose. Each has its own switch
(`enabled` for style, `headroom.enabled` for compression), its own settings
surface, its own statistics, and its own failure mode. Coupling them is a
packaging decision from `ROADMAP_UNIFIED.md`; it is not a claim that they
measure the same thing.

## Numbers

**Do not add a percentage claim to this plugin.** Upstream's original headline
was retracted; see upstream `docs/HONEST-NUMBERS.md`, where output reduction
is listed as "Not published" and the note reads "earlier stats releases
applied a fixed 65% output ratio without a committed reviewed result."
<!-- the line above quotes the retracted number in order to explain it; claim-guard: allow -->

This plugin therefore records only raw observations — observed turns and
observed output characters, per level — and labels them as such. A saving is a
difference against a control arm, and no control arm is recorded at runtime.
`execute.py` fails the health check if any user-facing file reintroduces an
unverifiable percentage, so the retracted number cannot creep back in.

The same rule applies to the Headroom half. CCR and the statistics store report
`len(text)/4` token estimates and are labelled as estimates; they are not
billing savings, and no provider-usage comparison has been run. The output-side
statistics bucket is intentionally always zero (see Output ownership below).

The style prompt is re-sent every turn, so it carries a fixed input cost of
roughly 750-850 tokens per turn on the shipped text, depending on level. On terse workloads that
can exceed the output reduction; upstream documents a measured net-loss case in
issue #145. `benchmarks/run.py` reports a break-even figure for this.

## Ownership / Layout

- `plugin.yaml` — manifest: name, version, `settings_sections`, per-project and
  per-agent config flags
- `default_config.yaml` / `config.json` — settings. Every key here must be
  listed in `helpers/plugins_config.DEFAULTS`; `execute.py` enforces that.
- `helpers/plugins_config.py` — the only reader of plugin config
- `helpers/state.py` — the only writer of every file the plugin owns
  (`state.json`, `stats.json`, `mode-log.jsonl`)
- `helpers/compat.py` — cross-module capability guard for both the state module
  and the config module. Imports nothing from the rest of the plugin, so it
  keeps working when its siblings are stale.
- `helpers/prompts.py` — the only reader of `prompts/`, and the only place the
  injected prompt is assembled. The benchmark calls it too.
- `helpers/compress.py` — prose compressor, ported from upstream
  `compress.js`
- `helpers/markdown.py` — compression validator for the `caveman-compress`
  skill
- `extensions/python/system_prompt/_20_caveman_style.py` — injects the style
- `extensions/python/monologue_start/_30_caveman_command.py` — slash commands
- `extensions/python/chat_model_call_before/_60_caveman_shrink_tools.py` —
  compresses tool descriptions
- `extensions/python/message_loop_result/_50_caveman_validate.py` — filler
  detection, optional strip
- `extensions/python/message_loop_result/_60_caveman_observe.py` — records
  observed output length
- `extensions/webui/page-head/caveman-injector.html` — loads the topbar selector
- `webui/caveman-dropdown.js`, `webui/config.html` — WebUI
- `agents/cavecrew-*/` — subagent profiles
- `skills/caveman-*/` — sub-skills; `caveman-compress/scripts/compress.py` is
  the validated compress CLI
- `prompts/` — `caveman.system.style.md` (level-independent rules),
  `caveman.intensity.md` (all six levels, filtered per level),
  `caveman.auto_clarity.md`
- `benchmarks/` — the A/B harness
- `tests/test_caveman.py` — Caveman unit tests, also run by `execute.py`
- `tests/test_headroom_integration.py` — cross-feature tests for the combined
  plugin, also run by `execute.py`
- `tests/test_healthcheck.py` — proves the health check can fail
- `install.py` — CLI installer and `--check-only` runner
- `execute.py` — health check; must exit non-zero on any failure

### Headroom half (ported from `usr/plugins/headroom_compress`)

- `helpers/headroom/config.py` — the only reader of the `headroom` config
  section; also owns `destination_config_path()` and the standalone plugin's
  name as a *constant* (never an import)
- `helpers/headroom/compressor.py` — safe/normal adapters, protections, CCR
  write-on-compress, `retrieve_original`
- `helpers/headroom/ccr_cache.py` — the reversible original store
- `helpers/headroom/stats.py` — compression events only
- `helpers/headroom/per_chat.py` — per-chat compression overrides (this
  plugin's own store, not the standalone plugin's)
- `helpers/headroom/clarity.py` — the destructive-command skip flag shared
  between the auto-clarity extension and the compressor
- `helpers/headroom/proxy_manager.py` — optional local proxy subprocess
- `helpers/headroom/coexistence.py` — read-only detection of the standalone
  plugin (roadmap P0.4)
- `helpers/headroom/migration.py` — settings import from the standalone
  plugin (roadmap P3.1); preview is the default, apply requires `confirm`
- `api/headroom_*.py` — settings, setup, per-chat, proxy, stats, migration
- `tools/compress_text.py`, `tools/headroom_retrieve.py` — on-demand tools
- `extensions/python/hist_add_before/_10_compress_user_message.py`,
  `extensions/python/hist_add_tool_result/_10_compress_tool_output.py`,
  `extensions/python/message_loop_prompts_before/_05_auto_clarity.py`,
  `.../_07_clear_old_tool_results.py`, `.../_10_compress_history.py`
- `extensions/python/banners/_10_headroom_compress.py` — dependency-missing hint
- `extensions/python/banners/_20_headroom_coexistence.py` — double-compression
  warning
- `webui/headroom-config.html`, `webui/headroom-store.js`,
  `webui/headroom-dashboard.html`, `webui/headroom-dashboard-store.js`
- `headroom_setup.py` / `headroom-requirements.txt` — pinned adapter install
  (`headroom-ai==0.38.0`, `--no-deps`)

## Local Contracts

- **All user-visible message text is read through `helpers/text_extract.py`.**
  Agent Zero stores every user message as the dict envelope rendered from
  `fw.user_message.md` (`{"user_message": ..., "system_message": ...,
  "attachments": ...}`), and `Message.output_text()` prefixes `"user: "` and
  JSON-dumps dict content — so the raw accessor returns
  `user: {"user_message":"/caveman ultra"}`. Three features shipped against a
  plain-string contract and were dead in production: slash-command
  classification, auto-clarity detection, and user-message compression
  (remediation 2026-09-28, findings 1-3). `text_extract.py` encodes the real
  contract once (`user_text_from_message`, `text_from_content`,
  `prose_from_llm_result`); readers of user message text or model prose go
  through it and gain nothing by reimplementing it. Likewise:
  `llm_result.response` is the function-call envelope JSON on a tool-only
  turn — never pattern-match, count, or rewrite it; use
  `prose_from_llm_result`.
- **Never claim a savings percentage.** See Numbers above. `execute.py`
  enforces it.
- **`helpers/state.py` is the only module that touches
  `<workdir>/.caveman/`.** It resolves the workdir from
  `get_settings()["workdir_path"]`. Do not read `AGENT_WORKDIR` or
  `A0_WORKDIR`; the framework never sets them, and depending on them put every
  install on the host onto one shared machine-global directory outside the
  workdir volume.
- **One lock, one writer.** All state and observation file access goes through
  `helpers/state.py` so a concurrent extension and API request cannot lose each
  other's read-modify-write. A state change and its mode-log row are appended
  under the same lock so concurrent transitions retain their actual order.
- **Resolve enabled + level through `state.resolve(chat_id, config)`**, reached
  via `compat.state_api(caveman_state, agent)`. Every extension and API handler
  must. Resolving the default level and the enabled flag independently is how
  the WebUI and the prompt injector previously disagreed about the same chat.
- **Never raise out of an extension point.** `agent.handle_exception` re-raises,
  so an exception here kills the agent turn, not just the plugin. This plugin is
  optional and every one of its hooks is cosmetic or observational, so a
  problem must degrade: `compat.state_api()` returns `None` and the caller
  returns immediately. A partial upgrade once killed a turn this way
  (`AttributeError: ... has no attribute 'resolve'`, v0.5.1).
- **Guard the config module the same way as the state module.** Every config
  read goes through `compat.config_api(plugin_cfg, agent)`, and every Headroom
  read goes through `helpers/headroom/config.py`, which guards once for all ten
  of its callers. A partial upgrade that leaves a v0.4.0 `plugins_config.py` in
  place raised `TypeError: get_config() got an unexpected keyword argument
  'agent'` out of `get_system_prompt` and stopped the agent (v0.5.2). A missing
  symbol and a signature too narrow to accept `agent=` are the same failure and
  `compat.missing_config_api()` reports both, because a stale `get_config` looks
  current on disk and naming only absent symbols sends the reader looking in the
  wrong place.
- **`execute.py` must be able to fail, and must not fail the way its subject
  fails.** `check_module_contract` runs before any check that touches the state
  module, because `check_state` calls `state.resolve` itself and would otherwise
  raise the identical `AttributeError` instead of diagnosing it. It asserts both
  guards (`compat.state_api(` and `compat.config_api(`) are present in every
  caller that loads those modules.
- **Editing a `helpers/` module requires restarting `run_ui`, and a green health
  check does not prove the running process is fixed.** `helpers/modules.py`
  `import_module()` builds extension modules with `spec_from_file_location` and
  never registers them in `sys.modules`, so extension files are re-read from disk
  on every call and pick up edits live. The `helpers/` modules they *import* are
  ordinary imports: cached in `sys.modules` for the life of the process. Editing
  one therefore produces new-caller/old-callee pairs that look like impossible
  errors - `module ... has no attribute 'config_api'` for a function that is
  plainly on disk. Only a restart re-imports them; `python execute.py` spawns a
  fresh interpreter and so always passes, which makes it blind to exactly this
  failure. Verify inside the live process, or restart and confirm.
- **The prompt is assembled in exactly one place**, `helpers/prompts.build_system_prompt`.
  The `system_prompt` extension and `benchmarks/run.py` both call it, so the
  benchmark cannot measure something other than what is sent. Do not re-read
  `prompts/` from anywhere else.
- **The intensity ruleset is one file, filtered per level.** Blocks are fenced
  with `[intensity: <level>]` / `[/intensity]`, not HTML comments, because
  HTML comments are stripped from shipped text and a comment-delimited marker
  would delete itself. Adding a `caveman.intensity.<level>.md` file is a
  regression: it reintroduces the copy drift this replaced.
- **The compressor must not be "simplified".** `(?<![\w-])` / `(?![\w-])`
  boundaries and position-matched `sure` are deliberate. Replacing either with
  `\b` or a collocation list reintroduces upstream #1055 (a dangling `-in-time`)
  and #1073 ("make sure the file exists" becoming a create). The path protected
  pattern also deliberately captures a leading `./` and refuses a
  sentence-final period. `tests/test_caveman.py` pins all of this.
- **Compression is validated before it is written.** The `caveman-compress`
  CLI refuses to write a file whose validation failed. Do not add an
  unvalidated inline path to that skill.
- **Subagent role prompts belong in
  `prompts/agent.system.main.specifics.md`.** Not `agent.system.main.role.md`:
  that is the framework's inherited base-role slot, and a profile shipping it
  replaces the base role wholesale. `execute.py` enforces the filename.
- **`agent.yaml` has no tool field** in this framework (`SubAgent` is
  `title` / `description` / `context` / `prompts`). A profile cannot remove
  tools from a subordinate. Any "read-only" or "no shell" limit in a role
  prompt is self-imposed; say so in the prompt rather than claiming the tool is
  unavailable.
- **Keep style instructions out of `agent.yaml` `description`.** That field is
  what the planner reads to route a `call_subordinate`, so prose addressed to
  the subordinate belongs in the role prompt.
- **The response sanitizer is phrase deletion, not rewriting.** Removing an
  opener can leave a fragment. That is why `sanitize_responses` defaults to
  false, why it only runs at `ultra` / `wenyan-*`, and why every strip is
  logged. Do not enable it by default.
- **`shrink_tools` defaults to false.** Tool descriptions are the contract the
  model reads when calling a tool. Do not change that default without a
  measurement on a real tool set.
- `install.py` is a CLI script (NOT a WebUI asset). It uses
  `Path(__file__).resolve().parent` to find itself, so it works from any
  location, and refuses to install onto its own source directory. Do not move
  it to `webui/`; that would break the Plugins-UI "execute.py" button workflow.

## Contracts for the combined plugin (Caveman + Headroom)

These apply to the Headroom half and to the boundary between the two halves.
`ROADMAP_UNIFIED.md` is the working analysis; this is the binding summary.

- **Two switches, never one.** `enabled` governs response style;
  `headroom.enabled` governs input compression. Neither reads the other.
  Compression is off by default so upgrading this plugin cannot change what
  reaches the model for an existing user.
- **Never import `usr.plugins.headroom_compress`.** The standalone plugin is
  referenced by name as a *string* (detection) and never as a module. An import
  would bind this plugin's config, CCR database and stats to another plugin's
  mutable files. `execute.py` fails on any such import, and the WebUI is
  checked for a stale `/plugins/headroom_compress/` URL.
- **Agent-scoped config reads.** `get_plugin_config(name, agent=...)` is
  called with an agent, so every reader must forward one. A narrower reader
  raises `TypeError`, which `plugins_config.get_config()` swallows into a
  silent fallback to `DEFAULTS` — that is how the sanitizer and the tool
  shrinker once looked permanently switched off.
  `tests/test_headroom_integration.py` guards the call sites.
- **Lost means recoverable or it did not happen.** A lossy compression result
  must carry a CCR key, and an old tool result is cleared only after its
  original is stored. If CCR is disabled or the write fails, the result is
  left intact. `tools/headroom_retrieve.py` restores by key.
- **Protected reads and code pass through byte-for-byte**, including CRLF,
  tabs and trailing whitespace. Keep `protect_reads` and `protect_code` on
  until task quality is measured without them.
- **Hook responsibilities do not overlap.** Caveman shapes the assistant's
  output and tool descriptions; Headroom transforms eligible input and history.
  System prompts and initial role messages are never compressed.
- **Output observations have exactly one owner:** `helpers/state.py`. The
  standalone plugin's `caveman_bridge` estimator was **not** ported — it
  multiplied observed output length by a per-level ratio and reported the
  product as savings, which is the retracted claim. The
  `caveman_bridge` event kind survives in `stats.py` for classification only
  and is never written; the output bucket is therefore always zero, which is
  what makes the single ownership visible rather than implied.
- **Coexistence is detected, never enforced.** `helpers/headroom/coexistence.py`
  only reads the plugin registry. It must never toggle, disable or write to the
  standalone plugin: one plugin silently changing another's state destroys the
  user's ability to tell which switch did what. Warn, link to settings, stop.
- **Migration is preview-first and additive.** `build_plan()` only reads.
  `apply_plan()` requires `confirm`, writes only this plugin's own
  `config.json`, backs it up first, fills in only keys that are *absent*,
  preserves unknown keys, and re-checks the no-overwrite rule against what is
  on disk at write time. Source files, CCR databases, stats and `.caveman`
  state are never modified. Per-project/per-agent destinations are reported as
  manual work rather than written — see the scope note in `migration.py`.
- **Rollback:** disable this plugin, re-enable `headroom_compress` in the
  Plugins UI. Nothing else is needed, because this plugin never wrote into the
  standalone plugin's state.
- **One class per file.** Agent Zero registers `classes[0]` per file, so a
  second `Extension` or `ApiHandler` in a file is dead code that looks alive.
  `execute.py` and the test suite both check this, along with unique banner
  ids.

## Extension points

| Point | File | Notes |
|---|---|---|
| `system_prompt` | `_20_caveman_style.py` | Appends style + level + auto-clarity. |
| `monologue_start` | `_30_caveman_command.py` | Reads `loop_data.user_message` via `Message.output_text()`. |
| `chat_model_call_before` | `_60_caveman_shrink_tools.py` | Rewrites `description` in `call_data["a0_responses_function_tools"]`, which `call_chat_model_turn` reads back after the hook. |
| `message_loop_result` | `_50_caveman_validate.py` | Runs before `hist_add_ai_response`, so a mutation reaches history. |
| `message_loop_result` | `_60_caveman_observe.py` | After validate, so recorded length is the kept length. |
| `banners` | `_10_caveman_discovery.py` | Welcome-screen card. |
| `banners` | `_10_headroom_compress.py` | Only when compression is on and `headroom-ai` is missing. |
| `banners` | `_20_headroom_coexistence.py` | Only when the standalone plugin is active too. |
| `hist_add_before` | `_10_compress_user_message.py` | Large user messages only; never the initial task. |
| `hist_add_tool_result` | `_10_compress_tool_output.py` | Mutates `data["tool_result"]` before it enters history. |
| `message_loop_prompts_before` | `_05_auto_clarity.py` | Sets the destructive-command skip flag. |
| `message_loop_prompts_before` | `_07_clear_old_tool_results.py` | Clears old tool results **only** after a successful CCR write, and **only** when no auto-clarity flag is set (peeks, never consumes). |
| `message_loop_prompts_before` | `_10_compress_history.py` | Walks live history; consumes the auto-clarity flag once. |

Two points are dead ends, and both shipped broken code once:

- **`response_stream_end`** is called with only `loop_data=`, carries no
  response text, and `helpers.extension.call_extensions_async` builds a fresh
  `kwargs` dict per extension, so `kwargs[key] = ...` inside `execute()` can
  never reach the caller. The previous validator lived there and never ran. Use
  `message_loop_result`, which receives a mutable `result_data`.
- **`message_loop_prompts_before`** fires at the very start of
  `prepare_prompt`, before `loop_data.system` is set and before any tool
  payload exists. `loop_data` has no `tools` attribute. The previous tool
  shrinker lived there and never ran; it now uses `chat_model_call_before`.

Ordering inside one point matters and is encoded in the filename prefixes:
`_05_auto_clarity` sets the skip flag before `_07_clear_old_tool_results` and
`_10_compress_history` consume it. Reversing that would make auto-clarity
protect nothing.

### Consuming vs peeking the auto-clarity flag

`_10_compress_history` is the **consumer**: it calls `clarity.consume_skip()`
exactly once, so the flag protects that whole history walk rather than just
its first message.

`_07_clear_old_tool_results` is the **destructive** stage — it replaces live
history content with a placeholder — so it must honour the flag too, but it
must call `clarity.peek_skip()`, not `consume_skip()`. Consuming there would
delete the flag before `_10` runs and silently unprotect the next stage.

This was a real defect, not a theoretical one (audit 2026-09-27): `_07` read
no safety flag at all, so on a turn where the user asked for `rm -rf /` the
tool results were cleared anyway — the exact outcome auto-clarity exists to
prevent. `_05` → `_07` (peek) → `_10` (consume) is now the enforced order, and
`tests/test_headroom_integration.py` guards all three properties: the flag
blocks clearing, clearing still works when no flag is set, and `peek_skip` is
not destructive. `peek_skip` and `consume_skip` share `_live_reason()` so both
apply the identical `FLAG_TTL_SECONDS` expiry rule; an expired flag protects
nothing, which is also tested.

## Storage lifecycle

Both `helpers/headroom/stats.py` and `helpers/headroom/ccr_cache.py` pool one
SQLite connection per resolved database path.

**Why.** Each store used to open *and close* a connection per operation. The
INSERT itself is microseconds; `Connection.close()` on a WAL database runs a
checkpoint and measured **~15 ms on Windows alone**. Because a `StatsRecorder`
was built for every compression *and every skip*, and a `CcrCache` for every
stored original, the fixed cost dominated the work: **48.7 ms per tool
result**, which is pure overhead on the agent's hot path. Pooling the
connections and having `close()` return the handle instead of closing it
brought that to **2.3 ms — a 20.8x speedup** (measured, 80 iterations).

**Why it is safe.**

- Every write autocommits (`isolation_level=None`), so a row written before
  `close()` is already durable and visible to any other connection. Returning
  the handle loses nothing.
- Each caller already holds a per-instance lock and the pool holds its own, so
  a shared connection is never used concurrently.
- The "clear only if durably stored" rule is unchanged: `_07` still requires
  `backend == "sqlite"` *and* a live connection before it edits history.
- A failed schema migration drops the pooled handle instead of leaving a
  half-initialised database to be reused.

**Cost of pooling.** On Windows an open handle makes its directory
undeletable. Tests that create a temp database and then delete it **must** call
`stats.close_all_connections()` and `ccr_cache.close_all_connections()` first;
`test_headroom_integration.py::_close_pools()` does this inside the `with`
block. Note also that `with sqlite3.connect(...)` commits but does **not**
close — the suite's `_query_db`/`_exec_db` helpers exist for that reason.

**Unbounded growth.** The CCR cache has a TTL and a byte budget; the stats
`events` table had neither, so it grew without limit (the standalone
plugin's copy had reached 12,345 rows). `StatsRecorder._prune()` now applies
`stats_retention_days` (default 30) and `stats_max_rows` (default 50,000). Both
use the existing indexes, and the row-count branch trims to `max_rows - 1`
because the prune runs *before* the caller's INSERT — otherwise the table
settles one row over the cap on every open. Retention is best-effort: a prune
failure is logged, never raised.

**CCR eviction cost.** `put()` used to run
`SELECT SUM(length(original)) FROM ccr` on every write — a full scan of every
stored blob, with no index that can serve it. It now takes a cheap
`page_count * page_size` upper bound first and only pays for the exact `SUM`
when that bound suggests the budget might be exceeded. The bound includes
indexes and free pages, so it is never used as the payload figure itself;
eviction still uses exact arithmetic, and `test_ccr_respects_its_byte_budget_under_pressure`
holds the cache to `ccr_max_bytes`.

## Verification

- `python execute.py` — full health check, non-zero on failure. Runs both unit
  suites, the state round-trip, the API action contract, slash command
  classification, prompt parsing, the claim and tool-availability guards, the
  standalone-plugin isolation scan, the `default_config.yaml`/DEFAULTS parity
  check, the registration-uniqueness check, and a live migration preview plus
  coexistence report.
- `python install.py --check-only` — same check via the installer.
- `python tests/test_caveman.py` — Caveman unit tests (also run by `execute.py`).
- `python tests/test_headroom_integration.py` — combined-plugin tests, including
  compression safety, CCR recoverability, the clear-only-if-stored rule,
  coexistence detection and the full migration contract. Runs in safe mode, so
  it needs neither `headroom-ai` nor a live runtime. Also covers the
  auto-clarity ordering contract, stats retention and the CCR byte budget
  (51 tests).
- `python tests/test_healthcheck.py` — proves the check can fail.
- `python benchmarks/run.py --validate` — fixtures and prompt fragments only,
  no model call and no invented numbers.

Run these **one at a time**, not in parallel. `tests/test_caveman.py` exercises
the real state layer in `<workdir>/.caveman/`, so two concurrent runs (for
example `execute.py` and `install.py --check-only` at the same time)
interleave their read-modify-writes and produce failures that look like
product bugs — a missing observation, an extra mode-log row. The module lock
protects threads inside one process, not two processes.
`tests/test_headroom_integration.py` uses temporary CCR/stats paths and has no
such constraint.

## Roadmap

- `AUDIT.md` — the 2026-09-27 production-readiness audit: the safety fix, the
  20x storage-performance work, the retention and eviction work, the
  deliberately-unfixed known issues, and the method used to confirm each
  finding. Read it before changing the storage layer or the auto-clarity
  ordering.
- `ROADMAP_UNIFIED.md` — the Caveman + Headroom integration roadmap
  (P0–P4) and its current status.
- `ROADMAP.md` — the Caveman-only roadmap: live runtime validation, the
  real-model A/B benchmark, community release preparation, optional features.

Keep the version and verification counts in both aligned with `plugin.yaml` and
`execute.py`.

## See also

- `README.md` — user-facing docs, the honest-numbers section, the install /
  upgrade note for users who also run the standalone Headroom plugin
- `benchmarks/README.md` — A/B method and its limits
- `helpers/plugins_config.py.DEFAULTS` — the authoritative settings list
- Framework references: `helpers/plugins.py` (lifecycle, config, scope
  resolution, toggles), `helpers/api.py` (API dispatch, CSRF),
  `helpers/extension.py` (dispatch), `helpers/responses_tools.py` (tool
  payload shape), `helpers/subagents.py` (profile schema),
  `helpers/settings.py` (workdir)
