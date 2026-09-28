"""
Caveman plugin - plugin configuration reader.

Single owner for reading the plugin's own config. Previously every extension
and API handler repeated the same try/except around
`helpers.plugins.get_plugin_config`, and they did not all agree on the
defaults, which is how the WebUI and the prompt injector ended up reporting
different states for the same chat.

The plugin is declared `per_project_config: true` / `per_agent_config: true`
in plugin.yaml (remediation 2026-09-28, finding 31: this docstring used to
say both were false), so agents can carry their own overrides; the agent
argument is how `get_plugin_config` scopes the read and must keep being
passed. The merge still yields one effective config per install when no
override exists.
"""

from typing import Any

PLUGIN_NAME = "caveman"

# Mirrors default_config.yaml. Kept here so a missing or malformed config file
# still yields a usable, self-consistent result.
DEFAULTS: dict[str, Any] = {
    "enabled": False,
    "level": "full",
    "auto_clarity": True,
    "shrink_tools": False,
    "sanitize_responses": False,
    # Headroom input compression is independent and off by default.
    "headroom": {
        "enabled": False,
        "dry_run": False,
        "mode": "safe",
        "strategy": "auto",
        "level": "balanced",
        "model_hint": "gpt-4o",
        "auto_compress_tool_outputs_min_tokens": 200,
        "auto_compress_history": True,
        "auto_compress_history_min_tokens": 4000,
        "ccr_enabled": True,
        "ccr_backend": "sqlite",
        "ccr_path": "",
        "ccr_ttl_days": 7,
        "ccr_max_bytes": 268435456,
        "stats_enabled": True,
        "stats_path": "",
        "stats_retention_days": 30,
        "stats_max_rows": 50000,
        "expose_compress_tool": True,
        "never_compress_system_prompts": True,
        "protect_reads": True,
        "protect_code": True,
        "clear_old_tool_results": True,
        "clear_keep_recent": 3,
        "clear_min_tokens": 300,
        "clear_exempt_tools": [],
        "verbose": True,
        "proxy": {
            "auto_start": False,
            "host": "127.0.0.1",
            "port": 8787,
            "mode": "token",
            "binary": "/opt/venv-a0/bin/headroom",
            "env": {},
        },
    },
}


def get_config(agent: Any = None) -> dict[str, Any]:
    """Merged config: framework values over documented defaults."""
    try:
        from helpers import plugins as plugins_helper

        raw = plugins_helper.get_plugin_config(PLUGIN_NAME, agent=agent)
    except Exception:
        raw = None

    if not isinstance(raw, dict):
        raw = {}

    config = dict(DEFAULTS)
    config.update(raw)
    headroom = dict(DEFAULTS["headroom"])
    configured_headroom = raw.get("headroom")
    if isinstance(configured_headroom, dict):
        headroom.update(configured_headroom)
        proxy = dict(DEFAULTS["headroom"]["proxy"])
        if isinstance(configured_headroom.get("proxy"), dict):
            proxy.update(configured_headroom["proxy"])
        headroom["proxy"] = proxy
    config["headroom"] = headroom
    return config


def get_bool(key: str, agent: Any = None) -> bool:
    return bool(get_config(agent=agent).get(key, DEFAULTS.get(key, False)))


def get_level(agent: Any = None) -> str:
    from usr.plugins.caveman.helpers import state as caveman_state

    level = get_config(agent=agent).get("level", DEFAULTS["level"])
    return level if level in caveman_state.VALID_LEVELS else "full"
