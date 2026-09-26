"""
Caveman plugin - welcome-screen discovery banner.

Adds a dismissible feature card to the welcome screen so users learn about
the plugin on their first session after enabling it.

Contract (v2.5): `Extension` subclass with async `execute(banners, **kwargs)`.
Banner `cta_action` uses the framework-dispatched prefix `open-modal:<path>`.
"""

from helpers.extension import Extension

from usr.plugins.caveman.helpers import plugins_config as plugin_cfg

PLUGIN_NAME = "caveman"
CARD_ID = "caveman_discovery_v1"


class CavemanDiscovery(Extension):
    async def execute(self, banners=None, frontend_context=None, **kwargs):
        if banners is None:
            banners = []
        if not plugin_cfg.get_bool("enabled"):
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
