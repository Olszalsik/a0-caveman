"""
Caveman plugin - system prompt injection.

Appends the caveman style prompt (base + per-chat intensity + optional
auto-clarity fragment) to the agent's system prompt on every turn, but
ONLY when the plugin is enabled for the current chat.

Per-chat state is read from usr.plugins.caveman.helpers.state, which is
populated by the /caveman <level> slash command (see
monologue_start/_30_caveman_command.py).

Global config (helpers.plugins.get_plugin_config) provides:
 - enabled: default bool (per-chat override takes precedence)
 - level: default intensity (per-chat override takes precedence)
 - auto_clarity: bool, controls whether auto-clarity rules are appended

Extension point: system_prompt
"""

import os
from typing import Any

from helpers.extension import Extension
from helpers import plugins as plugins_helper

from usr.plugins.caveman.helpers import state as caveman_state


PLUGIN_NAME = "caveman"

PROMPTS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "prompts",
)


def _read_fragment(filename: str) -> str:
    path = os.path.join(PROMPTS_DIR, filename)
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read().rstrip()
    except FileNotFoundError:
        return ""
    except Exception:
        return ""


def _get_config() -> dict:
    try:
        cfg = plugins_helper.get_plugin_config(PLUGIN_NAME) or {}
    except Exception:
        cfg = {}
    if not isinstance(cfg, dict):
        cfg = {}
    return cfg


def _chat_id_from(agent) -> str:
    if not agent:
        return ""
    ctx = getattr(agent, "context", None)
    if ctx is None:
        return ""
    return str(getattr(ctx, "id", "") or getattr(ctx, "chat_id", "") or "")


class CavemanStyle(Extension):
    async def execute(
        self,
        system_prompt: list = None,
        loop_data: Any = None,
        **kwargs: Any,
    ):
        if not self.agent:
            return
        if system_prompt is None:
            system_prompt = []

        cfg = _get_config()
        default_enabled = bool(cfg.get("enabled", False))
        default_level = cfg.get("level", "full")

        chat_id = _chat_id_from(self.agent)
        enabled = caveman_state.is_enabled(chat_id, default_enabled)
        if not enabled:
            return

        level = caveman_state.get_level(chat_id, default_level)
        if level not in caveman_state.VALID_LEVELS:
            level = "full"

        base = _read_fragment("caveman.system.style.md")
        intensity = _read_fragment(f"caveman.intensity.{level}.md")
        clarity = ""
        if cfg.get("auto_clarity", True):
            clarity = _read_fragment("caveman.auto_clarity.md")

        block_parts = [p for p in (base, intensity, clarity) if p]
        if not block_parts:
            return

        footer = f"\n\n<active_level>{level}</active_level>"
        block = "\n\n".join(block_parts) + footer

        marker = '<style name="caveman"'
        if any(marker in (s or "") for s in system_prompt):
            return

        system_prompt.append(block)
