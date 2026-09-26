"""
Caveman plugin - token/length observation API.

Route: POST /api/plugins/caveman/caveman_stats

Returns raw observed quantities only: how many assistant turns were produced
and how many characters the user actually saw, broken down by level.

  {"action": "get",     "chat_id": "<id>"}
  {"action": "list"}
  {"action": "summary"}
  {"action": "reset",   "chat_id": "<id>"}
  {"action": "history", "chat_id": "<id>"}   -> mode transitions, oldest first

There is deliberately no "estimated tokens saved" field. A saving cannot be
derived from a single observed length: it needs a measured control arm. The
previous revision multiplied the observed length by a hardcoded per-level
ratio and reported the product as "tokens saved"; upstream retracted exactly
that number (`docs/HONEST-NUMBERS.md`: "Output reduction ... Not published").
`benchmarks/run.py` now runs a real A/B instead.

`history` is what makes the observations attributable: it lists the level that
was actually active at each transition, so output can be attributed to the mode
in force when it was generated rather than to whatever the mode is at read
time. Only real transitions are logged.

All file access goes through `helpers.state`, which holds the single module
lock. This handler previously carried its own `_LOCK` and its own copy of the
read-modify-write path, so a concurrent monologue_end and reset could lose
each other's writes.
"""

from helpers.api import ApiHandler  # type: ignore

from usr.plugins.caveman.helpers import state as caveman_state


PLUGIN_NAME = "caveman"


def _payload(input_data) -> dict:
    return input_data if isinstance(input_data, dict) else {}


class CavemanStats(ApiHandler):
    async def process(self, input_data, request):
        data = _payload(input_data)
        action = data.get("action") or "get"
        chat_id = str(data.get("chat_id") or "")

        if action == "list":
            return {
                "ok": True,
                "action": "list",
                "chats": caveman_state.all_stats(),
                "stats_path": caveman_state.stats_path(),
            }

        if action == "summary":
            return {
                "ok": True,
                "action": "summary",
                "summary": caveman_state.summary(),
            }

        if action == "history":
            if not chat_id:
                return {
                    "ok": False,
                    "action": action,
                    "error": "chat_id is required for this action",
                }
            return {
                "ok": True,
                "action": "history",
                "chat_id": chat_id,
                "transitions": caveman_state.mode_history(chat_id),
                "mode_log_path": caveman_state.mode_log_path(),
            }

        if not chat_id:
            return {
                "ok": False,
                "action": action,
                "error": "chat_id is required for this action",
            }

        if action == "get":
            return {
                "ok": True,
                "action": "get",
                "chat_id": chat_id,
                **caveman_state.get_stats(chat_id),
            }

        if action == "reset":
            return {
                "ok": caveman_state.reset_stats(chat_id),
                "action": "reset",
                "chat_id": chat_id,
            }

        return {
            "ok": False,
            "action": action,
            "error": f"unknown action: {action!r}",
        }
