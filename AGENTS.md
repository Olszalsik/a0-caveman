# caveman

> Squeezes the assistant's own wording into ultra-short form while keeping full technical accuracy. Prompt-based: it changes how the model writes, not what it knows.

**Version:** 0.5.0 · **Plugin ID:** `caveman`

## Purpose

Reduce the length of the assistant's own prose by injecting a style prompt
into the system prompt. Prompt-based only: no output rewriting is on by
default, and the plugin makes no claim about how large the effect is.

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
- `tests/test_caveman.py` — unit tests, also run by `execute.py`
- `tests/test_healthcheck.py` — proves the health check can fail
- `install.py` — CLI installer and `--check-only` runner
- `execute.py` — health check; must exit non-zero on any failure

## Local Contracts

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
  other's read-modify-write.
- **Resolve enabled + level through `caveman_state.resolve(chat_id, config)`.**
  Every extension must. Resolving the default level and the enabled flag
  independently is how the WebUI and the prompt injector previously disagreed
  about the same chat.
- **`execute.py` must be able to fail.** Do not downgrade a failed import or a
  failed assertion to a warning. `tests/test_healthcheck.py` mutates one thing
  at a time and asserts the check catches each one; run it after touching
  anything the check inspects.
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

## Extension points

| Point | File | Notes |
|---|---|---|
| `system_prompt` | `_20_caveman_style.py` | Appends style + level + auto-clarity. |
| `monologue_start` | `_30_caveman_command.py` | Reads `loop_data.user_message` via `Message.output_text()`. |
| `chat_model_call_before` | `_60_caveman_shrink_tools.py` | Rewrites `description` in `call_data["a0_responses_function_tools"]`, which `call_chat_model_turn` reads back after the hook. |
| `message_loop_result` | `_50_caveman_validate.py` | Runs before `hist_add_ai_response`, so a mutation reaches history. |
| `message_loop_result` | `_60_caveman_observe.py` | After validate, so recorded length is the kept length. |
| `banners` | `_10_caveman_discovery.py` | Welcome-screen card. |

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

## Verification

- `python execute.py` — full health check, non-zero on failure. Runs the unit
  suite, the state round-trip, the API action contract, slash command
  classification, prompt parsing, and the claim and tool-availability guards.
- `python install.py --check-only` — same check via the installer.
- `python tests/test_caveman.py` — unit tests (also run by `execute.py`).
- `python tests/test_healthcheck.py` — proves the check can fail.
- `python benchmarks/run.py --validate` — fixtures and prompt fragments only,
  no model call and no invented numbers.

## See also

- `README.md` — user-facing docs and the honest-numbers section
- `benchmarks/README.md` — A/B method and its limits
- `helpers/plugins_config.py.DEFAULTS` — the authoritative settings list
- Framework references: `helpers/plugins.py` (lifecycle, config),
  `helpers/api.py` (API dispatch, CSRF), `helpers/extension.py` (dispatch),
  `helpers/responses_tools.py` (tool payload shape),
  `helpers/subagents.py` (profile schema), `helpers/settings.py` (workdir)
