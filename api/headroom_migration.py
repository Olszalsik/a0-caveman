"""headroom_migration: preview and apply the standalone-plugin settings import.

POST /api/plugins/caveman/headroom_migration

Body:
    {"action": "preview"}
    {"action": "apply", "confirm": true}

`preview` is read-only and is the default. `apply` refuses without
`confirm: true`, and even then only fills in keys this plugin does not
already have - see helpers/headroom/migration.py for the full contract
(no destructive writes, idempotent, unknown keys preserved, source files
untouched).

IMPORTANT: this file must contain only ONE ApiHandler subclass. The route
slug is the filename (headroom_migration), and the framework only registers
classes[0] per file (see /a0/helpers/api.py line 238).
"""

from __future__ import annotations

from helpers.api import ApiHandler, Input, Output, Request, Response

from usr.plugins.caveman.helpers.headroom import migration as _migration


class HeadroomMigration(ApiHandler):
    @classmethod
    def requires_auth(cls):
        return True

    async def process(self, input: Input, request: Request) -> Output:
        data = input or {}
        action = str(data.get("action") or "preview").strip().lower()

        if action not in ("preview", "apply"):
            return Response(
                {
                    "ok": False,
                    "error": f"unknown action '{action}'. valid: preview, apply",
                },
                400,
            )

        try:
            if action == "preview":
                return {"ok": True, "action": "preview", "plan": _migration.build_plan()}

            if not data.get("confirm"):
                # No write without an explicit confirmation, and no implicit
                # confirmation either: the client has to ask for it.
                return Response(
                    {
                        "ok": False,
                        "error": (
                            "apply requires \"confirm\": true. Run a preview "
                            "first; nothing has been written."
                        ),
                    },
                    400,
                )

            plan = _migration.build_plan()
            outcome = _migration.apply_plan(plan, confirm=True)
            return {
                "ok": bool(outcome.get("applied")) and not outcome.get("error"),
                "action": "apply",
                "outcome": outcome,
                # Re-plan after writing so the response shows the new state
                # instead of the state that was just replaced.
                "plan": _migration.build_plan(),
            }
        except Exception as exc:  # noqa: BLE001
            return Response({"ok": False, "error": str(exc)}, 500)
