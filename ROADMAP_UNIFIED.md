# Caveman + Headroom Integration Roadmap

**Status (2026-09-28, plugin v0.5.3):** P0, P1, P2 and P4.1–P4.3 are
implemented and covered by tests. P3.1–P3.3 are implemented except the
per-scope write, which is deliberately reported as manual work (see
"Scope handling" below). What remains is P4.4: a live Agent Zero smoke test
and a provider-backed A/B benchmark. Neither can be faked, and no savings
claim is made until they are run.

Two audits have run: `AUDIT.md` (2026-09-27 — safety fix, storage
performance, known issues) and `REMEDIATION.md` (2026-09-28 — 34 findings
including the message-envelope contract that left three features dead in
production; all fixed).

| Area | State |
|---|---|
| P0 feature contracts, settings model, migration rules | done (`helpers/headroom/config.py`, `helpers/headroom/migration.py`) |
| P0.4 coexistence detection | done (`helpers/headroom/coexistence.py`, banner `_20_headroom_coexistence.py`) — read-only, by design |
| P1 port into Caveman-owned paths | done (`helpers/headroom/`, `api/headroom_*.py`, `tools/`, extensions, `webui/`) |
| P1.2 no accidental standalone imports | done, enforced by `execute.py` and a test |
| P2 hooks, protections, CCR, stats, dashboard | done |
| P2.5 the old Caveman bridge | **removed, not ported.** See "Decisions" below. |
| P3 migration, coexistence UX, install notice | done (global scope only) |
| P4.1–P4.3 both suites + registration/ordering checks | done |
| P4.4 live smoke test + A/B benchmark | **outstanding** — needs a live runtime and a provider |

## Product proposal

**Recommended name:** Caveman + Headroom — Lean Context  
**Plugin ID / directory:** keep `caveman`  
**Short description:** “Pair concise response-style controls with protected context compression and recoverable originals, so more of each conversation stays useful.”

The name makes both capabilities discoverable. Avoid promising a fixed token or percentage saving: Caveman's style prompt has an input cost, Headroom's compression is content-dependent, and the net result must be measured on representative tasks before making a savings claim.

## Goal and boundaries

Create the unified product by extending the existing Caveman plugin with Headroom's capabilities while retaining both plugins' existing behavior. Keep `usr/plugins/headroom_compress` intact as the standalone Headroom plugin so it can continue to be developed independently. Caveman is the base that gets the new visible name; do not merge changes back into or rename the standalone Headroom plugin.

Recommended visible title: `Caveman + Headroom — Lean Context`. Retain the existing plugin directory and ID `caveman` to preserve the extension/package namespace, global configuration lookup and most existing state URLs. Existing Headroom users receive migration guidance and can roll back to the standalone plugin. The Caveman experience must retain its six response-style levels, per-chat state and slash commands, prompt assembly, auto-clarity, optional tool-description shrinking, optional response sanitizer, observations, skills, subagent profiles, and benchmark harness. The Headroom experience must retain safe and normal modes, content routing and SmartCrusher, eligible tool/user/history compression, code/read protections, old-result clearing only after durable CCR storage succeeds, original retrieval, thresholds, per-scope config, and its dashboard/statistics.

## Current integration seams

- Caveman owns response generation style and per-chat style state in `usr/plugins/caveman`; its plugin config is currently global and `per_project_config` / `per_agent_config` are false.
- Headroom owns input/history transformations, CCR storage/retrieval, compression statistics, and per-chat/project/agent configuration in `usr/plugins/headroom_compress`.
- Headroom currently contains a Caveman bridge for approximate output-side observations. This is an integration seam, not a replacement for Caveman's own observation store or a measured savings figure. The combined plugin should avoid double-counting these observations.
- Headroom's hooks and helpers import from the hard-coded `usr.plugins.headroom_compress` package. Copying files unchanged into Caveman would continue to load the standalone plugin's code/config/cache and is not a valid integration.
- Both plugins register framework hooks and user-facing controls. Running both enabled while the combined plugin is active risks duplicate transformations, confusing switches, or duplicate UI/actions.
- Headroom is pinned to `headroom-ai==0.38.0` with `--no-deps`; the existing adapter was smoke-tested against 0.39.0, but its pipeline-level veto API is not yet exposed through the adapter used here.

## Roadmap

### P0 — Freeze feature contracts and migration behavior

