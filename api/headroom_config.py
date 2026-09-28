"""headroom_config: return the merged, scope-aware plugin config.

POST /api/plugins/caveman/headroom_config

Body:
    {"action": "get"}                       (default; also the legacy shape -
                                             an empty body behaves as "get")
    {"action": "set", "section": {...}}     write the headroom section

`get` returns the merged, effective headroom config wrapped in the plugin
config shape ({"headroom": {...}}) plus the read-only coexistence report, and
is what webui/headroom-store.js uses to populate the settings form.

`set` writes the headroom section. webui/headroom-config.html is opened
standalone via openModal (banner CTAs, config.html button), outside the
framework's plugin-settings wrapper, so it has no `config` scope and no Save
footer - it loads through `get` and persists through `set` instead. The write:
  * touches only this plugin's own config.json (the global scope, same file
    the migration writes),
  * updates only the `headroom` key; every other top-level key is preserved,
  * keeps unknown keys inside the existing headroom section, and inside a
    known `proxy` subsection,
  * filters incoming keys to the documented defaults, so a stale or hostile
    client cannot inject arbitrary keys,
  * refuses when config.json is unreadable rather than overwriting it.

IMPORTANT: this file must contain only ONE ApiHandler subclass. The route slug
is the filename (headroom_config), and the framework only registers
classes[0] per file (see /a0/helpers/api.py line 238).
"""

from __future__ import annotations

import json
from typing import Any

from helpers.api import ApiHandler, Input, Output, Request, Response
from usr.plugins.caveman.helpers.headroom import config as _config
from usr.plugins.caveman.helpers.headroom import coexistence as _coexistence

_ACTIONS_GET = ("get", "get_section")
_ACTIONS_SET = ("set", "set_section")


def _read_dest(path) -> tuple[dict[str, Any] | None, str]:
    """Read this plugin's config.json. Returns (config or None, error)."""
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None, ""
    except Exception as exc:  # noqa: BLE001
        return None, f"unreadable: {exc}"
    if not raw.strip():
        return {}, ""
    try:
        parsed = json.loads(raw)
    except Exception as exc:  # noqa: BLE001
        return None, f"malformed JSON: {exc}"
    if not isinstance(parsed, dict):
        return None, "config.json does not contain a JSON object"
    return parsed, ""


def _filter_section(section: Any) -> dict[str, Any]:
    """Keep only keys the plugin actually implements (defaults whitelist)."""
    known = _config._DEFAULTS  # noqa: SLF001 - same package, one owner
    proxy_known = known.get("proxy") or {}
    filtered: dict[str, Any] = {}
    for key, value in (section or {}).items():
        if key == "proxy":
            if isinstance(value, dict):
                filtered["proxy"] = {
                    k: v for k, v in value.items() if k in proxy_known
                }
            continue
        if key in known:
            filtered[key] = value
    return filtered


def _write_section(section: dict[str, Any]) -> dict[str, Any]:
    """Merge the filtered section into config.json's headroom key."""
    path = _config.destination_config_path()
    dest, error = _read_dest(path)
    if error:
        return {"ok": False, "error": f"config.json is unusable ({error}); not written"}

    filtered = _filter_section(section)
    if not filtered:
        return {"ok": False, "error": "no known headroom keys in section; not written"}

    existing_section = {}
    if isinstance(dest, dict) and isinstance(dest.get("headroom"), dict):
        existing_section = dict(dest["headroom"])

    # Unknown keys already in the file survive; only whitelisted keys change.
    merged_section = {**existing_section, **filtered}

    written_keys = sorted(
        key for key in filtered if existing_section.get(key) != filtered[key]
    )
    proxy_changed = existing_section.get("proxy") != merged_section.get("proxy")
    if "proxy" in filtered and proxy_changed and "proxy" not in written_keys:
        written_keys.append("proxy")

    merged = dict(dest) if isinstance(dest, dict) else {}
    merged["headroom"] = merged_section

    # Write a sibling temp file first, then replace: a crash mid-write must
    # not leave a truncated config.json behind.
    tmp = path.with_name(f"{path.name}.tmp")
    tmp.write_text(json.dumps(merged, indent=2), encoding="utf-8")
    tmp.replace(path)
    return {"written_keys": written_keys, "changed": bool(written_keys)}


class HeadroomConfig(ApiHandler):
    @classmethod
    def requires_auth(cls):
        return True

    async def process(self, input: Input, request: Request) -> Output:
        data = input or {}
        action = str(data.get("action") or "get").strip().lower()

        try:
            if action in _ACTIONS_GET:
                return {
                    "ok": True,
                    "action": "get",
                    # Wrapped in the plugin-config shape ({"headroom": {...}}):
                    # webui/headroom-config.html binds config.headroom.<key>,
                    # and this is the same shape the stats endpoint reports.
                    "config": {"headroom": _config.get_config(agent=None)},
                    # Read-only report. Never toggles the other plugin; see
                    # helpers/headroom/coexistence.py.
                    "coexistence": _coexistence.overlap(agent=None),
                }

            if action in _ACTIONS_SET:
                section = data.get("section")
                if not isinstance(section, dict):
                    return Response(
                        {"ok": False, "error": "set requires 'section' to be an object"},
                        400,
                    )
                outcome = _write_section(section)
                if "error" in outcome:
                    return Response(outcome, 500)
                return {
                    "ok": True,
                    "action": "set",
                    **outcome,
                    # Echo the merged, effective config so the client shows
                    # what is actually in force now.
                    "config": {"headroom": _config.get_config(agent=None)},
                }

            return Response(
                {
                    "ok": False,
                    "error": (
                        f"unknown action '{action}'. valid: get, get_section, "
                        "set, set_section"
                    ),
                },
                400,
            )
        except Exception as exc:  # noqa: BLE001
            return Response({"ok": False, "error": str(exc)}, 500)