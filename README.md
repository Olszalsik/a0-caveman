# Caveman + Headroom — Lean Context (Agent Zero plugin)

Two independent features in one plugin:

- **Caveman** squeezes the assistant's own wording into ultra-short form while
  keeping full technical accuracy. Prompt-based: it changes how the model
  writes, not what it knows.
- **Headroom compression** shrinks eligible *input* — tool outputs, logs and
  long history — before it reaches the model, and keeps the original in a
  local reversible cache (CCR) so nothing is really lost.

They have separate switches. Compression is **off by default**, so installing
this plugin does not change what reaches your model until you turn it on.

Caveman is derived from [Julius Brussee's caveman plugin][upstream] and adapted
to Agent Zero's plugin conventions. The compression half was ported from the
standalone `headroom_compress` plugin, which continues to exist and develop
separately. MIT licensed.

[upstream]: https://github.com/juliusbrussee/caveman

---

## If you also run the standalone `headroom_compress` plugin

The two plugins overlap: both compress tool outputs and history on the same
framework hook points. Running both at once means every eligible message is
transformed twice, two CCR caches hold separate copies of the same originals,
and the two dashboards report numbers that do not add up. Nothing crashes,
which is what makes it easy to miss.

This plugin **detects** that situation and warns you. It will never toggle,
disable or edit the other plugin for you — one plugin silently changing
another's state would leave you unable to tell which switch did what. Turn the
standalone plugin off in the Plugins UI, or leave this plugin's compression
off. Either is fine; running both is not.

**Importing your old settings.** Open *Settings → Developer → Caveman →
Headroom compression*. If the standalone plugin is installed, a *Preview
import* button appears:

- Preview reads only. It reports the source configs at each scope, the values
  it would import, the keys it will **not** touch because you already set them
  here, the per-project/per-agent scopes you have to set yourself, and the
  backup path it would use.
- Apply requires an explicit confirmation, writes a timestamped backup next to
  this plugin's `config.json`, and fills in only the keys that are *absent*.
  It never overwrites a value set here, never drops a key it does not
  recognise, and never modifies the standalone plugin's config, CCR cache or
  statistics.

**Rolling back.** Disable this plugin in the Plugins UI and re-enable
`headroom_compress`. Nothing else is needed: this plugin never wrote into the
standalone plugin's config, cache or statistics, and your per-chat Caveman
levels are untouched either way.

---

## What Caveman does

Injects a system-prompt fragment that tells the model to answer in tight
caveman-speak: drop articles, filler, pleasantries, hedging. Code, commands,
errors and technical terms stay byte-exact.

Six intensity levels:

| Level | Same sentence, shrunk |
|---|---|
| `lite` | Wrap object in `useMemo`. New ref created every render. |
| `full` *(default)* | New ref each render. Wrap object in `useMemo`. |
| `ultra` | New ref/render. `useMemo` it. |
| `wenyan-lite` | 組件頻重繪，以每繪新生對象參照故。以 useMemo 包之。 |
| `wenyan-full` | 每繪新生對象參照，故重繪；以 useMemo 包之則免。 |
| `wenyan-ultra` | 新參照則重繪。useMemo 包之。 |

---

## Honest numbers

**This plugin does not claim a percentage, and neither does upstream.**

Upstream's original "cuts 65% of output tokens (measured)" headline has been
<!-- quotes the retracted headline in order to explain it; claim-guard: allow -->
retracted. From upstream `docs/HONEST-NUMBERS.md` on current `main`:

> Output reduction vs default verbose replies — **Not published**. Harness
> exists, but repository has no committed reviewed raw result.
>
> "Earlier stats releases applied a **fixed 65% output ratio without a
> committed reviewed result**. Current reports ignore those historical
> `est_saved_*` fields."

Earlier versions of this port inherited that number and reported it as a live
measurement. They no longer do.

What this plugin actually reports, in **Settings → Developer → Caveman** and
via `POST /api/plugins/caveman/caveman_stats`, is a count of observed turns
and observed output characters. Those are `observed`, not `measured savings`.
A saving is a difference against a control you did not run.

To get a real number for your workload:

1. Run the same task with caveman off, then on, and compare **provider-billed
   totals**. That outranks any local estimate.
2. Or use the harness, which runs a real A/B against a terse control arm:

```bash
python benchmarks/run.py --model gpt-4o-mini --repeats 3 --output results.json
```

See `benchmarks/README.md` for the method and its limits.

**When caveman loses.** The style prompt is re-sent every turn, so it has a
fixed input cost of roughly 750-850 tokens per turn on the shipped prompt
fragments. On terse workloads, or under per-request / per-message pricing,
that cost can exceed the output reduction. Upstream documents a measured
net-loss case in
[issue #145](https://github.com/juliusbrussee/caveman/issues/145). Measure
before enabling it broadly.

---

## Install

The plugin lives at `usr/plugins/caveman/`. To enable:

1. Open **Settings → Developer** in the WebUI.
2. Find the **Caveman** section.
3. Toggle **Enabled** ON and pick a **Level** (default: `full`).
4. Save, then reload the WebUI so the topbar selector appears.

Or edit `config.json` / `default_config.yaml`.

### Settings

| Key | Default | Effect |
|---|---|---|
| `enabled` | `false` | Master switch. |
| `level` | `full` | Default level for chats with no override. |
| `auto_clarity` | `true` | Tell the model to drop the style for security warnings, irreversible operations, and ambiguous multi-step sequences. Recommended. |
| `shrink_tools` | `false` | Compress tool descriptions before each model call. Off by default: tool descriptions are the contract the model reads when calling a tool. |
| `sanitize_responses` | `false` | Delete banned filler phrases from the model's output at `ultra` and `wenyan-*`. Off by default: it is phrase deletion, not rewriting. |

`execute.py` fails if `config.json` or `webui/config.html` names a setting
that no backend code reads, so a decorative toggle cannot be added silently.

---

## Per-chat control

Each chat keeps its own level, so one chat can run `ultra` while another runs
`lite`. State: `<workdir>/.caveman/state.json`.

### Slash commands

| Command | Effect |
|---|---|
| `/caveman` | Turn ON at the default level. |
| `/caveman lite` … `/caveman wenyan-ultra` | Switch this chat to that level. |
| `/caveman off`, `normal mode`, `stop caveman`, `disable caveman` | Turn OFF for this chat. |
| `/caveman on`, `talk like caveman`, `use caveman`, `caveman on` | Turn ON for this chat. |

### Topbar selector

A level dropdown sits at the end of the chat input actions. It writes through
the same API as the slash commands and reports real failures instead of
optimistically updating the label.

---

## HTTP API

`POST /api/plugins/caveman/caveman_state` (auth + CSRF protected):

```jsonc
{ "action": "get",         "chat_id": "<id>" }
{ "action": "set",         "chat_id": "<id>", "level": "ultra" }   // implies enabled
{ "action": "set",         "chat_id": "<id>", "level": "off" }     // implies disabled
{ "action": "set",         "chat_id": "<id>", "enabled": false }
{ "action": "set_level",   "chat_id": "<id>", "level": "wenyan-full" }  // null clears
{ "action": "set_enabled", "chat_id": "<id>", "enabled": true }    // null clears
{ "action": "clear",       "chat_id": "<id>" }
{ "action": "list" }
```

`POST /api/plugins/caveman/caveman_stats`:

```jsonc
{ "action": "get",     "chat_id": "<id>" }   // observed turns + chars, by level
{ "action": "history", "chat_id": "<id>" }   // level transitions, oldest first
{ "action": "summary" }
{ "action": "list" }
{ "action": "reset",   "chat_id": "<id>" }
```

`history` is what makes the observations attributable: it records the level
actually in force at each transition, so output can be attributed to the mode
that produced it rather than to whatever the mode is when you read the stats.
Only real transitions are logged.

All handlers return HTTP 200 with an `ok` flag, so clients must check `ok`.

---

## Sub-skills

| Skill | Purpose |
|---|---|
| `caveman-stats` | Show observed turns and output length for this session. |
| `caveman-commit` | Terse Conventional Commits. Subject <=50 chars. |
| `caveman-review` | One-line PR review comments. |
| `caveman-compress` | Compress `.md` memory files into caveman-speak. |
| `caveman-help` | Help card. |

---

## Cavecrew subagents

| Profile | Role |
|---|---|
| `cavecrew-investigator` | Read-only code locator. Returns a `path:line` table. |
| `cavecrew-builder` | Surgical 1-2 file editor. Refuses 3+ file scope. |
| `cavecrew-reviewer` | PR/diff reviewer. One-line findings. |

Use with `call_subordinate(profile="cavecrew-investigator", ...)`.

---

## How it is wired

| Extension point | File | Role |
|---|---|---|
| `system_prompt` | `_20_caveman_style.py` | Injects style + the active level's rules + auto-clarity. |
| `monologue_start` | `_30_caveman_command.py` | Reads the user message, applies slash commands. |
| `chat_model_call_before` | `_60_caveman_shrink_tools.py` | Compresses tool descriptions (off by default). |
| `message_loop_result` | `_50_caveman_validate.py` | Filler detection, optional strip. |
| `message_loop_result` | `_60_caveman_observe.py` | Records observed output length per turn. |
| `banners` | `_10_caveman_discovery.py` | Welcome-screen discovery card. |
| WebUI `page-head` | `caveman-injector.html` | Loads the topbar selector. |

The level rules live in a single ruleset file, `prompts/caveman.intensity.md`,
and the plugin filters it to the active level. The model therefore never sees
the levels it is not running.

## Compressing files

`caveman-compress` has a real script now, and it validates before it writes:

```bash
S=usr/plugins/caveman/skills/caveman-compress/scripts/compress.py

python $S --check notes.md   # report only
python $S notes.md           # compress in place, writes notes.md.original.md
python $S --stdout notes.md  # preview
```

It refuses to write when validation fails, because a false pass would overwrite
your file with unvalidated output. Validation checks that fenced code blocks,
inline code, URLs, definite paths and the heading sequence all survived, and
that any file containing CJK round-trips byte-identically.

Compression protects code, inline code, URLs, paths, identifiers and version
numbers, short-circuits on CJK, and uses `(?<![\w-])` word boundaries so that
`just-in-time` and `make sure` survive. Text containing any CJK character is
returned unchanged, because English deletion rules are not valid for mixed CJK
prose.

## Health check

```bash
python usr/plugins/caveman/execute.py                 # full check, non-zero on failure
python usr/plugins/caveman/install.py --check-only
python usr/plugins/caveman/tests/test_caveman.py      # unit tests
python usr/plugins/caveman/tests/test_healthcheck.py  # proves the check can fail
```

`execute.py` runs the unit suite and exercises the state round-trip, the API
action contract, slash command classification, prompt parsing, and the claim
and tool-availability guards. It fails if the WebUI sends an action the API does
not implement, if a settings key is bound but unread, if state would land
outside the workdir, if a level stops parsing, or if an unverifiable
percentage reappears.

`tests/test_healthcheck.py` mutates one thing at a time and asserts the check
catches each one, so the check cannot rot into a rubber stamp.

## Troubleshooting

### `AttributeError: module 'usr.plugins.caveman.helpers.state' has no attribute 'resolve'`

The plugin directory is mixing file versions: some modules are current and
`helpers/state.py` is older. It is not a logic bug and no single file explains
it, which is why the traceback points at an innocent line.

Fix it by replacing the whole plugin directory in one step, not file by file:

```bash
cd <agent-zero-root>
rm -rf usr/plugins/caveman
# copy the plugin back, completely
python usr/plugins/caveman/execute.py    # must print 'health check PASSED'
```

Clearing `__pycache__` alone does not help, because the stale source file is
what gets loaded.

Since v0.5.1 this no longer breaks a chat turn. The plugin detects the
mismatch, logs one warning naming the cause, and skips itself for the turn; the
API keeps serving reads and refuses writes. `execute.py` also reports it
directly instead of crashing:

```
[caveman] FAIL: helpers/state.py does not provide the API the shipped modules call: resolve
[caveman] stopping: the remaining checks call the state module directly and would fail the same way.
```

`python usr/plugins/caveman/tests/test_healthcheck.py` covers this case.

## License & attribution

MIT. Original caveman plugin by Julius Brussee
(<https://github.com/juliusbrussee/caveman>). Agent Zero port by Agent Zero
contributors. See `LICENSE`.
