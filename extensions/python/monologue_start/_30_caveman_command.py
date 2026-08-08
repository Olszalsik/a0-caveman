"""
Caveman plugin - slash command detection.

At the start of each agent turn, inspects the most recent user message
for caveman slash commands / natural phrases and updates the per-chat
level state.

Recognised patterns (case-insensitive):
 /caveman [lite|full|ultra|wenyan-lite|wenyan-full|wenyan-ultra]
 /caveman off
 /caveman on
 talk like caveman
 use caveman
 caveman on
 normal mode | stop caveman | caveman off | caveman disabled

Extension point: monologue_start
"""

import re
from typing import Any, Optional

from helpers.extension import Extension

from usr.plugins.caveman.helpers import state as caveman_state


PLUGIN_NAME = "caveman"

COMMAND_PATTERNS = [
    (re.compile(r"^\s*/caveman\s+(lite|full|ultra|wenyan-lite|wenyan-full|wenyan-ultra)\b", re.IGNORECASE), "set_level"),
    (re.compile(r"^\s*/caveman\s+(off|disable|disabled|stop)\b", re.IGNORECASE), "off"),
    (re.compile(r"^\s*/caveman\s+(on|enable|enabled)\b", re.IGNORECASE), "on"),
    (re.compile(r"^\s*/caveman\s*$", re.IGNORECASE), "on_default"),
    (re.compile(r"^\s*(talk\s+like\s+caveman|use\s+caveman|caveman\s+on)\b[.!?]*\s*$", re.IGNORECASE), "on_default"),
    (re.compile(r"^\s*(normal\s+mode|stop\s+caveman|caveman\s+off|caveman\s+disabled|disable\s+caveman)\b[.!?]*\s*$", re.IGNORECASE), "off"),
]


def _classify(text: str) -> Optional[tuple]:
    if not text:
        return None
    for pat, action in COMMAND_PATTERNS:
        m = pat.match(text)
        if m:
            if action == "set_level":
                return (action, m.group(1).lower())
            return (action, None)
    return None


def _strip_command(text: str) -> str:
    out = text
    for pat, _ in COMMAND_PATTERNS:
        out = pat.sub("", out).strip()
    out = re.sub(r"\s+", " ", out).strip()
    out = re.sub(r"^[\s,.;:!?\-]+", "", out).strip()
    return out


def _chat_id_from(agent) -> str:
    if not agent:
        return ""
    ctx = getattr(agent, "context", None)
    if ctx is None:
        return ""
    return str(getattr(ctx, "id", "") or getattr(ctx, "chat_id", "") or "")


def _get_latest_user_text(loop_data: Any) -> str:
    if loop_data is None:
        return ""
    for attr_chain in (
        ("last_user_message",),
        ("user_message",),
        ("last_message",),
        ("messages", -1),
    ):
        obj = loop_data
        try:
            for a in attr_chain:
                if isinstance(a, int):
                    obj = obj[a]
                else:
                    obj = getattr(obj, a)
            if isinstance(obj, str) and obj.strip():
                return obj
        except Exception:
            pass
    return ""


class CavemanCommand(Extension):
    async def execute(
        self,
        loop_data: Any = None,
        **kwargs: Any,
    ):
        if not self.agent:
            return

        text = _get_latest_user_text(loop_data)
        if not text:
            return

        classification = _classify(text)
        if not classification:
            return

        action, value = classification
        chat_id = _chat_id_from(self.agent)
        if not chat_id:
            return

        if action == "set_level":
            caveman_state.set_level(chat_id, value)
            caveman_state.set_enabled(chat_id, True)
        elif action == "off":
            caveman_state.set_enabled(chat_id, False)
        elif action == "on":
            caveman_state.set_enabled(chat_id, True)
        elif action == "on_default":
            caveman_state.set_enabled(chat_id, True)

        residual = _strip_command(text)
        if not residual and hasattr(loop_data, "last_user_message"):
            try:
                setattr(loop_data, "last_user_message", "[caveman command processed]")
            except Exception:
                pass
