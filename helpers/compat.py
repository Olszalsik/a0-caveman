"""
Caveman plugin - cross-module capability guard.

Why this exists
---------------
A partial upgrade produces a runtime `AttributeError` that kills the agent
turn. Observed in a Docker install where the extension modules were updated but
`helpers/state.py` was not:

    File "/a0/usr/plugins/caveman/extensions/python/system_prompt/_20_caveman_style.py",
      line 55, in execute
        resolved = caveman_state.resolve(_chat_id_from(self.agent), config)
    AttributeError: module 'usr.plugins.caveman.helpers.state' has no attribute 'resolve'

`state.resolve` was introduced in v0.5.0. v0.4.0's `state.py` has no such
function, so a directory holding new callers and an old `state.py` fails on the
first prompt build. The traceback is far from the cause, and the plugin takes
down a turn it is only supposed to style.

Policy
------
This plugin is optional, so it degrades: it warns once, names the cause, and
skips itself for the turn. A broken optional plugin must never raise out of an
extension point, because `agent.handle_exception` re-raises and the monologue
dies.

This module deliberately imports nothing from the rest of the plugin, so it
keeps working when its siblings are stale - which is exactly the case it exists
to handle.
"""

import threading
from typing import Any, Optional

PLUGIN_NAME = "caveman"

# The state API the shipped extensions and API handlers rely on. Every one of
# these is v0.5.0+, so their absence means a stale `state.py`.
REQUIRED_STATE_API = (
    "resolve",
    "get_level",
    "is_enabled",
    "set_state",
    "set_level",
    "set_enabled",
    "get_entry",
    "get_all_chats",
    "state_path",
    "record_turn",
    "get_stats",
    "reset_stats",
    "summary",
)

STALE_INSTALL_HINT = (
    "This is a partial upgrade, not a code bug. The plugin directory is "
    "mixing file versions: some modules are v0.5.0 and helpers/state.py is "
    "older. Replace the whole plugin directory in one step rather than "
    "copying individual files:\n"
    "  1. delete the installed plugin directory and its __pycache__ folders\n"
    "  2. copy the plugin across again, completely\n"
    "  3. run `python <plugin>/execute.py` in the install; it must print "
    "'health check PASSED'\n"
    "Clearing __pycache__ alone is not enough, because the stale source file "
    "is what is being loaded."
)

_lock = threading.Lock()
_warned: set = set()


def missing_state_api(state_module: Any) -> list:
    """Names in REQUIRED_STATE_API that `state_module` does not provide."""
    if state_module is None:
        return list(REQUIRED_STATE_API)
    return [name for name in REQUIRED_STATE_API if not hasattr(state_module, name)]


def has_state_api(state_module: Any) -> bool:
    return not missing_state_api(state_module)


def state_api(state_module: Any, agent: Any = None) -> Optional[Any]:
    """Return `state_module` if usable, else None after warning once.

    Callers must handle None by skipping their own work. They should not raise:
    see the module docstring.
    """
    missing = missing_state_api(state_module)
    if not missing:
        return state_module
    warn_once(
        agent,
        f"incompatible plugin files, caveman disabled for this turn "
        f"(helpers/state.py is missing: {', '.join(missing[:4])}"
        f"{'...' if len(missing) > 4 else ''}). {STALE_INSTALL_HINT}",
    )
    return None


def warn_once(agent: Any, message: str) -> None:
    """Log a message at most once per process.

    A stale install would otherwise emit the same warning on every prompt build
    and every turn for the life of the server.
    """
    key = message[:120]
    with _lock:
        if key in _warned:
            return
        _warned.add(key)
    _log(agent, message)


def reset_warnings() -> None:
    """Test hook: forget which warnings have been emitted."""
    with _lock:
        _warned.clear()


def _log(agent: Any, message: str) -> None:
    target = None
    if agent is not None:
        target = getattr(getattr(agent, "context", None), "log", None)
    if target is None:
        # Last resort: stderr, so the message is not lost entirely.
        import sys

        print(f"[{PLUGIN_NAME}] {message}", file=sys.stderr)
        return
    try:
        target.log(type="warning", content=f"{PLUGIN_NAME}: {message}")
    except Exception:
        # Telemetry must never raise into a model call.
        pass
