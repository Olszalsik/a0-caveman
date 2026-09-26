---
name: caveman-stats
description: >
  Show what caveman has actually observed for the current chat: assistant turns
  produced and output characters recorded, broken down by level, plus the level
  transition history. Triggers on /caveman-stats or "show caveman stats".
  Reports observations only, never an estimated saving. Output is a short,
  scannable summary - not a long report.
---

# Caveman Stats

Report what the plugin observed. Do not report a saving.

## Method

Read the observation store through the plugin's own API. It is auth + CSRF
protected, so use the framework's `callJsonApi` helper rather than a bare
`fetch`.

```jsonc
// observed turns and characters for one chat
POST /api/plugins/caveman/caveman_stats
{ "action": "get", "chat_id": "<id>" }

// level transitions, oldest first
POST /api/plugins/caveman/caveman_stats
{ "action": "history", "chat_id": "<id>" }
```

Response fields:

- `turns` - assistant turns observed with caveman active
- `chars` - output characters across those turns
- `by_level` - the same two counts, split by level
- `last_level` - the level in force for the most recent turn

All handlers return HTTP 200 with an `ok` flag. Check `ok`; on `ok: false` read
`error` and say so rather than reporting zeros as a real result.

## Do not report a saving

A saving is a difference against a control arm that was never run. Earlier
versions of this skill applied a fixed per-level ratio to the observed length
and reported the product as "tokens saved"; upstream retracted exactly that
number, and this skill no longer has the table.

If the user asks "how much am I saving", the honest answer is:

1. Here is what was observed: `<turns>` turns, `<chars>` characters.
2. The style prompt is re-sent every turn, so it costs roughly 750-850 input
   tokens per turn on the shipped text, depending on level.
3. To get a real number, compare provider-billed totals for the same task with
   caveman on and off, or run `benchmarks/run.py`, which does a two-arm A/B
   against a terse control.

## Output

Short summary, no decoration:

```
[caveman-stats] chat: <id> | level: <level> | turns: <n> | chars: <n>
```

With a per-level breakdown when more than one level was used:

```
[caveman-stats] chat: <id> | turns: <n> | chars: <n>
  full: <n> turns / <n> chars
  ultra: <n> turns / <n> chars
```

With the transition history when the user asks how the level changed:

```
[caveman-stats] chat: <id> | transitions:
  <ts> off -> full
  <ts> full -> ultra
```

## Boundaries

Read-only. Does not modify state, does not call external services, and does not
write to `stats.json`. If `turns` is 0, say caveman was not active for any
observed turn in this chat rather than reporting a zero saving.
