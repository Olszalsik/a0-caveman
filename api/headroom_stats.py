"""headroom_stats: dashboard data API for the Headroom plugin.

POST /api/plugins/caveman/headroom_stats

Returns aggregate counters from the local CCR cache and the events DB plus
recent entries. Used by the webui/dashboard.html modal. All queries are
read-only and fail-safe: if the plugin is not installed, the handler returns
an empty payload instead of a stack trace.

window_hours: rolling window for summary_window; 0 means lifetime
(summary_window == summary_lifetime). Default when absent/invalid: 24.

IMPORTANT: this file must contain only ONE ApiHandler subclass. The route slug
is the filename (headroom_stats), and the framework only registers
classes[0] per file (see /a0/helpers/api.py line 238).
"""

from __future__ import annotations

import time
from typing import Any

from helpers.api import ApiHandler, Input, Output, Request, Response
from usr.plugins.caveman.helpers.headroom.ccr_cache import CcrCache
from usr.plugins.caveman.helpers.headroom.stats import StatsRecorder
from usr.plugins.caveman.helpers.headroom import config as _config
from usr.plugins.caveman.helpers.headroom import compressor


class HeadroomStats(ApiHandler):
    @classmethod
    def requires_auth(cls):
        return True

    async def process(self, input: Input, request: Request) -> Output:
        ccr = None
        stats = None
        try:
            cfg = _config.get_config(agent=getattr(self, "agent", None))
            ccr = CcrCache(cfg)
            stats = StatsRecorder(cfg)

            try:
                limit = int((input or {}).get("limit", 25) or 25)
            except (TypeError, ValueError):
                limit = 25
            limit = max(1, min(limit, 200))
            # window_hours=0 is meaningful: it means "lifetime" (since=None).
            # Default to 24 only when the argument is absent or not a number -
            # never coerce an explicit 0 into the default (a plain `or 24`
            # would do exactly that and silently turn lifetime into 24h).
            raw_window = (input or {}).get("window_hours", 24)
            try:
                window_hours = int(raw_window)
            except (TypeError, ValueError):
                window_hours = 24
            window_hours = max(0, window_hours)

            since = time.time() - (window_hours * 3600) if window_hours else None
            ccr_stats = ccr.stats()
            stats_summary = stats.summary(since=since)
            lifetime_summary = stats.summary(since=None)
            recent_events = stats.recent(limit=limit)
            recent_ccr = ccr.list_keys(limit=limit)

            ccr_saved = max(
                0,
                int(ccr_stats.get("original_tokens", 0))
                - int(ccr_stats.get("compressed_tokens", 0)),
            )

            return {
                "ok": True,
                "plugin": "caveman",
                "config": {
                    "enabled": bool(cfg.get("enabled", False)),
                    "strategy": cfg.get("strategy"),
                    "level": cfg.get("level"),
                    "model_hint": cfg.get("model_hint"),
                    "ccr_enabled": bool(cfg.get("ccr_enabled", True)),
                    "ccr_backend": cfg.get("ccr_backend"),
                    "stats_enabled": bool(cfg.get("stats_enabled", True)),
                    "headroom": compressor.headroom_status(),
                },
                "ccr": {
                    **ccr_stats,
                    "saved_tokens": ccr_saved,
                    "recent": recent_ccr,
                },
                "events": {
                    "window_hours": window_hours,
                    "summary_window": stats_summary,
                    "summary_lifetime": lifetime_summary,
                    "recent": recent_events,
                },
            }
        except Exception as exc:  # noqa: BLE001 - never crash the dashboard
            return Response(
                {"ok": False, "error": str(exc)},
                500,
            )
        finally:
            if ccr is not None:
                ccr.close()
            if stats is not None:
                stats.close()
