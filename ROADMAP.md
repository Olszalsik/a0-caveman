# Caveman Plugin - Roadmap

**Current state:** v0.5.2 — the plugin is now **Caveman + Headroom**, the
combined product described in `ROADMAP_UNIFIED.md`. The health check and 97
tests pass (53 Caveman + 44 cross-feature), and 13 health-check self-tests
prove the check can still fail. Agent Zero v2.2 compatible.

The integration work is tracked in `ROADMAP_UNIFIED.md`; this file keeps the
Caveman-only concerns (live runtime validation, the real-model A/B, community
release preparation).

This roadmap was written during v0.4.0, when the plugin had no tests and three
of its features could not execute. Those items are marked done rather than
proposed. Items that were built on a retracted number or on an extension point
that carries no data are marked removed, with the reason, so they are not
re-attempted.

---

## Done in v0.5.1

- Keep each state update and its mode-transition log row inside the same
  process lock. Without that, concurrent mode changes could appear in the log
  in a different order from the state writes.
- Compare only prompts with the full repeat count in every benchmark arm;
  partial provider failures must not make arm sample sizes incomparable.

Correctness. Three features shipped non-functional and now work:

- The slash command could never match. It probed `loop_data.last_user_message`
  and gated every branch on `isinstance(obj, str)`, but `LoopData` has no such
  attribute and `user_message` is a `history.Message`. Now reads
  `Message.output_text()`.
- The topbar dropdown POSTed an action the API did not implement, got HTTP 200
  with `{"ok": false}`, and updated the label anyway. Added a real atomic
  `set` action and made the client check `ok`.
- The response validator sat on `response_stream_end`, which is called with
  only `loop_data=` and builds a fresh `kwargs` dict per extension. Moved to
  `message_loop_result`, where a mutation actually reaches history.
- The tool shrinker sat on `message_loop_prompts_before`, which fires before any
  tool payload exists. Moved to `chat_model_call_before`.
- State resolved from `AGENT_WORKDIR` / `A0_WORKDIR`, which the framework never
  sets, so every install on a host shared one directory outside the workdir.
  Now uses `get_settings()["workdir_path"]`.

Honesty. The headline 65% figure was upstream's retracted number
(`docs/HONEST-NUMBERS.md`: output reduction "Not published"). Removed from the
manifest, hub index, README, DOX, prompts and skills. The stats store now records
observed turns and characters and nothing else, and `execute.py` fails if an
unverifiable percentage reappears.

Benchmark. The old harness never used the plugin's prompts, had no control arm,
and reported a figure that simplified algebraically to its own hardcoded
constant. Replaced with a two-arm A/B (`__baseline__`, `__terse__`, one arm per
level) that calls a real model, reports median/mean/min/max/stdev, records raw
output, and reports a break-even against the prompt's input cost.

Tests. `tests/test_caveman.py` (45) and `tests/test_healthcheck.py` (13). The
second mutates one thing at a time and asserts the health check catches each
one, so the check cannot become a rubber stamp.

Also: per-chat state, tool-description compression ported from upstream
`compress.js` (protected segments, `(?<![\w-])` boundaries, position-matched
`sure`, CJK guard), a validated `caveman-compress` CLI, a mode-transition log,
one level-filtered intensity ruleset instead of seven drifted prompt files, and
subagent profiles moved to the `agent.system.main.specifics.md` slot.

---

## Still worth doing

### 1. Real-world smoke test (do this first)

The plugin has never been exercised inside a running Agent Zero chat. Every
test here stubs the framework, so the extension wiring itself is unverified
against a live runtime.

- Reload the WebUI, open a chat, type `/caveman ultra`, confirm the level applies.
- Switch through all six levels, then `/caveman off`, confirm the style reverts.
- Click every entry in the topbar selector, including `off`.
- Enable `shrink_tools`, confirm tool descriptions shorten and that tool calls
  still resolve.
- Enable `sanitize_responses` at `ultra`, confirm a filler opener is stripped and
  logged.
- Check `Settings -> Developer -> Caveman` binds and saves.

### 2. Benchmark with a real model

```bash
python benchmarks/run.py --model gpt-4o-mini --repeats 3 --output results.json
```

Commit the raw file alongside any number quoted from it. Upstream's bar is
committed raw pairs plus separate review, not a headline percentage.

### 3. Community listing maintenance

`plugin-hub/index.yaml`, `thumbnail.png`, `CHANGELOG.md`, and `CONTRIBUTING.md`
are present. The community listing still advertises v0.4.0 and includes the
retracted 65% claim, so refresh the listing metadata and README when preparing
the next public release. Keep the listing aligned with the local README's
honest-numbers policy.

### 4. Polish

- Keyboard shortcut to toggle caveman for the current chat.
- `/caveman-stats` as a slash command. It is a skill today; a command would be
  more discoverable. Format the observed numbers, not a saving.
- A level badge near the chat input.
- Dark-mode icon variant if the current one does not read on dark themes.

### 5. Optional

- **Per-project defaults.** Per-chat works; per-project is the natural next
  scope. `plugin.yaml` would need `per_project_config: true`.
- **Memory integration.** If the user picks the same level across many chats,
  remember it and suggest it on a new chat. Advisory only.
- **Per-profile defaults.** `plugin.yaml` would need `per_agent_config: true`.
- **Auto-level suggestion.** Advisory, never automatic.
- **Multi-chat sync.** A config flag defaulting to off; per-chat is the default.
- **Prometheus export** of the observation counters.
- **MCP server wrapper.** Upstream ships `caveman-shrink` as an MCP middleware
  for external tool descriptions. Local tool shrinking already works via
  `chat_model_call_before`; the MCP path would cover tools from other servers.

---

## Removed, with the reason

- **A "risk floor" HUD and `risk_floor_pct`.** The setting was bound in the
  settings UI and read by nothing. A risk floor needs a measured quality signal
  and a threshold that means something; inventing both produced a slider that
  did nothing. If this is wanted, it needs a real quality metric first.
- **High-water-mark and rolling-window stats** (`hwm`, `risk=ok`). Never
  implemented, and the format string that referenced them would have printed
  invented values.
- **A `caveman-stats` HUD showing "tokens saved".** There is no control arm at
  runtime, so there is no saving to show. The HUD, such as it is, shows observed
  turns and characters and labels them as observations.
- **Benchmarking against a "63.5% dry-run estimate".** The dry run returned a
  number algebraically equal to its own input constant. It is gone; there is no
  number without a model call.
- **Growing the `REDUCTION_FRACTION` table.** That table is the retracted
  figure. Re-adding it in any form is a regression, and `execute.py` fails if a
  per-level ratio table reappears.
