"""History compression hook — runs in `message_loop_prompts_before`.

This is the SECONDARY fix. The primary fix (tool-output compression) already
catches new large outputs as they enter history. This hook catches OLD
uncompressed content already sitting in history from before the plugin was
active, and also compresses long assistant responses that accumulated.

Agent Zero calls:
    await extension.call_extensions_async("message_loop_prompts_before", self, loop_data=...)

We walk self.agent.history.all_messages() and compress large text content in place.
We NEVER touch:
  - system messages
  - the first user message (initial task)
  - content under auto_compress_history_min_tokens

When the plugin is disabled, this hook is a zero-cost no-op.
"""

from __future__ import annotations

import sys
from typing import Any

from helpers.extension import Extension

from usr.plugins.caveman.helpers.headroom import compressor
from usr.plugins.caveman.helpers.headroom import config as _config


def _print(msg: str) -> None:
    sys.stderr.write(f"[caveman/headroom] {msg}\n")
    sys.stderr.flush()


def _extract_text(content: Any) -> str | None:
    """Extract a plain-text string from a MessageContent value.
    Returns None if the content is not a simple string.

    Finding 3 (remediation 2026-09-28): the key list missed `user_message` --
    the key `hist_add_user_message` actually stores (fw.user_message.md
    envelope) -- so user-message compression was inert. Reads now go through
    the shared envelope-aware helper; `_set_text` keeps `user_message` in its
    key list for the same reason.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, dict):
        tool_result = content.get("tool_result")
        if isinstance(tool_result, str):
            return tool_result
        from usr.plugins.caveman.helpers import text_extract

        text = text_extract.text_from_content(content)
        return text or None
    return None


def _set_text(content: Any, new_text: str) -> Any:
    """Replace text in a MessageContent value, preserving structure."""
    if isinstance(content, str):
        return new_text
    if isinstance(content, dict):
        for key in ("tool_result", "user_message", "content", "message", "text"):
            if key in content and isinstance(content[key], str):
                content[key] = new_text
                return content
    return content


def _is_system_prompt(msg: Any) -> bool:
    """True when a history entry carries system-prompt content.

    Agent Zero builds the system prompt into `loop_data.system` and does not
    store it as a history Message, so today this is structurally always False
    and the `never_compress_system_prompts` guarantee holds for a structural
    reason rather than an enforced one. This predicate makes the guarantee
    explicit and testable: if a future framework version (or a plugin that
    writes one) does put system content into history, the walk below skips it
    instead of compressing it, and the setting is read for real.
    """
    metadata = getattr(msg, "metadata", None)
    if isinstance(metadata, dict):
        if str(metadata.get("role", "")).lower() == "system":
            return True
        if metadata.get("is_system_prompt") or metadata.get("system_prompt"):
            return True
    # Some producers mark it in the content envelope rather than metadata.
    content = getattr(msg, "content", None)
    if isinstance(content, dict):
        if str(content.get("role", "")).lower() == "system":
            return True
    return False


class CompressHistory(Extension):
    """Compress large history entries before the main LLM call."""

    async def execute(self, loop_data: dict | None = None, **kwargs) -> None:
        if not self.agent:
            return

        cfg = _config.get_config(agent=self.agent)
        if not cfg.get("enabled", False):
            return
        if not cfg.get("auto_compress_history", False):
            return

        min_tokens = int(cfg.get("auto_compress_history_min_tokens", 4000) or 0)
        if min_tokens <= 0:
            return

        # Auto-clarity: consume the destructive-command skip flag ONCE per
        # turn, here, so it protects the whole history walk. If we let each
        # per-message compress_text() call consume it instead, only the first
        # large message would be protected and the rest would compress anyway.
        # (compress_text also consults the flag for single-shot calls; after
        # this consume it finds nothing, which is the desired outcome.)
        try:
            ctx_id = str(getattr(getattr(self.agent, "context", None), "id", "") or "")
            if ctx_id:
                from usr.plugins.caveman.helpers.headroom import clarity as _clarity_store

                skip_label = _clarity_store.consume_skip(ctx_id)
                if skip_label:
                    if cfg.get("verbose", True):
                        _print(f"history compression skipped (auto_clarity:{skip_label})")
                    return
        except Exception:
            pass

        history = getattr(self.agent, "history", None)
        if history is None:
            return

        # v0.4.3 fix: History has no `.messages` attribute (that lives on
        # Topic) -- this read None every turn and the whole history-walk
        # compression feature was dead. Use History.all_messages(); the
        # Message objects it returns are the live history entries, so the
        # in-place msg.content rewrite below still sticks.
        messages = history.all_messages()
        if not messages:
            return

        total_compressed = 0
        total_saved = 0

        for idx, msg in enumerate(messages):
            if idx == 0:
                continue
            content = getattr(msg, "content", None)
            is_tool_result = isinstance(content, dict) and isinstance(
                content.get("tool_result"), str
            )
            if getattr(msg, "ai", False) is False and idx < 2 and not is_tool_result:
                continue
            if getattr(msg, "summary", False):
                continue
            # `never_compress_system_prompts` (K2, audit 2026-09-27). Reads as
            # True today only because system prompts never enter history; the
            # explicit check means the guarantee survives a framework change
            # instead of resting on an assumption.
            if cfg.get("never_compress_system_prompts", True) and _is_system_prompt(msg):
                continue

            if content is None:
                continue

            text = _extract_text(content)
            if not text:
                continue

            cheap_tokens = compressor._cheap_token_count(text)
            if cheap_tokens < min_tokens:
                continue

            if isinstance(content, dict) and isinstance(content.get("tool_result"), str):
                source = f"tool:{content.get('tool_name') or 'unknown'}"
            else:
                source = f"history:msg[{idx}]"
            result = compressor.compress_text(
                text,
                agent=self.agent,
                source=source,
            )

            if result.get("compressed") and result.get("saved_tokens", 0) > 0:
                new_text = result["text"]
                if result.get("ccr_key"):
                    ccr_key = result["ccr_key"]
                    new_text = (
                        f"[compressed by headroom - original saved as CCR key {ccr_key}; "
                        f"call headroom_retrieve with action=get key={ccr_key} to restore]\n"
                        f"{new_text}"
                    )
                msg.content = _set_text(content, new_text)
                # Message token counts are cached when history is built; keep
                # them aligned with the rewritten content for context budgets.
                try:
                    msg.tokens = msg.calculate_tokens()
                except Exception:
                    pass
                total_compressed += 1
                total_saved += result.get("saved_tokens", 0)

        if total_compressed > 0 and cfg.get("verbose", True):
            _print(
                f"history compression: {total_compressed} messages compressed, "
                f"{total_saved} tokens saved"
            )
