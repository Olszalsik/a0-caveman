"""Caveman plugin - user-visible text extraction from framework messages.

Why this module exists
----------------------
Agent Zero stores every user message as the dict envelope rendered from
``prompts/fw.user_message.md`` (see ``agent.py::hist_add_user_message``)::

    {"user_message": "...", "system_message": ..., "attachments": ...}

and ``helpers/history.py::_stringify_output`` prefixes ``"user: "`` and
JSON-dumps dict content, so ``Message.output_text()`` returns::

    user: {"user_message":"/caveman ultra"}

Three features shipped against the wrong contract and were silently dead in
production (remediation 2026-09-28, findings 1-3): the slash-command
classifier (anchored patterns never matched the label prefix), the
auto-clarity destructive-command detector (read the keys
``content``/``message``/``text`` — the real key is ``user_message``), and
user-message compression. The unit tests did not catch it because the test
fakes returned raw strings without the prefix or the envelope.

Every reader of user-visible message text goes through here so the four
call sites cannot drift apart again. The extraction deliberately fails open:
when nothing text-like is found it returns ``""`` and the caller skips, the
same conservative outcome as before this module existed.
"""

from __future__ import annotations

import json
from typing import Any

# Keys a user-message envelope or content dict may carry. ``user_message``
# is the real key ``fw.user_message.md`` produces; the others cover plugins
# and framework versions that store plain prose under different names.
_ENVELOPE_KEYS = ("user_message", "message", "text", "content")

# Labels ``history._stringify_output`` puts in front of stringified content.
_LABEL_PREFIXES = ("user: ", "human: ", "ai: ")


def text_from_content(content: Any) -> str:
    """Best-effort user-visible text from a MessageContent value.

    Returns "" when nothing text-like is found (never None, so callers can
    test truthiness uniformly).
    """
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, dict):
        # Known keys only. No "first string value" fallback: a dict content
        # can be a tool result (`tool_name`/`tool_result`), and harvesting an
        # arbitrary value from that would treat tool output as user text.
        for key in _ENVELOPE_KEYS:
            value = content.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return ""


def _unwrap_framework_string(text: str) -> str:
    """Undo ``output_text()`` framing: strip the label, unwrap the JSON dump."""
    lowered = text.lower()
    for prefix in _LABEL_PREFIXES:
        if lowered.startswith(prefix):
            text = text[len(prefix):]
            break
    stripped = text.strip()
    if not stripped.startswith("{"):
        return stripped
    try:
        envelope = json.loads(stripped)
    except ValueError:
        return stripped
    if isinstance(envelope, dict):
        unwrapped = text_from_content(envelope)
        if unwrapped:
            return unwrapped
    return stripped


def user_text_from_message(message: Any) -> str:
    """User-visible text of a ``history.Message`` (or compatible object).

    Reads the live ``content`` first — that is the real envelope — and only
    falls back to ``output_text()`` (unwrapping its ``"user: "`` label and
    JSON dump) for objects that carry no content attribute, such as string
    stubs.
    """
    if message is None:
        return ""
    content = getattr(message, "content", None)
    if content is not None:
        text = text_from_content(content)
        if text:
            return text
    try:
        output = message.output_text()
    except Exception:
        return ""
    if isinstance(output, str) and output.strip():
        return _unwrap_framework_string(output)
    return ""


def _is_tool_call_envelope(text: str) -> bool:
    """True when `text` is the JSON envelope ``function_calls_text()`` emits."""
    stripped = text.strip()
    if not stripped.startswith(("{", "[")):
        return False
    try:
        data = json.loads(stripped)
    except ValueError:
        return False
    if isinstance(data, dict):
        return "tool_name" in data or "tool_args" in data
    if isinstance(data, list) and data and isinstance(data[0], dict):
        return "tool_name" in data[0] or "tool_args" in data[0]
    return False


def prose_from_llm_result(llm_result: Any) -> str:
    """The user-visible prose of an LLM turn, or "" when there is none.

    ``llm_result.response`` falls back to the function-call envelope JSON when
    the model produced no prose (``helpers/llm_result.py``):
    ``if not result.response and result.function_calls: response =
    function_calls_text()``. That envelope is narration metadata, not output
    the user read — the observer used to count its length and the validator
    could rewrite JSON inside it. When the turn is a bare ``response`` tool
    call whose answer lives in the tool arguments, that argument text is the
    prose, so it is returned instead.
    """
    text = getattr(llm_result, "response", "")
    if not isinstance(text, str):
        return ""
    try:
        calls = llm_result.function_calls
    except Exception:
        calls = None
    if not calls or not _is_tool_call_envelope(text):
        return text.strip()
    for call in calls:
        if getattr(call, "name", "") != "response":
            continue
        arguments = getattr(call, "arguments", None) or {}
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except ValueError:
                arguments = {}
        for key in ("text", "response", "message"):
            value = arguments.get(key) if isinstance(arguments, dict) else None
            if isinstance(value, str) and value.strip():
                return value.strip()
    return ""