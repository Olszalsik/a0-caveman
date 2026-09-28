"""Preview and apply a settings migration from the standalone Headroom plugin.

Roadmap P0.3 / P3.1 / P3.2. The rules, and why each one exists:

* **Read-only by default.** `build_plan()` only reads. Nothing is written
  until `apply_plan(plan, confirm=True)` is called, and the API refuses
  without an explicit confirm flag. A migration that cannot be inspected
  first is not a migration, it is a surprise.
* **Never destructive.** Source files are opened read-only and are never
  written, moved or deleted - not the standalone plugin's `config.json`, its
  `default_config.yaml`, its CCR database, its stats database, this plugin's
  `config.json`, or Caveman's per-chat `.caveman` state. The only file this
  module writes is this plugin's own `config.json`, and only after a backup.
* **Idempotent.** Re-running produces the same result and reports
  `changed: false` once the destination already holds the values.
* **Never overwrites an explicit choice.** If this plugin's config already
  contains a `headroom` key, that key is a decision the user (or the WebUI)
  already made. It is reported as a `conflict` and left alone. Only
  *missing* keys are filled in.
* **Preserves unknown keys.** Anything in the destination file that this
  module does not understand is written back untouched, at the top level and
  inside the `headroom` section.

Scope handling - a deliberate limit
----------------------------------
The plan reports source configs at every scope the framework knows about
(global, per-project, per-agent) plus per-chat overrides, and names the
destination for each. `apply_plan()` writes the **global** scope only.

That is intentional. Writing a per-project or per-agent destination means
creating files inside a user's project tree or agent profile from a plugin,
which needs its own permission model, and the settings modal already has a
scoped save path for exactly that. Scoped sources are therefore reported
with `reason: "manual"` and the exact keys involved, and the user applies
them through a path that was already reviewed.

CCR / stats databases are deliberately NOT migrated. They are caches of
content the agent can regenerate or re-fetch, they are bounded and TTL
pruned, and copying rows across two schemas is where silent data loss would
live. The plan says so explicitly rather than leaving it implied.
"""

from __future__ import annotations

import json
import shutil
import time
from pathlib import Path
from typing import Any

from usr.plugins.caveman.helpers.headroom import config as _config

PLAN_VERSION = 2


# ---------------------------------------------------------------------------
# reading
# ---------------------------------------------------------------------------


def _read_json(path: Path) -> tuple[Any, str]:
    """Return (value, error). A malformed file is reported, never raised."""
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None, "missing"
    except Exception as exc:  # noqa: BLE001
        return None, f"unreadable: {exc}"
    if not raw.strip():
        return {}, ""
    try:
        return json.loads(raw), ""
    except Exception as exc:  # noqa: BLE001
        return None, f"malformed JSON: {exc}"


def _standalone_dir() -> tuple[Path | None, str]:
    """Locate the standalone plugin without importing its code.

    Returns (path, error). The error distinguishes "not installed" from
    "could not ask the framework", because those lead to different advice:
    one needs an install, the other needs a look at the runtime.
    """
    try:
        from helpers import plugins as plugins_helper

        plugin_dir = plugins_helper.find_plugin_dir(_config.STANDALONE_PLUGIN_NAME)
    except Exception as exc:  # noqa: BLE001
        return None, f"could not query the plugin registry: {type(exc).__name__}: {exc}"
    return (Path(plugin_dir) if plugin_dir else None), ""


def _destination_config() -> tuple[Path, dict[str, Any], str]:
    path = _config.destination_config_path()
    raw, error = _read_json(path)
    if error == "missing":
        return path, {}, ""
    if error:
        return path, {}, error
    if not isinstance(raw, dict):
        return path, {}, "config.json does not contain a JSON object"
    return path, raw, ""


def _scoped_sources(standalone_dir: Path | None = None) -> list[dict[str, Any]]:
    """Every config.json the framework can find for the standalone plugin."""
    found: list[dict[str, Any]] = []
    # Finding 9 (remediation 2026-09-28): `assets` used to be bound only
    # inside the try. When `find_plugin_assets` raised (it imports
    # helpers.projects -> PIL, unavailable outside the runtime), the except
    # appended the error entry and then the loop hit UnboundLocalError -- the
    # handler for the enumeration failure crashed itself, and the API
    # returned 500 instead of the degraded-but-honest scope list.
    assets: list[Any] = []
    try:
        from helpers import plugins as plugins_helper

        assets = plugins_helper.find_plugin_assets(
            "config.json",
            plugin_name=_config.STANDALONE_PLUGIN_NAME,
            project_name="*",
            agent_profile="*",
            only_first=False,
        ) or []
    except Exception as exc:  # noqa: BLE001
        found.append(
            {
                "scope": "",
                "path": "",
                "error": f"could not enumerate scopes: {exc}",
            }
        )
    for asset in assets or []:
        path = Path(str(asset.get("path", "")))
        raw, error = _read_json(path)
        if error == "missing":
            continue
        project = str(asset.get("project_name", "") or "")
        profile = str(asset.get("agent_profile", "") or "")
        if project and profile:
            scope = "project+agent"
        elif project:
            scope = "project"
        elif profile:
            scope = "agent"
        else:
            scope = "global"
        found.append(
            {
                "scope": scope,
                "project_name": project,
                "agent_profile": profile,
                "path": str(path),
                "config": raw if isinstance(raw, dict) else {},
                "error": error if error != "missing" else "",
            }
        )
    return _add_global_fallback(standalone_dir, found)

