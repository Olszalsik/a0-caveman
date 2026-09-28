"""
Caveman plugin - per-chat state and observation store.

Single owner of every on-disk file the plugin writes, so the extension
modules and the API handlers cannot drift or race:

  <workdir>/.caveman/state.json   per-chat level + enabled override
  <workdir>/.caveman/stats.json   raw per-turn observations (no estimates)

The workdir comes from the framework's own runtime setting
(`helpers.settings.get_settings()["workdir_path"]`), which is already
dockerized. Earlier revisions read `AGENT_WORKDIR` / `A0_WORKDIR`, but the
framework never sets either, so every install on a host silently collapsed
onto one machine-global `~/.cache/agent0/caveman` directory and bypassed the
container workdir volume.

Everything here is guarded by a single module lock and written through a
temp file + `os.replace`, so a concurrent read-modify-write from another
extension or from an API handler cannot lose an update.
"""

import json
import os
import threading
from datetime import datetime, timezone
from typing import Any, Optional


PLUGIN_NAME = "caveman"

VALID_LEVELS = (
    "lite",
    "full",
    "ultra",
    "wenyan-lite",
    "wenyan-full",
    "wenyan-ultra",
)

DEFAULT_LEVEL = "full"

STATE_DIRNAME = ".caveman"

# Sentinel for set_state(): "leave this field alone".
KEEP = object()

_lock = threading.RLock()


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------


def _workdir() -> str:
    """Resolve the Agent Zero workdir, with a framework-path fallback."""
    try:
        from helpers.settings import get_settings

        workdir = (get_settings() or {}).get("workdir_path")
        if isinstance(workdir, str) and workdir.strip():
            return workdir
    except Exception:
        pass
    try:
        from helpers import files

        return files.get_abs_path("usr/workdir")
    except Exception:
        pass
    return os.path.join(os.path.expanduser("~"), ".cache", "agent0", "caveman")


def state_dir() -> str:
    return os.path.join(_workdir(), STATE_DIRNAME)


def _state_path() -> str:
    return os.path.join(state_dir(), "state.json")


def _stats_path() -> str:
    return os.path.join(state_dir(), "stats.json")


def state_path() -> str:
    """Public accessor (the API reports it for diagnostics)."""
    return _state_path()


def stats_path() -> str:
    return _stats_path()


def mode_log_path() -> str:
    return os.path.join(state_dir(), "mode-log.jsonl")


# ---------------------------------------------------------------------------
# Mode transition log
#
# Records only actual transitions, so token spend can later be attributed to
# the level that was active when the text was generated rather than to
# whatever the level happens to be when stats are read. Ported from upstream
# `caveman-config.js` `recordModeChange`, which appends to a JSONL file on a
# real change only and normalises `off -> None` first so that turning a level
# off does not also emit a spurious level-change row.
# ---------------------------------------------------------------------------


def _normalize_mode(level: Any, enabled: Any) -> str:
    if enabled is False or not level:
        return "off"
    return str(level)


def record_mode_change(chat_id: str, level: Any, enabled: Any) -> bool:
    """Log a transition by reading the stored mode first.

    Callers that already know both the previous and the next mode should use
    `_append_mode_change` instead, because by the time a write has landed the
    stored value is the new one and every transition would look like a no-op.
    """
    if not chat_id:
        return False
    with _lock:
        entry = _read_json(_state_path()).get(chat_id)
        previous = None
        if isinstance(entry, dict):
            previous = _normalize_mode(entry.get("level"), entry.get("enabled"))
        return _append_mode_change(chat_id, _normalize_mode(level, enabled), previous)


