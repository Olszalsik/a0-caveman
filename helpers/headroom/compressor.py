"""Headroom compression library mode.

This module is the public entry point for Headroom Context Compression.
It wraps the optional headroom-ai package when available, falls back to a
safe deterministic local transformer when it is not, and exposes a single
`compress_text` function used by all other plugin code (tools, extensions,
API handlers, on-demand tools).

v0.4.0 additions:
- Per-chat override (helpers/per_chat.py) checked before any work.
- Auto-clarity destructive-command skip (extensions/python/message_loop_prompts_before/_05_auto_clarity.py)
  consumed before compression.
"""

from __future__ import annotations

import hashlib
import re
import sys
import time
import ast
from typing import Any

from usr.plugins.caveman.helpers.headroom import config as _config
from usr.plugins.caveman.helpers.headroom.ccr_cache import CcrCache
from usr.plugins.caveman.helpers.headroom.stats import StatsRecorder

PLUGIN_NAME = _config.PLUGIN_NAME

_HEADROOM_MODULE: Any = None
_HEADROOM_IMPORT_ATTEMPTED = False
_HEADROOM_IMPORT_ERROR: str | None = None

_per_chat = None
_clarity = None


def _get_per_chat():
    global _per_chat
    if _per_chat is None:
        try:
            from usr.plugins.caveman.helpers.headroom import per_chat as _mod
            _per_chat = _mod
        except Exception as exc:
            _print(f"per-chat module unavailable: {exc}")
            _per_chat = False
    return _per_chat if _per_chat else None


def _get_clarity():
    """Return the shared auto-clarity flag store (helpers/clarity.py).

    IMPORTANT: the flag store must live in a *helper* module, not in the
    extension file. A0 loads extension files as synthetic modules (basename,
    not in sys.modules), so importing the extension file via the package path
    would create a second module instance with separate state and the skip
    flag would never reach the compressor. helpers.clarity is imported via the
    canonical usr.plugins package path by both the extension and us, so both
    sides share one store.
    """
    global _clarity
    if _clarity is None:
        try:
            from usr.plugins.caveman.helpers.headroom import clarity as _mod
            _clarity = _mod
        except Exception as exc:
            _print(f"auto-clarity module unavailable: {exc}")
            _clarity = False
    return _clarity if _clarity else None


def _resolve_context_id(agent):
    try:
        if agent is not None and getattr(agent, "context", None) is not None:
            return str(getattr(agent.context, "id", "") or "")
    except Exception:
        pass
    return ""


def _print(msg: str) -> None:
    sys.stderr.write(f"[{PLUGIN_NAME}] {msg}\n")
    sys.stderr.flush()


