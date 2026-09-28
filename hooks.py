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

So install / pre_update / uninstall are mostly logging only - with one
exception: install() dispatches a background proxy auto-start when the
headroom plugin is enabled and proxy.auto_start is configured (fire-and-forget,
never delays or breaks plugin load).
"""

import logging
import threading
import traceback

log = logging.getLogger(__name__)

PLUGIN_NAME = "caveman"
PLUGIN_VERSION = "0.5.4"


def _auto_start_proxy() -> None:
    """Dispatch ProxyManager.auto_start_if_configured() on a daemon thread.

    ProxyManager.start() polls the port for up to 10s, so it must never run
    inline in install(). Everything here is guarded: an import or config
    failure only logs, it cannot fail plugin activation.
    """
    try:
        from usr.plugins.caveman.helpers.headroom import config as _config
        from usr.plugins.caveman.helpers.headroom import proxy_manager

        cfg = _config.get_config(agent=None)
        if not bool(cfg.get("enabled", False)):
            return
        if not bool((cfg.get("proxy") or {}).get("auto_start", False)):
            return
        # Reference via the module attribute (not a from-import) so tests -
        # or future code - can patch proxy_manager.auto_start_if_configured.
        target = proxy_manager.auto_start_if_configured
        threading.Thread(
            target=target, name="caveman-proxy-autostart", daemon=True
        ).start()
        log.info("[%s] proxy auto-start dispatched (headroom enabled + proxy.auto_start)",
                 PLUGIN_NAME)
    except Exception:  # noqa: BLE001 - never break plugin load
        log.warning("[%s] proxy auto-start skipped:\n%s", PLUGIN_NAME, traceback.format_exc())


def install() -> None:
    """Called after the plugin is placed in usr/plugins/."""
    log.info("[%s] install() called (v%s) - prompt-injection only, no setup required",
             PLUGIN_NAME, PLUGIN_VERSION)
    _auto_start_proxy()


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
