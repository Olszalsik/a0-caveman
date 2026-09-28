"""compress_text: on-demand headroom-ai compression tool for the agent.

The agent can call this tool on any string it has just produced or received that
looks like it could be much shorter. Useful for:
  * Large log dumps from a code_execution call
  * JSON API responses with tons of repeated fields
  * Long stack traces
  * Source code pastes

The compressor is a no-op when the plugin is toggled OFF, so this tool is safe
to register always and costs nothing extra when disabled.

The compress action passes force=True to the compressor when the plugin is
enabled: an explicit on-demand call should bypass the min_tokens gate and
must not consume the one-shot auto-clarity flag that belongs to the automatic
extension path. force deliberately does NOT bypass the enabled gate itself -
with the plugin toggled off this tool stays a documented no-op.

Actions:
  - compress       : compress a single string, return the compressed version
  - estimate       : return the cheap token estimate without compressing
  - toggle_info    : return the current enable / scope / config state
"""

from __future__ import annotations

import json

from helpers.tool import Tool, Response
from usr.plugins.caveman.helpers.headroom import compressor
from usr.plugins.caveman.helpers.headroom import config as _config


class CompressText(Tool):
    async def execute(self, **kwargs) -> Response:
        action = (self.args.get("action") or "compress").strip().lower()

        # `expose_compress_tool` (K1, audit 2026-09-27) was a checkbox in the
        # settings UI that no backend read, so toggling it did nothing.
        # Agent Zero registers plugin tools by directory convention and offers
        # no runtime hook to withdraw one, so the tool cannot be unregistered.
        # The honest implementation is to gate the capability: when the setting
        # is off, the tool refuses and says why, instead of silently working
        # while the UI claims it is disabled.
        #
        # `toggle_info` and `estimate` stay available in that state, because
        # they are read-only diagnostics and refusing them would only make the
        # setting harder to debug.
        # Fetch the merged config once for both the expose gate and compress.
        _cfg = {}
        try:
            _cfg = _config.get_config(agent=self.agent)
        except Exception:  # noqa: BLE001
            _cfg = {}
        if action not in ("toggle_info", "estimate"):
            if not bool(_cfg.get("expose_compress_tool", True)):
                return Response(
                    message=(
                        "compress_text is disabled. Enable 'Expose compress_text "
                        "tool' in Settings -> Headroom to use on-demand "
                        "compression."
                    ),
                    break_loop=False,
                )

        if action == "toggle_info":
            cfg = _cfg
            enabled_flag = bool(cfg.get("enabled", False))
            try:
                from helpers.plugins import get_toggle_state  # type: ignore

                # get_toggle_state returns a plain string ("enabled" /
                # "disabled" / "always_enabled"), not a str-enum - it has no
                # .value attribute, so getattr(..., "value", ...) always
                # yielded "" and the toggle showed as "unknown".
                state = str(get_toggle_state("caveman") or "")
                toggle_state = {
                    "enabled": "on",
                    "always_enabled": "on",
                    "disabled": "off",
                }.get(state, "unknown")
            except Exception:  # noqa: BLE001
                toggle_state = "unknown"
            data = {
                "plugin_toggle": toggle_state,
                "config_enabled": enabled_flag,
                "effective": enabled_flag and toggle_state != "off",
                "strategy": cfg.get("strategy"),
                "level": cfg.get("level"),
                "model_hint": cfg.get("model_hint"),
                "ccr_enabled": cfg.get("ccr_enabled"),
                "stats_enabled": cfg.get("stats_enabled"),
            }
            return Response(
                message="Current Headroom plugin state:\n\n```json\n"
                + json.dumps(data, indent=2)
                + "\n```",
                break_loop=False,
            )

        if action == "estimate":
            text = self.args.get("text") or ""
            est = compressor._cheap_token_count(text)  # type: ignore[attr-defined]
            data = {"text_length": len(text), "estimated_tokens": est}
            return Response(
                message=f"Estimated tokens: {est} (text length {len(text)}).\n\n```json\n"
                + json.dumps(data, indent=2)
                + "\n```",
                break_loop=False,
            )

        if action == "compress":
            text = self.args.get("text") or ""
            if not isinstance(text, str) or not text:
                return Response(
                    message="compress_text: 'text' argument is required and must be a non-empty string.",
                    break_loop=False,
                )
            level = self.args.get("level")
            model_hint = self.args.get("model_hint")
            source = self.args.get("source") or "compress_text.tool"
            # Explicit on-demand intent: force=True (see module docstring).
            # Gated on the enabled flag so force never revives the compressor
            # while the plugin is toggled off.
            force = bool(_cfg.get("enabled", False))

            result = compressor.compress_text(
                text,
                agent=self.agent,
                level=level,
                model_hint=model_hint,
                source=source,
                force=force,
            )
            return Response(
                message=f"Headroom compress_text result:\n\n```json\n"
                + json.dumps(result, indent=2, default=str)
                + "\n```\n\nCompressed text:\n\n"
                + result["text"],
                break_loop=False,
            )

        return Response(
            message=(
                "Unknown action. Valid actions: "
                "compress, estimate, toggle_info."
            ),
            break_loop=False,
        )
