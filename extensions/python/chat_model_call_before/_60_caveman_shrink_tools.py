"""
Caveman plugin - tool-description compression.

Extension point: chat_model_call_before

Why this point
--------------
The previous revision lived on `message_loop_prompts_before` and never ran.
`Agent.prepare_prompt` calls that point at the very start, before
`loop_data.system` is assigned and long before any tool payload exists. The
extension read `loop_data.tools`, which is not an attribute of `LoopData`
(`agent.py`), so it iterated an empty list forever.

`chat_model_call_before` is the correct point. `Agent.call_chat_model_turn`
builds the payload with `build_responses_function_tools(agent)`, stores it in
`call_data["a0_responses_function_tools"]`, fires the hook with that mutable
dict, and only then reads the key back out. So replacing the `description`
entries in place here is what the provider actually sends.

Behaviour
---------
Gated on `shrink_tools`, which defaults to **false**. A tool description is the
contract the model reads when deciding whether and how to call a tool, so
rewriting it is not a free win. Each level's own definition is compressed; the
tool `name` and `parameters` schema are never touched, because a renamed tool
or an altered schema breaks tool-call matching rather than shortening prose.

Compression itself lives in `helpers/compress.py`, which protects fenced code,
inline code, URLs, paths, CONST_CASE, dotted paths, function calls and version
numbers, short-circuits on CJK, and uses `(?<![\\w-])` boundaries so that
`just-in-time` and `make sure` survive. See that module for why the naive
`\b` version was wrong.
"""

from typing import Any

from helpers.extension import Extension

from usr.plugins.caveman.helpers import compress as caveman_compress
from usr.plugins.caveman.helpers import plugins_config as plugin_cfg


PLUGIN_NAME = "caveman"

# Keys that carry the model-facing contract. Deliberately NOT included:
#   name       - a renamed tool cannot be matched back to its implementation
#   parameters - an altered JSON schema makes tool calls fail validation
COMPRESSIBLE_FIELDS = ("description",)


def _chat_id(agent) -> str:
    if not agent:
        return ""
    ctx = getattr(agent, "context", None)
    if ctx is None:
        return ""
    return str(getattr(ctx, "id", "") or "")


def compress_tool_descriptions(tools: Any) -> tuple[int, int]:
    """Compress descriptions in place. Returns (tools_touched, chars_saved)."""
    if not isinstance(tools, list):
        return (0, 0)

    touched = 0
    saved = 0
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        for field in COMPRESSIBLE_FIELDS:
            original = tool.get(field)
            if not isinstance(original, str) or not original:
                continue
            compressed, before, after = caveman_compress.compress(original)
            if compressed is original or after >= before:
                # Unchanged, or a case where compression did not help
                # (CJK guard returns an identical string).
                continue
            tool[field] = compressed
            touched += 1
            saved += before - after
    return (touched, saved)


class CavemanShrinkTools(Extension):
    async def execute(self, call_data: dict[str, Any] | None = None, **kwargs: Any):
        agent = self.agent
        if not agent or not isinstance(call_data, dict):
            return
        if not plugin_cfg.get_bool("shrink_tools"):
            return

        # Not decoration: the plugin records per-chat state, so the level has to
        # come from the same resolver every other extension uses.
        from usr.plugins.caveman.helpers import state as caveman_state

        resolved = caveman_state.resolve(_chat_id(agent), plugin_cfg.get_config())
        if not resolved["enabled"]:
            return

        tools = call_data.get("a0_responses_function_tools")
        touched, saved = compress_tool_descriptions(tools)
        if not touched:
            return

        try:
            agent.context.log.log(
                type="info",
                content=(
                    f"{agent.agent_name}: caveman shrank {touched} tool "
                    f"description(s) by {saved} chars (level={resolved['level']})"
                ),
            )
        except Exception:
            # Telemetry must never break a model call.
            pass