def _close_cache(cache: Any) -> None:
    close = getattr(cache, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            pass


def _cheap_token_count(text: str) -> int:
    """Rough token estimate without importing tiktoken."""
    if not isinstance(text, str) or not text:
        return 0
    return max(1, len(text) // 4)


def _ccr_key_for(text: str, source: str | None) -> str:
    h = hashlib.sha256()
    h.update((source or "").encode("utf-8"))
    h.update(text.encode("utf-8", errors="replace"))
    return h.hexdigest()[:32]


def _load_headroom() -> Any:
    """Lazy, cached import of the headroom-ai package."""
    global _HEADROOM_MODULE, _HEADROOM_IMPORT_ATTEMPTED, _HEADROOM_IMPORT_ERROR
    if _HEADROOM_IMPORT_ATTEMPTED:
        return _HEADROOM_MODULE
    _HEADROOM_IMPORT_ATTEMPTED = True
    try:
        import headroom
        _HEADROOM_MODULE = headroom
    except Exception as exc:
        _HEADROOM_IMPORT_ERROR = str(exc)
        _HEADROOM_MODULE = None
    return _HEADROOM_MODULE


def reset_headroom_cache() -> None:
    """Allow a successful in-process plugin install to refresh capability state."""
    global _HEADROOM_MODULE, _HEADROOM_IMPORT_ATTEMPTED, _HEADROOM_IMPORT_ERROR
    _HEADROOM_MODULE = None
    _HEADROOM_IMPORT_ATTEMPTED = False
    _HEADROOM_IMPORT_ERROR = None


def headroom_status() -> dict[str, Any]:
    """Describe the optional adapter dependency without importing it elsewhere."""
    module = _load_headroom()
    version = getattr(module, "__version__", None) if module is not None else None
    if module is not None and not version:
        try:
            from importlib.metadata import version as package_version
            version = package_version("headroom-ai")
        except Exception:
            version = "unknown"
    return {
        "available": module is not None,
        "version": version,
        "error": _HEADROOM_IMPORT_ERROR if module is None else None,
        "wired_strategies": ["auto", "smart_crusher"],
    }


def _safe_transform(text: str) -> str:
    """Deterministic local compression that does not need headroom-ai."""
    import re
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    lines = [ln.rstrip() for ln in text.split("\n")]
    return "\n".join(lines).strip()


# v0.4.5 (P2 coding-profile protect_reads): tool sources whose output is
# *file content* the agent reasons from. Structural (lossy-capable)
# compression is skipped for these - lossless whitespace collapse only.
_PROTECTED_SOURCE_RE = re.compile(
    r"read|editor|file|cat|view|content", re.IGNORECASE
)
_SOURCE_CODE_HINT_RE = re.compile(
    r"(?m)^\s*(?:async\s+def|def|class|from\s+\S+\s+import|import\s+\S+|"
    r"function\s+\w+|(?:const|let|var|func|fn)\s+\w+|"
    r"export\s+(?:default\s+)?(?:function|class|const|let)|"
    r"#include\s*[<\"]|(?:public|private|protected)\s+(?:static\s+)?class)\b"
)


def _looks_like_source_code(text: str) -> bool:
    """Conservatively protect syntactically valid source from lossy routing.

    Headroom 0.38's pure-Python detector may label short or repetitive code as
    plain text. Only parse code-shaped inputs so ordinary tool output avoids
    the cost of Python parsing on every compression call.
    """
    if not isinstance(text, str) or not _SOURCE_CODE_HINT_RE.search(text):
        return False
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError, TypeError):
        # Still protect recognizable non-Python constructs.
        return bool(_SOURCE_CODE_HINT_RE.search(text))
    return bool(tree.body)


def _normal_transform(text: str, *, level: str, model: str, strategy: str, source: str | None, stats: Any) -> tuple[str | None, str | None]:
    """Compress a single text via headroom-ai's per-content transforms.

    headroom-ai >= 0.28 replaced the old string-level ``compress(text, level=…)``
    API with a message-list ``compress(messages, model, config)`` that is a
    context-window *fit* engine (no-ops until the context approaches the model
    limit) and a per-content transforms suite.  The old string call raises with
    today's package, so the plugin silently fell back to whitespace collapse.

    This adapter targets the v0.28 layer that matches this plugin's per-message
    hooks: ``ContentRouter`` (quality-first: logs/diffs/search results get
    structural compression, traceback/code pass through untouched; measured
    95% on repetitive logs, 0% on code) and ``SmartCrusher`` for tabular
    arrays.  The ``level`` knob is advisory only from 0.28 on — the router
    self-selects strategy per content type.
    """
    # The current adapter only wires the content router and SmartCrusher.
    # Do not silently route explicitly selected, currently unsupported modes
    # through the auto router; report a no-op instead.
    if strategy not in {"auto", "smart_crusher"}:
        return None, f"unsupported strategy: {strategy}"
    try:
        if strategy == "smart_crusher":
            from headroom.transforms.smart_crusher import smart_crush_tool_output

            crushed, was_modified, _info = smart_crush_tool_output(text)
            if was_modified and crushed:
                return crushed, None
        else:
            from headroom.transforms.content_router import route_and_compress

            routed = route_and_compress(text, context=source or "")
            if routed and len(routed) < len(text):
                return routed, None
    except Exception as exc:  # noqa: BLE001 - never break the agent on compression
        return _safe_transform(text), str(exc)
    # Router declined to compress (content type protected, or no net gain) —
    # return the original so compress_text records an honest 0-saved event.
    return text, None


def compress_text(
    text: str,
    agent: Any = None,
    *,
    level: str | None = None,
    model_hint: str | None = None,
    source: str | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """Compress a single string. Safe to call from anywhere.

    With ``dry_run`` enabled in config the transform is computed for the
    economics but the input text is returned unchanged and nothing is
    persisted (no CCR write); the result carries ``skipped_reason="dry_run"``
    plus ``would_be_text``/``would_save_tokens``.
    """
    cfg = _config.get_config(agent=agent)
    dry_run = bool(cfg.get("dry_run", False))

    empty_result = {
        "text": text,
        "compressed": False,
        "original_tokens": _cheap_token_count(text or ""),
        "output_tokens": _cheap_token_count(text or ""),
        "saved_tokens": 0,
        "ratio": 1.0,
        "ccr_key": None,
        "skipped_reason": None,
        "mode": cfg.get("mode", "safe"),
        "dry_run": dry_run,
    }

    def skipped(reason: str) -> dict[str, Any]:
        try:
            recorder = StatsRecorder(cfg)
            recorder.record(
                kind="compress_skip",
                source=source or "unknown",
                input_tokens=empty_result["original_tokens"],
                output_tokens=empty_result["output_tokens"],
                details={"skipped_reason": reason},
            )
            recorder.close()
        except Exception:
            pass
        return {**empty_result, "skipped_reason": reason}

    if not (cfg.get("enabled", False) or force):
        return skipped("disabled")
    if not text or not isinstance(text, str):
        return skipped("empty")

    context_id = _resolve_context_id(agent)
    if context_id:
        pc = _get_per_chat()
        if pc is not None and not pc.is_enabled(context_id):
            return skipped("per_chat_disabled")
        # Auto-clarity skip: never consulted for explicit (force=True) tool
        # calls - the flag is meant to protect *automatic* compression only.
        if not force:
            ac = _get_clarity()
            auto_clarity_label = None
            if ac is not None:
                try:
                    auto_clarity_label = ac.consume_skip(context_id)
                except Exception:
                    auto_clarity_label = None
            if auto_clarity_label:
                return skipped(f"auto_clarity:{auto_clarity_label}")

    if not force:
        # Finding 7 (remediation 2026-09-28): this used to read
        # `auto_compress_tool_outputs_min_tokens` for EVERY source. That key
        # defaults to 0, so the tool-output feature being off silently killed
        # history and user-message compression too (verified: with tool_min=0
        # a 20k-token history message skipped with reason "min_tokens=0").
        # The tool threshold is a tool-output policy and now applies to
        # tool: sources only; the history/user hooks apply their own
        # `auto_compress_history_min_tokens` before calling in.
        if source and source.startswith("tool:"):
            min_tokens = int(cfg.get("auto_compress_tool_outputs_min_tokens", 0) or 0)
            if min_tokens <= 0:
                return skipped("min_tokens=0")
            if _cheap_token_count(text) < min_tokens:
                return skipped("below_threshold")
        elif not source:
            # Direct API calls with no source have no caller-side threshold:
            # fall back to the tool threshold rather than compress anything.
            min_tokens = int(cfg.get("auto_compress_tool_outputs_min_tokens", 0) or 0)
            if min_tokens <= 0 or _cheap_token_count(text) < min_tokens:
                return skipped("below_threshold")

    mode = (cfg.get("mode") or "safe").lower()
    if mode not in {"safe", "normal"}:
        mode = "safe"

    use_level = level or cfg.get("level") or "balanced"
    use_model = model_hint or cfg.get("model_hint") or "gpt-4o"
    use_strategy = (cfg.get("strategy") or "auto").lower()

    stats = StatsRecorder(cfg)
    ccr = CcrCache(cfg) if cfg.get("ccr_enabled", True) else None

    original_tokens = _cheap_token_count(text)
    candidate_ccr_key = _ccr_key_for(text, source) if ccr is not None else None
    ccr_key = None

    started = time.perf_counter()
    compressed: str | None = None
    err: str | None = None

    if cfg.get("protect_reads", True) and source and _PROTECTED_SOURCE_RE.search(source):
        # File reads are source-of-truth data. Preserve them byte-for-byte;
        # whitespace folding or strip() can change indentation and boundaries.
        compressed = text
    elif cfg.get("protect_code", True) and _looks_like_source_code(text):
        # The upstream content detector can misclassify repetitive source as
        # plain text; keep executable syntax intact until task-quality
        # measurements justify an explicitly configurable lossy code path.
        compressed = text
    elif mode == "safe":
        compressed = _safe_transform(text)
    else:
        compressed, err = _normal_transform(
            text,
            level=use_level,
            model=use_model,
            strategy=use_strategy,
            source=source,
            stats=stats,
        )

    if err and compressed is None:
        if ccr is not None:
            _close_cache(ccr)
        stats.close()
        return {**empty_result, "skipped_reason": "unsupported_strategy", "error": err}
    if not compressed:
        if ccr is not None:
            _close_cache(ccr)
        stats.close()
        return {**empty_result, "skipped_reason": "transform_failed", "error": err}

    output_tokens = _cheap_token_count(compressed)
    saved_tokens = max(0, original_tokens - output_tokens)
    ratio = (output_tokens / original_tokens) if original_tokens else 1.0

    if dry_run and compressed != text:
        # Finding 8 (remediation 2026-09-28): `dry_run` was read into every
        # result dict and reported by the tools, but never enforced -- the
        # plugin compressed history for real (and wrote CCR entries) even
        # with dry_run: true. Enforce it here: report the economics, return
        # the original text untouched, persist nothing.
        try:
            stats.record(
                kind="compress_dry_run",
                source=source or "unknown",
                input_tokens=original_tokens,
                output_tokens=output_tokens,
                duration_ms=int((time.perf_counter() - started) * 1000),
                details={"would_save_tokens": saved_tokens, "ratio": ratio},
            )
        except Exception:
            pass
        stats.close()
        if ccr is not None:
            _close_cache(ccr)
        return {
            "text": text,
            "compressed": False,
            "original_tokens": original_tokens,
            "output_tokens": output_tokens,
            "saved_tokens": 0,
            "ratio": ratio,
            "ccr_key": None,
            "skipped_reason": "dry_run",
            "mode": mode,
            "dry_run": True,
            "would_be_text": compressed,
            "would_save_tokens": saved_tokens,
        }

    if ccr is not None and candidate_ccr_key is not None and saved_tokens > 0:
        try:
            stored = ccr.put(
                candidate_ccr_key,
                text,
                original_tokens,
                output_tokens,
                source=source,
            )
        except Exception as exc:
            _print(f"CCR write failed for {candidate_ccr_key[:8]}...: {exc}")
            stored = False
        storage_backend = (cfg.get("ccr_backend") or "sqlite").lower()
        storage_is_usable = bool(
            stored
            and (
                (ccr.backend == "sqlite" and ccr._db is not None)
                or (ccr.backend == "in_memory" and storage_backend == "in_memory")
            )
        )
        if storage_is_usable:
            ccr_key = candidate_ccr_key
        else:
            # CCR is enabled as the recovery contract. If persistence fails,
            # do not return a lossy result whose original cannot be restored.
            compressed = text
            output_tokens = original_tokens
            saved_tokens = 0
            ratio = 1.0
            ccr_key = None

    try:
        repeated_tool_output = stats.observe_tool_output(source, text)
        stats.record(
            kind="compress_text",
            source=source or "unknown",
            input_tokens=original_tokens,
            output_tokens=output_tokens,
            duration_ms=int((time.perf_counter() - started) * 1000),
            details={
                "compressed": compressed != text,
                "skipped_reason": None if compressed != text else ("no_change" if not err else "transform_error"),
                "ccr_write": "stored" if ccr_key else ("not_needed" if saved_tokens <= 0 else "failed_or_unavailable"),
            },
            repeat_output=repeated_tool_output,
        )
    except Exception:
        pass
    finally:
        stats.close()
        if ccr is not None:
            _close_cache(ccr)

    return {
        "text": compressed,
        "compressed": compressed != text,
        "original_tokens": original_tokens,
        "output_tokens": output_tokens,
        "saved_tokens": saved_tokens,
        "ratio": ratio,
        "ccr_key": ccr_key,
        "skipped_reason": None,
        "mode": mode,
        "dry_run": dry_run,
    }


def retrieve_original(ccr_key: str, agent: Any = None) -> str | None:
    """Return the full original text stored in the CCR cache for `ccr_key`.

    Used by the headroom_retrieve tool (action=get). Returns None when the
    key is unknown, expired, or CCR is disabled - never raises.
    """
    if not ccr_key or not isinstance(ccr_key, str):
        return None
    key = ccr_key.strip()
    # Tolerate the LLM pasting the key out of the hint line, e.g.
    # "key=abc123" or "CCR key abc123".
    for prefix in ("key=", "key ", "ccr key ", "ccr_key="):
        if key.lower().startswith(prefix):
            key = key[len(prefix):].strip()
    key = key.split()[0] if key else ""
    if not key:
        return None
    try:
        cfg = _config.get_config(agent=agent)
        if not cfg.get("ccr_enabled", True):
            return None
        ccr = CcrCache(cfg)
        try:
            original, retrieve_status = ccr.get_with_status(key)
        finally:
            _close_cache(ccr)
        try:
            recorder = StatsRecorder(cfg)
            recorder.record(
                kind=f"ccr_retrieve_{retrieve_status}",
                source="ccr:retrieve",
            )
            recorder.close()
        except Exception:
            pass
        return original
    except Exception as exc:  # noqa: BLE001 - retrieval must never break the agent
        _print(f"retrieve_original failed for {key[:8]}...: {exc}")
        return None
