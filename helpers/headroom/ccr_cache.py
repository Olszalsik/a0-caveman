"""
Headroom Context Compression - CCR (reversible compression) cache.

Stores the full original content for any string the compressor shrank, so the
LLM (or the user) can call `headroom_retrieve(key)` and get the full original
back on demand. Without this, lossy compression would be irreversible - the
LLM could only see the compressed version.

We deliberately use a *tiny* SQLite-backed implementation instead of importing
the headroom-ai CCR store directly:
  * Zero risk of clashing with the headroom-ai Rust-backed default backend.
  * Survives even when headroom-ai is not installed (e.g. on a fresh toggle-ON
    before the user has run execute.py).
  * Easy to inspect, dump, and back up from the file system.
  * Trivially prunable by TTL.

Three backends are supported but only SQLite and in_memory are implemented
locally; the headroom-ai Redis backend can be enabled in the config but we
don't take a hard dep on redis-py.
"""

from __future__ import annotations

import sqlite3
import sys
import threading
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any

from usr.plugins.caveman.helpers.headroom import config as _config

_MEMORY_REGISTRY_LOCK = threading.Lock()
_MEMORY_STORES: dict[str, dict[str, tuple[bytes, int, int, float, str | None]]] = {}
_MEMORY_STORE_LOCKS: dict[str, threading.Lock] = {}


# ---------------------------------------------------------------------------
# Shared connections
# ---------------------------------------------------------------------------
# Same reasoning as helpers/headroom/stats.py: opening and closing a SQLite
# connection per `put()` cost ~25ms, dominated by the WAL checkpoint that
# `Connection.close()` performs on Windows. Connections are pooled per
# resolved path; `close()` releases the handle back to the pool instead of
# closing it. Every write autocommits under `isolation_level=None`.
#
# Callers additionally require a durable, reopenable store before performing
# a destructive history edit (`_07_clear_old_tool_results` checks
# `backend == "sqlite"` and that the connection is live), so the "clear only
# if stored" rule is unchanged by pooling.
_CCR_POOL_LOCK = threading.Lock()
_CCR_POOL: "OrderedDict[str, sqlite3.Connection]" = OrderedDict()
_CCR_POOL_MAX = 8


def _acquire_connection(path: Path) -> sqlite3.Connection | None:
    key = str(path)
    with _CCR_POOL_LOCK:
        existing = _CCR_POOL.get(key)
        if existing is not None:
            _CCR_POOL.move_to_end(key)
            return existing
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(key, check_same_thread=False, isolation_level=None)
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
    except Exception:  # noqa: BLE001
        return None
    with _CCR_POOL_LOCK:
        existing = _CCR_POOL.get(key)
        if existing is not None:
            _CCR_POOL.move_to_end(key)
            conn.close()
            return existing
        _CCR_POOL[key] = conn
        while len(_CCR_POOL) > _CCR_POOL_MAX:
            _k, evicted = _CCR_POOL.popitem(last=False)
            try:
                evicted.close()
            except Exception:  # noqa: BLE001
                pass
        return conn


def close_all_connections() -> None:
    """Close and forget every pooled CCR connection.

    Needed by tests that delete a database directory: on Windows an open
    handle makes the containing directory undeletable.
    """
    with _CCR_POOL_LOCK:
        while _CCR_POOL:
            _key, conn = _CCR_POOL.popitem()
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass




def _iso_from_epoch(epoch: float) -> str:
    """Best-effort ISO-8601 UTC string from a Unix epoch.
    Falls back to the raw number on platforms where datetime fails.
    """
    try:
        e = float(epoch or 0.0)
    except (TypeError, ValueError):
        return ""
    if e <= 0.0:
        return ""
    try:
        from datetime import datetime, timezone
        return datetime.fromtimestamp(e, tz=timezone.utc).isoformat()
    except Exception:  # noqa: BLE001
        return f"{e:.0f}"


