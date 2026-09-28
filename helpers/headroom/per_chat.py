"""Per-chat overrides for Headroom.

JSON file at <plugin_dir>/cache/per_chat_overrides.json maps
context_id -> { enabled: bool, updated_ts: float, note: str }.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any

from usr.plugins.caveman.helpers.headroom import config as _config

PLUGIN_DIR = _config.PLUGIN_DIR
STORE_PATH = PLUGIN_DIR / "cache" / "per_chat_overrides.json"
LOCK_PATH = PLUGIN_DIR / "cache" / "per_chat_overrides.lock"

# Serialises read-modify-write cycles on the overrides file so concurrent
# API calls cannot lose each other's updates.
_STORE_LOCK = threading.Lock()

# K3 (audit 2026-09-27): the threading.Lock above only covers one process.
# Two Agent Zero instances sharing a workdir could still clobber each other,
# because read -> modify -> replace is not atomic across processes. The
# replacement is now guarded by a lock file as well.
#
# Deliberately a best-effort, bounded wait rather than a hard requirement:
# if the platform cannot lock, the write still goes through atomically
# (temp file + os.replace), which is the pre-existing behaviour. The lock
# closes the race window; it does not become a new failure mode.
_LOCK_WAIT_SECONDS = 2.0
_LOCK_POLL_SECONDS = 0.02


class _FileLock:
    """A cross-process advisory lock backed by an exclusively-created file.

    Uses O_CREAT|O_EXCL rather than fcntl/msvcrt so the behaviour is identical
    on every platform Agent Zero runs on, with no conditional imports. The
    trade-off is a stale lock file if a process dies mid-write, so the
    lock records its own age and a lock older than the wait budget is
    treated as abandoned and broken.
    """

    def __init__(self, path: Path | str):
        # Accept a str as well as a Path: callers override STORE_PATH with a
        # plain string in tests, and a lock that raises on that is worse than
        # useless - it fails the write it was meant to protect.
        self.path = Path(path)
        self._fd: int | None = None

    def _try_acquire(self) -> bool:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(self._fd, str(time.time()).encode("utf-8"))
            return True
        except FileExistsError:
            return False
        except OSError:
            return False

    def _is_stale(self) -> bool:
        """Break a lock left behind by a process that died holding it."""
        try:
            age = time.time() - self.path.stat().st_mtime
        except OSError:
            return False
        return age > _LOCK_WAIT_SECONDS * 4

    def _break(self) -> None:
        try:
            self.path.unlink(missing_ok=True)
        except OSError:
            pass

    def __enter__(self) -> "_FileLock":
        deadline = time.time() + _LOCK_WAIT_SECONDS
        while not self._try_acquire():
            if self._is_stale():
                self._break()
                continue
            if time.time() >= deadline:
                # Proceed unlocked rather than failing the request. The write
                # itself is still atomic; only the race window reopens.
                return self
            time.sleep(_LOCK_POLL_SECONDS)
        return self

    def __exit__(self, *exc) -> bool:
        if self._fd is not None:
            try:
                os.close(self._fd)
            except OSError:
                pass
            self._fd = None
        self._break()
        return False


def _print(msg: str) -> None:
    sys.stderr.write("[%s/per_chat] %s\n" % (_config.PLUGIN_NAME, msg))
    sys.stderr.flush()


def _ensure_store() -> None:
    STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not STORE_PATH.exists():
        STORE_PATH.write_text("{}", encoding="utf-8")


def _read_all() -> dict[str, dict[str, Any]]:
    try:
        with open(STORE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, dict):
                return data
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    return {}


def _write_all(data: dict[str, dict[str, Any]]) -> None:
    _ensure_store()
    tmp = STORE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(STORE_PATH)


def is_enabled(context_id: str, default: bool = True) -> bool:
    """Return True if compression is enabled for this chat."""
    if not context_id:
        return default
    data = _read_all()
    entry = data.get(context_id)
    if not entry:
        return default
    return bool(entry.get("enabled", default))


def get_override(context_id: str) -> dict[str, Any] | None:
    """Return the raw override entry for a chat, or None when unset."""
    if not context_id:
        return None
    return _read_all().get(context_id)


def set_enabled(context_id: str, enabled: bool, note: str = "") -> dict[str, Any]:
    """Set the per-chat override. Returns the new entry."""
    if not context_id:
        raise ValueError("context_id required")
    with _STORE_LOCK, _FileLock(LOCK_PATH):
        data = _read_all()
        entry = {
            "enabled": bool(enabled),
            "updated_ts": time.time(),
            "note": note or "",
        }
        data[context_id] = entry
        _write_all(data)
    _print("set %s -> enabled=%s" % (context_id, enabled))
    return entry


def clear(context_id: str) -> bool:
    with _STORE_LOCK, _FileLock(LOCK_PATH):
        data = _read_all()
        if context_id in data:
            del data[context_id]
            _write_all(data)
            _print("cleared override for %s" % context_id)
            return True
    return False


def list_all() -> list[dict[str, Any]]:
    data = _read_all()
    return [
        {"context_id": k, **v}
        for k, v in sorted(data.items(), key=lambda kv: kv[1].get("updated_ts", 0), reverse=True)
    ]
