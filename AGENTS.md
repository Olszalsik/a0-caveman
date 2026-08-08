# caveman

> Cuts 65% of output tokens (measured) by speaking like a caveman. A prompt-injection style plugin that rewrites the agent's outbound text into ultra-short, low-token forms while keeping semantics intact.

**Version:** 0.4.0 · **Plugin ID:** `caveman`

## Purpose

Cuts 65% of output tokens (measured) by speaking like a caveman. A prompt-injection style plugin that rewrites the agent's outbound text into ultra-short, low-token forms while keeping semantics intact.

## Ownership / Layout

- `agents/caveman/agent.yaml` — caveman-flavored agent profile
- `benchmarks/` — token-savings benchmark scripts and historical results
- `extensions/` — WebUI status badge
- `prompts/` — the caveman system prompt(s)
- `skills/` — guided skills (when to apply caveman style)
- `install.py` — CLI installer; runs from a terminal or the Plugins UI's execute.py button

## Local Contracts

- `install.py` is a CLI script (NOT a WebUI asset). It uses `Path(__file__).resolve().parent` to find itself, so it works from any location. Do not move it to `webui/` — the asset server's 403 guard is irrelevant for a CLI script, and moving it would break the Plugins-UI 'execute.py' button workflow.
- `agents/caveman/agent.yaml` is the only agent profile the plugin ships. Loading it from a custom subdirectory (not the root `agents/`) is intentional — keeps it isolated from the framework's own profiles.

## v2.5 Status

- v2.5 banner CTA changed from `open-plugin-config:caveman` (dead) to `open-modal:/usr/plugins/caveman/webui/config.html` (works).

## Verification

After install, the WebUI Settings → Plugins card for caveman must show token-savings stats. If it shows 'API endpoint not found', the install is broken — run `python install.py --force`.

## See also

- `plugin.yaml` — manifest (name, version, settings_sections, per_project_config, per_agent_config)
- `default_config.yaml` — defaults (referenced by `install()` and the WebUI settings UI)
- `README.md` — user-facing docs (what the plugin does from a user's perspective)
- Framework references: `helpers/plugins.py` (lifecycle), `helpers/api.py` (API dispatch), `helpers/ui_server.py` (asset serving)
