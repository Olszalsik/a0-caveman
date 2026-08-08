"""
Caveman plugin - welcome-screen discovery banner.

Adds a dismissible feature card to the welcome screen so users learn about
the plugin on their first session after enabling it.

Contract (v2.5): `Extension` subclass with async `execute(banners, **kwargs)`.
Banner `cta_action` uses the framework-dispatched prefix `open-modal:<path>`.
"""

from helpers.extension import Extension
from helpers import plugins as plugins_helper

PLUGIN_NAME = "caveman"
CARD_ID = "caveman_discovery_v1"


def _is_active():
    try:
        cfg = plugins_helper.get_plugin_config(PLUGIN_NAME) or {}
    except Exception:
        cfg = {}
    if isinstance(cfg, dict) and cfg.get("enabled") is True:
        return True
    return False


class CavemanDiscovery(Extension):
    async def execute(self, banners=None, frontend_context=None, **kwargs):
        if banners is None:
            banners = []
        if not _is_active():
            return
        for existing in banners:
            if isinstance(existing, dict) and existing.get("id") == CARD_ID:
                return
        banners.append({
            "id": CARD_ID,
            "type": "feature",
            "priority": 50,
            "title": "Caveman mode is ON",
            "description": (
                "Replies are compressed for token efficiency. "
                "Adjust level, or turn off, in Settings -> Developer -> Caveman."
            ),
            "icon": "zap",
            "cta_text": "Open Settings",
            "cta_action": "open-modal:/usr/plugins/caveman/webui/config.html",
            "dismissible": True,
            "source": "backend",
        })
