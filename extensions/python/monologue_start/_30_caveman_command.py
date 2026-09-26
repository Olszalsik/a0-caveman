"""
Caveman plugin - slash command detection.

Extension point: monologue_start

At the start of each agent turn, reads the user message for caveman commands
and updates the per-chat level state.

Recognised (case-insensitive):
  /caveman [lite|full|ultra|wenyan-lite|wenyan-full|wenyan-ultra]
  /caveman off | stop | disable | disabled
  /caveman on | enable | enabled
  /caveman
  talk like caveman | use caveman | caveman on
  normal mode | stop caveman | caveman off | disable caveman

The user message is read through `history.Message.output_text()`, which is the
framework's own accessor for the recursive `MessageContent` union. An earlier
revision probed `loop_data.last_user_message` / `.last_message` / `.messages`
and gated every branch on `isinstance(obj, str)`. None of those attributes
exist on `LoopData`, and `loop_data.user_message` is a `history.Message`, not
a `str`, so the probe always returned "" and every command below was
unreachable.

The user message is deliberately left untouched. Replacing it would corrupt
state that other extensions read for the same turn (memory and skill recall
both read `loop_data.user_message`), and the injected style prompt already
forbids the model from announcing the mode.
"""

import re
from typing import Any, Optional

from helpers.extension import Extension

from usr.plugins.caveman.helpers import state as caveman_state


PLUGIN_NAME = "caveman"

_LEVELS = "|".join(re.escape(level) for level in caveman_state.VALID_LEVELS)
_OFF_WORDS = "off|stop|disable|disabled"
_ON_WORDS = "on|enable|enabled"

COMMAND_PATTERNS = [
    (
        re.compile(rf"^\s*/caveman\s+({_LEVELS})\b", re.IGNORECASE),
        "set_level",
    ),
    (re.compile(rf"^\s*/caveman\s+({_OFF_WORDS})\b", re.IGNORECASE), "off"),
    (re.compile(rf"^\s*/caveman\s+({_ON_WORDS})\b", re.IGNORECASE), "on"),
    (re.compile(r"^\s*/caveman\s*$", re.IGNORECASE), "on_default"),
    (
        re.compile(
            r"^\s*(talk\s+like\s+caveman|use\s+caveman|caveman\s+on)\b[.!?]*\s*$",
            re.IGNORECASE,
        ),
        "on_default",
    ),
    (
        re.compile(
            r"^\s*(normal\s+mode|stop\s+caveman|caveman\s+off|"
            r"caveman\s+disabled|disable\s+caveman)\b[.!?]*\s*$",
            re.IGNORECASE,
        ),
        "off",
    ),
]


def _classify(text: str) -> Optional[tuple]:
    if not text:
        return None
    for pat, action in COMMAND_PATTERNS:
        match = pat.match(text)
        if match:
            if action == "set_level":
                return (action, match.group(1).lower())
            return (action, None)
    return None


def _chat_id_from(agent) -> str:
    if not agent:
        return ""
    ctx = getattr(agent, "context", None)
    if ctx is None:
        return ""
    return str(getattr(ctx, "id", "") or "")


def _get_latest_user_text(loop_data: Any) -> str:
    """Extract the user-visible text of the current turn's user message."""
    if loop_data is None:
        return ""

    message = getattr(loop_data, "user_message", None)
    if message is not None:
        # Canonical accessor; unwraps the recursive MessageContent union.
        for attr in ("output_text",):
            getter = getattr(message, attr, None)
            if callable(getter):
                try:
                    text = getter()
                except Exception:
                    text = None
                if isinstance(text, str) and text.strip():
                    return text
        # Fallback for a bare string content or a stub message object.
        content = getattr(message, "content", None)
        if isinstance(content, str) and content.strip():
            return content

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
            # One atomic write: set the level and turn the mode on together, so
            # no reader can observe a half-applied command.
            ok = caveman_state.set_state(chat_id, level=value, enabled=True)
        elif action == "off":
            ok = caveman_state.set_state(chat_id, enabled=False)
        elif action in ("on", "on_default"):
            ok = caveman_state.set_state(chat_id, enabled=True)
        else:
            return

        if not ok:
            self.agent.context.log.log(
                type="warning",
                content=(
                    f"{self.agent.agent_name}: caveman command failed to persist "
                    f"(action={action!r}, value={value!r})"
                ),
            )
