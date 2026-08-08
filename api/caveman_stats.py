"""
Caveman plugin - live token-savings stats API.

Route: POST /api/plugins/caveman/caveman_stats

Returns per-chat and lifetime stats: turns, chars, est_tokens_saved, last_level.

Request body:
 { "action": "get" | "list" | "reset", "chat_id": "<id>" }

Response:
 { "ok": true, "chat_id": "...", "turns": N, "chars": N, "est_tokens_saved": N, "last_level": "full" }
 { "ok": true, "action": "list", "chats": { ... } }
 { "ok": true, "action": "reset", "chat_id": "..." }
"""

import json
import os
import threading

from helpers.api import ApiHandler


PLUGIN_NAME = "caveman"
_LOCK = threading.Lock()


def _stats_path() -> str:
    workdir = os.environ.get("AGENT_WORKDIR") or os.environ.get("A0_WORKDIR")
    if not workdir:
        workdir = os.path.join(os.path.expanduser("~"), ".cache", "agent0", "caveman")
    return os.path.join(workdir, ".caveman", "stats.json")


def _read() -> dict:
    p = _stats_path()
    if not os.path.isfile(p):
        return {}
    try:
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception:
        return {}


def _write(data: dict) -> None:
    p = _stats_path()
    try:
        os.makedirs(os.path.dirname(p), exist_ok=True)
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, sort_keys=True)
        os.replace(tmp, p)
    except OSError:
        pass


class CavemanStats(ApiHandler):
    async def process(self, input_data, request):
        action = (input_data or {}).get("action", "get")
        chat_id = (input_data or {}).get("chat_id") or ""
        with _LOCK:
            data = _read()
        if action == "list":
            return {"ok": True, "action": "list", "chats": data, "stats_path": _stats_path()}
        if not chat_id:
            return {"ok": False, "error": "chat_id is required for this action"}
        if action == "get":
            entry = data.get(chat_id) or {}
            return {"ok": True, "action": "get", "chat_id": chat_id, **entry}
        if action == "reset":
            with _LOCK:
                data.pop(chat_id, None)
                _write(data)
            return {"ok": True, "action": "reset", "chat_id": chat_id}
        return {"ok": False, "error": f"unknown action: {action!r}"}
