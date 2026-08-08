---
name: caveman-help
description: >
 Quick-reference card for all caveman modes, skills, and commands.
 One-shot display, not a persistent mode. Trigger: /caveman-help,
  "caveman help", "what caveman commands", "how do I use caveman".
---

# Caveman Help (Agent Zero port)

Display this reference card when invoked. One-shot - do NOT change mode, write
flag files, or persist anything. Output in caveman style.

## Modes

| Mode | Trigger | What change |
|------|---------|-------------|
| **Lite** | `/caveman lite` | Drop filler. Keep sentence structure. |
| **Full** | `/caveman` | Drop articles, filler, pleasantries, hedging. Fragments OK. Default. |
| **Ultra** | `/caveman ultra` | Extreme compression. Bare fragments. Tables over prose. |
| **Wenyan-Lite** | `/caveman wenyan-lite` | Classical Chinese style, light compression. |
| **Wenyan-Full** | `/caveman wenyan-full` | Full 文言文. Maximum classical terseness. |
| **Wenyan-Ultra** | `/caveman wenyan-ultra` | Extreme. Ancient scholar on a budget. |

Mode stick until changed or chat end.

## Skills (Agent Zero port)

Loadable via `skills_tool: load`:

| Skill | Trigger | What it do |
|-------|---------|-----------|
| **caveman-commit** | `/caveman-commit` | Terse commit messages. Conventional Commits. <=50 char subject. |
| **caveman-review** | `/caveman-review` | One-line PR comments: `L42: bug: user null. Add guard.` |
| **caveman-compress** | `/caveman-compress <file>` | Compress .md files to caveman prose. Saves ~46% input tokens. |
| **caveman-stats** | `/caveman-stats` | Show estimated tokens saved this session. |
| **caveman-help** | `/caveman-help` | This card. |

## Cavecrew subagents

Usable via `call_subordinate(profile="<name>", ...)`:

| Profile | Role | Tools |
|---------|------|-------|
| **cavecrew-investigator** | Read-only code locator. Returns file:line table. | Read, Grep, Glob, Bash |
| **cavecrew-builder** | Surgical 1-2 file edit. Refuses 3+ file scope. | Read, Edit, Write, Grep, Glob |
| **cavecrew-reviewer** | PR/diff review. One-line findings. | Read, Grep, Glob, Bash |

## Deactivate

Say "stop caveman" or "normal mode". Resume anytime with `/caveman`.

## Per-chat state (Agent Zero specific)

Unlike the Claude Code version, caveman port stores the active level per chat
in `<workdir>/.caveman/state.json`. One chat can be on `ultra` while another
is on `lite`.

## Configure Default Mode

Default mode = `full`. Change it in **Settings -> Developer -> Caveman**,
or by editing `default_config.yaml`:

```yaml
enabled: true
level: ultra
auto_clarity: true
```

## More

Full upstream docs: <https://github.com/juliusbrussee/caveman>
Plugin source: `/a0/usr/plugins/caveman/`
