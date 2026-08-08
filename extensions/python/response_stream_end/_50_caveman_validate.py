"""
Caveman plugin - response validator + hard sanitizer (v0.4.0).

At the end of each response stream, scans the final text for banned filler
phrases. Two behaviors depending on the active level:

 - full: emit a soft warning (chat_extras) but do NOT mutate the response.
 - ultra / wenyan-*: STRIP the banned phrases from the response and emit a
   soft warning noting the rewrite.

Also: at the same time, this extension can rewrite the response content
in-place if the framework's call site passes a mutable reference (best-
effort, plan A). When the framework only exposes a string copy (plan B),
the sanitization is advisory only.

Extension point: response_stream_end
"""

import re
from typing import Any

from helpers.extension import Extension
from helpers import plugins as plugins_helper

from usr.plugins.caveman.helpers import state as caveman_state


PLUGIN_NAME = "caveman"

BANNED_FILLER = (
    "i'd be happy to",
    "i would be happy to",
    "of course!",
    "certainly!",
    "sure!",
    "no problem!",
    "i'm happy to",
    "feel free to",
    "don't hesitate to",
    "i hope this helps",
    "let me know if you need",
    "if you have any other questions",
    "great question!",
    "that's a great",
    "you make a good point",
)

BANNED_RE = re.compile("|".join(re.escape(p) for p in BANNED_FILLER), re.IGNORECASE)

STRIP_LEVELS = ("ultra", "wenyan-lite", "wenyan-full", "wenyan-ultra")


def _is_strip_level(level):
    return level in STRIP_LEVELS


def _chat_id(agent):
    if not agent:
        return ""
    ctx = getattr(agent, "context", None)
    if ctx is None:
        return ""
    return str(getattr(ctx, "id", "") or getattr(ctx, "chat_id", "") or "")


def _get_response_text(kwargs):
    if not isinstance(kwargs, dict):
        return "", None
    for key in ("response", "message", "content", "text"):
        v = kwargs.get(key)
        if isinstance(v, str) and v.strip():
            return v, (key, v)
    resp = kwargs.get("response")
    if resp is not None:
        for attr in ("content", "text", "message"):
            v = getattr(resp, attr, None)
            if isinstance(v, str) and v.strip():
                return v, (attr, getattr(resp, attr))
    return "", None


def _emit_warning(agent, level, matches):
    if not agent:
        return
    try:
        ctx = agent.context
        extras = getattr(ctx, "extras", None)
        if not isinstance(extras, list):
            return
        unique = sorted({m.lower() for m in matches})
        extras.append({
            "type": "caveman_warning",
            "title": "Caveman: filler " + ("stripped" if _is_strip_level(level) else "detected"),
            "detail": (
                f"{len(matches)} banned phrase(s) "
                + ("stripped" if _is_strip_level(level) else "left as-is")
                + f" (level={level}): {', '.join(unique[:3])}"
            ),
            "dismissible": True,
            "priority": 40,
        })
    except Exception:
        pass


def _strip_in_place(text, pattern):
    out, count = pattern.subn("", text)
    if count:
        out = re.sub(r"\s{2,}", " ", out)
        out = re.sub(r"\s+([,.;:!?])", r"\1", out)
    return out, count


class CavemanValidate(Extension):
    async def execute(self, **kwargs):
        agent = self.agent
        if not agent:
            return
        try:
            cfg = plugins_helper.get_plugin_config(PLUGIN_NAME) or {}
        except Exception:
            cfg = {}
        if not isinstance(cfg, dict) or not cfg.get("enabled", False):
            return
        chat_id = _chat_id(agent)
        if not caveman_state.is_enabled(chat_id, default_enabled=False):
            return
        level = caveman_state.get_level(chat_id, cfg.get("level", "full"))
        text, ref = _get_response_text(kwargs)
        if not text:
            return
        matches = BANNED_RE.findall(text)
        if not matches:
            return
        if _is_strip_level(level) and ref is not None:
            key, target = ref
            new_text, _ = _strip_in_place(text, BANNED_RE)
            if isinstance(target, str):
                try:
                    kwargs[key] = new_text
                except TypeError:
                    pass
        _emit_warning(agent, level, matches)
