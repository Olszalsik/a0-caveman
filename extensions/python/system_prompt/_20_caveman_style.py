"""
Caveman plugin - system prompt injection.

Extension point: system_prompt

Appends the base style, the active level's ruleset block, and (when enabled) the
auto-clarity rules to the agent's system prompt.

Enabled and level are resolved through `caveman_state.resolve(chat_id, config)`,
the same call every other extension uses, so the prompt injector, the slash
command handler, the observer, the validator and the API cannot disagree about
what a chat is doing.

The prompt text itself is assembled by `helpers/prompts.py`, which the
benchmark harness also calls. Keeping the assembly in one place is what stops
the benchmark from measuring something other than what is sent.
"""

from typing import Any, Optional

from helpers.extension import Extension

from usr.plugins.caveman.helpers import compat
from usr.plugins.caveman.helpers import plugins_config as plugin_cfg
from usr.plugins.caveman.helpers import prompts as caveman_prompts
from usr.plugins.caveman.helpers import state as caveman_state


PLUGIN_NAME = "caveman"

MARKER = caveman_prompts.MARKER


def _chat_id_from(agent) -> str:
    if not agent:
        return ""
    ctx = getattr(agent, "context", None)
    if ctx is None:
        return ""
    return str(getattr(ctx, "id", "") or "")


class CavemanStyle(Extension):
    async def execute(
        self,
        system_prompt: Optional[list] = None,
        loop_data: Any = None,
        **kwargs: Any,
    ):
        if not self.agent:
            return
        if system_prompt is None:
            system_prompt = []

        # A stale helpers/state.py used to raise AttributeError here, which
        # propagated out of get_system_prompt and killed the agent turn. See
        # helpers/compat.py.
        state = compat.state_api(caveman_state, self.agent)
        if state is None:
            return

        config = plugin_cfg.get_config()
        resolved = state.resolve(_chat_id_from(self.agent), config)
        if not resolved["enabled"]:
            return

        block = caveman_prompts.build_system_prompt(
            resolved["level"],
            auto_clarity=plugin_cfg.get_bool("auto_clarity"),
        )
        if not block:
            return

        # `get_system_prompt` builds a fresh list on every call, so this cannot
        # currently fire. Kept as a guard: it costs one pass over a short list
        # and turns a duplicated block into a visible warning rather than
        # silently doubling the prompt.
        if any(MARKER in (section or "") for section in system_prompt):
            try:
                self.agent.context.log.log(
                    type="warning",
                    content=(
                        f"{self.agent.agent_name}: caveman style block already "
                        f"present in the system prompt; not appending a second "
                        f"copy"
                    ),
                )
            except Exception:
                pass
            return

        system_prompt.append(block)
