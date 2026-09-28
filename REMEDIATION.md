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

## Deliberately left open

- P4.4 (unchanged from AUDIT.md): live Agent Zero smoke test and the
  provider-backed A/B benchmark. Neither can be automated offline.
- `headroom-ai==0.38.0` adapter validation (still not installed here).
- ApiHandler agent-scoping is a framework limitation: handlers have no agent.
  The WebUI reads global scope; per-project/per-agent configs are honoured on
  the model-facing path only. Documented rather than papered over.