"""
Caveman plugin - per-chat token-savings stats tracker.

At the end of each agent monologue, measures the assistant response length
and increments a per-chat counter in the state file. The dashboard API
reads these counters to show lifetime savings.

Estimation method: chars / 4 = tokens (matches upstream caveman's order of
magnitude; not exact Claude-API telemetry).

Extension point: monologue_end
"""

import os
import json
import threading
from typing import Any

from helpers.extension import Extension
from helpers import plugins as plugins_helper

from usr.plugins.caveman.helpers import state as caveman_state


PLUGIN_NAME = "caveman"

_LOCK = threading.Lock()

REDUCTION_FRACTION = {
    "lite": 0.30,
    "full": 0.65,
    "ultra": 0.80,
    "wenyan-lite": 0.55,
    "wenyan-full": 0.70,
    "wenyan-ultra": 0.80,
}


def _stats_path() -> str:
    workdir = os.environ.get("AGENT_WORKDIR") or os.environ.get("A0_WORKDIR")
    if not workdir:
        workdir = os.path.join(os.path.expanduser("~"), ".cache", "agent0", "caveman")
    return os.path.join(workdir, ".caveman", "stats.json")


def _read_stats() -> dict:
    path = _stats_path()
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception:
        return {}


def _write_stats(data: dict) -> None:
    path = _stats_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, sort_keys=True)
        os.replace(tmp, path)
    except OSError:
        pass


def _chat_id(agent) -> str:
    if not agent:
        return ""
    ctx = getattr(agent, "context", None)
    if ctx is None:
        return ""
    return str(getattr(ctx, "id", "") or getattr(ctx, "chat_id", "") or "")


def _get_response_text(loop_data) -> str:
    if loop_data is None:
        return ""
    for attr in ("last_response", "response", "last_message"):
        v = getattr(loop_data, attr, None)
        if isinstance(v, str) and v.strip():
            return v
    return ""


class CavemanStats(Extension):
    async def execute(self, loop_data: Any = None, **kwargs: Any):
        if not self.agent:
            return
        try:
            cfg = plugins_helper.get_plugin_config(PLUGIN_NAME) or {}
        except Exception:
            cfg = {}
        if not cfg.get("enabled", False):
            return
        chat_id = _chat_id(self.agent)
        if not caveman_state.is_enabled(chat_id, cfg.get("enabled", False)):
            return
        level = caveman_state.get_level(chat_id, cfg.get("level", "full"))
        reduction = REDUCTION_FRACTION.get(level)
        if reduction is None:
            return
        text = _get_response_text(loop_data)
        if not text:
            return
        chars = len(text)
        est_tokens = max(1, chars // 4)
        est_saved = int(est_tokens * reduction)
        with _LOCK:
            stats = _read_stats()
            entry = stats.get(chat_id) or {"turns": 0, "chars": 0, "est_tokens_saved": 0}
            entry["turns"] = int(entry.get("turns", 0)) + 1
            entry["chars"] = int(entry.get("chars", 0)) + chars
            entry["est_tokens_saved"] = int(entry.get("est_tokens_saved", 0)) + est_saved
            entry["last_level"] = level
            stats[chat_id] = entry
            _write_stats(stats)
