# Caveman + Headroom — production-readiness audit (2026-09-27)

Audit of `usr/plugins/caveman` v0.5.2 (the unified Caveman + Headroom plugin),
covering `helpers/headroom/`, the extension hooks, the API handlers, the tools
and the WebUI assets. Verdict and remediation status below.

> **Read `REMEDIATION.md` too (2026-09-28).** A follow-up review found that
> this audit's F1 fix was protecting a flag that production could never set —
> the user-message contract behind it was wrong in three shipped code paths,
> and the test fakes masked it. 34 findings, all fixed; version 0.5.3.

Baseline before this audit: health check green, 107 tests. After: green, 114
tests, plus the performance work.

## Verdict

**Conditionally production ready.** The architecture is sound and the safety
posture is genuinely well thought out — CCR recoverability, "clear only if
durably stored", read-only coexistence detection, preview-first migration, and
an honest-numbers policy that is machine-enforced. The defects found were
concentrated in three places, and all three are now fixed:

1. a real safety hole in the destructive history-clearing path,
2. a 20x performance tax on the agent's hot path,
3. two unbounded-growth / O(n)-per-write problems in the local stores.

What is *not* done, and cannot be faked, is unchanged from the roadmap: a live
Agent Zero smoke test and a provider-backed A/B benchmark. Until those run,
**no savings claim may be made** — see the Numbers section of `AGENTS.md`.

## Fixed in this audit

### F1 — Auto-clarity did not protect the destructive path (severity: high)

`_05_auto_clarity` sets a per-context skip flag when the user's message matches
a destructive command. `_10_compress_history` consumed that flag.
`_07_clear_old_tool_results` — the hook that actually deletes live tool output
from history — never read it.

Consequence: on a turn containing something like `rm -rf /` or
`git push --force`, the tool results were cleared anyway. The safety feature
protected only the compression pass while leaving the destructive pass
unprotected, which is the inverse of the intent.

Fix: `_07` now calls the new non-destructive `clarity.peek_skip()`. It must
*peek* rather than *consume*, because `_10` runs later in the same extension
point and still needs the flag; consuming in `_07` would have protected one
stage and silently un-protected the next. `peek_skip` and `consume_skip` share
`_live_reason()` so the TTL rule cannot drift between them.

Verified by reverting the guard: the new test fails without the fix and passes
with it. Four tests cover this (flag blocks clearing; clearing still works
unflagged; peek is non-destructive; expired flags protect nothing).

### F2 — ~49 ms of fixed overhead per tool result (severity: high)

Both local stores opened **and closed** a SQLite connection per operation. On
Windows, `Connection.close()` on a WAL database performs a checkpoint and
measured ~15 ms by itself. A `StatsRecorder` was constructed for every
compression *and every skip* (the skip path was the more expensive half,
since a below-threshold result still opened a database to record that it was
skipped), and a `CcrCache` for every stored original.

Measured: **48.7 ms per tool result**, against ~1 ms of actual work. On a
turn with 20 tool results this is a full second of pure latency.

Fix: both modules pool one connection per resolved path and `close()` returns
the handle to the pool. Safe because writes autocommit under
`isolation_level=None` (nothing is pending at close), the pool and the callers
each hold a lock, and the "clear only if stored" rule still requires a live
SQLite connection before any destructive edit.

Result: **2.3 ms per tool result — 20.8x faster** (80-iteration measurement).
Schema creation is now also once per path rather than per instantiation.

### F3 — Stats log grew without bound (severity: medium)

The CCR cache has a TTL and a byte budget. The `events` table had neither, and
one row is written per compression *and* per skip. The standalone plugin's copy
had reached 12,345 rows; a busy long-lived install grows without limit, and the
dashboard aggregates scan the whole table.

Fix: `StatsRecorder._prune()` applies `stats_retention_days` (default 30) and
`stats_max_rows` (default 50,000), both newly documented in
`default_config.yaml` and `DEFAULTS`. Uses existing indexes; prunes to
`max_rows - 1` because it runs before the caller's INSERT; failures are logged
rather than raised.

### F4 — Full-table scan on every CCR write (severity: medium)

`put()` ran `SELECT SUM(length(original)) FROM ccr` before every insert. There
is no index that can serve an aggregate over `length(blob)`, so this scanned
every stored original on every single write. On the standalone plugin's
6,799-row / 87 MB cache that is expensive and grows linearly.

Fix: a cheap `page_count * page_size` bound is checked first; the exact `SUM`
runs only when the budget may be exceeded. The bound is never used as the
payload figure (it includes indexes and free pages), so eviction arithmetic
stays exact and the cache still honours `ccr_max_bytes`.

## K1-K4 - resolved after review

The four lower-severity items were left open in the first pass because each
needed a product decision. They were then approved and fixed. All four are
closed; one of them turned out to be a genuine bug once the file lock was
written.

### K1 - `expose_compress_tool` was a dead checkbox -> now gates the tool

Agent Zero registers plugin tools by directory convention and exposes no
runtime hook to withdraw one, so the tool genuinely cannot be unregistered.
Rather than delete the setting or leave a control that does nothing, the
setting now gates the capability: with it off, `compress_text` refuses and
says which setting to enable. `toggle_info` and `estimate` stay available in
that state, because refusing read-only diagnostics would only make the setting
harder to debug. The WebUI help text now states the behaviour instead of
implying the tool disappears.

