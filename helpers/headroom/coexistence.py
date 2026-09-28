"""Detect the standalone `headroom_compress` plugin (roadmap P0.4).

Why this exists
---------------
This plugin now contains Headroom's capabilities. If the standalone
`usr/plugins/headroom_compress` plugin is installed *and* enabled at the same
time, both register overlapping hooks on the same extension points:

    hist_add_before             -> compress user message
    hist_add_tool_result        -> compress tool output
    message_loop_prompts_before -> auto-clarity, clear old results, history

Running both means every eligible message is transformed twice, two CCR
databases hold separate copies of the same originals, two dashboards report
different numbers, and the user has two switches that both claim to control
the same thing. None of that is a crash, which is what makes it dangerous: it
looks like it works.

Contract
--------
* **Read-only.** This module never toggles, writes, disables or otherwise
  touches the other plugin. The framework exposes no supported way for one
  plugin to change another's toggle, and silently editing another plugin's
  state is what the roadmap forbids. Detection produces a report; the user
  acts on it.
* **Fails open to "unknown".** If `helpers.plugins` cannot be imported or
  raises, `present` stays False and `error` is filled. A missing framework
  module must never break a Caveman turn.
* **Presence is not a conflict.** `overlaps` is true only when the standalone
  is present, enabled, *and* this plugin's own compression is enabled - i.e.
  when two active transformers would really run.

Rollback (roadmap P0.5)
-----------------------
Disable this plugin, re-enable `headroom_compress` in the Plugins UI, and
nothing else is needed: the standalone keeps its own config, CCR database and
stats, because this plugin never wrote into any of them.
"""

from __future__ import annotations

from typing import Any

from usr.plugins.caveman.helpers.headroom import config as _config

# Shared with extensions/python/banners/_20_headroom_coexistence.py.
BANNER_ID = "caveman_headroom_standalone_overlap"


def _plugins_helper():
    """Import helpers.plugins lazily; not importable in every context."""
    from helpers import plugins as plugins_helper

    return plugins_helper


def standalone_state() -> dict[str, Any]:
    """Report on the standalone plugin without modifying anything."""
    report: dict[str, Any] = {
        "plugin": _config.STANDALONE_PLUGIN_NAME,
        "present": False,
        "toggle": "unknown",
        "enabled": False,
        "path": "",
        "error": "",
    }
    try:
        plugins_helper = _plugins_helper()
        plugin_dir = plugins_helper.find_plugin_dir(_config.STANDALONE_PLUGIN_NAME)
    except Exception as exc:  # noqa: BLE001
        report["error"] = str(exc)
        return report

    if not plugin_dir:
        return report

    report["present"] = True
    report["path"] = str(plugin_dir)
    try:
        state = str(plugins_helper.get_toggle_state(_config.STANDALONE_PLUGIN_NAME))
    except Exception as exc:  # noqa: BLE001
        report["error"] = str(exc)
        return report

    # ToggleState is a str enum: "enabled" / "disabled" / "always_enabled".
    report["toggle"] = state
    report["enabled"] = state != "disabled"
    return report


def overlap(agent: Any = None) -> dict[str, Any]:
    """Combined report: would anything actually run twice?"""
    standalone = standalone_state()
    try:
        combined_on = _config.is_enabled(agent=agent)
    except Exception as exc:  # noqa: BLE001
        return {
            **standalone,
            "combined_enabled": False,
            "overlaps": False,
            "guidance": (
                "Caveman + Headroom could not read its own compression setting. "
                "Check the plugin config; the standalone plugin was left alone."
            ),
        }

    overlaps = bool(standalone["present"] and standalone["enabled"] and combined_on)
    guidance = ""
    if overlaps:
        guidance = (
            "The standalone headroom_compress plugin is installed and enabled, "
            "and Caveman + Headroom compression is also on. Both transform the "
            "same messages, keep separate CCR copies, and report separate "
            "numbers. Turn the standalone plugin off in the Plugins UI, or turn "
            "this plugin's compression off - this plugin will not change the "
            "other plugin's state for you."
        )
    elif standalone["present"] and standalone["enabled"]:
        guidance = (
            "The standalone headroom_compress plugin is enabled. That is fine: "
            "Caveman + Headroom compression is off, so only one transformer "
            "runs."
        )

    return {
        **standalone,
        "combined_enabled": combined_on,
        "overlaps": overlaps,
        "guidance": guidance,
    }
