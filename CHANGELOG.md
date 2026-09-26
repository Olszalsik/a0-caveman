# Changelog

All notable changes to this plugin. The plugin is an Agent Zero port of
[juliusbrussee/caveman][upstream] (MIT), so upstream's own release notes apply
to the style rules and the level set.

[upstream]: https://github.com/juliusbrussee/caveman

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