### K2 - `never_compress_system_prompts` was unread -> now enforced

System prompts are built into `loop_data.system` and never enter history, so
the guarantee held for a structural reason. `Message` carries no `role`
field, so the check is `metadata["role"] == "system"`, a
`is_system_prompt`/`system_prompt` metadata marker, or a `role` in the content
envelope. `_is_system_prompt()` is consulted by the history walk, so the
setting is now read for real and the guarantee survives a framework change
instead of resting on an assumption.

A UI checkbox for this key was deliberately *not* added: the value is not
user-adjustable in any meaningful sense, and adding a control that cannot
change anything would reproduce K1.

### K3 - per-chat overrides lost updates across processes -> file lock added

`per_chat.py` guarded read-modify-write with a `threading.Lock`, which only
serialises threads inside one process. A `_FileLock` (O_CREAT|O_EXCL, no
platform-conditional imports) now guards `set_enabled` and `clear` as well.
It is deliberately best-effort with a bounded 2s wait: if the lock cannot be
taken the write still proceeds through the existing atomic temp-file +
`os.replace` path, so the lock closes the race window without becoming a new
failure mode. A lock left behind by a dead process is broken after a staleness
timeout, and `_FileLock` accepts a `str` path as well as a `Path`.

Verified: 6 concurrent threads x 20 writes produced 120/120 entries, zero
lost. That probe is now a permanent test.

### K4 - proxy could signal a recycled PID -> ownership verified

`stop()` signals a process *group*, so a stale `proxy.pid` whose PID had been
recycled would have signalled an unrelated process and possibly several
others. `_verify_ownership()` now confirms the PID is really the headroom
proxy - the configured binary basename *and* the `proxy` subcommand must both
appear in the command line, read from `/proc/<pid>/cmdline` - and refuses,
clears the stale file, and reports instead of signalling.

Design note: the check returns a three-tuple `(owned, reason, could_verify)`.
Where the command line cannot be read at all, that is reported as *unknown*,
not as *someone else's*, and the previous behaviour is kept - refusing to
stop on Windows would be a regression, and the default binary is a Linux
path. The result always states which case occurred.

## Still open

None. The audit's original "headroom-ai is not installed here" note was
stale: the A0 venv has had `headroom-ai==0.38.0` (pip, 2026-09-24) since
before this audit was written. The real adapter was validated 2026-09-28 -
see REMEDIATION.md, "Adapter validation closed".

## Regression coverage added

Twelve tests, all previously unverified behaviours:

| Test | Guards |
|---|---|
| `test_auto_clarity_prevents_destructive_clearing` | F1 |
| `test_clearing_still_works_when_no_safety_flag_is_set` | F1, against over-blocking |
| `test_peek_skip_does_not_consume_the_flag` | F1 ordering contract |
| `test_expired_safety_flag_protects_nothing` | F1 TTL correctness |
| `test_stats_retention_bounds_an_unbounded_log` | F3 |
| `test_stats_age_retention_drops_only_expired_rows` | F3 |
| `test_ccr_respects_its_byte_budget_under_pressure` | F4 |
| `test_expose_compress_tool_actually_gates_the_tool` | K1 |
| `test_never_compress_system_prompts_is_enforced_not_assumed` | K2 |
| `test_per_chat_concurrent_writers_lose_nothing` | K3 |
| `test_per_chat_lock_accepts_a_string_path` | K3 robustness |
| `test_per_chat_lock_survives_concurrent_writers` | K3 release/staleness |
| `test_proxy_refuses_to_signal_a_recycled_pid` | K4 |
| `test_proxy_ownership_is_conservative_when_unverifiable` | K4 honesty about unknown |

Suite totals: `test_caveman.py` 63, `test_headroom_integration.py` 58, 121 in
the health check.

Note on the K4 tests: they stub `os.kill` rather than signalling for real.
`os.kill(os.getpid(), 0)` hangs on some Windows hosts, which is a platform
quirk rather than anything the plugin controls - the behaviour under test is
the decision made for each liveness answer, not the signal delivery.

## Remaining roadmap

P4.4 is unchanged and is the only thing between this plugin and a full
production sign-off. Neither item can be faked or automated offline, and
both need a live Agent Zero instance:

1. **Live Agent Zero smoke test** - enable both switches, drive a real turn
   with large tool output, and confirm compressed history, a working
   `headroom_retrieve` round trip, and a correct token budget in the UI.
2. **Provider-backed A/B benchmark** - `benchmarks/run.py` against a real
   model on a representative workload, to produce the break-even figure the
   Numbers section already promises. Until then the honest position is
   unchanged: raw observations only, no percentage claim.

## Method

Findings were confirmed by reading the shipped code and by measuring it, not
by inspection alone:

- the auto-clarity gap was proved by grepping `_07` for any safety-flag
  reference (none) and then by reverting the new guard and watching the new
  test fail;
- the 48.7 ms figure came from timing 60 real `StatsRecorder` and `CcrCache`
  open/operate/close cycles, then `cProfile`-ing one to attribute the cost
  (0.602 s of 0.852 s was `Connection.close`);
- the unbounded-growth and O(n) findings came from querying the standalone
  plugin's actual databases (12,345 event rows; 6,799 CCR rows / 87 MB);
- the header-accumulation and double-compression hypotheses were tested
  against a 5-turn simulation and disproved, so they are not listed as bugs.
