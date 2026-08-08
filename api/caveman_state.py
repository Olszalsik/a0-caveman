"""
Caveman plugin - backend state API.

Route: POST /api/plugins/caveman/caveman_state

Allows the WebUI to read and set per-chat caveman state.

Request body:
 {
 "action": "get" | "set_level" | "set_enabled" | "list",
 "chat_id": "<id>",
 "level": "full",
 "enabled": true
 }
"""

from helpers.api import ApiHandler  # type: ignore

from usr.plugins.caveman.helpers import state as caveman_state
from usr.plugins.caveman.helpers import state as _state


PLUGIN_NAME = "caveman"


class CavemanState(ApiHandler):
    async def process(self, input_data, request):
        action = (input_data or {}).get("action", "get")
        chat_id = (input_data or {}).get("chat_id") or ""

        if action == "list":
            return {
                "ok": True,
                "action": "list",
                "chats": caveman_state.get_all_chats(),
                "state_path": _state._state_path(),
            }

        if not chat_id:
            return {"ok": False, "error": "chat_id is required for this action"}

        if action == "get":
            return {
                "ok": True,
                "action": "get",
                "chat_id": chat_id,
                "level": caveman_state.get_level(chat_id, default="full"),
                "enabled": caveman_state.is_enabled(chat_id, default_enabled=False),
            }

        if action == "set_level":
            level = input_data.get("level")
            if level not in caveman_state.VALID_LEVELS and level is not None:
                return {
                    "ok": False,
                    "error": f"invalid level: {level!r}. Must be one of {caveman_state.VALID_LEVELS}",
                }
            ok = caveman_state.set_level(chat_id, level)
            return {
                "ok": ok,
                "action": "set_level",
                "chat_id": chat_id,
                "level": caveman_state.get_level(chat_id, default="full"),
                "enabled": caveman_state.is_enabled(chat_id, default_enabled=False),
            }

        if action == "set_enabled":
            enabled = input_data.get("enabled")
            if not isinstance(enabled, bool):
                return {"ok": False, "error": "enabled must be a boolean"}
            ok = caveman_state.set_enabled(chat_id, enabled)
            return {
                "ok": ok,
                "action": "set_enabled",
                "chat_id": chat_id,
                "level": caveman_state.get_level(chat_id, default="full"),
                "enabled": caveman_state.is_enabled(chat_id, default_enabled=False),
            }

        return {"ok": False, "error": f"unknown action: {action!r}"}