def _add_global_fallback(
    standalone_dir: Path | None, found: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Read the global config directly when scope enumeration failed.

    `helpers.plugins.find_plugin_assets` imports helpers.projects, which pulls
    in PIL and is therefore unavailable outside the framework runtime. The
    global scope needs none of that: the standalone plugin's own directory is
    `usr/plugins/headroom_compress`, and its `config.json` sits at the root.
    Losing the global values - the ones a migration is mostly about - because
    an optional import was missing would be the wrong trade.
    """
    if standalone_dir is None:
        return found
    if any(src.get("scope") == "global" for src in found):
        return found

    path = standalone_dir / "config.json"
    raw, error = _read_json(path)
    if error == "missing":
        return found
    if error:
        found.append(
            {
                "scope": "global",
                "project_name": "",
                "agent_profile": "",
                "path": str(path),
                "config": {},
                "error": error,
            }
        )
        return found
    if isinstance(raw, dict):
        found.insert(
            0,
            {
                "scope": "global",
                "project_name": "",
                "agent_profile": "",
                "path": str(path),
                "config": raw,
                "error": "",
                "source": "direct read (scope enumeration unavailable)",
            },
        )
    return found


def _chat_overrides(standalone_dir: Path | None) -> dict[str, Any]:
    """Per-chat overrides. Read-only, and never part of the written section."""
    if standalone_dir is None:
        return {}
    path = standalone_dir / "cache" / "per_chat_overrides.json"
    raw, error = _read_json(path)
    if error or not isinstance(raw, dict):
        return {}
    return {
        "path": str(path),
        "count": len(raw),
        "note": (
            "Per-chat compression overrides stay with the standalone plugin. "
            "This plugin has its own per-chat override store "
            "(helpers/headroom/per_chat.py) and does not import the other one."
        ),
    }


# ---------------------------------------------------------------------------
# plan
# ---------------------------------------------------------------------------


def _classify(
    source_config: dict[str, Any], destination_section: dict[str, Any]
) -> tuple[dict[str, Any], list[dict[str, Any]], list[str]]:
    """Split source keys into import / conflict / unknown-but-kept."""
    to_import: dict[str, Any] = {}
    conflicts: list[dict[str, Any]] = []
    unknown: list[str] = []

    known = _config._DEFAULTS  # noqa: SLF001 - same package, one owner
    for key, value in (source_config or {}).items():
        if key not in known:
            # Keep it. An unknown key may be a newer Headroom setting this
            # plugin does not implement yet, and dropping the user's value
            # would be the one irreversible thing we could do here.
            unknown.append(key)
            if key not in destination_section:
                to_import[key] = value
            continue
        if key not in destination_section:
            to_import[key] = value
        elif destination_section[key] != value:
            conflicts.append(
                {
                    "key": key,
                    "source": value,
                    "destination": destination_section[key],
                }
            )
    return to_import, conflicts, unknown


def build_plan() -> dict[str, Any]:
    """Read everything, decide everything, write nothing."""
    standalone_dir, lookup_error = _standalone_dir()
    dest_path, dest_config, dest_error = _destination_config()
    destination_section = dest_config.get("headroom")
    if not isinstance(destination_section, dict):
        destination_section = {}

    plan: dict[str, Any] = {
        "version": PLAN_VERSION,
        "generated_at": time.time(),
        "standalone": {
            "present": standalone_dir is not None,
            "path": str(standalone_dir) if standalone_dir else "",
            "lookup_error": lookup_error,
        },
        "destination": {
            "plugin": _config.PLUGIN_NAME,
            "path": str(dest_path),
            "readable": not dest_error,
            "error": dest_error,
            "existing_keys": sorted(destination_section.keys()),
        },
        "sources": [],
        "to_import": {},
        "conflicts": [],
        "unknown_keys": [],
        "manual": [],
        "chat_overrides": {},
        "notes": [],
        "warnings": [],
    }

    if standalone_dir is None:
        if lookup_error:
            plan["warnings"].append(lookup_error)
            plan["notes"].append(
                "The plugin registry could not be queried, so the standalone "
                "plugin's presence is unknown. Nothing was read and nothing "
                "was written."
            )
        else:
            plan["notes"].append(
                "The standalone headroom_compress plugin is not installed. "
                "There is nothing to migrate; this plugin's own defaults "
                "already apply."
            )
        return plan

    sources = _scoped_sources(standalone_dir)
    plan["sources"] = sources

    global_source: dict[str, Any] = {}
    for src in sources:
        if src.get("scope") == "global" and not src.get("error"):
            global_source = src.get("config") or {}
            break

    if not global_source:
        plan["notes"].append(
            "The standalone plugin has no readable global config.json, so there "
            "are no global values to import. Its other scopes are still listed."
        )

    to_import, conflicts, unknown = _classify(global_source, destination_section)
    plan["to_import"] = to_import
    plan["conflicts"] = conflicts
    plan["unknown_keys"] = unknown

    for src in sources:
        if src.get("scope") == "global":
            # The global entry is the one this migration would actually write
            # from, so a read failure there is a warning in its own right -
            # not silently skipped just because it is not "manual" work.
            if src.get("error"):
                plan["warnings"].append(
                    f"global config at {src.get('path')}: {src['error']}"
                )
            continue
        if src.get("error"):
            plan["warnings"].append(
                f"scope {src.get('scope')} at {src.get('path')}: {src['error']}"
            )
            continue
        plan["manual"].append(
            {
                "scope": src.get("scope"),
                "project_name": src.get("project_name", ""),
                "agent_profile": src.get("agent_profile", ""),
                "source_path": src.get("path", ""),
                "keys": sorted((src.get("config") or {}).keys()),
                "reason": (
                    "Scoped destination files are written through the framework's "
                    "scoped settings save, not by this migration. Set the listed "
                    "keys in the plugin settings modal for that scope."
                ),
            }
        )

    plan["chat_overrides"] = _chat_overrides(standalone_dir)
    plan["notes"].append(
        "CCR and statistics databases are not migrated. They are bounded, TTL "
        "pruned caches of content the agent can regenerate or re-fetch, and "
        "copying rows between two schemas is where silent data loss would live."
    )
    plan["notes"].append(
        "Caveman's own settings and per-chat .caveman state are untouched by "
        "this migration and keep their current paths."
    )
    if conflicts:
        plan["notes"].append(
            f"{len(conflicts)} key(s) already have a value here and will NOT be "
            "overwritten. Remove them from this plugin's config to accept the "
            "standalone value."
        )
    if dest_error:
        plan["warnings"].append(
            f"destination config is unusable ({dest_error}); apply is blocked "
            "until it is fixed"
        )
    return plan


# ---------------------------------------------------------------------------
# apply
# ---------------------------------------------------------------------------


def _backup_path(dest_path: Path) -> Path:
    # Finding 33 (remediation 2026-09-28): two applies within the same second
    # generated the same stamp and the second silently overwrote the first's
    # backup. Microsecond precision makes a collision practically impossible,
    # and an exists() loop is the belt to those braces.
    from datetime import datetime, timezone

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    candidate = dest_path.with_name(f"{dest_path.name}.bak-{stamp}")
    counter = 1
    while candidate.exists():
        candidate = dest_path.with_name(f"{dest_path.name}.bak-{stamp}-{counter}")
        counter += 1
    return candidate


def apply_plan(plan: dict[str, Any], confirm: bool = False) -> dict[str, Any]:
    """Write the plan's importable keys. Requires an explicit confirm."""
    result: dict[str, Any] = {
        "applied": False,
        "changed": False,
        "written_keys": [],
        "preserved_conflicts": [],
        "backup": "",
        "destination": "",
        "error": "",
    }

    dest_path, dest_config, dest_error = _destination_config()
    result["destination"] = str(dest_path)

    if not confirm:
        result["error"] = "apply requires confirm=true; run a preview first"
        return result
    if dest_error:
        result["error"] = f"destination config is unusable: {dest_error}"
        return result

    to_import = dict((plan or {}).get("to_import") or {})
    conflicts = list((plan or {}).get("conflicts") or [])
    result["preserved_conflicts"] = [c.get("key", "") for c in conflicts]

    if not to_import:
        # Idempotent: nothing to add, nothing written, no backup churn.
        result["applied"] = True
        result["changed"] = False
        return result

    # Re-check against what is on disk right now, not against the plan. The
    # user may have edited the config between preview and apply, and the
    # no-overwrite rule has to hold at write time, not just at preview time.
    section = dest_config.get("headroom")
    if not isinstance(section, dict):
        section = {}
    fresh_to_import = {k: v for k, v in to_import.items() if k not in section}

    if not fresh_to_import:
        result["applied"] = True
        result["changed"] = False
        return result

    merged = dict(dest_config)
    merged_section = dict(section)
    merged_section.update(fresh_to_import)
    merged["headroom"] = merged_section

    try:
        backup = _backup_path(dest_path)
        if dest_path.exists():
            shutil.copy2(dest_path, backup)
            result["backup"] = str(backup)
        # Write a sibling temp file first, then replace. A crash mid-write must
        # not leave a truncated config.json behind.
        tmp = dest_path.with_name(f"{dest_path.name}.tmp")
        tmp.write_text(json.dumps(merged, indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(dest_path)
    except Exception as exc:  # noqa: BLE001
        result["error"] = f"write failed: {exc}"
        return result

    result["applied"] = True
    result["changed"] = True
    result["written_keys"] = sorted(fresh_to_import.keys())
    return result

