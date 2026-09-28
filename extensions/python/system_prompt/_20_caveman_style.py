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

        # The same guard for helpers/plugins_config.py. This is the call that
        # produced `TypeError: get_config() got an unexpected keyword argument
        # 'agent'` on a partial upgrade, and it was unguarded while the state
        # read above was not.
        config_mod = compat.config_api(plugin_cfg, self.agent)
        if config_mod is None:
            return

        config = config_mod.get_config(agent=self.agent)
        resolved = state.resolve(_chat_id_from(self.agent), config)
        if not resolved["enabled"]:
            return

        lean = config_mod.get_bool("lean_style_prompt", agent=self.agent)
        try:
            block = caveman_prompts.build_system_prompt(
                resolved["level"],
                auto_clarity=config_mod.get_bool("auto_clarity", agent=self.agent),
                lean=lean,
            )
        except TypeError as exc:
            # Extension files are hot-loaded by the extensions watchdog, but
            # `helpers/prompts.py` is imported once and cached in sys.modules.
            # After upgrading the plugin without a server restart, the fresh
            # extension can call a cached prompts module that predates the
            # `lean` parameter - that TypeError killed the whole agent turn
            # (production 2026-09-28). Fall back to the plain build; lean is
            # simply not applied until the server restarts and re-imports the
            # current module.
            if "lean" not in str(exc):
                raise
            block = caveman_prompts.build_system_prompt(
                resolved["level"],
                auto_clarity=config_mod.get_bool("auto_clarity", agent=self.agent),
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
