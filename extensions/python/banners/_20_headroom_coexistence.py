"""Warn when the standalone headroom_compress plugin would also be active.

Roadmap P0.4. This banner only appears when all three are true:

  1. `usr/plugins/headroom_compress` is installed,
  2. it is enabled, and
  3. this plugin's own `headroom.enabled` is on.

That combination means two transformers compress the same messages, two
CCR databases hold separate copies of the same originals, and two
dashboards report different numbers - while both look healthy.

This extension reports. It never toggles, disables or edits the other
plugin: one plugin changing another plugin's state is not a supported
framework operation, and doing it silently would remove the user's
ability to understand which switch did what. The banner links to the
settings modal so the user can decide.

It also must never raise. A welcome-screen banner that aborts framework
startup over a plugin-detection problem would be a far worse outcome than
a missing warning.

IMPORTANT: this file must contain exactly ONE Extension subclass. Agent
Zero discovers extension classes by subclassing helpers.extension.Extension
and registers classes[0] per file.
"""

from __future__ import annotations

from typing import Any

from helpers.extension import Extension

from usr.plugins.caveman.helpers.headroom import coexistence as _coexistence


class HeadroomCoexistence(Extension):
    async def execute(
        self,
        banners: list | None = None,
        frontend_context: dict | None = None,
        **kwargs: Any,
    ) -> None:
        if not isinstance(banners, list):
            return
        try:
            report = _coexistence.overlap(agent=self.agent)
        except Exception:
            return

        if not report.get("overlaps"):
            return
        for existing in banners:
            if (
                isinstance(existing, dict)
                and existing.get("id") == _coexistence.BANNER_ID
            ):
                return

        banners.append(
            {
                "id": _coexistence.BANNER_ID,
                "type": "warning",
                "priority": 60,
                "title": "Two compression plugins are active",
                "description": (
                    "The standalone headroom_compress plugin is enabled and so is "
                    "Caveman + Headroom compression. Both will compress the same "
                    "messages and keep separate originals, so the numbers will "
                    "not add up. Turn the standalone plugin off in the Plugins "
                    "UI, or turn compression off here."
                ),
                "dismissible": True,
                "cta_text": "Open compression settings",
                "cta_action": (
                    "open-modal:/usr/plugins/caveman/webui/headroom-config.html"
                ),
            }
        )
