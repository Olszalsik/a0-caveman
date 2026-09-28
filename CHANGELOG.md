# Changelog

All notable changes to this plugin. The plugin is an Agent Zero port of
[juliusbrussee/caveman][upstream] (MIT), so upstream's own release notes apply
to the style rules and the level set.

[upstream]: https://github.com/juliusbrussee/caveman

## 0.5.3 (2026-09-28)

Remediation pass documented in `REMEDIATION.md` (follow-up to `AUDIT.md`).
Headline: three shipped features were dead in production because they read
user messages against a contract Agent Zero does not have, and the tests that
were supposed to catch it used fakes that matched the wrong contract.

### Fixed

- **Slash commands were dead in production.** `hist_add_user_message` stores
  the user message as the dict envelope from `fw.user_message.md`
  (`{"user_message": ...}`), and `Message.output_text()` prefixes `"user: "`
  and JSON-dumps dict content — so `/caveman ultra` arrived as
  `user: {"user_message":"/caveman ultra"}` and the anchored command patterns
  never matched. Extraction now goes through the new
  `helpers/text_extract.py`, shared by every reader of user-visible message
  text.
- **Auto-clarity detection was dead in production.** `_05_auto_clarity` probed
  the envelope keys `content`/`message`/`text`; the real key is
  `user_message`, so the destructive-command skip flag was never set. The
  AUDIT F1 ordering fix (peek in `_07`, consume in `_10`) had been protecting
  a flag that could not exist. Detection now uses the shared extractor and
  prefers the current turn's message from `loop_data`.
- **User-message compression was inert.** `_10_compress_history` and the
  `hist_add_before` hook used the same wrong key list; both now read and
  write through the envelope-aware helper (including the `user_message` key
  on rewrite).
- **The clarity flag store lost 100% of updates under concurrent chats**
  (measured: 266 corrupted + 134 lost of 400 racing writes — unlocked
  read-modify-write with a plain `write_text`). Writes are now serialised by
  an in-process lock plus the cross-process lock file `per_chat.py` uses, and
  land atomically via temp file + `os.replace`; a corrupt store fails open
  instead of crashing a turn.
- **The tool-output threshold silently killed history compression.**
  `compressor.compress_text` read `auto_compress_tool_outputs_min_tokens` for
  every source, so with the tool feature off (threshold 0) a 20k-token
  history message skipped with reason `min_tokens=0`. The threshold now
  applies to `tool:` sources only; history/user hooks apply their own
  `auto_compress_history_min_tokens`; direct no-source calls keep a fallback
  minimum.
- **`dry_run` was a phantom setting**: read into every result, reported by
  the tools, never enforced, in no defaults. It is now enforced in
  `compress_text` (original text returned, nothing persisted, economics
  reported via `would_be_text`/`would_save_tokens`) and added to `DEFAULTS`
  and `default_config.yaml`.
- **Tool-call turns corrupted the observation and validation paths.**
  `llm_result.response` falls back to the function-call envelope JSON on a
  tool-only turn: `_60_caveman_observe` counted that JSON as model output and
  `_50_caveman_validate` could have rewritten JSON inside it. Both now use
  the shared prose extractor (a bare `response`-tool turn yields the answer
  text from the tool arguments); the validator never rewrites an envelope.
- **`_strip_filler` had no code-block protection**: the whitespace collapse
  ran over fenced code and destroyed indentation. Fenced blocks (including an
  unclosed fence at EOF) are now excluded, and the collapse never touches
  line-leading indentation.
- **`migration._scoped_sources` crashed in its own fallback path**:
  `assets` was bound only inside the `try`, so an enumeration failure
  (`helpers.plugins` imports PIL, unavailable outside the runtime) hit
  `UnboundLocalError` and the migration API returned 500 instead of the
  degraded scope list.
- **Migration backups could overwrite each other** on a same-second apply;
  the stamp now has microsecond precision plus an exists-loop.
- **`api/caveman_stats.py` had zero compat guards** — a partial upgrade
  surfaced as 500s from every stats action; it now degrades with an honest
  error like the other handlers.
- **`_30_caveman_command` was the only extension point writing state without
  the `compat.state_api()` guard**; a stale `helpers/state.py` would have
  raised out of the hook and killed the turn. It now skips like the rest.
