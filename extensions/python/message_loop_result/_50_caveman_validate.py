"""
Caveman plugin - response filler detector (and optional sanitizer).

Extension point: message_loop_result

Why this point and not `response_stream_end`
---------------------------------------------
The previous revision lived on `response_stream_end` and was dead code:

  * `agent.py` calls that point with only `loop_data=`, so the extension
    received no response text at all and returned on its first text check.
  * Even with text, it tried to "sanitize in place" by assigning
    `kwargs[key] = new_text`. `helpers.extension.call_extensions_async`
    calls `cls(agent=agent).execute(**kwargs)`, so `kwargs` is a fresh dict
    per extension; the write landed in a local and was discarded. Mutating a
    `**kwargs` dict can never reach the caller.
  * Its warning path appended to `agent.context.extras`, which is not a
    framework attribute (the `agent.context.extras.md` hits are prompt
    filenames). `getattr(ctx, "extras", None)` was always None, so the
    warning could not have rendered either.

`message_loop_result` receives a mutable `result_data` dict holding
`llm_result`, and it runs before `hist_add_ai_response`, so an edit to
`llm_result.response` here is the value that reaches history and the UI.

Behaviour
---------
Default is a **soft, non-mutating** warning: the text is scanned, and if a
banned filler phrase is present a warning is logged. Nothing is rewritten.

Stripping is opt-in via `sanitize_responses: true` in the plugin config, and
only applies on a turn that is actually producing the user-visible answer
(every function call on the turn is the final `response` tool, or there are
no function calls at all). Prose on an intermediate tool-call turn is
narration, not the answer, and is left alone.

Known limitation: this is phrase-level deletion, not rewriting, so removing
an opener can leave a fragment behind ("Sure! I'd be happy to help."
becomes "help."). That is why it is off by default and why every strip is
reported. A turn whose text would be emptied entirely is not modified.
"""

import re
from typing import Any

from helpers.extension import Extension

from usr.plugins.caveman.helpers import plugins_config as plugin_cfg
from usr.plugins.caveman.helpers import state as caveman_state


PLUGIN_NAME = "caveman"

BANNED_FILLER = (
    "i'd be happy to",
    "i would be happy to",
    "i'm happy to",
    "of course!",
    "certainly!",
    "sure!",
    "no problem!",
    "feel free to",
    "don't hesitate to",
    "i hope this helps",
    "let me know if you need",
    "if you have any other questions",
    "great question!",
    "that's a great",
    "you make a good point",
)

BANNED_RE = re.compile(
    "|".join(re.escape(phrase) for phrase in BANNED_FILLER), re.IGNORECASE
)

# Levels where stripping is meaningful even when opted in. `lite` and `full`
# are explicitly allowed conversational openers.
STRIP_LEVELS = ("ultra", "wenyan-lite", "wenyan-full", "wenyan-ultra")

FINAL_RESPONSE_TOOL = "response"


def _is_strip_level(level: str) -> bool:
    return level in STRIP_LEVELS


def _chat_id(agent) -> str:
    if not agent:
        return ""
    ctx = getattr(agent, "context", None)
    if ctx is None:
        return ""
    return str(getattr(ctx, "id", "") or "")


def _is_final_answer_turn(llm_result) -> bool:
    """True when this turn's prose is the user-visible answer.

    A turn that calls real tools is still working; only a turn whose calls
    are all the final `response` tool (or that makes no call at all) is done.
    """
    try:
        calls = llm_result.function_calls
    except Exception:
        return True
    if not calls:
        return True
    return all(getattr(call, "name", "") == FINAL_RESPONSE_TOOL for call in calls)


def _strip_filler(text: str) -> tuple[str, int]:
    out, count = BANNED_RE.subn("", text)
    if count:
        out = re.sub(r"[ \t]{2,}", " ", out)
        out = re.sub(r"[ \t]+([,.;:!?])", r"\1", out)
        # Removing a leading interjection can leave the line starting with
        # punctuation ("! Removed the file.").
        out = re.sub(r"^[\s,.;:!?]+", "", out)
    return out, count


def _warn(agent, level: str, phrases: list[str], stripped: bool) -> None:
    try:
        log = agent.context.log
    except Exception:
        return
    unique = sorted({p.lower() for p in phrases})
    verb = "stripped" if stripped else "detected"
    log.log(
        type="warning",
        content=(
            f"{agent.agent_name}: caveman filler {verb} "
            f"({len(phrases)} phrase(s), level={level}): {', '.join(unique[:3])}"
        ),
    )


class CavemanValidate(Extension):
    async def execute(self, result_data: dict[str, Any] | None = None, **kwargs: Any):
        agent = self.agent
        if not agent or not isinstance(result_data, dict):
            return
        if result_data.get("skip_default_processing"):
            return

        llm_result = result_data.get("llm_result")
        if llm_result is None:
            return

        text = getattr(llm_result, "response", "")
        if not isinstance(text, str) or not text.strip():
            return

        config = plugin_cfg.get_config()
        chat_id = _chat_id(agent)
        if not caveman_state.resolve(chat_id, config)["enabled"]:
            return

        matches = BANNED_RE.findall(text)
        if not matches:
            return

        level = caveman_state.get_level(
            chat_id, config.get("level", caveman_state.DEFAULT_LEVEL)
        )

        should_strip = (
            plugin_cfg.get_bool("sanitize_responses")
            and _is_strip_level(level)
            and _is_final_answer_turn(llm_result)
        )
        if not should_strip:
            _warn(agent, level, matches, stripped=False)
            return

        new_text, _ = _strip_filler(text)
        if new_text.strip():
            llm_result.response = new_text
            _warn(agent, level, matches, stripped=True)
