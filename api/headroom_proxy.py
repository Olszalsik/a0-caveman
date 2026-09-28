"""headroom_proxy: start/stop/status the local headroom proxy subprocess.

POST /api/plugins/caveman/headroom_proxy

Body: {"action": "start|stop|restart|status"}

The proxy is a separate process (headroom proxy --port 8787) that sits
between Agent Zero and the LLM API, transparently compressing every
request. When running, the user can re-point their model provider's
api_base to http://127.0.0.1:8787 in the Model settings to enable it.

IMPORTANT: this file must contain only ONE ApiHandler subclass. The route
slug is the filename (headroom_proxy), and the framework only registers
classes[0] per file (see /a0/helpers/api.py line 238).
"""

from __future__ import annotations

import asyncio
from typing import Any

from helpers.api import ApiHandler, Input, Output, Request, Response

from usr.plugins.caveman.helpers.headroom.proxy_manager import ProxyManager
from usr.plugins.caveman.helpers.headroom import config as _config


class HeadroomProxy(ApiHandler):
    @classmethod
    def requires_auth(cls):
        return True

    async def process(self, input: Input, request: Request) -> Output:
        action = ((input or {}).get("action") or "status").strip().lower()

        try:
            cfg = _config.get_config(agent=None)
            pm = ProxyManager(cfg)
        except Exception as exc:  # noqa: BLE001
            return Response({"ok": False, "error": str(exc)}, 500)

        # ProxyManager is synchronous and slow by design: start() polls the
        # port for up to 10s, stop() waits up to 5s before SIGKILL, and even
        # status() probes the port with a 1s timeout. Run it off the event
        # loop - blocking here would stall every concurrent request (and any
        # running agent turn) for that whole time.
        if action == "status":
            return {"ok": True, **(await asyncio.to_thread(pm.status))}

        if action == "start":
            result = await asyncio.to_thread(pm.start)
            return Response(result, 200 if result.get("ok") else 500)

        if action == "stop":
            result = await asyncio.to_thread(pm.stop)
            return Response(result, 200 if result.get("ok") else 500)

        if action == "restart":
            result = await asyncio.to_thread(pm.restart)
            return Response(result, 200 if result.get("ok") else 500)

        return Response(
            {"ok": False, "error": f"unknown action '{action}'. valid: start, stop, restart, status"},
            400,
        )