- **`api/caveman_state.py` claimed `ApiHandler` exposes `self.agent`** — it
  does not (framework limitation: handlers are built with only
  `app`/`thread_lock`). The docstring now states the real consequence: the
  WebUI resolves the global config while model-facing extension points
  resolve per-project/per-agent scopes, and the two can disagree.
- **The dropdown refreshed on every DOM mutation** — a `MutationObserver` on
  `document.body` ran `build()` + a POST `refresh()` on every mutation batch
  (typing in the composer was enough to flood the backend). It now acts only
  when the control is actually missing, debounced; and the `select()` error
  path re-reads state inline instead of calling `refresh()`, which
  early-returns while `pending` is true and therefore never re-read anything.
- **Version desync**: `install.py` expected 0.5.1 while `plugin.yaml` said
  0.5.2. Everything is synced at 0.5.3.

### Added

- `helpers/text_extract.py` — the single envelope-aware extractor for
  user-visible message text and tool-call prose.
- 11 new tests in `tests/test_caveman.py` pinning the real framework message
  contract (label + JSON envelope — the anti-fake-drift tests), clarity-store
  concurrency and corrupt-file tolerance, threshold decoupling, `dry_run`
  enforcement, envelope-safe validation/observation, and code-block-safe
  filler stripping. Suite: 73/73. `tests/test_headroom_integration.py`: 78/78.

## Unreleased (folded into 0.5.3)

### Fixed

- **`benchmarks/run.py` crashed with `ZeroDivisionError`** when a control arm
  returned 0 output tokens (seen with a reasoning model whose no-system-prompt
  baseline arm burned its entire `--max-tokens` budget on reasoning and
  emitted nothing). The `terse_total` divisions are now guarded; with an
  empty control arm the affected metrics report `None`/`0` instead of
  crashing.
- **P4.4 closed.** The live smoke test and the provider-backed A/B benchmark
  both ran against a real turn / real model; results and the two production
  realities they surfaced (shared CCR/stats DBs, safe-mode savings by content
  type) are recorded in `REMEDIATION.md`.
- **Adapter validation closed.** The audit's "headroom-ai is not installed
  here" was stale - the A0 venv has had `headroom-ai==0.38.0` since
  2026-09-24. The plugin's `normal`-mode adapter was validated 12/12 against
  the real package (router imports, content-type routing, decline guards,
  SmartCrusher, error fallback, CCR round trip). No code change needed.

- **A partial upgrade could stop the agent outright.** An install whose
  `helpers/plugins_config.py` was older than its extension files raised
  `TypeError: get_config() got an unexpected keyword argument 'agent'` from
  `get_system_prompt`, which propagated through `prepare_prompt` into
  `monologue` — where `agent.handle_exception` re-raises, so the turn died and
  the UI reported the agent as stopped. `get_config` gained its `agent`
  parameter in 0.5.0; the deployed module had not.
  `helpers/compat.py` now guards the config module exactly as it already
  guarded the state module: `config_api()` returns `None` after warning once
  with the partial-upgrade hint, and every caller skips itself instead of
  raising. `helpers/headroom/config.py` guards once on behalf of its ten
  callers and falls back to the documented defaults, which means "off".
  `missing_config_api()` reports a function whose signature is too narrow to
  accept `agent=` as well as a missing symbol — the two are the same failure,
  and a stale `get_config` looks current on disk.
- **`helpers/headroom/config.py` no longer raises at import** when the parent
  config module has no `headroom` section, which previously broke every hook
  that imported it rather than only the compression ones.

### Operational

- **Editing `helpers/compat.py` (or any `helpers/` module) needs a `run_ui`
  restart, and `python execute.py` cannot detect this.** Extension modules are
  re-read from disk on every call, but the `helpers/` modules they import stay
  cached in `sys.modules` for the life of the process. A live server therefore
  runs new caller code against an old cached `compat`, and reports
  `module 'usr.plugins.caveman.helpers.compat' has no attribute 'config_api'`
  for a function that is present on disk. `execute.py` runs in a fresh
  interpreter, so it passes and hides the problem. After changing a `helpers/`
  module, restart the UI and confirm the fix inside the live process.

### Changed

- `execute.py`'s `check_module_contract` now asserts the config guard is
  present in every caller and that the loaded `plugins_config` provides the API
  they call, so a partial upgrade is diagnosed instead of discovered mid-turn.
