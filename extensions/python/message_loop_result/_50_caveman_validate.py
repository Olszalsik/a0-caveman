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

from usr.plugins.caveman.helpers import compat
from usr.plugins.caveman.helpers import plugins_config as plugin_cfg
from usr.plugins.caveman.helpers import state as caveman_state
from usr.plugins.caveman.helpers import text_extract

PLUGIN_NAME = "caveman"

# Runs of spaces/tabs NOT at line start. Runs at line start can be markdown
# indented-code indentation, which collapsing would silently destroy.
_WS_RUN_RE = re.compile(r"(?<=\S)[ \t]{2,}(?=\S)")

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


# Fenced code blocks, including an unclosed fence at EOF so the tail is
# protected too. Non-greedy: stops at the first matching closing fence.
_FENCED_CODE_RE = re.compile(r"```[\s\S]*?(?:```|\Z)|~~~[\s\S]*?(?:~~~|\Z)")


def _split_code_segments(text: str) -> list[tuple[str, bool]]:
    """Split text into (segment, is_code) pairs, keeping order and content."""
    segments: list[tuple[str, bool]] = []
    pos = 0
    for match in _FENCED_CODE_RE.finditer(text):
        if match.start() > pos:
            segments.append((text[pos:match.start()], False))
        segments.append((match.group(0), True))
        pos = match.end()
    if pos < len(text):
        segments.append((text[pos:], False))
    return segments


def _strip_filler(text: str) -> tuple[str, int]:
    # Finding 14 (remediation 2026-09-28): the whitespace collapse ran over
    # the whole text with no code-block protection, silently mangling fenced
    # and indented code whenever a filler phrase appeared anywhere in the
    # answer. Phrase removal and cleanup now apply outside fences only, and
    # the collapse never touches line-leading indentation.
    parts: list[str] = []
    total = 0
    for segment, is_code in _split_code_segments(text):
        if is_code:
            parts.append(segment)
            continue
        out, count = BANNED_RE.subn("", segment)
        if count:
            out = _WS_RUN_RE.sub(" ", out)
            out = re.sub(r"[ \t]+([,.;:!?])", r"\1", out)
            # Removing a leading interjection can leave the line starting with
            # punctuation ("! Removed the file.").
            out = re.sub(r"^[\s,.;:!?]+", "", out)
        total += count
        parts.append(out)
    return "".join(parts), total


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

        # See helpers/compat.py: a stale state module must not raise out of an
        # extension point.
        state = compat.state_api(caveman_state, self.agent)
        if state is None:
            return

        llm_result = result_data.get("llm_result")
        if llm_result is None:
            return

        raw_response = getattr(llm_result, "response", "")
        if not isinstance(raw_response, str) or not raw_response.strip():
            return
        # Finding 13 (remediation 2026-09-28): on a tool-only turn
        # `llm_result.response` is the function-call envelope JSON (the
        # framework's fallback in helpers/llm_result.py), not prose the user
        # read -- matching filler against it was meaningless and rewriting it
        # would have replaced the envelope with mangled JSON. Use the shared
        # prose extractor: on a bare `response`-tool turn it returns the tool
        # call's text argument (the actual answer), otherwise the response.
        prose = text_extract.prose_from_llm_result(llm_result)
        if not prose.strip():
            return

        # Agent-scoped: per_project_config / per_agent_config are true for this
        # plugin, so the gate must resolve for THIS agent, not globally.
        config_mod = compat.config_api(plugin_cfg, agent)
        if config_mod is None:
            return
        config = config_mod.get_config(agent=agent)
        chat_id = _chat_id(self.agent)
        if not state.resolve(chat_id, config)["enabled"]:
            return

        matches = BANNED_RE.findall(prose)
        if not matches:
            return

        level = state.get_level(chat_id, config.get("level", state.DEFAULT_LEVEL))

        should_strip = (
            config_mod.get_bool("sanitize_responses", agent=agent)
            and _is_strip_level(level)
            and _is_final_answer_turn(llm_result)
        )
        if not should_strip:
            _warn(agent, level, matches, stripped=False)
            return

        if prose != raw_response:
            # The display text is the function-call envelope JSON, not prose:
            # never rewrite it (a stripped fragment would be stored as the
            # response field). Report the detection so it stays visible.
            _warn(agent, level, matches, stripped=False)
            return

        new_text, _ = _strip_filler(prose)
        if new_text.strip():
            llm_result.response = new_text
            _warn(agent, level, matches, stripped=True)
