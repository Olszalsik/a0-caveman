"""Headroom settings nested under Caveman's plugin configuration.

This is the single funnel for every Headroom reader, so it is also the one place
that has to absorb a stale parent config module. See `helpers/compat.py`:
`get_config` gained its `agent` parameter in v0.5.0, and a v0.4.0
`plugins_config.py` loaded beside these extensions raises `TypeError:
get_config() got an unexpected keyword argument 'agent'` from the first
`hist_add_tool_result` call - taking down the turn for a feature that is off by
default.

Falling back to the documented defaults is the right degradation here: Headroom
is disabled unless the user turns it on, so defaults mean "off" and nothing is
compressed. The partial upgrade is still reported, once, via
`compat.warn_once`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from usr.plugins.caveman.helpers import compat
from usr.plugins.caveman.helpers import plugins_config as _plugin_config

PLUGIN_NAME = "caveman"

# The standalone plugin this feature was ported from. It is never imported,
# read or written by this plugin - detection is by directory presence and
# toggle state only. See helpers/headroom/coexistence.py.
STANDALONE_PLUGIN_NAME = "headroom_compress"
PLUGIN_DIR = Path(__file__).resolve().parents[2]
# Resolved with `.get` rather than `[]`: a stale `plugins_config.py` predates the
# `headroom` section entirely, and an AttributeError at import time would break
# every hook that imports this module, not just the compression ones.
_DEFAULTS = dict(getattr(_plugin_config, "DEFAULTS", {}).get("headroom") or {})


def _merge(defaults: dict[str, Any], values: Any) -> dict[str, Any]:
    merged = dict(defaults)
    if isinstance(values, dict):
        for key, value in values.items():
            if isinstance(merged.get(key), dict) and isinstance(value, dict):
                merged[key] = _merge(merged[key], value)
            else:
                merged[key] = value
    return merged


def get_config(agent: Any = None) -> dict[str, Any]:
    """Return the active namespaced config without reading standalone state."""
    config_mod = compat.config_api(_plugin_config, agent)
    if config_mod is None:
        return dict(_DEFAULTS)
    full_config = config_mod.get_config(agent=agent)
    if not isinstance(full_config, dict):
        return dict(_DEFAULTS)
    return _merge(_DEFAULTS, full_config.get("headroom"))


def is_enabled(agent: Any = None) -> bool:
    return bool(get_config(agent=agent).get("enabled", False))


def destination_config_path() -> Path:
    """The user config file this plugin's settings are written to.

    Always the plugin's own `config.json`. `helpers/plugins.py` resolves
    per-project and per-agent overrides from other files, but the global
    scope is the one place a migration may write: it is a single file the
    user can read, back up and diff by hand.
    """
    return (PLUGIN_DIR / "config.json").resolve()


def ccr_db_path(cfg: dict[str, Any]) -> Path:
    raw = (cfg.get("ccr_path") or "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    return (PLUGIN_DIR / "ccr" / "ccr.db").resolve()


def stats_db_path(cfg: dict[str, Any]) -> Path:
    raw = (cfg.get("stats_path") or "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    return (PLUGIN_DIR / "stats" / "stats.db").resolve()
