"""
Caveman plugin - per-chat level state.

Stores caveman level (and 'enabled' override) per chat_id in a small JSON
file under the user's workdir.

File location: <workdir>/.caveman/state.json
"""

import json
import os
import threading
from datetime import datetime, timezone
from typing import Optional


PLUGIN_NAME = "caveman"
VALID_LEVELS = (
    "lite",
    "full",
    "ultra",
    "wenyan-lite",
    "wenyan-full",
    "wenyan-ultra",
)


def _state_dir() -> str:
    workdir = os.environ.get("AGENT_WORKDIR") or os.environ.get("A0_WORKDIR")
    if not workdir:
        workdir = os.path.join(os.path.expanduser("~"), ".cache", "agent0", "caveman")
    return workdir


def _state_path() -> str:
    return os.path.join(_state_dir(), ".caveman", "state.json")


_lock = threading.Lock()


def _read_state() -> dict:
    path = _state_path()
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _write_state(data: dict) -> bool:
    path = _state_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, sort_keys=True)
        os.replace(tmp, path)
        return True
    except OSError:
        return False


def get_level(chat_id: Optional[str], default: str = "full") -> str:
    if not chat_id:
        return default
    with _lock:
        state = _read_state()
        entry = state.get(chat_id) or {}
        lvl = entry.get("level")
        if isinstance(lvl, str) and lvl in VALID_LEVELS:
            return lvl
        return default


def is_enabled(chat_id: Optional[str], default_enabled: bool) -> bool:
    if not chat_id:
        return default_enabled
    with _lock:
        state = _read_state()
        entry = state.get(chat_id) or {}
        en = entry.get("enabled")
        if isinstance(en, bool):
            return en
        return default_enabled


def set_level(chat_id: str, level: Optional[str]) -> bool:
    if not chat_id:
        return False
    with _lock:
        state = _read_state()
        entry = state.get(chat_id) or {}
        if level is None:
            entry.pop("level", None)
        else:
            if level not in VALID_LEVELS and level != "off":
                return False
            entry["level"] = level
        entry["set_at"] = datetime.now(timezone.utc).isoformat()
        if not entry:
            state.pop(chat_id, None)
        else:
            state[chat_id] = entry
        return _write_state(state)


def set_enabled(chat_id: str, enabled: Optional[bool]) -> bool:
    if not chat_id:
        return False
    with _lock:
        state = _read_state()
        entry = state.get(chat_id) or {}
        if enabled is None:
            entry.pop("enabled", None)
        else:
            entry["enabled"] = bool(enabled)
        entry["set_at"] = datetime.now(timezone.utc).isoformat()
        if not entry:
            state.pop(chat_id, None)
        else:
            state[chat_id] = entry
        return _write_state(state)


def get_all_chats() -> dict:
    with _lock:
        return dict(_read_state())
