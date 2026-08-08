# Caveman plugin for Agent Zero

Ultra-compressed communication mode for your Agent Zero agent. Same answers,
**~65% fewer output tokens** (measured upstream), full technical accuracy.

Derived from [Julius Brussee's caveman plugin][upstream] and adapted to Agent
Zero's plugin conventions. MIT licensed.

[upstream]: https://github.com/juliusbrussee/caveman

---

## What it does

Injects a system-prompt fragment that tells the model to answer in tight
caveman-speak: drop articles, filler, pleasantries, hedging. Code, commands,
errors, and technical terms stay byte-exact.

Six intensity levels are supported:

| Level | Same sentence, shrunk |
|---|---|
| `lite` | Wrap object in `useMemo`. New ref created every render. |
| `full` *(default)* | New ref each render. Wrap object in `useMemo`. |
| `ultra` | New ref/render. `useMemo` it. |
| `wenyan-lite` | 組件頻重繪，以每繪新生對象參照故。以 useMemo 包之。 |
| `wenyan-full` | 每繪新生對象參照，故重繪；以 useMemo 包之則免。 |
| `wenyan-ultra` | 新參照則重繪。useMemo 包之。 |

**Honest numbers:** caveman only shrinks *output* tokens. The skill itself adds
a small amount of input tokens per turn. The real wins are readability and
speed. Cost savings are the bonus. See upstream's
[HONEST-NUMBERS.md](https://github.com/juliusbrussee/caveman/blob/main/docs/HONEST-NUMBERS.md)
for the full breakdown.

---

## Install (local plugin)

The plugin is already placed at `/a0/usr/plugins/caveman/`. To enable it:

1. Open **Settings → Developer** in the WebUI.
2. Find the **Caveman** subsection.
3. Toggle **Enabled** ON.
4. Pick a **Level** (default: `full`).
5. Save.

Alternatively, edit `default_config.yaml` and set:

```yaml
enabled: true
level: full
auto_clarity: true
```

---

## Slash commands (per-chat)

| Command | Effect |
|---|---|
| `/caveman` | Turn ON at the default level (`full`). |
| `/caveman lite` | Switch this chat to `lite`. |
| `/caveman full` | Switch this chat to `full`. |
| `/caveman ultra` | Switch this chat to `ultra`. |
| `/caveman wenyan-lite` | Switch this chat to `wenyan-lite`. |
| `/caveman wenyan-full` | Switch this chat to `wenyan-full`. |
| `/caveman wenyan-ultra` | Switch this chat to `wenyan-ultra`. |
| `/caveman off` / `stop caveman` / `normal mode` | Turn OFF for this chat. |
| `/caveman on` / `talk like caveman` / `use caveman` | Turn ON for this chat. |

**Per-chat state is the killer feature:** one chat can be on `ultra` while
another is on `lite`. State is stored in `<workdir>/.caveman/state.json`.

---

## Sub-skills (loadable)

Loadable via `skills_tool: load` or by saying "use caveman-X":

| Skill | Purpose |
|---|---|
| `caveman-stats` | Show estimated tokens saved this session. |
| `caveman-commit` | Terse Conventional Commits. Subject <=50 chars. |
| `caveman-review` | One-line PR review comments. |
| `caveman-compress` | Compress .md memory files into caveman-speak. |
| `caveman-help` | This help card. |

---

## Cavecrew subagents (Commit 3)

| Profile | Role |
|---|---|
| `cavecrew-investigator` | Read-only code locator. Returns `path:line` table. |
| `cavecrew-builder` | Surgical 1-2 file editor. Refuses 3+ file scope. |
| `cavecrew-reviewer` | PR/diff reviewer. One-line findings. |

Use with `call_subordinate(profile="cavecrew-investigator", ...)`.

---

## HTTP API (read/write per-chat state)

`POST /api/plugins/caveman/caveman_state`

```json
// Read
{ "action": "get", "chat_id": "<id>" }
// Response: { "ok": true, "level": "full", "enabled": true, ... }

// Set level
{ "action": "set_level", "chat_id": "<id>", "level": "ultra" }

// Toggle enabled
{ "action": "set_enabled", "chat_id": "<id>", "enabled": true }

// List all chats (diagnostics)
{ "action": "list" }
```

---

## What ships in v0.1.0 (Plan A - thin prompt port)

- `plugin.yaml`, `default_config.yaml`, `hooks.py`, `execute.py`
- One `system_prompt` extension that injects the caveman style into every
  agent turn when the plugin is enabled.
- Six intensity fragments + the base style prompt + an auto-clarity fragment.
- A welcome-screen discovery banner (dismissible, low priority).

## What ships in v0.2.0 (Plan B - per-chat state + slash command)

- Per-chat level state (`<workdir>/.caveman/state.json`).
- `/caveman <level>` slash command detection (`monologue_start` extension).
- HTTP API for read/write per-chat state.
- Five sub-skills (`caveman-stats`, `caveman-commit`, `caveman-review`,
  `caveman-compress`, `caveman-help`) loadable via `skills_tool`.

## What ships in v0.3.0 (Plan B complete)

- Three cavecrew subagent profiles (investigator, builder, reviewer) usable
  via `call_subordinate(profile="cavecrew-investigator", ...)`.
- Soft response validator (`response_stream_end` extension) that flags
  banned filler phrases in the model's output as a soft warning in
  `chat_extras` (never a hard block).

## What's planned for v0.4.0 (Plan C - deep port)

- `caveman-shrink` equivalent (compress tool descriptions via the `tool_data`
  extension point).
- Live token-savings API + dashboard.
- Response sanitizer (`response_before` post-processor).
- `install.py` for community distribution.
- Reproducible benchmarks against the Agent Zero port.

---

## License & attribution

MIT. Original caveman plugin by Julius Brussee
(<https://github.com/juliusbrussee/caveman>). Agent Zero port by Agent Zero
contributors. See `LICENSE` for the full text.

```
MIT License

Copyright (c) 2025 Julius Brussee
Copyright (c) 2026 Agent Zero contributors (port to Agent Zero)
```
