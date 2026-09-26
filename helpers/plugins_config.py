"""
Caveman plugin - plugin configuration reader.

Single owner for reading the plugin's own config. Previously every extension
and API handler repeated the same try/except around
`helpers.plugins.get_plugin_config`, and they did not all agree on the
defaults, which is how the WebUI and the prompt injector ended up reporting
different states for the same chat.

The plugin is declared `per_project_config: false` / `per_agent_config: false`,
so there is one config per install and the agent argument is only passed for
consistency with the framework hook signature.
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
}


def get_config() -> dict[str, Any]:
    """Merged config: framework values over documented defaults."""
    try:
        from helpers import plugins as plugins_helper

        raw = plugins_helper.get_plugin_config(PLUGIN_NAME)
    except Exception:
        raw = None

    if not isinstance(raw, dict):
        raw = {}

    config = dict(DEFAULTS)
    config.update(raw)
    return config


def get_bool(key: str) -> bool:
    return bool(get_config().get(key, DEFAULTS.get(key, False)))


def get_level() -> str:
    from usr.plugins.caveman.helpers import state as caveman_state

    level = get_config().get("level", DEFAULTS["level"])
    return level if level in caveman_state.VALID_LEVELS else "full"