def _append_mode_change(chat_id: str, mode: str, previous: Optional[str]) -> bool:
    """Append one transition row. Caller holds `_lock`."""
    if not chat_id or previous == mode:
        return False
    row = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "chat_id": chat_id,
        "mode": mode,
        "prev": previous,
    }
    try:
        os.makedirs(state_dir(), exist_ok=True)
        with open(mode_log_path(), "a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        return True
    except OSError:
        return False


def read_mode_log(chat_id: Optional[str] = None, limit: int = 200) -> list:
    """Read mode transitions, newest last. Tolerates a truncated last line."""
    path = mode_log_path()
    if not os.path.isfile(path):
        return []
    rows: list = []
    try:
        with open(path, "r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    # A partial write at the tail is expected after a crash.
                    continue
                if not isinstance(row, dict):
                    continue
                if chat_id and row.get("chat_id") != chat_id:
                    continue
                rows.append(row)
    except OSError:
        return []
    return rows[-limit:] if limit > 0 else rows


def mode_history(chat_id: str) -> list:
    """Only real transitions for one chat, oldest first."""
    return read_mode_log(chat_id=chat_id)


def clear_mode_log(chat_id: str) -> bool:
    """Drop one chat's transition rows, keeping every other chat's.

    The log is append-only, so a test run or a user resetting a chat would
    otherwise see its own earlier rows forever. Rewrites the file in place
    rather than deleting it, because other chats share it.
    """
    if not chat_id:
        return False
    with _lock:
        rows = read_mode_log()
        kept = [row for row in rows if row.get("chat_id") != chat_id]
        if len(kept) == len(rows):
            return False
        try:
            os.makedirs(state_dir(), exist_ok=True)
            body = "".join(
                json.dumps(row, ensure_ascii=False) + "\n" for row in kept
            )
            tmp = mode_log_path() + ".tmp"
            with open(tmp, "w", encoding="utf-8") as handle:
                handle.write(body)
            os.replace(tmp, mode_log_path())
            return True
        except OSError:
            return False


# ---------------------------------------------------------------------------
# Generic atomic JSON file helpers
# ---------------------------------------------------------------------------


def _read_json(path: str) -> dict:
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError, ValueError):
        return {}


def _write_json(path: str, data: dict) -> bool:
    tmp = path + ".tmp"
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2, sort_keys=True)
        os.replace(tmp, path)
        return True
    except OSError:
        try:
            if os.path.isfile(tmp):
                os.remove(tmp)
        except OSError:
            pass
        return False


# ---------------------------------------------------------------------------
# Per-chat level / enabled state
# ---------------------------------------------------------------------------


def get_entry(chat_id: Optional[str]) -> dict:
    if not chat_id:
        return {}
    with _lock:
        entry = _read_json(_state_path()).get(chat_id)
    return entry if isinstance(entry, dict) else {}


def get_level(chat_id: Optional[str], default: str = DEFAULT_LEVEL) -> str:
    level = get_entry(chat_id).get("level")
    if isinstance(level, str) and level in VALID_LEVELS:
        return level
    return default


def is_enabled(chat_id: Optional[str], default_enabled: bool) -> bool:
    enabled = get_entry(chat_id).get("enabled")
    if isinstance(enabled, bool):
        return enabled
    return default_enabled


def resolve(chat_id: Optional[str], config: Optional[dict] = None) -> dict:
    """Single source of truth for "is caveman on, and at what level".

    Every extension must go through this instead of resolving the default
    level and the enabled flag independently, which is how the WebUI and the
    stats tracker previously disagreed with the prompt injector.
    """
    cfg = config if isinstance(config, dict) else {}
    default_enabled = bool(cfg.get("enabled", False))
    raw_default_level = cfg.get("level", DEFAULT_LEVEL)
    default_level = (
        raw_default_level if raw_default_level in VALID_LEVELS else DEFAULT_LEVEL
    )
    return {
        "enabled": is_enabled(chat_id, default_enabled),
        "level": get_level(chat_id, default_level),
    }


def set_state(
    chat_id: str,
    level: Any = KEEP,
    enabled: Any = KEEP,
) -> bool:
    """Atomically set and/or clear the level and enabled override in one write.

    Pass `KEEP` (the default) to leave a field untouched, `None` to clear it
    back to the configured default, and a real value to set it. `/caveman
    <level>` needs both fields in a single write, so a reader can never
    observe a half-applied command.
    """
    if not chat_id:
        return False
    with _lock:
        state = _read_json(_state_path())
        entry = state.get(chat_id)
        entry = dict(entry) if isinstance(entry, dict) else {}

        # Capture the mode we are transitioning FROM before the write, or the
        # log would compare the new state against itself and record nothing.
        previous_mode = _normalize_mode(entry.get("level"), entry.get("enabled"))

        if level is not KEEP:
            if level is None:
                entry.pop("level", None)
            elif isinstance(level, str) and level in VALID_LEVELS:
                entry["level"] = level
            else:
                return False

        if enabled is not KEEP:
            if enabled is None:
                entry.pop("enabled", None)
            elif isinstance(enabled, bool):
                entry["enabled"] = enabled
            else:
                return False

        if not entry.get("level") and entry.get("enabled") is None:
            state.pop(chat_id, None)
        else:
            entry["set_at"] = datetime.now(timezone.utc).isoformat()
            state[chat_id] = entry
        written = _write_json(_state_path(), state)
        if written:
            # Keep the state write and its transition row in one critical
            # section. Otherwise concurrent setters can append transitions in
            # the opposite order from the state changes they describe.
            _append_mode_change(
                chat_id,
                _normalize_mode(entry.get("level"), entry.get("enabled")),
                previous_mode,
            )
    return written