- 8 new tests cover the stale config module, including the exact traceback, and
  assert a healthy config still reaches `get_config` with the agent forwarded.

## 0.5.2

The plugin becomes **Caveman + Headroom — Lean Context**: Caveman's
response-style controls plus the compression half of the standalone
`headroom_compress` plugin, with separate switches. Compression is **off by
default**, so upgrading does not change what reaches your model until you
enable it. `ROADMAP_UNIFIED.md` is the working analysis.

### Added

- **Headroom input compression** under `helpers/headroom/`, `api/headroom_*`,
  `tools/`, and the `hist_add_before` / `hist_add_tool_result` /
  `message_loop_prompts_before` hooks: safe and normal modes, content routing,
  code and file-read protections, old-tool-result clearing, CCR storage and
  retrieval, per-chat overrides, statistics and a dashboard. Settings live
  under a `headroom` key so they cannot collide with Caveman's own.
- **Coexistence detection** (`helpers/headroom/coexistence.py`) plus a welcome
  banner, for when the standalone `headroom_compress` plugin is also enabled.
  Read-only by design: it warns, and never toggles the other plugin.
- **Settings migration** (`helpers/headroom/migration.py`,
  `api/headroom_migration.py`) with a read-only preview and an explicit
  `confirm` for apply. It fills in only missing keys, preserves unknown keys,
  backs up `config.json` first, and never modifies the source plugin, its CCR
  cache or its statistics.
- **Settings/dashboard modals** for compression, reachable from the Caveman
  settings page, and a pointer to the standalone-plugin import.
- **44 cross-feature tests** (`tests/test_headroom_integration.py`) covering
  compression safety, CCR recoverability, the clear-only-if-stored rule,
  coexistence detection, the migration contract, and registration uniqueness.
- **Health-check coverage for the whole combined surface**: a scan for
  accidental imports of the standalone plugin, `default_config.yaml` /
  `DEFAULTS` parity for the headroom section, one-class-per-file and
  unique-banner-id checks, and a live migration preview.
- `per_project_config` / `per_agent_config` are now `true`, so scoped configs
  resolve for both halves.

### Fixed

- **The response sanitizer and the tool-description shrinker appeared to be
  permanently switched off.** `get_plugin_config` is called with `agent=`, and
  readers that did not forward one raised `TypeError`, which
  `plugins_config.get_config()` swallowed into a silent fallback to defaults.
  Every Caveman call site is now agent-scoped, and a test guards them.
- **Three unit tests were failing on arrival** because the test stub's
  `get_plugin_config` did not accept `agent=`. Fixed in the stub, with a note
  explaining why the narrow signature is dangerous.
- **The settings API read the global config** rather than the caller's scope,
  so the topbar dropdown could disagree with the prompt the chat received.

### Changed

- The standalone plugin's `caveman_bridge` output-savings estimator was **not**
  ported. It multiplied observed output length by a per-level constant and
  reported the product as savings — the retracted claim in a different hat,
  and a double count against Caveman's own observation store. Output
  observations now have exactly one owner, `helpers/state.py`.
- Log prefixes are `[caveman/headroom…]` instead of `[headroom_compress…]`.
- Health-check messages name the failing suite and report the standalone
  plugin's state.

## 0.5.1

Fixes a reported production crash caused by a partial upgrade, and makes the
plugin degrade instead of taking down the agent turn.

### Fixed

- **`AttributeError: module 'usr.plugins.caveman.helpers.state' has no attribute
  'resolve'`, raised from `_20_caveman_style.py` inside
  `Agent.get_system_prompt`, which killed the agent turn.** The plugin directory
  held v0.5.0 extension modules beside a v0.4.0 `helpers/state.py`; `state.resolve`
  is new in v0.5.0. `agent.handle_exception` re-raises, so an optional styling
  plugin could abort a monologue. The same exposure existed in the two
  `message_loop_result` extensions, the tool shrinker, and the state API.
- **An optional plugin now degrades instead of raising.** `helpers/compat.py`
  checks the state API before use. On a mismatch it logs one warning naming the
  cause and the fix, then skips itself for the turn. Reads over HTTP keep
  working and writes are refused with a reason, rather than the WebUI getting a
  500.
