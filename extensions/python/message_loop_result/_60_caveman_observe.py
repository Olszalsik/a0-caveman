"""
Caveman plugin - raw output observation.

Extension point: message_loop_result (runs after _50_caveman_validate)

Records, per model turn, the length of the text the model actually produced.

Why this point
--------------
The previous revision recorded at `monologue_end` from
`loop_data.last_response`. That field is assigned in
`Agent.hist_add_ai_response` as:

    message = llm_result.function_calls_text() or message
    self.loop_data.last_response = message

On the terminal turn the agent emits the `response` **tool call**, so
`function_calls_text()` is non-empty and `last_response` holds the
`{"tool_name": "response", "tool_args": {...}}` envelope rather than the
prose the user read. The counter was measuring the length of a tool-call
JSON blob.

`message_loop_result` hands over the real `llm_result` before
`hist_add_ai_response` runs, so `llm_result.response` is the model's actual
output for the turn. It runs at `_60`, after `_50_caveman_validate`, so the
recorded length is the length that was actually kept.

What is recorded
----------------
`turns`, `chars`, and a per-level breakdown. Nothing else. There is
deliberately no "estimated tokens saved" figure: a saving needs a measured
control arm, and upstream retracted the fixed ratio this used to apply
(`docs/HONEST-NUMBERS.md`). See `benchmarks/run.py` for the real A/B.
"""

from typing import Any

from helpers.extension import Extension

from usr.plugins.caveman.helpers import compat
from usr.plugins.caveman.helpers import plugins_config as plugin_cfg
from usr.plugins.caveman.helpers import state as caveman_state
from usr.plugins.caveman.helpers import text_extract


PLUGIN_NAME = "caveman"


def _chat_id(agent) -> str:
    if not agent:
        return ""
    ctx = getattr(agent, "context", None)
    if ctx is None:
        return ""
    return str(getattr(ctx, "id", "") or "")


class CavemanObserve(Extension):
    async def execute(self, result_data: dict[str, Any] | None = None, **kwargs: Any):
        agent = self.agent
        if not agent or not isinstance(result_data, dict):
            return

        # See helpers/compat.py: a stale state module must not raise out of an
        # extension point. Losing one observation is acceptable; killing the
        # turn is not.
        state = compat.state_api(caveman_state, agent)
        if state is None:
            return

        llm_result = result_data.get("llm_result")
        if llm_result is None:
            return

        raw_response = getattr(llm_result, "response", "")
        if not isinstance(raw_response, str) or not raw_response.strip():
            return
        # Finding 13 (remediation 2026-09-28): on a tool-only turn the
        # framework stores the function-call envelope JSON into `.response`,
        # so the per-turn char stats measured tool-call JSON instead of model
        # output and inflated the numbers. On a bare `response`-tool turn the
        # shared extractor returns the answer text from the tool arguments.
        text = text_extract.prose_from_llm_result(llm_result)
        if not text.strip():
            return

        config_mod = compat.config_api(plugin_cfg, agent)
        if config_mod is None:
            return
        config = config_mod.get_config(agent=agent)
        chat_id = _chat_id(agent)
        resolved = state.resolve(chat_id, config)
        if not resolved["enabled"]:
            return

        state.record_turn(chat_id, resolved["level"], len(text))
