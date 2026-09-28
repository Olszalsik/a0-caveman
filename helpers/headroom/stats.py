"""
Headroom Context Compression - stats recorder.

Writes one row per compression / retrieval event into a local SQLite DB. Used
by the dashboard to show real savings and by the user to verify the plugin is
actually doing work.

The recorder is a no-op if `stats_enabled` is false in the config, so users
who care about maximum performance can disable persistence entirely.
"""

from __future__ import annotations

import json
import hashlib
import sqlite3
import sys
import threading
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any

from usr.plugins.caveman.helpers.headroom import config as _config

_TOOL_REPEAT_LOCK = threading.Lock()
_TOOL_REPEAT_WINDOW_SECONDS = 1800
_TOOL_REPEAT_MAX_KEYS = 4096
_TOOL_REPEAT_RECENT: OrderedDict[tuple[str, bytes], float] = OrderedDict()


# ---------------------------------------------------------------------------
# Shared connections
# ---------------------------------------------------------------------------
# Opening (and especially closing) a SQLite connection costs far more than
# the single INSERT this recorder performs. On Windows, `Connection.close()`
# on a WAL database runs a checkpoint and measured ~15ms on its own, so a
# recorder built per event cost ~24ms per tool result - pure overhead on the
# hot path, repeated for every compressed message and every skipped one.
#
# Connections are therefore pooled per resolved database path and shared.
# `close()` now returns the connection to the pool instead of closing it;
# the schema is created once per path rather than on every instantiation.
#
# Two invariants make this safe:
#   * every caller already holds a per-instance lock, and the pool adds its
#     own, so a shared connection is never used concurrently;
#   * `isolation_level=None` means SQLite autocommits each statement, so a
#     row written before `close()` is already durable and readable by any
#     other connection. Returning rather than closing loses nothing.
#
# Tests that assert on a database they then delete must call
# `close_all_connections()` first: on Windows an open handle makes the
# directory undeletable, which is a test-harness concern, not a data one.
_DB_POOL_LOCK = threading.Lock()
_DB_POOL: "OrderedDict[str, sqlite3.Connection]" = OrderedDict()
_DB_POOL_MAX = 8


def _acquire_connection(path: Path) -> sqlite3.Connection | None:
    """Return a shared, ready connection for `path`, or None on failure."""
    key = str(path)
    with _DB_POOL_LOCK:
        existing = _DB_POOL.get(key)
        if existing is not None:
            _DB_POOL.move_to_end(key)
            return existing

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(key, check_same_thread=False, isolation_level=None)
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
    except Exception:  # noqa: BLE001
        return None

    with _DB_POOL_LOCK:
        # Another thread may have installed one while we connected.
        existing = _DB_POOL.get(key)
        if existing is not None:
            _DB_POOL.move_to_end(key)
            conn.close()
            return existing
        _DB_POOL[key] = conn
        while len(_DB_POOL) > _DB_POOL_MAX:
            _evicted_key, evicted = _DB_POOL.popitem(last=False)
            try:
                evicted.close()
            except Exception:  # noqa: BLE001
                pass
        return conn


def close_all_connections() -> None:
    """Close and forget every pooled connection.

    Used by tests that delete a database directory, and safe to call at any
    time: the next `StatsRecorder` simply reconnects.
    """
    with _DB_POOL_LOCK:
        while _DB_POOL:
            _key, conn = _DB_POOL.popitem()
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass


class StatsRecorder:
    def __init__(self, cfg: dict[str, Any]):
        self.cfg = cfg
        self.enabled = bool(cfg.get("stats_enabled", True))
        self._lock = threading.Lock()
        self._db: sqlite3.Connection | None = None
        if self.enabled:
            self._init_db()

    def _print(self, msg: str) -> None:
        sys.stderr.write(f"[caveman/headroom.stats] {msg}\n")
        sys.stderr.flush()

    def _db_path(self) -> Path:
        return _config.stats_db_path(self.cfg)

    def _init_db(self) -> None:
        path = self._db_path()
        self._db = _acquire_connection(path)
        if self._db is None:
            self._print("stats db connect failed; disabling stats")
            return
        try:
            self._ensure_schema()
        except Exception as exc:  # noqa: BLE001
            self._print(f"stats db init failed ({exc}); disabling stats")
            # Drop the handle: a half-initialised database must not be reused.
            with _DB_POOL_LOCK:
                if _DB_POOL.get(str(path)) is self._db:
                    _DB_POOL.pop(str(path), None)
            try:
                self._db.close()
            except Exception:  # noqa: BLE001
                pass
            self._db = None

    def _ensure_schema(self) -> None:
        """Create/patch the schema. Cheap and idempotent on an existing DB."""
        self._db.executescript(
            """
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts REAL NOT NULL,
                kind TEXT NOT NULL,
                source TEXT,
                input_tokens INTEGER NOT NULL DEFAULT 0,
                output_tokens INTEGER NOT NULL DEFAULT 0,
                saved_tokens INTEGER NOT NULL DEFAULT 0,
                duration_ms REAL NOT NULL DEFAULT 0,
                repeat_output INTEGER NOT NULL DEFAULT 0,
                details TEXT
            );
            CREATE INDEX IF NOT EXISTS events_ts ON events(ts);
            CREATE INDEX IF NOT EXISTS events_kind ON events(kind);
            """
        )
        columns = {row[1] for row in self._db.execute("PRAGMA table_info(events)")}
        if "repeat_output" not in columns:
            try:
                self._db.execute(
                    "ALTER TABLE events ADD COLUMN repeat_output INTEGER NOT NULL DEFAULT 0"
                )
            except sqlite3.OperationalError:
                # Another process/thread may have migrated the same
                # database after our PRAGMA read. Treat that race as
                # success only if the column now exists.
                columns = {row[1] for row in self._db.execute("PRAGMA table_info(events)")}
                if "repeat_output" not in columns:
                    raise
        self._prune()

    def _prune(self) -> None:
        """Bound the events table by age and by row count.

        The stats database is an append-only observation log, so without a
        retention policy it grows without limit: a long-lived install
        accumulates one row per compression *and* one per skip, and a busy
        agent reaches tens of thousands of rows within weeks. Unlike the CCR
        cache these rows are pure telemetry, so dropping the oldest is safe
        and is what keeps the dashboard queries cheap.

        Deliberately cheap: one indexed count and, only when over the limit,
        one indexed DELETE. The events_ts index makes the age delete and the
        primary key makes the row-count delete cheap.

        Called at the end of `_ensure_schema`, which runs before the caller's
        INSERT. The row-count branch therefore trims down to `max_rows - 1`
        so the table settles at exactly `max_rows` after that insert, rather
        than overshooting the cap by one on every open.
        """
        if self._db is None:
            return
        try:
            max_age_days = int(self.cfg.get("stats_retention_days", 0) or 0)
            if max_age_days > 0:
                cutoff = time.time() - max_age_days * 86400
                self._db.execute("DELETE FROM events WHERE ts < ?", (cutoff,))

            max_rows = int(self.cfg.get("stats_max_rows", 0) or 0)
            if max_rows > 1:
                keep = max_rows - 1
                total = int(
                    self._db.execute("SELECT COUNT(*) FROM events").fetchone()[0] or 0
                )
                if total > keep:
                    # Keep the newest `keep` rows. The inner ORDER BY ... LIMIT
                    # is served by the primary key, so no full-table sort.
                    self._db.execute(
                        "DELETE FROM events WHERE id NOT IN ("
                        "  SELECT id FROM events ORDER BY id DESC LIMIT ?"
                        ")",
                        (keep,),
                    )
        except Exception as exc:  # noqa: BLE001
            # Retention is best-effort housekeeping; never fail a turn over it.
            self._print(f"stats prune skipped: {exc}")

    def record(
        self,
        kind: str,
        source: str | None = None,
        input_tokens: int = 0,
        output_tokens: int = 0,
        duration_ms: float = 0.0,
        details: dict[str, Any] | None = None,
        repeat_output: bool = False,
    ) -> None:
        if not self.enabled or self._db is None:
            return
        saved = max(0, int(input_tokens) - int(output_tokens))
        details_blob = json.dumps(details or {}, ensure_ascii=False, default=str)
        with self._lock:
            try:
                self._db.execute(
                    """
                    INSERT INTO events(ts, kind, source, input_tokens, output_tokens,
                                       saved_tokens, duration_ms, repeat_output, details)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        time.time(),
                        kind,
                        source,
                        int(input_tokens),
                        int(output_tokens),
                        saved,
                        float(duration_ms),
                        int(bool(repeat_output)),
                        details_blob,
                    ),
                )
            except Exception as exc:  # noqa: BLE001
                self._print(f"stats insert failed: {exc}")

    def observe_tool_output(self, source: str | None, content: str) -> bool:
        """Detect exact repeats within this process without persisting fingerprints."""
        if not self.enabled or not source or not source.startswith("tool:"):
            return False
        digest = hashlib.sha256(content.encode("utf-8", errors="replace")).digest()
        now = time.monotonic()
        key = (source, digest)
        with _TOOL_REPEAT_LOCK:
            while _TOOL_REPEAT_RECENT:
                _old_key, seen_at = next(iter(_TOOL_REPEAT_RECENT.items()))
                if now - seen_at <= _TOOL_REPEAT_WINDOW_SECONDS:
                    break
                _TOOL_REPEAT_RECENT.popitem(last=False)
            repeated = key in _TOOL_REPEAT_RECENT
            _TOOL_REPEAT_RECENT[key] = now
            _TOOL_REPEAT_RECENT.move_to_end(key)
            while len(_TOOL_REPEAT_RECENT) > _TOOL_REPEAT_MAX_KEYS:
                _TOOL_REPEAT_RECENT.popitem(last=False)
        return repeated

    def summary(self, since: float | None = None) -> dict[str, Any]:
        """Return aggregate counters for the dashboard. If `since` is given
        (unix timestamp), only events newer than that are counted.

        v0.3.0: returns per-kind (input vs output side) so the dashboard can
        show the multiplication effect when Headroom is stacked with Caveman.
        """
        empty = {
            "enabled": False,
            "count": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "saved_tokens": 0,
            "avg_duration_ms": 0.0,
            "by_side": {
                "input": {"count": 0, "saved_tokens": 0},
                "output": {"count": 0, "saved_tokens": 0},
                "other": {"count": 0, "saved_tokens": 0},
            },
            "by_kind": {},
            "tool_output_repeats": {
                "observations": 0,
                "repeats": 0,
                "rate": 0.0,
                "window_seconds": _TOOL_REPEAT_WINDOW_SECONDS,
            },
            "ccr_retrievals": {"hit": 0, "miss": 0, "expired": 0, "error": 0},
        }
        if not self.enabled or self._db is None:
            return empty
        params: tuple = ()
        where = ""
        if since is not None:
            where = " WHERE ts >= ?"
            params = (float(since),)
        try:
            cur = self._db.execute(
                f"""
                SELECT COUNT(*),
                       COALESCE(SUM(input_tokens),0),
                       COALESCE(SUM(output_tokens),0),
                       COALESCE(SUM(saved_tokens),0),
                       COALESCE(AVG(NULLIF(duration_ms, 0)),0.0)
                FROM events{where}
                """,
                params,
            )
            count, in_t, out_t, saved, avg = cur.fetchone()

            cur2 = self._db.execute(
                f"""
                SELECT kind, COUNT(*), COALESCE(SUM(saved_tokens),0)
                FROM events{where}
                GROUP BY kind
                """,
                params,
            )
            by_kind: dict[str, dict[str, int]] = {}
            for kind, k_count, k_saved in cur2.fetchall():
                by_kind[kind] = {
                    "count": int(k_count or 0),
                    "saved_tokens": int(k_saved or 0),
                }

            repeat_since = " AND ts >= ?" if since is not None else ""
            cur3 = self._db.execute(
                "SELECT COUNT(*), COALESCE(SUM(repeat_output),0) FROM events "
                "WHERE kind='compress_text' AND source LIKE 'tool:%'" + repeat_since,
                params,
            )
            observations, repeats = cur3.fetchone()
            observations, repeats = int(observations or 0), int(repeats or 0)
            ccr_retrievals = {
                status: int(by_kind.get(f"ccr_retrieve_{status}", {}).get("count", 0))
                for status in ("hit", "miss", "expired", "error")
            }

            input_total = {"count": 0, "saved_tokens": 0}
            output_total = {"count": 0, "saved_tokens": 0}
            other_total = {"count": 0, "saved_tokens": 0}
            for kind, bucket in by_kind.items():
                if kind in (
                    "compress_text",
                    "compress_history",
                    "compress_tool_output",
                    "compress_user_message",
                    "compress_skip",
                    "shrink_tool_descriptions",
                    "retrieve",
                    "clear:tool_results",
                ) or kind.startswith(("tool:", "history:", "hist_add_before:")):
                    input_total["count"] += bucket["count"]
                    input_total["saved_tokens"] += bucket["saved_tokens"]
                elif kind in ("caveman_bridge",):
                    # Kept for classification only, never written.
                    #
                    # The standalone Headroom plugin recorded estimated
                    # Caveman output savings here. That writer was NOT ported:
                    # helpers/headroom/caveman_bridge.py is gone, so nothing in
                    # this plugin can emit this kind. Output-side observations
                    # have exactly one owner - Caveman's own state/stats store
                    # (helpers/state.py), which records raw observed turns and
                    # characters per level. Keeping the bucket at zero is what
                    # makes that single ownership visible instead of implied.
                    output_total["count"] += bucket["count"]
                    output_total["saved_tokens"] += bucket["saved_tokens"]
                else:
                    other_total["count"] += bucket["count"]
                    other_total["saved_tokens"] += bucket["saved_tokens"]

            return {
                "enabled": True,
                "count": int(count or 0),
                "input_tokens": int(in_t or 0),
                "output_tokens": int(out_t or 0),
                "saved_tokens": int(saved or 0),
                "avg_duration_ms": float(avg or 0.0),
                "by_side": {
                    "input": input_total,
                    "output": output_total,
                    "other": other_total,
                },
                "by_kind": by_kind,
                "tool_output_repeats": {
                    "observations": observations,
                    "repeats": repeats,
                    "rate": round(repeats / observations, 4) if observations else 0.0,
                    "window_seconds": _TOOL_REPEAT_WINDOW_SECONDS,
                },
                "ccr_retrievals": ccr_retrievals,
            }
        except Exception as exc:  # noqa: BLE001
            self._print(f"stats summary failed: {exc}")
            return empty

    def recent(self, limit: int = 50) -> list[dict[str, Any]]:
        if not self.enabled or self._db is None:
            return []
        limit = max(1, min(int(limit), 200))
        try:
            cur = self._db.execute(
                """
                SELECT id, ts, kind, source, input_tokens, output_tokens, saved_tokens,
                       duration_ms, details
                FROM events
                ORDER BY id DESC
                LIMIT ?
                """,
                (int(limit),),
            )
            out: list[dict[str, Any]] = []
            for row in cur.fetchall():
                out.append(
                    {
                        "id": row[0],
                        "ts": row[1],
                        "kind": row[2],
                        "source": row[3],
                        "input_tokens": row[4],
                        "output_tokens": row[5],
                        "saved_tokens": row[6],
                        "duration_ms": row[7],
                        "details": row[8],
                    }
                )
            return out
        except Exception as exc:  # noqa: BLE001
            self._print(f"stats recent failed: {exc}")
            return []

    def close(self) -> None:
        """Release this recorder's handle back to the pool.

        The connection is intentionally NOT closed: it is shared per path
        (see the pooling note at the top of this module), and every write
        already autocommits under `isolation_level=None`, so nothing is
        pending. Callers keep calling `close()` and the pool keeps the cost
        of opening a database off the hot path.
        """
        with self._lock:
            self._db = None