- **`execute.py` no longer crashes on the fault it is meant to diagnose.**
  `check_state` ran before the new contract check and called `state.resolve`
  itself, so a partial upgrade produced the identical `AttributeError` instead
  of a diagnosis. The cross-module contract check now runs first and stops the
  run with a clear message.

### Added

- `helpers/compat.py`: `REQUIRED_STATE_API`, `missing_state_api()`,
  `state_api()`, and warn-once reporting.
- A cross-module contract check in `execute.py`, which asks the loaded modules
  what they call on each other. Per-file checks cannot see a version mismatch;
  this is the question a partial upgrade gets wrong.
- 6 tests: the stale-state module is reproduced, and each affected extension plus
  the HTTP API is asserted to degrade. Two new mutation cases in
  `tests/test_healthcheck.py` remove the v0.5.0 API from `state.py` and strip
  the compat guard from an extension, and assert the health check catches both.
  Suite is now 51 unit tests and 15 self-tests.

## 0.5.0

Correctness, honesty, and tests. Three of the plugin's features could not
execute at all, and the metrics reported were derived from a number upstream
has since retracted.

### Fixed — features that could never run

- **`/caveman` never matched.** The user-message probe looked for
  `loop_data.last_user_message` / `.last_message` / `.messages` and required
  each to be a `str`. `LoopData` has none of those attributes, and
  `loop_data.user_message` is a `history.Message`. The probe now uses the
  framework's `Message.output_text()`.
- **The topbar dropdown reported success for every click.** It POSTed
  `action: "set"` to a handler that implemented `get | set_level |
  set_enabled | list`, received HTTP 200 with `{"ok": false}`, and the
  `.then()` still closed the menu and updated the label. Added a real atomic
  `set` action; the client now checks `ok` and surfaces failures.
- **The response validator was inert.** It sat on `response_stream_end`, which
  is called with only `loop_data=` and therefore carries no response text, and
  it "sanitized" by assigning `kwargs[key] = ...`, which cannot reach the
  caller because `helpers.extension.call_extensions_async` builds a fresh
  `kwargs` dict per extension. Its warning path also appended to
  `agent.context.extras`, which is not a framework attribute. Moved to
  `message_loop_result`, where a mutation reaches history before
  `hist_add_ai_response`.
- **The tool shrinker was inert.** It sat on `message_loop_prompts_before`,
  which fires at the start of `prepare_prompt` before `loop_data.system` is set
  and before any tool payload exists, and read `loop_data.tools`, which does
  not exist. Moved to `chat_model_call_before` and now rewrites `description` in
  the payload that `call_chat_model_turn` reads back after the hook.
- **State resolved from environment variables the framework never sets.** It read
  `AGENT_WORKDIR` / `A0_WORKDIR`, so every install on a host collapsed onto one
  machine-global `~/.cache/agent0/caveman` directory, outside the workdir and
  outside the container volume. Now uses
  `get_settings()["workdir_path"]`.
- **Concurrent writes could be lost.** The extension, the API handler and the
  stats reader each held their own lock over their own copy of the
  read-modify-write path. All file access now goes through `helpers/state.py`
  under one lock, with atomic `os.replace` writes.
- **The health check could not fail.** It printed
  `WARN: could not import helpers.state` and then `health check PASSED`, and it
  put the wrong directories on `sys.path` to get that import. It now treats any
  failure as a failure and additionally executes the state round-trip, the API
  action contract, slash command classification, and the level ruleset.
- **`install.py` could delete its own source.** With `--prefix` pointing at the
  real Agent Zero root, `src == dst`, so it ran `shutil.rmtree` on the running
  plugin. Guarded, with root auto-detection and `--check-only`.
- **`webui/config.html` bound three settings nothing read**, including
  `risk_floor_pct`, whose description promised a rolling-10-turn HUD that turns
  red. Removed; the remaining settings now drive real code paths.
- **The WebUI sent no CSRF token** (bare `fetch`), polled every 2 seconds for
  the page lifetime, and cleared the host `x-extension` slot's `innerHTML`.
  Switched to `fetchApi` / `callJsonApi`, made polling event-driven, and
  appends instead of clearing.
- **Subagent profiles shipped `agent.system.main.role.md`**, which replaces the
  framework's inherited base role rather than layering on it. Moved to
  `agent.system.main.specifics.md`.
