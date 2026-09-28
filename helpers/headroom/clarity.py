"""Auto-clarity flag store for Headroom v0.4.0.

The auto-clarity extension detects destructive user commands and asks Headroom
to skip compression for that turn. The flag is stored here so the compressor
can consume it without importing the extension module (no cycle).

State file: <plugin_dir>/cache/clarity_flags.json
Shape: {"flags": {"<context_id>": {"reason": "...", "set_at": <epoch>}}, ...}

Concurrency (remediation 2026-09-28, finding 6): parallel chats each run
set_skip/consume_skip from their own extension thread. The original store did
unlocked read-modify-write with a plain ``write_text``, and a measured 400-write
concurrency probe lost 134 updates and corrupted 266 files (interleaved writes
truncate each other -> ``JSONDecodeError`` -> the whole store silently reset).
Writes are now serialised by an in-process lock plus the same cross-process
lock file `per_chat.py` uses, and land atomically via temp file + ``os.replace``.
A corrupt store still reads as empty (fail open, no protection) but never
crashes a turn.
"""

from __future__ import annotations

import json
import os
import threading
import time
from typing import Any

from usr.plugins.caveman.helpers.headroom import config as _config
from usr.plugins.caveman.helpers.headroom.per_chat import _FileLock

PLUGIN_DIR = _config.PLUGIN_DIR
FLAGS_PATH = PLUGIN_DIR / "cache" / "clarity_flags.json"
LOCK_PATH = PLUGIN_DIR / "cache" / "clarity_flags.lock"
FLAG_TTL_SECONDS = 60 * 30

# Serialises read-modify-write cycles on the flags file across threads in one
# process; _FileLock extends that across processes sharing the workdir.
_STORE_LOCK = threading.Lock()


def _read_store() -> dict[str, Any]:
    try:
        with open(FLAGS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, dict):
                return data
    except (FileNotFoundError, ValueError, OSError):
        pass
    return {"flags": {}}


def _write_store(store: dict[str, Any]) -> None:
    try:
        FLAGS_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = FLAGS_PATH.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(json.dumps(store, indent=2, sort_keys=True))
        os.replace(str(tmp), str(FLAGS_PATH))
    except OSError:
        pass


def set_skip(context_id: str, reason: str) -> None:
    if not context_id:
        return
    with _STORE_LOCK, _FileLock(LOCK_PATH):
        store = _read_store()
        store.setdefault("flags", {})[context_id] = {
            "reason": str(reason)[:200],
            "set_at": int(time.time()),
        }
        _write_store(store)


def _live_reason(entry: Any) -> str | None:
    """Return an entry's reason if it is still inside the TTL, else None.

    Shared by `consume_skip` and `peek_skip` so both apply the identical
    expiry rule. A flag older than FLAG_TTL_SECONDS protects nothing.
    """
    if not isinstance(entry, dict):
        return None
    try:
        set_at = int(entry.get("set_at", 0))
    except (TypeError, ValueError):
        return None
    if FLAG_TTL_SECONDS > 0 and (int(time.time()) - set_at) > FLAG_TTL_SECONDS:
        return None
    reason = entry.get("reason")
    return str(reason) if reason else None


def consume_skip(context_id: str) -> str | None:
    """Take the flag for this context, removing it. Returns None when unset.

    Destructive: exactly one consumer per turn may call this, or the flag
    vanishes before the next stage sees it.
    """
    if not context_id:
        return None
    with _STORE_LOCK, _FileLock(LOCK_PATH):
        store = _read_store()
        flags = store.get("flags", {})
        entry = flags.pop(context_id, None)
        if entry is None:
            return None
        _write_store(store)
    return _live_reason(entry)


def peek_skip(context_id: str) -> str | None:
    """Read the flag WITHOUT removing it.

    Several stages of one turn independently need to honour the flag, and
    only the last of them may consume it. A non-consuming read is what lets
    `message_loop_prompts_before/_07_clear_old_tool_results` protect itself
    without stealing the flag that `_10_compress_history` still has to see.
    """
    if not context_id:
        return None
    return _live_reason(_read_store().get("flags", {}).get(context_id))


def clear(context_id: str) -> bool:
    with _STORE_LOCK, _FileLock(LOCK_PATH):
        store = _read_store()
        flags = store.get("flags", {})
        if context_id in flags:
            flags.pop(context_id, None)
            _write_store(store)
            return True
    return False


def list_active() -> list[dict[str, Any]]:
    store = _read_store()
    flags = store.get("flags", {})
    return [
        {"context_id": cid, "reason": v.get("reason"), "set_at": v.get("set_at")}
        for cid, v in flags.items()
    ]
