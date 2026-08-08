"""
Caveman plugin - lifecycle hooks.

Runs inside the Agent Zero framework runtime (not the agent execution env).
The plugin installer calls install() after placement, the updater calls
pre_update() before pulling new code, and uninstall() runs before the
plugin directory is deleted.

This plugin is mostly self-contained:
 - no pip dependencies
 - no external services
 - no symlinks
 - one small JSON file under the user's workdir for per-chat level state
   (created lazily on first use by extensions/python/...; not here).

So install / pre_update / uninstall are mostly logging only.
"""

import logging

log = logging.getLogger(__name__)

PLUGIN_NAME = "caveman"
PLUGIN_VERSION = "0.1.0"


def install() -> None:
    """Called after the plugin is placed in usr/plugins/."""
    log.info("[%s] install() called (v%s) - prompt-injection only, no setup required",
             PLUGIN_NAME, PLUGIN_VERSION)


def pre_update() -> None:
    """Called immediately before the updater pulls new plugin code."""
    log.info("[%s] pre_update() called (v%s) - no state to migrate",
             PLUGIN_NAME, PLUGIN_VERSION)


def uninstall() -> None:
    """Called before the plugin directory is deleted.

    The per-chat state JSON file lives at <workdir>/.caveman/state.json and is
    created lazily. We do NOT delete it on uninstall so users who reinstall the
    plugin keep their per-chat level preferences.
    """
    log.info("[%s] uninstall() called (v%s) - leaving .caveman state intact",
             PLUGIN_NAME, PLUGIN_VERSION)
