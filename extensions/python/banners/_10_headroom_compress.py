"""Advertise Headroom setup only when the combined feature is enabled."""

from __future__ import annotations

import importlib.util
from typing import Any

from helpers.extension import Extension
from usr.plugins.caveman.helpers.headroom import config as _config


class HeadroomBanner(Extension):
    async def execute(
        self,
        banners: list | None = None,
        frontend_context: dict | None = None,
        **kwargs: Any,
    ) -> None:
        if not isinstance(banners, list):
            return
        try:
            if not _config.is_enabled(agent=self.agent):
                return
            if importlib.util.find_spec("headroom") is not None:
                return
        except Exception:
            # A discovery banner must never affect framework startup.
            return

        banners.append(
            {
                "id": "caveman_headroom_setup_hint",
                "type": "warning",
                "priority": 50,
                "title": "Caveman + Headroom adapter unavailable",
                "description": (
                    "Headroom compression is enabled, but headroom-ai is not "
                    "installed in the framework runtime. Safe local mode remains "
                    "available; install the pinned adapter to use normal mode."
                ),
                "dismissible": True,
                "cta_text": "Open compression settings",
                "cta_action": "open-modal:/usr/plugins/caveman/webui/headroom-config.html",
            }
        )