- **Role prompts claimed a tool was unavailable.** `agent.yaml` has no tool
  field in this framework, so `Bash` is present and only self-restraint
  prevents its use. The prompts now say the limit is self-imposed.

### Fixed — the reported numbers were not measurements

Upstream retracted its headline figure. From `docs/HONEST-NUMBERS.md` on
current `main`: output reduction is "Not published", and *"earlier stats
releases applied a fixed 65% output ratio without a committed reviewed
result."*

- **The 65% claim is gone** from the manifest, hub index, README, DOX, prompt
  fragments and skills. `execute.py` fails if an unverifiable percentage
  reappears; a line that quotes the number in order to explain the retraction
  must opt out with an explicit `claim-guard: allow` marker.
- **The stats counter measured the wrong string.** It read
  `loop_data.last_response` at `monologue_end`, which
  `hist_add_ai_response` assigns from `llm_result.function_calls_text()`. On the
  terminal turn that is the `response` tool-call JSON, not the prose the user
  read. Observation now happens at `message_loop_result`, on
  `llm_result.response`, after validation, so the recorded length is the length
  that was kept.
- **No fabricated estimate is stored.** The store records observed turns and
  observed output characters, per level, and nothing else.

### Fixed — the benchmark measured nothing

- The old harness never used the plugin's prompts, had no control arm, and
  reported `100 * total_saved / total_est_tokens`, which simplifies
  algebraically to the hardcoded ratio it started from. `--level full` printed
  65% forever, and `--dry-run` never called a model at all.
- Replaced with a two-arm A/B — `__baseline__`, a `Answer concisely.` control,
  and one arm per level — matching upstream's method. Reports median, mean,
  min, max and stdev, records raw output, and reports a **break-even**: the
  style prompt is re-sent every turn and costs roughly 750–850 input tokens
  per turn, so on terse workloads it can exceed the output reduction. Upstream
  documents a measured net-loss case in issue #145.

### Added

- `helpers/compress.py` — port of upstream `caveman-shrink`'s `compress.js`,
  with protected segments, `(?<![\w-])` boundaries and position-matched `sure`
  so `just-in-time` (#1055) and `make sure` (#1073) survive, and a CJK
  short-circuit (#575).
- `helpers/markdown.py` and a validated `caveman-compress` CLI. The skill was
  prose only, with no script and no validation. It now compresses, validates,
  writes a `.original.md` backup, and refuses to write when validation fails.
- A mode-transition log (`mode-log.jsonl`) recording only real transitions, so
  observed output is attributable to the level that produced it. Exposed as
  `{"action": "history"}` on the stats API.
- `prompts/caveman.intensity.md` — one level-filtered ruleset replacing six
  per-level files that had drifted from the base style. The model no longer sees
  levels it is not running. Maintainer comments are stripped before injection,
  which also cut about 580 characters per turn off the prompt.
- `tests/test_caveman.py` (45 tests) and `tests/test_healthcheck.py` (13
  mutation cases that assert the health check catches each one). The port
  previously shipped with no tests, which is how three inert features survived.
- New health-check guards: unverifiable savings claims, per-level ratio tables,
  false tool-availability claims, unparsed intensity levels, and a fabricated
  estimator returning to the store.

### Changed

- Version 0.4.0 → 0.5.0 across `plugin.yaml`, `hooks.py`, `install.py` and the
  style prompt, which had drifted to 0.1.0.
- The response sanitizer is now opt-in (`sanitize_responses`, default false), only
  runs at `ultra` and `wenyan-*`, only on a turn that is producing the
  user-visible answer, logs every strip, and will not empty a response. It is
  phrase deletion, not rewriting.
- `shrink_tools` stays default-off; it rewrites the contract the model reads
  when calling a tool.
- The `caveman-stats` skill reports observations and the transition history, and
  explains how to get a real number instead of estimating one.

## 0.1.0 – 0.4.0

Initial port: style prompt injection, six intensity levels, auto-clarity rules,
per-chat state, slash commands, an HTTP API, five sub-skills, three cavecrew
subagent profiles, a welcome-screen banner, a WebUI selector, a discovery
banner, and an installer. See `ROADMAP.md` for the state of the v0.4.0 tree and
the items that were found broken in 0.5.0.