def _format_age(seconds: float) -> str:
    """Human-readable age string from a seconds count."""
    s = int(max(0.0, seconds))
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m{s % 60}s"
    if s < 86400:
        h, rem = divmod(s, 3600)
        return f"{h}h{rem // 60}m"
    d, rem = divmod(s, 86400)
    return f"{d}d{rem // 3600}h"


class CcrCache:
    """Thread-safe CCR store keyed by 32-char sha256 prefixes."""

    def __init__(self, cfg: dict[str, Any]):
        self.cfg = cfg
        self.backend = (cfg.get("ccr_backend") or "sqlite").lower()
        self.ttl_days = int(cfg.get("ccr_ttl_days", 7) or 0)
        raw_max_bytes = cfg.get("ccr_max_bytes", 268435456)
        self.max_bytes = max(0, int(raw_max_bytes if raw_max_bytes is not None else 268435456))
        self._lock = threading.Lock()
        self._mem: dict[str, tuple[bytes, int, int, float, str | None]] = {}
        self._db: sqlite3.Connection | None = None

        if self.backend == "in_memory":
            self._init_memory()
        elif self.backend == "sqlite":
            self._init_sqlite()
        elif self.backend == "redis":
            self._print(
                "redis backend requested - the local plugin does not depend on redis-py. "
                "Install with: pip install redis. Falling back to in_memory until then."
            )
            self.backend = "in_memory"
            self._init_memory()
        else:
            self._print(f"unknown ccr_backend={self.backend!r}, falling back to in_memory")
            self.backend = "in_memory"
            self._init_memory()

    def _init_memory(self) -> None:
        """Share memory entries across short-lived CcrCache instances."""
        store_key = str(_config.ccr_db_path(self.cfg))
        with _MEMORY_REGISTRY_LOCK:
            self._mem = _MEMORY_STORES.setdefault(store_key, {})
            self._lock = _MEMORY_STORE_LOCKS.setdefault(store_key, threading.Lock())

    def _print(self, msg: str) -> None:
        sys.stderr.write(f"[caveman/headroom.ccr] {msg}\n")
        sys.stderr.flush()

    def _db_path(self) -> Path:
        return _config.ccr_db_path(self.cfg)

    def _init_sqlite(self) -> None:
        path = self._db_path()
        self._db = _acquire_connection(path)
        if self._db is None:
            self._print("sqlite connect failed; falling back to in_memory")
            self.backend = "in_memory"
            self._init_memory()
            return
        try:
            self._db.executescript(
                """
                CREATE TABLE IF NOT EXISTS ccr (
                    key TEXT PRIMARY KEY,
                    original BLOB NOT NULL,
                    original_tokens INTEGER NOT NULL,
                    compressed_tokens INTEGER NOT NULL,
                    created_at REAL NOT NULL,
                    source TEXT
                );
                CREATE INDEX IF NOT EXISTS ccr_created_at ON ccr(created_at);
                """
            )
        except Exception as exc:  # noqa: BLE001
            self._print(f"sqlite init failed ({exc}); falling back to in_memory")
            try:
                self._db.close()
            except Exception:  # noqa: BLE001
                pass
            with _CCR_POOL_LOCK:
                if _CCR_POOL.get(str(path)) is self._db:
                    _CCR_POOL.pop(str(path), None)
            self._db = None
            self.backend = "in_memory"
            self._init_memory()

    def put(
        self,
        key: str,
        original: str,
        original_tokens: int,
        compressed_tokens: int,
        source: str | None = None,
    ) -> bool:
        if not key or not original:
            return False
        now = time.time()
        blob = original.encode("utf-8", errors="replace")
        if self.max_bytes and len(blob) > self.max_bytes:
            return False
        with self._lock:
            if self.backend == "sqlite" and self._db is not None:
                try:
                    # Evict oldest originals until the logical payload budget
                    # can accommodate this replacement. The operation is
                    # serialized by SQLite's write lock across cache instances.
                    self._db.execute("BEGIN IMMEDIATE")
                    if self.ttl_days > 0:
                        self._db.execute(
                            "DELETE FROM ccr WHERE created_at < ?",
                            (now - self.ttl_days * 86400,),
                        )
                    existing = self._db.execute(
                        "SELECT length(original) FROM ccr WHERE key = ?", (key,)
                    ).fetchone()
                    # Only pay for the full-table SUM when a budget is set and
                    # the cheap upper bound says we might be near it.
                    # SUM(length(original)) has no usable index, so on a large
                    # cache it is a full scan of every blob on every single
                    # write. A miss therefore skips it entirely; a hit falls
                    # back to the exact query, which is correct and rare.
                    if self.max_bytes:
                        approx = int(
                            self._db.execute(
                                "SELECT page_count * page_size FROM pragma_page_count(), pragma_page_size()"
                            ).fetchone()[0]
                            or 0
                        )
                        blob_len = len(blob)
                        need_exact = approx == 0 or (
                            approx + blob_len > self.max_bytes
                        )
                    else:
                        need_exact = False
                    if need_exact:
                        used = int(
                            self._db.execute(
                                "SELECT COALESCE(SUM(length(original)),0) FROM ccr"
                            ).fetchone()[0]
                            or 0
                        )
                    else:
                        # page_count includes indexes and free pages, so it is
                        # an upper bound on payload; pretending it is the
                        # payload would over-evict, so treat it as "0 used"
                        # only when we know we are under budget, and let the
                        # exact path handle everything else.
                        used = 0
                    used -= int(existing[0] or 0) if existing else 0
                    excess = used + len(blob) - self.max_bytes if self.max_bytes else 0
                    if excess > 0:
                        rows = self._db.execute(
                            "SELECT key, length(original) FROM ccr WHERE key != ? ORDER BY created_at ASC",
                            (key,),
                        ).fetchall()
                        for old_key, old_size in rows:
                            self._db.execute("DELETE FROM ccr WHERE key = ?", (old_key,))
                            excess -= int(old_size or 0)
                            if excess <= 0:
                                break
                    self._db.execute(
                        """
                        INSERT INTO ccr(key, original, original_tokens, compressed_tokens, created_at, source)
                        VALUES (?, ?, ?, ?, ?, ?)
                        ON CONFLICT(key) DO UPDATE SET
                            original=excluded.original,
                            original_tokens=excluded.original_tokens,
                            compressed_tokens=excluded.compressed_tokens,
                            created_at=excluded.created_at,
                            source=excluded.source
                        """,
                        (key, blob, original_tokens, compressed_tokens, now, source),
                    )
                    self._db.commit()
                    return True
                except Exception as exc:  # noqa: BLE001
                    try:
                        self._db.rollback()
                    except Exception:
                        pass
                    self._print(f"sqlite put failed: {exc}")
                    return False
            else:
                if self.max_bytes:
                    used = sum(len(v[0]) for k, v in self._mem.items() if k != key)
                    if used + len(blob) > self.max_bytes:
                        for old_key, entry in sorted(self._mem.items(), key=lambda item: item[1][3]):
                            if old_key == key:
                                continue
                            self._mem.pop(old_key, None)
                            used -= len(entry[0])
                            if used + len(blob) <= self.max_bytes:
                                break
                self._mem[key] = (blob, original_tokens, compressed_tokens, now, source)
                return True

    def get(self, key: str) -> str | None:
        value, _status = self.get_with_status(key)
        return value

    def get_with_status(self, key: str) -> tuple[str | None, str]:
        """Return an original and distinguish hit, miss, expiry, and I/O error."""
        if not key:
            return None, "miss"
        with self._lock:
            if self.backend == "sqlite" and self._db is not None:
                try:
                    cur = self._db.execute(
                        "SELECT original, created_at FROM ccr WHERE key = ?", (key,)
                    )
                    row = cur.fetchone()
                    if not row:
                        return None, "miss"
                    blob, created_at = row
                    if self.ttl_days > 0 and (time.time() - float(created_at)) > self.ttl_days * 86400:
                        try:
                            self._db.execute("DELETE FROM ccr WHERE key = ?", (key,))
                        except Exception:  # noqa: BLE001
                            pass
                        return None, "expired"
                    return blob.decode("utf-8", errors="replace"), "hit"
                except Exception as exc:  # noqa: BLE001
                    self._print(f"sqlite get failed: {exc}")
                    return None, "error"
            entry = self._mem.get(key)
            if not entry:
                return None, "miss"
            blob, _ot, _ct, created_at, _src = entry
            if self.ttl_days > 0 and (time.time() - created_at) > self.ttl_days * 86400:
                self._mem.pop(key, None)
                return None, "expired"
            return blob.decode("utf-8", errors="replace"), "hit"

    def list_keys(self, limit: int = 200) -> list[dict[str, Any]]:
        """Lightweight listing for the dashboard. Returns dicts with metadata,
        not the original blob (which can be huge)."""
        limit = max(1, min(int(limit), 200))
        self.prune_expired()
        out: list[dict[str, Any]] = []
        with self._lock:
            if self.backend == "sqlite" and self._db is not None:
                try:
                    cur = self._db.execute(
                        "SELECT key, original_tokens, compressed_tokens, created_at, source "
                        "FROM ccr ORDER BY created_at DESC LIMIT ?",
                        (int(limit),),
                    )
                    for row in cur.fetchall():
                        out.append(
                            {
                                "key": row[0],
                                "original_tokens": row[1],
                                "compressed_tokens": row[2],
                                "created_at": row[3],
                                "source": row[4],
                            }
                        )
                    return out
                except Exception as exc:  # noqa: BLE001
                    self._print(f"sqlite list_keys failed: {exc}")
                    return out
            for key, entry in sorted(self._mem.items(), key=lambda kv: kv[1][3], reverse=True)[:limit]:
                blob, ot, ct, ts, src = entry
                out.append(
                    {
                        "key": key,
                        "original_tokens": ot,
                        "compressed_tokens": ct,
                        "created_at": ts,
                        "source": src,
                    }
                )
        return out

    def prune_expired(self) -> int:
        """Delete entries older than the configured TTL. Returns the number of
        rows removed. Safe to call on a schedule (e.g. daily from a scheduled
        task) or on demand from the dashboard."""
        if self.ttl_days <= 0:
            return 0
        cutoff = time.time() - self.ttl_days * 86400
        with self._lock:
            if self.backend == "sqlite" and self._db is not None:
                try:
                    cur = self._db.execute("DELETE FROM ccr WHERE created_at < ?", (cutoff,))
                    return int(cur.rowcount or 0)
                except Exception as exc:  # noqa: BLE001
                    self._print(f"sqlite prune failed: {exc}")
                    return 0
            before = len(self._mem)
            self._mem = {k: v for k, v in self._mem.items() if v[3] >= cutoff}
            return before - len(self._mem)

    def get_meta(self, key: str) -> dict[str, Any] | None:
        """Return rich metadata for a CCR key without the full blob.

        Returned dict (always safe to JSON-encode):
        - key: the CCR key
        - exists: True
        - original_tokens: tokens in the original blob
        - compressed_tokens: tokens in the compressed replacement (approximate)
        - size_bytes: byte length of the original (utf-8 encoded)
        - size_chars: character length of the original
        - age_seconds: seconds since the entry was stored
        - created_at: epoch seconds
        - created_at_iso: ISO-8601 UTC string for human display
        - source: optional source label passed at compression time
        - preview: first 400 chars of the original (truncated)
        - confidence: heuristic 0.0-1.0 quality estimate based on compression ratio
        - ratio: original_tokens / compressed_tokens (>= 1.0)

        Returns None when the key is not found or expired.
        """
        with self._lock:
            entry = self._read_entry(key)
            if entry is None:
                return None
            blob_bytes, orig_tok, comp_tok, created_at, source = entry
            try:
                blob = blob_bytes.decode("utf-8", errors="replace")
            except Exception:  # noqa: BLE001
                return None
            age = max(0.0, time.time() - float(created_at or 0.0))
            ratio = (orig_tok / comp_tok) if comp_tok else 1.0
            # Confidence: higher ratio = cleaner reversible compression. Clamp 0..1.
            confidence = max(0.0, min(1.0, 0.5 + 0.05 * (ratio - 1.0)))
            return {
                "key": key,
                "exists": True,
                "original_tokens": int(orig_tok or 0),
                "compressed_tokens": int(comp_tok or 0),
                "size_bytes": len(blob_bytes),
                "size_chars": len(blob),
                "age_seconds": int(age),
                "created_at": float(created_at or 0.0),
                "created_at_iso": _iso_from_epoch(float(created_at or 0.0)),
                "source": source or "",
                "preview": (blob[:400] + ("…" if len(blob) > 400 else "")),
                "confidence": round(confidence, 3),
                "ratio": round(ratio, 3),
            }

    def _read_entry(self, key: str) -> tuple | None:
        """Internal: read a raw entry tuple or None. Caller must hold the lock."""
        if self.backend == "sqlite" and self._db is not None:
            try:
                cur = self._db.execute(
                    "SELECT original, original_tokens, compressed_tokens, created_at, source "
                    "FROM ccr WHERE key = ?",
                    (key,),
                )
                row = cur.fetchone()
                if row is None:
                    return None
                if self.ttl_days > 0 and (time.time() - (row[3] or 0.0)) > self.ttl_days * 86400:
                    try:
                        self._db.execute("DELETE FROM ccr WHERE key = ?", (key,))
                        self._db.commit()
                    except Exception:  # noqa: BLE001
                        pass
                    return None
                return (row[0], row[1], row[2], row[3], row[4])
            except Exception as exc:  # noqa: BLE001
                self._print(f"sqlite get_meta failed: {exc}")
                return None
        entry = self._mem.get(key)
        if entry is None:
            return None
        blob, ot, ct, ts, src = entry
        if self.ttl_days > 0 and (time.time() - ts) > self.ttl_days * 86400:
            self._mem.pop(key, None)
            return None
        return entry

    def stats(self) -> dict[str, int]:
        """Aggregate counters for the dashboard: total entries, total original
        tokens, total compressed tokens."""
        self.prune_expired()
        with self._lock:
            if self.backend == "sqlite" and self._db is not None:
                try:
                    cur = self._db.execute(
                        "SELECT COUNT(*), COALESCE(SUM(original_tokens),0), "
                        "COALESCE(SUM(compressed_tokens),0) FROM ccr"
                    )
                    row = cur.fetchone()
                    return {
                        "count": int(row[0] or 0),
                        "original_tokens": int(row[1] or 0),
                        "compressed_tokens": int(row[2] or 0),
                        "bytes": int(self._db.execute("SELECT COALESCE(SUM(length(original)),0) FROM ccr").fetchone()[0] or 0),
                        "max_bytes": self.max_bytes,
                    }
                except Exception as exc:  # noqa: BLE001
                    self._print(f"sqlite stats failed: {exc}")
                    return {"count": 0, "original_tokens": 0, "compressed_tokens": 0}
            total_o = sum(v[1] for v in self._mem.values())
            total_c = sum(v[2] for v in self._mem.values())
            return {
                "count": len(self._mem),
                "original_tokens": int(total_o),
                "compressed_tokens": int(total_c),
                "bytes": sum(len(v[0]) for v in self._mem.values()),
                "max_bytes": self.max_bytes,
            }

    def close(self) -> None:
        """Release this cache's handle back to the pool.

        The connection is shared per path (see the pooling note at the top of
        this module) and every write already autocommits, so returning the
        handle instead of closing it is durable and keeps the ~25ms
        open/checkpoint cost off the hot path.
        """
        with self._lock:
            self._db = None