1. Use the feature inventory above as the acceptance checklist. Record current defaults and behavior from both manifests/configs; do not silently change a default during consolidation. Keep Caveman config keys and `.caveman` per-chat state in place.
2. Define one combined settings model with separately controllable Caveman style and Headroom compression switches. Preserve current per-chat Caveman overrides and Headroom global/project/agent/chat precedence. Store imported Headroom settings under a dedicated `headroom` config section to avoid key collisions. Keep Headroom's capability gates and Caveman's existing safety defaults (`shrink_tools: false`, `sanitize_responses: false`).
3. Specify migration reads for prior Headroom configs. Caveman config and per-chat state remain at their existing paths. Migration must be idempotent, preserve unknown keys, never overwrite an explicit combined-plugin choice, and leave source files untouched. Provide a preview/report and backup before any write.
4. Define coexistence behavior: when the combined plugin is active, detect an enabled standalone `headroom_compress` and warn/disable its overlapping hooks through documented user action. Do not silently edit or toggle another plugin's state. Keep one Caveman plugin identity/directory so two copies of Caveman hooks cannot be installed as separate active plugins.
5. Record a rollback procedure: disable combined plugin, restore the previous plugin toggles/configs, and verify original cache/state paths remain available.

### P1 — Extend Caveman as the combined plugin

1. Evolve the existing `usr/plugins/caveman` plugin in place; set its visible title and description to the combined branding while retaining `name: caveman`. Keep the `usr/plugins/headroom_compress` directory and manifest untouched for standalone development and rollback.
2. Port Headroom implementation into Caveman-owned module paths (for example `helpers/headroom/`) and systematically update imports, config readers, cache/stat paths, API endpoints, UI URLs, banner IDs, tool names and extension IDs. No combined-plugin runtime code may import mutable config/cache helpers from `usr.plugins.headroom_compress` by accident.
3. Decide whether existing command/tool names remain aliases (`/caveman`, `headroom_retrieve`, `headroom_compress_text`) and add namespaced aliases only where needed. Prevent duplicate tool registration and preserve current calling contracts.
4. Preserve the Headroom dependency pin and `--no-deps` installation path. Setup must report missing package/unsupported runtime clearly and must not upgrade Agent Zero's shared dependencies.

### P2 — Integrate behavior without coupling the feature implementations

1. Port Caveman's prompt builder, levels, state resolver, slash command, dropdown/settings, optional tool-description shrinker, optional response sanitizer, observation UI/API, skills, subagent profiles and benchmark. Keep its honest-numbers policy and use the existing prompt builder as the single source of injected prompt text.
2. Port Headroom's safe/normal adapters, thresholds, hooks, safe failure behavior, read/code protections, history token recounting, old tool-result clearing guard, CCR storage/retrieval, statistics, dashboard and on-demand tools.
3. Keep hook responsibilities distinct: Caveman prepares assistant response style/tool descriptions; Headroom transforms eligible input/history. Never send system prompts, initial role messages or tool arguments through content compression. Protect file reads and detected source code under the existing defaults.
4. Integrate the two dashboards/settings surfaces under one plugin entry while retaining distinct switches and clear descriptions of scope. Preserve raw Caveman observations separately from Headroom transform events; do not infer provider savings from either stream.
5. Replace or remove the old Headroom Caveman bridge only after proving the combined metrics have one owner and do not double-count output observations.

### P3 — Migration, coexistence, and user experience

1. Add a dry-run migration command/API that reports standalone Headroom source configs at global/project/agent/chat scopes, selected destination values, conflicts, and backup paths. Caveman settings and `.caveman` state stay at their current paths. Require an explicit apply action for writes.
2. Make migration repeatable and test precedence/conflicts, malformed input, missing files, interrupted writes, rollback, and data-path permissions. Never delete old config, Caveman state, CCR entries, or stats during migration.
3. Add a clear install/upgrade notice explaining that Caveman now includes Headroom capabilities and overlaps with standalone Headroom. Keep standalone Headroom installable and maintain its independent README and release flow.
4. Keep branding honest: describe it as response-style controls plus context compression. Do not market it as a guaranteed “token saver” until provider-side input/output usage and task quality are measured with a reproducible A/B benchmark.

### P4 — Verification and release gate

