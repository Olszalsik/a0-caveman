---
name: caveman-stats
description: >
 Show estimated token usage and savings for the current session. Triggers on
 /caveman-stats or "show caveman stats". Numbers are best-effort estimates
 based on output character counts, not exact Claude-API telemetry (Agent
 Zero does not surface that by default). Output is a short, scannable
 summary - not a long report.
---

# Caveman Stats

Compute a quick, best-effort estimate of tokens saved since the start of the chat.

## Method

- Count characters in every assistant message in this chat's history (rough proxy
 for output tokens - real Claude tokens are about `chars/4` for English, slightly
 more for CJK).
- Apply the average per-prompt reduction for the active level:
 - `lite`: ~30%
 - `full`: ~65%
 - `ultra`: ~80%
 - `wenyan-*`: ~70% (estimated; varies widely by language)
- Multiply: `est_saved_tokens = total_chars / 4 * reduction_fraction`

## Output

Short summary, no decoration:

```
[caveman-stats] chat: <id> | level: <level> | turns: <n> | chars: <n> | est tokens saved: <n>
```

If the user asked for `--share` style, also include a one-line shareable:

```
[caveman-stats] caveman on /a0 - <n> tokens saved / <n> turns
```

## Boundaries

Read-only. Does not modify state. Does not call external services. Numbers are
estimates; label them so.