def set_level(chat_id: str, level: Optional[str]) -> bool:
    """Set the level override. `None` clears it back to the configured default."""
    if not chat_id:
        return False
    if level is not None and level not in VALID_LEVELS:
        return False
    return set_state(chat_id, level=level)


def set_enabled(chat_id: str, enabled: Optional[bool]) -> bool:
    """Set or clear the enabled override for one chat."""
    if not chat_id:
        return False
    if enabled is not None and not isinstance(enabled, bool):
        return False
    return set_state(chat_id, enabled=enabled)


def get_all_chats() -> dict:
    with _lock:
        return dict(_read_json(_state_path()))


# ---------------------------------------------------------------------------
# Raw observations
#
# Deliberately stores only what was actually observed: turn count and the
# character length of the text the user actually saw. It does NOT store an
# "estimated tokens saved" figure. Upstream retracted its fixed 65% ratio
# (`docs/HONEST-NUMBERS.md`), and a saving cannot be derived from a single
# observed length without a measured control arm. See benchmarks/run.py.
# ---------------------------------------------------------------------------


def _empty_stats_entry() -> dict:
    return {"turns": 0, "chars": 0, "by_level": {}}


def record_turn(chat_id: str, level: str, chars: int) -> bool:
    """Record one observed assistant turn. `chars` must be real output length."""
    if not chat_id or not isinstance(chars, int) or chars < 0:
        return False
    level = level if level in VALID_LEVELS else "unknown"
    with _lock:
        stats = _read_json(_stats_path())
        entry = stats.get(chat_id)
        entry = _empty_stats_entry() if not isinstance(entry, dict) else entry
        entry["turns"] = int(entry.get("turns", 0)) + 1
        entry["chars"] = int(entry.get("chars", 0)) + chars
        by_level = entry.get("by_level")
        if not isinstance(by_level, dict):
            by_level = {}
        bucket = by_level.get(level)
        bucket = {"turns": 0, "chars": 0} if not isinstance(bucket, dict) else bucket
        bucket["turns"] = int(bucket.get("turns", 0)) + 1
        bucket["chars"] = int(bucket.get("chars", 0)) + chars
        by_level[level] = bucket
        entry["by_level"] = by_level
        entry["last_level"] = level
        entry["updated_at"] = datetime.now(timezone.utc).isoformat()
        stats[chat_id] = entry
        return _write_json(_stats_path(), stats)


def get_stats(chat_id: str) -> dict:
    if not chat_id:
        return {}
    with _lock:
        entry = _read_json(_stats_path()).get(chat_id)
    return entry if isinstance(entry, dict) else {}


def all_stats() -> dict:
    with _lock:
        return dict(_read_json(_stats_path()))


def reset_stats(chat_id: str) -> bool:
    if not chat_id:
        return False
    with _lock:
        stats = _read_json(_stats_path())
        stats.pop(chat_id, None)
        return _write_json(_stats_path(), stats)


def summary() -> dict[str, Any]:
    """Aggregate raw observations across every chat."""
    data = all_stats()
    turns = 0
    chars = 0
    by_level: dict[str, dict[str, int]] = {}
    for entry in data.values():
        if not isinstance(entry, dict):
            continue
        turns += int(entry.get("turns", 0))
        chars += int(entry.get("chars", 0))
        levels = entry.get("by_level")
        if not isinstance(levels, dict):
            continue
        for level, bucket in levels.items():
            if not isinstance(bucket, dict):
                continue
            slot = by_level.setdefault(level, {"turns": 0, "chars": 0})
            slot["turns"] += int(bucket.get("turns", 0))
            slot["chars"] += int(bucket.get("chars", 0))
    return {
        "chats": len(data),
        "turns": turns,
        "chars": chars,
        "by_level": by_level,
    }