1. Run Caveman's existing health check and tests, Headroom's complete regression/integration suite, and new combined-plugin tests from the combined plugin's own namespace.
2. Add cross-feature tests for: each feature independently enabled; both enabled; both disabled; Caveman per-chat override with Headroom project/agent config; auto-clarity skip; protected read/code inputs; long history; durable CCR recovery; settings and dashboard actions; missing Headroom dependency; standalone-plugin conflict detection; and migration/rollback.
3. Verify extension ordering and that no hook/tool/API/UI/banner is registered twice. Confirm Headroom failures do not break Caveman, Caveman state failures do not break Headroom, and optional extension failures do not abort an agent turn.
4. Run a live Agent Zero chat smoke test and repeatable provider-backed task A/B benchmark. Report input/output usage, output quality/task success, prompt-token overhead, compression/retrieval outcomes, model, settings and raw results. Make no net-savings claim unless those results support it.
5. Publish the combined plugin separately. Keep the Caveman and standalone Headroom source trees and development roadmaps independent; share only reviewed fixes explicitly ported to each target.

## Decisions taken during implementation

Recorded because the roadmap above is the original plan, and a reader
comparing the two should be able to see where the implementation deliberately
differs from it.

- **The Caveman bridge was not ported.** The standalone plugin's
  `caveman_bridge` multiplied observed response length by a per-level
  reduction constant and recorded the product as output savings. Inside this
  plugin that is not an integration seam, it is the retracted claim (upstream
  `docs/HONEST-NUMBERS.md`: output reduction "Not published") wearing a
  different hat, and it would double-count output against Caveman's own
  observation store. So `helpers/headroom/caveman_bridge.py` and its
  `monologue_end` extension were left behind. The `caveman_bridge` event kind
  is still classified in `stats.py` so an old row cannot be miscounted, but
  nothing writes it and the output bucket is always zero. Output observations
  have exactly one owner: `helpers/state.py`.
- **Migration writes the global scope only.** P3.1 asks for per-scope
  reporting *and* an explicit apply action, which is what is implemented.
  Creating a per-project or per-agent destination file from inside a plugin
  needs its own permission model, and the settings modal already has a scoped
  save path for exactly that case, so scoped sources are reported with the keys
  involved and the reason they are manual. This is a narrower promise than the
  roadmap's wording and is the safer reading of it.
- **CCR and statistics databases are not migrated.** They are bounded,
  TTL-pruned caches of content the agent can regenerate or re-fetch, and
  copying rows between two schemas is where silent data loss would live. The
  plan says so in its own output instead of leaving it implied.
- **Coexistence is reported, never enforced.** P0.4 says "warn/disable its
  overlapping hooks through documented user action" and, in the same step,
  "do not silently edit or toggle another plugin's state". Those pull against
  each other, and the second constraint is the stronger one: there is no
  supported framework operation for one plugin to change another's toggle.
  Detection therefore only reads the registry, and the banner plus the
  settings section tell the user which switch to flip.
- **Tool and command names were kept as-is** (P1.3). `compress_text`,
  `headroom_retrieve` and `/caveman` keep their existing names rather than
  gaining a `caveman_` prefix. A rename would break every saved prompt,
  skill and muscle memory that already calls them, and since the standalone
  plugin is not imported and the standalone plugin's own copy is never
  registered twice, there is no collision to disambiguate. `compress_text`
  reads the toggle for `caveman`, because that is the plugin that owns it now.
- **P3.3's install/upgrade notice lives in `README.md`**, since that is what a
  user reads before enabling the plugin. The manifest description deliberately
  avoids "token saver" wording.

## Scope handling for the migration

`helpers/headroom/migration.py` reports source configs at every scope the
framework knows (global, per-project, per-agent, plus per-chat override
counts) and applies only the global scope. A global `config.json` can be read
directly, which also keeps the preview working outside the full framework
runtime, where `helpers.plugins.find_plugin_assets` is unavailable because it
imports `helpers.projects` (and therefore PIL). When scope enumeration fails,
the plan says so in `warnings` and falls back to the direct read rather than
silently reporting "nothing to migrate".

## Acceptance criteria

- The combined plugin preserves each feature listed under Goal and boundaries, with prior safety defaults and user data recoverable.
- The standalone `headroom_compress` plugin remains unchanged by the integration and can continue separate development.
- Integrated Headroom code uses Caveman-owned module/API names and namespaced config/data paths; Caveman's established state remains readable, and no combined hook accidentally imports mutable standalone Headroom plugin code.
- Caveman observations and Headroom transform metrics are distinct, accurately labeled, and not double-counted or presented as guaranteed savings.
- Migration is previewable, idempotent, non-destructive, conflict-aware, and reversible.
- Both original test suites and the combined cross-feature suite pass; live smoke/benchmark limitations are reported clearly.

## Suggested short description for the manifest

> Caveman response-style controls plus Headroom context compression in one plugin. Tune concise replies, compress eligible logs and history, protect source reads, and retrieve stored originals through CCR. Compression and style controls remain independently configurable; token savings depend on workload.
