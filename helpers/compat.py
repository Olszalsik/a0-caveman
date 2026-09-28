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

The same partial upgrade reaches `helpers/plugins_config.py`, not only
`helpers/state.py`, and it is the more likely one: `get_config` gained its
`agent` parameter in v0.5.0, and every shipped extension and API handler passes
one. A v0.4.0 `plugins_config.py` loaded beside them raises

    TypeError: get_config() got an unexpected keyword argument 'agent'

from `get_system_prompt`, which propagates through `prepare_prompt` into
`monologue` and stops the agent. That is the traceback this module also exists
to absorb, so the config reader is guarded here for the same reason as the state
module.
"""

import inspect
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
    "mixing file versions: some modules are v0.5.0 and the module named in "
    "this message is older. Replace the whole plugin directory in one step "
    "rather than copying individual files:\n"
    "  1. delete the installed plugin directory and its __pycache__ folders\n"
    "  2. copy the plugin across again, completely\n"
    "  3. run `python <plugin>/execute.py` in the install; it must print "
    "'health check PASSED'\n"
    "Clearing __pycache__ alone is not enough, because the stale source file "
    "is what is being loaded."
)

# The config API the shipped extensions and API handlers rely on. All of it is
# v0.5.0+, so their absence means a stale `plugins_config.py`.
REQUIRED_CONFIG_API = (
    "get_config",
    "get_bool",
    "get_level",
    "DEFAULTS",
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


def accepts_kwarg(func: Any, name: str) -> bool:
    """Whether `func` can be called with `name=`.

    A builtin or a C function has no introspectable signature; it is reported
    as accepting the argument, so an unusual callable is not mistaken for a
    stale one. The alternative - treating "cannot tell" as "does not accept" -
    would disable a working install.
    """
    if func is None:
        return False
    try:
        signature = inspect.signature(func)
    except (TypeError, ValueError):
        return True
    for parameter in signature.parameters.values():
        if parameter.kind is inspect.Parameter.VAR_KEYWORD:
            return True
    return name in signature.parameters


def missing_config_api(config_module: Any) -> list:
    """Names in REQUIRED_CONFIG_API that `config_module` does not provide.

    A name the installed module lacks, and a function whose signature is too
    narrow to accept `agent=`, are the same failure: the caller cannot resolve
    a scoped config. Both are reported, because both are fixed by replacing the
    directory, and naming only the missing symbol would send the reader looking
    for a file that is present and looks current.
    """
    if config_module is None:
        return list(REQUIRED_CONFIG_API)
    missing = [name for name in REQUIRED_CONFIG_API if not hasattr(config_module, name)]
    for name in ("get_config", "get_bool"):
        func = getattr(config_module, name, None)
        if func is not None and not accepts_kwarg(func, "agent"):
            missing.append(f"{name}(agent=...)")
    return missing


def has_config_api(config_module: Any) -> bool:
    return not missing_config_api(config_module)


def config_api(config_module: Any, agent: Any = None) -> Optional[Any]:
    """Return `config_module` if usable, else None after warning once.

    The config counterpart of `state_api`, with the same contract: callers must
    handle None by skipping their own work, and must not raise. See the module
    docstring for why an optional plugin may not raise out of an extension
    point.
    """
    missing = missing_config_api(config_module)
    if not missing:
        return config_module
    warn_once(
        agent,
        f"incompatible plugin files, caveman disabled for this turn "
        f"(helpers/plugins_config.py is missing: {', '.join(missing[:4])}"
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
