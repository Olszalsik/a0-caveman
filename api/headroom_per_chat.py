"""headroom_per_chat: get/set/clear per-chat compression overrides.

POST /api/plugins/caveman/headroom_per_chat
Body: {"action": "get|set|clear|list", "context_id": "...", "enabled": bool, "note": "..."}
"""

from __future__ import annotations

import asyncio
from typing import Any

from helpers.api import ApiHandler, Input, Output, Request, Response

from usr.plugins.caveman.helpers.headroom import per_chat as _per_chat


class HeadroomPerChat(ApiHandler):
    @classmethod
    def requires_auth(cls):
        return True

    async def process(self, input: Input, request: Request) -> Output:
        try:
            data = input or {}
            action = str(data.get("action") or "list").strip().lower()

            if action == "list":
                return {"ok": True, "overrides": _per_chat.list_all()}

            if action == "get":
                context_id = str(data.get("context_id") or "")
                return {"ok": True, "override": _per_chat.get_override(context_id)}

            if action == "set":
                context_id = str(data.get("context_id") or "")
                if not context_id:
                    return Response({"ok": False, "error": "context_id required"}, 400)
                # set_enabled/clear acquire the shared _FileLock (up to a 2s
                # blocking wait) - keep that off the event loop.
                entry = await asyncio.to_thread(
                    _per_chat.set_enabled,
                    context_id,
                    enabled=bool(data.get("enabled", True)),
                    note=str(data.get("note") or ""),
                )
                return {"ok": True, "entry": entry}

            if action == "clear":
                context_id = str(data.get("context_id") or "")
                if not context_id:
                    return Response({"ok": False, "error": "context_id required"}, 400)
                cleared = await asyncio.to_thread(_per_chat.clear, context_id)
                return {"ok": True, "cleared": cleared}

            return Response({"ok": False, "error": "unknown action"}, 400)
        except Exception as exc:
            return Response({"ok": False, "error": str(exc)}, 500)
