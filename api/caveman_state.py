"""
Caveman plugin - backend state API.

Route: POST /api/plugins/caveman/caveman_state

Actions:
  {"action": "get",         "chat_id": "<id>"}
      -> resolved level + enabled for that chat, using the plugin config as
         the default rather than a hardcoded "full".

  {"action": "set",         "chat_id": "<id>", "level": "ultra", "enabled": true}
      -> atomic combined write. Either field may be omitted. This is what the
         topbar dropdown uses, so "off" (a level of "off" plus enabled:false)
         is one round trip instead of two.

  {"action": "set_level",   "chat_id": "<id>", "level": "ultra"}   (level: null clears)
  {"action": "set_enabled", "chat_id": "<id>", "enabled": true}    (null clears)
  {"action": "list"}
  {"action": "clear",       "chat_id": "<id>"}

All handlers return HTTP 200 with an `ok` flag, so the client must check
`ok` and not just the status code.
"""

from helpers.api import ApiHandler  # type: ignore

from usr.plugins.caveman.helpers import compat
from usr.plugins.caveman.helpers import plugins_config as plugin_cfg
from usr.plugins.caveman.helpers import state as caveman_state


PLUGIN_NAME = "caveman"


def _payload(input_data) -> dict:
    return input_data if isinstance(input_data, dict) else {}


def _valid_levels() -> tuple:
    try:
        return tuple(caveman_state.VALID_LEVELS)
    except Exception:
        return ("lite", "full", "ultra", "wenyan-lite", "wenyan-full", "wenyan-ultra")


def _resolved(chat_id: str) -> dict:
    """Current level + enabled for a chat, defaults from the plugin config.

    Falls back to the configured defaults when `helpers/state.py` is stale, so
    the WebUI shows a usable state instead of a 500. See helpers/compat.py.
    """
    config = plugin_cfg.get_config()
    state = compat.state_api(caveman_state)
    if state is None:
        level = config.get("level", "full")
        return {
            "enabled": bool(config.get("enabled", False)),
            "level": level if level in _valid_levels() else "full",
        }
    return state.resolve(chat_id, config)


def _error(message: str, action: str) -> dict:
    return {"ok": False, "action": action, "error": message}


class CavemanState(ApiHandler):
    async def process(self, input_data, request):
        data = _payload(input_data)
        action = data.get("action") or "get"
        chat_id = str(data.get("chat_id") or "")

        # Refuse writes when the state module cannot support them, rather than
        # raising AttributeError into a 500. Reads still work via _resolved.
        state = compat.state_api(caveman_state)
        needs_write = action in {"set", "set_level", "set_enabled", "clear"}
        if needs_write and state is None:
            return _error(
                "helpers/state.py is an older version than this handler expects; "
                "replace the whole plugin directory. See the plugin README.",
                action,
            )
        levels = _valid_levels()

        if action == "list":
            if state is None:
                return {
                    "ok": False,
                    "action": "list",
                    "error": "helpers/state.py is an older version; replace the plugin",
                }
            return {
                "ok": True,
                "action": "list",
                "chats": state.get_all_chats(),
                "config": plugin_cfg.get_config(),
                "state_path": state.state_path(),
            }

        if not chat_id:
            return _error("chat_id is required for this action", action)

        if action == "get":
            return {
                "ok": True,
                "action": "get",
                "chat_id": chat_id,
                **_resolved(chat_id),
            }

        if action == "set":
            # Atomic combined write. Validate everything first so a bad value
            # cannot half-apply the other field.
            has_level = "level" in data
            has_enabled = "enabled" in data
            if not has_level and not has_enabled:
                return _error("set requires 'level' and/or 'enabled'", action)

            level = data.get("level")
            enabled = data.get("enabled")

            if has_level and level is not None:
                if not isinstance(level, str) or (
                    level != "off" and level not in levels
                ):
                    return _error(
                        f"invalid level: {level!r}. Must be 'off' or one of "
                        f"{list(levels)}",
                        action,
                    )
            if has_enabled and enabled is not None and not isinstance(enabled, bool):
                return _error("enabled must be a boolean or null", action)

            if not has_enabled:
                # Picking a level implies "turn it on" (matches /caveman
                # <level>). "off" implies "turn it off".
                if level == "off":
                    enabled = False
                elif has_level and level is not None:
                    enabled = True

            if level == "off":
                # "off" is shorthand for "clear the level, disable for this
                # chat", not a storable level.
                level = None

            # `enabled` is always meaningful at this point: either it came
            # from the request and was validated, or it was derived above.
            ok = state.set_state(
                chat_id,
                level=level if has_level else state.KEEP,
                enabled=enabled,
            )
            return {
                "ok": ok,
                "action": action,
                "chat_id": chat_id,
                **_resolved(chat_id),
            }

        if action == "set_level":
            level = data.get("level")
            if level is not None and (
                not isinstance(level, str) or level not in levels
            ):
                return _error(
                    f"invalid level: {level!r}. Must be null or one of "
                    f"{list(levels)}",
                    action,
                )
            ok = state.set_level(chat_id, level)
            return {
                "ok": ok,
                "action": action,
                "chat_id": chat_id,
                **_resolved(chat_id),
            }

        if action == "set_enabled":
            enabled = data.get("enabled")
            if enabled is not None and not isinstance(enabled, bool):
                return _error("enabled must be a boolean or null", action)
            ok = state.set_enabled(chat_id, enabled)
            return {
                "ok": ok,
                "action": action,
                "chat_id": chat_id,
                **_resolved(chat_id),
            }

        if action == "clear":
            ok = state.set_state(chat_id, level=None, enabled=None)
            return {
                "ok": ok,
                "action": action,
                "chat_id": chat_id,
                **_resolved(chat_id),
            }

        return _error(f"unknown action: {action!r}", action)
