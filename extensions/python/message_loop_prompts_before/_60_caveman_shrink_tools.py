"""
Caveman plugin - tool description shrinker (caveman-shrink equivalent).

Agent Zero v2.2 does not have a dedicated `tool_data` extension point, but
`message_loop_prompts_before` runs right before the LLM call, with access
to the tools payload. This extension walks the tools list and compresses
tool names + descriptions using the same caveman rules.

Only runs when the plugin is enabled and the active level is full/ultra/wenyan-*.

Extension point: message_loop_prompts_before
"""

import re
from typing import Any

from helpers.extension import Extension
from helpers import plugins as plugins_helper

from usr.plugins.caveman.helpers import state as caveman_state


PLUGIN_NAME = "caveman"
ACTIVE_LEVELS = ("full", "ultra", "wenyan-lite", "wenyan-full", "wenyan-ultra")

FILLER = (
    "i would like to", "you can", "this tool", "this function",
    "use this", "please note that", "note that", "in order to",
    "make sure to", "be sure to", "it is important to",
)


def _shrink_text(text):
    if not text or not isinstance(text, str):
        return text
    out = text
    for f in FILLER:
        out = re.sub(rf"\b{re.escape(f)}\b", "", out, flags=re.IGNORECASE)
    out = re.sub(r"\s+", " ", out).strip()
    if out.endswith("."):
        out = out[:-1]
    return out


def _shrink_tool(tool):
    if not isinstance(tool, dict):
        return tool
    if "name" in tool and isinstance(tool["name"], str):
        tool["name"] = tool["name"].replace("caveman_", "c_")
    if "description" in tool and isinstance(tool["description"], str):
        tool["description"] = _shrink_text(tool["description"])
    return tool


class CavemanShrinkTools(Extension):
    async def execute(self, loop_data=None, **kwargs):
        if not self.agent:
            return
        try:
            cfg = plugins_helper.get_plugin_config(PLUGIN_NAME) or {}
        except Exception:
            cfg = {}
        if not cfg.get("enabled", False):
            return
        try:
            ctx = self.agent.context
            chat_id = str(getattr(ctx, "id", "") or getattr(ctx, "chat_id", "") or "")
        except Exception:
            chat_id = ""
        if not caveman_state.is_enabled(chat_id, cfg.get("enabled", False)):
            return
        level = caveman_state.get_level(chat_id, cfg.get("level", "full"))
        if level not in ACTIVE_LEVELS:
            return
        tools = getattr(loop_data, "tools", None) or []
        if not isinstance(tools, list):
            return
        for t in tools:
            _shrink_tool(t)
