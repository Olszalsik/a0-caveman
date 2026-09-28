#!/usr/bin/env python3
"""
Caveman plugin - health check (v0.5.0).

Run from the Plugins UI or via `python usr/plugins/caveman/execute.py`.
Returns 0 on success, non-zero on the first failing check.

What changed, and why
---------------------
The previous revision reported PASSED while printing

    [caveman] WARN: could not import helpers.state: No module named 'usr'
    caveman v0.4.0 (Plan C COMPLETE) - health check PASSED

It downgraded a failed import of the module every extension depends on to a
warning, and it put the wrong directories on `sys.path` to get that import
(`usr` is an implicit namespace package, so the Agent Zero root has to be on
the path, not the plugin directory or `/a0`).

It also only checked that files existed, that the manifest parsed, and that
each module compiled. None of that proves anything works, which is how three
non-functional features survived: a slash command that could never match, a
WebUI that POSTed an action the API did not implement, and a response
validator attached to an extension point that carries no response text.

This version treats any failure as a failure, and additionally exercises the
state layer, the API action contract, and command classification for real.

Hardening round (v0.5.2 remediation)
------------------------------------
Checks that could still pass vacuously were closed: the standalone-import scan
is AST-based (it now catches `from usr.plugins import headroom_compress`,
parenthesised multi-line imports, dynamically built importlib calls, and it
covers hooks.py, install.py, headroom_setup.py, skills/ and benchmarks/, not
just helpers/api/extensions/tools); the migration and coexistence report runs
against the REAL framework helpers when they import and its output says which
mode ran, because stub data must never be reported as a fact about the install;
the bare-fetch guard is per call site; the claim guard also scans .html/.js
assets and the banner extensions' user-visible strings; banner ids defined as
helpers constants are resolved; config parity covers the Caveman top-level keys
as well as the headroom section; intensity levels are read from the shipped
markers, not only through available_levels()'s VALID_LEVELS filter; and the
Headroom settings page's bindings and asset references are validated.
"""

import importlib.util
import json
import os
import re
import subprocess
import sys
import types

PLUGIN_NAME = "caveman"
EXPECTED_VERSION = "0.5.4"

HERE = os.path.dirname(os.path.abspath(__file__))

# Layout independence.
#
# Installed, the plugin sits at <a0>/usr/plugins/caveman/ and its modules are
# imported as `usr.plugins.caveman.helpers.*`. In a standalone clone - which is
# what a contributor or a Plugin Hub reviewer has - the plugin is the repo
# root, there is no `usr/` package, and the same imports fail. Since the docs
# tell people to run this file from a clone, both layouts have to work.
#
# The fix is to synthesise the package chain with this directory as the
# caveman package's search path, rather than to special-case the checks.
PLUGIN_ROOT = HERE


def _find_agent_zero_root():
    """Walk up for a directory containing both `usr/` and `helpers/plugins.py`."""
    current = HERE
    while True:
        parent = os.path.dirname(current)
        if parent == current:
            return None
        if os.path.isdir(os.path.join(parent, "usr")) and os.path.isfile(
            os.path.join(parent, "usr", "plugins", "caveman", "plugin.yaml")
        ):
            return parent
        current = parent


A0_ROOT = _find_agent_zero_root()
# Standalone: use a scratch workdir so the state round-trip still runs.
ROOT = A0_ROOT or HERE
STANDALONE = A0_ROOT is None


def _register_package(name: str, path: str | None) -> None:
    """Register a package whose search path is `path` (None = bare namespace)."""
    if name in sys.modules:
        return
    module = types.ModuleType(name)
    if path is not None:
        module.__path__ = [path]
    else:
        module.__path__ = []
    sys.modules[name] = module


def _bootstrap_imports() -> None:
    """Make `usr.plugins.caveman.*` importable from either layout."""
    if A0_ROOT is not None:
        if A0_ROOT not in sys.path:
            sys.path.insert(0, A0_ROOT)
        return
    _register_package("usr", None)
    _register_package("usr.plugins", None)
    _register_package("usr.plugins.caveman", PLUGIN_ROOT)


_bootstrap_imports()

REQUIRED_FILES = [
    "plugin.yaml",
    "default_config.yaml",
    "hooks.py",
    "LICENSE",
    "icon.svg",
    "install.py",
    # helpers
    "helpers/state.py",
    "helpers/compat.py",
    "helpers/plugins_config.py",
    "helpers/compress.py",
    "helpers/markdown.py",
    "helpers/prompts.py",
    # api
    "api/caveman_state.py",
    "api/caveman_stats.py",
    # prompts: ONE ruleset, filtered per level, not one file per level
    "prompts/caveman.system.style.md",
    "prompts/caveman.intensity.md",
    "prompts/caveman.auto_clarity.md",
    # extensions
    "extensions/python/banners/_10_caveman_discovery.py",
    "extensions/python/system_prompt/_20_caveman_style.py",
    "extensions/python/monologue_start/_30_caveman_command.py",
    "extensions/python/chat_model_call_before/_60_caveman_shrink_tools.py",
    "extensions/python/message_loop_result/_50_caveman_validate.py",
    "extensions/python/message_loop_result/_60_caveman_observe.py",
    "extensions/webui/page-head/caveman-injector.html",
    # Headroom half of the combined plugin (roadmap P1/P2)
    "headroom-requirements.txt",
    "headroom_setup.py",
    "helpers/headroom/config.py",
    "helpers/headroom/compressor.py",
    "helpers/headroom/ccr_cache.py",
    "helpers/headroom/stats.py",
    "helpers/headroom/per_chat.py",
    "helpers/headroom/clarity.py",
    "helpers/headroom/proxy_manager.py",
    "helpers/headroom/coexistence.py",
    "helpers/headroom/migration.py",
    "api/headroom_config.py",
    "api/headroom_execute.py",
    "api/headroom_per_chat.py",
    "api/headroom_proxy.py",
    "api/headroom_stats.py",
    "api/headroom_migration.py",
    "extensions/python/banners/_10_headroom_compress.py",
    "extensions/python/banners/_20_headroom_coexistence.py",
    "extensions/python/hist_add_before/_10_compress_user_message.py",
    "extensions/python/hist_add_tool_result/_10_compress_tool_output.py",
    "extensions/python/message_loop_prompts_before/_05_auto_clarity.py",
    "extensions/python/message_loop_prompts_before/_07_clear_old_tool_results.py",
    "extensions/python/message_loop_prompts_before/_10_compress_history.py",
    "tools/compress_text.py",
    "tools/headroom_retrieve.py",
    "webui/headroom-store.js",
    "webui/headroom-config.html",
    "webui/headroom-dashboard-store.js",
    "webui/headroom-dashboard.html",
    "webui/caveman-dropdown.js",
    "webui/config.html",
    # sub-skills
    "skills/caveman-stats/SKILL.md",
    "skills/caveman-commit/SKILL.md",
    "skills/caveman-review/SKILL.md",
    "skills/caveman-compress/SKILL.md",
    "skills/caveman-compress/scripts/compress.py",
    "skills/caveman-help/SKILL.md",
    # tests
    "tests/test_caveman.py",
    "tests/test_healthcheck.py",
    "tests/test_headroom_integration.py",
    # cavecrew subagents
    "agents/cavecrew-investigator/agent.yaml",
    "agents/cavecrew-investigator/prompts/agent.system.main.specifics.md",
    "agents/cavecrew-builder/agent.yaml",
    "agents/cavecrew-builder/prompts/agent.system.main.specifics.md",
    "agents/cavecrew-reviewer/agent.yaml",
    "agents/cavecrew-reviewer/prompts/agent.system.main.specifics.md",
    # benchmarks
    "benchmarks/run.py",
    "benchmarks/prompts.json",
    "benchmarks/README.md",
]

# config.json is gitignored and written on first save, so a fresh install or
# clone legitimately lacks it; the runtime creates it on first save. Its
# absence is a note, not a failure. Everything about its content is still
# checked when it exists (dead keys in check_manifest).
OPTIONAL_FILES = ("config.json",)

# Actions the WebUI is allowed to send. Checked against the handler below.
FRONTEND_ACTIONS = ("get", "set", "list")

failures: list[str] = []


def fail(message: str) -> None:
    failures.append(message)
    print(f"[{PLUGIN_NAME}] FAIL: {message}")


def ok(message: str) -> None:
    print(f"[{PLUGIN_NAME}] ok: {message}")


# ---------------------------------------------------------------------------
# 1. Files
# ---------------------------------------------------------------------------


def check_files() -> None:
    missing = [p for p in REQUIRED_FILES if not os.path.isfile(os.path.join(HERE, p))]
    if missing:
        for name in missing:
            fail(f"missing file: {name}")
        return
    ok(f"all {len(REQUIRED_FILES)} required files present")

    for name in OPTIONAL_FILES:
        if not os.path.isfile(os.path.join(HERE, name)):
            print(
                f"[{PLUGIN_NAME}] note: {name} absent (created on first "
                "save); documented defaults apply"
            )

    # The manifest must not advertise extension points that no longer exist.
    stale = {
        "extensions/python/response_stream_end/_50_caveman_validate.py",
        "extensions/python/monologue_end/_70_caveman_stats.py",
        "extensions/python/message_loop_prompts_before/_60_caveman_shrink_tools.py",
    }
    for name in stale:
        if os.path.exists(os.path.join(HERE, name)):
            fail(f"removed extension still present: {name}")


# ---------------------------------------------------------------------------
# 2. Manifest / config
# ---------------------------------------------------------------------------


def check_manifest() -> None:
    try:
        import yaml
    except ImportError:
        yaml = None

    with open(os.path.join(HERE, "plugin.yaml"), "r", encoding="utf-8") as handle:
        text = handle.read()

    data = {}
    if yaml is not None:
        try:
            data = yaml.safe_load(text) or {}
        except Exception as exc:
            fail(f"plugin.yaml is not valid YAML: {exc}")
            return
    else:
        for line in text.splitlines():
            if ":" in line and not line.lstrip().startswith("#"):
                key, _, value = line.partition(":")
                data[key.strip()] = value.strip()

    name = str(data.get("name") or "").strip()
    if name != PLUGIN_NAME:
        fail(f"plugin name is {name!r}, expected {PLUGIN_NAME!r}")
    version = str(data.get("version") or "").strip()
    if version != EXPECTED_VERSION:
        fail(f"plugin.yaml version is {version!r}, expected {EXPECTED_VERSION!r}")
    else:
        ok(f"manifest name={name} version={version}")

    # hooks.py carried a hardcoded 0.1.0 while the manifest said 0.4.0.
    hooks = os.path.join(HERE, "hooks.py")
    with open(hooks, "r", encoding="utf-8") as handle:
        hooks_text = handle.read()
    if f'PLUGIN_VERSION = "{EXPECTED_VERSION}"' not in hooks_text:
        fail(f"hooks.py PLUGIN_VERSION does not match {EXPECTED_VERSION}")

    # config.json must not ship a key no backend reads.
    config_path = os.path.join(HERE, "config.json")
    if os.path.isfile(config_path):
        with open(config_path, "r", encoding="utf-8") as handle:
            config = json.load(handle)
        from usr.plugins.caveman.helpers import plugins_config as plugin_cfg

        known = set(plugin_cfg.DEFAULTS)
        for key in config:
            if key not in known:
                fail(
                    f"config.json has key {key!r} that no backend reads "
                    f"(known: {sorted(known)})"
                )
        if not failures:
            ok(f"config.json keys all read by the backend: {sorted(config)}")


# ---------------------------------------------------------------------------
# 3. Import every module the runtime actually loads
# ---------------------------------------------------------------------------


def _stub_framework():
    """Minimal stand-ins so modules import outside a full A0 runtime."""
    if "helpers.extension" in sys.modules:
        return

    class _Ext:
        def __init__(self, agent=None, **kwargs):
            self.agent = agent

    class _Handler:
        def __init__(self, app, lock):
            self.app = app
            self.lock = lock

    ext = types.ModuleType("helpers.extension")
    ext.Extension = _Ext
    api = types.ModuleType("helpers.api")
    api.ApiHandler = _Handler
    helpers = types.ModuleType("helpers")
    helpers.__path__ = []
    settings = types.ModuleType("helpers.settings")
    settings.get_settings = lambda: {
        "workdir_path": os.path.join(ROOT, "usr", "workdir")
    }
    files = types.ModuleType("helpers.files")
    files.get_abs_path = lambda *parts: os.path.join(ROOT, *parts)
    files.get_abs_path_dockerized = files.get_abs_path

    sys.modules.setdefault("helpers", helpers)
    sys.modules.setdefault("helpers.extension", ext)
    sys.modules.setdefault("helpers.api", api)
    sys.modules.setdefault("helpers.settings", settings)
    sys.modules.setdefault("helpers.files", files)

    # plugins_config imports helpers.plugins lazily; provide it if absent.
    #
    # The signature must accept `agent=`: a narrower one raises TypeError,
    # plugins_config.get_config() swallows it, and every setting silently
    # falls back to defaults. That is a real bug this stub once hid.
    if "helpers.plugins" not in sys.modules:
        plugins_mod = types.ModuleType("helpers.plugins")
        plugins_mod.get_plugin_config = lambda name, agent=None, **kw: None
        # The coexistence and migration checks need these to exist. Reporting
        # "not installed" exercises the normal path; leaving them off would
        # only prove the error path.
        plugins_mod.find_plugin_dir = lambda name: None
        plugins_mod.get_toggle_state = lambda name: "disabled"
        plugins_mod.find_plugin_assets = lambda *a, **k: []
        sys.modules["helpers.plugins"] = plugins_mod


def _load_module(relative: str, alias: str):
    path = os.path.join(HERE, relative)
    name = f"_caveman_check_{alias}"
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {relative}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def check_imports() -> dict:
    _stub_framework()
    if ROOT not in sys.path:
        sys.path.insert(0, ROOT)

    modules = {}
    targets = {
        "state": "helpers/state.py",
        "compat": "helpers/compat.py",
        "plugins_config": "helpers/plugins_config.py",
        "compress": "helpers/compress.py",
        "markdown": "helpers/markdown.py",
        "prompts": "helpers/prompts.py",
        "command": "extensions/python/monologue_start/_30_caveman_command.py",
        "validate": "extensions/python/message_loop_result/_50_caveman_validate.py",
        "observe": "extensions/python/message_loop_result/_60_caveman_observe.py",
        "shrink": "extensions/python/chat_model_call_before/_60_caveman_shrink_tools.py",
        "style": "extensions/python/system_prompt/_20_caveman_style.py",
        "discovery": "extensions/python/banners/_10_caveman_discovery.py",
        "api_state": "api/caveman_state.py",
        "api_stats": "api/caveman_stats.py",
    }
    for alias, relative in targets.items():
        try:
            modules[alias] = _load_module(relative, alias)
        except Exception as exc:
            fail(f"cannot import {relative}: {type(exc).__name__}: {exc}")
    if not failures:
        ok(f"all {len(targets)} runtime modules import")
    return modules


# ---------------------------------------------------------------------------
# 4. Compile every shipped Python file
# ---------------------------------------------------------------------------


def check_syntax() -> None:
    """Compile every shipped Python file in memory.

    Uses `compile()` rather than `py_compile` so nothing is written to disk
    (and so it works on Windows, where py_compile refuses os.devnull as a
    target).
    """
    count = 0
    for base, _dirs, names in os.walk(HERE):
        if "__pycache__" in base:
            continue
        for name in names:
            if not name.endswith(".py") or name.startswith("tmp_probe_"):
                continue
            full = os.path.join(base, name)
            try:
                with open(full, "r", encoding="utf-8") as handle:
                    compile(handle.read(), full, "exec")
                count += 1
            except (SyntaxError, ValueError, UnicodeDecodeError) as exc:
                fail(f"syntax error in {os.path.relpath(full, HERE)}: {exc}")
    ok(f"{count} Python files compile")


# ---------------------------------------------------------------------------
# 5. State layer round-trip
# ---------------------------------------------------------------------------


def check_state(modules: dict) -> None:
    state = modules.get("state")
    if state is None:
        return True

    # In a standalone clone there is no Agent Zero workdir to stay inside, so
    # point the stub at a scratch directory rather than creating usr/workdir/
    # inside the repository. The round-trip is still exercised.
    if STANDALONE:
        import tempfile

        scratch = tempfile.mkdtemp(prefix="caveman-health-")
        expected_root = os.path.abspath(scratch)
        for name, module in list(sys.modules.items()):
            if name == "helpers.settings" and hasattr(module, "get_settings"):
                module.get_settings = lambda: {"workdir_path": expected_root}
    else:
        expected_root = os.path.abspath(os.path.join(ROOT, "usr", "workdir"))

    path = state.state_path()
    # os.sep-aware: a sibling directory sharing the prefix (.../workdir-evil)
    # must not pass a raw startswith.
    path_abs = os.path.abspath(path)
    prefix = os.path.join(expected_root, "")
    if path_abs != expected_root and not path_abs.startswith(prefix):
        fail(
            f"state path {path!r} is outside the Agent Zero workdir "
            f"{expected_root!r}"
        )
        return

    chat = "_caveman_healthcheck_" + str(os.getpid())
    try:
        if not state.set_state(chat, level="ultra", enabled=True):
            fail("set_state returned False")
            return
        got = state.resolve(chat, {"enabled": False, "level": "lite"})
        if got != {"enabled": True, "level": "ultra"}:
            fail(f"roundtrip mismatch: {got!r}")
            return

        state.set_state(chat, enabled=False)
        got = state.resolve(chat, {"enabled": True, "level": "lite"})
        if got["enabled"] is not False or got["level"] != "ultra":
            fail(f"disable did not stick: {got!r}")
            return

        if state.set_level(chat, "off"):
            fail("set_level accepted 'off', which is not a storable level")

        state.record_turn(chat, "ultra", 120)
        stats = state.get_stats(chat)
        if stats.get("turns") != 1 or stats.get("chars") != 120:
            fail(f"record_turn mismatch: {stats!r}")
            return
        if "est_tokens_saved" in stats:
            fail("stats still carry a fabricated 'est_tokens_saved' field")
            return
        # The per-level ratio table is what made the old estimate self-fulfilling.
        if hasattr(state, "REDUCTION_FRACTION"):
            fail(
                "helpers/state.py defines REDUCTION_FRACTION; the retracted "
                "per-level ratio must not come back"
            )
            return
        summary = state.summary()
        for forbidden in ("est_tokens_saved", "saved", "reduction", "pct_saved"):
            if forbidden in summary:
                fail(f"summary exposes a fabricated field: {forbidden!r}")
                return
        state.reset_stats(chat)
        ok("state + observation round-trip works (set, resolve, record, reset)")
    finally:
        try:
            state.set_state(chat, level=None, enabled=None)
            state.reset_stats(chat)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# 6. API action contract (the WebUI/backend mismatch that shipped broken)
# ---------------------------------------------------------------------------


def check_api_contract(modules: dict) -> None:
    api = modules.get("api_state")
    if api is None:
        return

    import asyncio

    handler = api.CavemanState(None, None)
    chat = "_caveman_healthcheck_api_" + str(os.getpid())

    def call(**payload):
        return asyncio.run(handler.process(payload, None))

    try:
        api.caveman_state.set_state(chat, level=None, enabled=None)

        result = call(action="set", chat_id=chat, level="ultra")
        if not result.get("ok"):
            fail(f"'set' action rejected: {result.get('error')}")
            return
        if result.get("level") != "ultra" or result.get("enabled") is not True:
            fail(f"'set' did not apply level+enabled: {result!r}")
            return

        result = call(action="set", chat_id=chat, level="off")
        if result.get("enabled") is not False:
            fail(f"'set off' did not disable: {result!r}")
            return

        for action in FRONTEND_ACTIONS:
            if action == "list":
                probe = call(action="list")
            elif action == "set":
                # "set" requires a payload; probe it the way the dropdown does.
                probe = call(action="set", chat_id=chat, level="full")
            else:
                probe = call(action=action, chat_id=chat)
            if not probe.get("ok"):
                fail(f"frontend action {action!r} not served: {probe.get('error')}")

        result = call(action="bogus", chat_id=chat)
        if result.get("ok") is not False or "unknown action" not in str(
            result.get("error")
        ):
            fail(f"unknown action was not rejected: {result!r}")

        result = call(action="set", chat_id=chat, level="nope")
        if result.get("ok") is not False:
            fail(f"invalid level was not rejected: {result!r}")

        result = call(action="set", chat_id=chat, enabled="yes")
        if result.get("ok") is not False:
            fail(f"non-boolean enabled was not rejected: {result!r}")

        ok(f"API serves every WebUI action {list(FRONTEND_ACTIONS)} and rejects bad input")
    finally:
        try:
            api.caveman_state.set_state(chat, level=None, enabled=None)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# 7. Command classification (the feature that could never match)
# ---------------------------------------------------------------------------


def check_commands(modules: dict) -> None:
    command = modules.get("command")
    if command is None:
        return

    cases = [
        ("/caveman", "on_default"),
        ("/caveman lite", "set_level"),
        ("/caveman full", "set_level"),
        ("/caveman ultra", "set_level"),
        ("/caveman wenyan-lite", "set_level"),
        ("/caveman wenyan-full", "set_level"),
        ("/caveman wenyan-ultra", "set_level"),
        ("/caveman off", "off"),
        ("/caveman stop", "off"),
        ("/caveman on", "on"),
        ("talk like caveman", "on_default"),
        ("use caveman", "on_default"),
        ("caveman on", "on_default"),
        ("normal mode", "off"),
        ("stop caveman", "off"),
        ("disable caveman", "off"),
        ("/Caveman ULTRA", "set_level"),
        # must NOT match
        ("please fix the failing test", None),
        ("caveman", None),
        ("/caveman nonsense", None),
        ("/caveman lite extra words here", "set_level"),
    ]
    bad = []
    for text, expected in cases:
        result = command._classify(text)
        action = result[0] if result else None
        if action != expected:
            bad.append(f"{text!r} -> {result!r}, expected {expected!r}")
    if bad:
        for line in bad:
            fail(f"command misclassified: {line}")
        return

    # The user message is a history.Message, not a str. Prove the extractor
    # reads it, since the previous revision could never do so.
    class _Msg:
        def __init__(self, text):
            self._text = text

        def output_text(self):
            return self._text

    class _Loop:
        def __init__(self, message):
            self.user_message = message

    extracted = command._get_latest_user_text(_Loop(_Msg("/caveman ultra")))
    if extracted != "/caveman ultra":
        fail(f"_get_latest_user_text returned {extracted!r}")
        return
    if command._get_latest_user_text(_Loop(None)) != "":
        fail("_get_latest_user_text did not tolerate a missing message")
        return

    ok(f"all {len(cases)} command cases classify correctly; user text is readable")


# ---------------------------------------------------------------------------
# 8. WebUI/backend agreement
# ---------------------------------------------------------------------------


def _strip_js_comments(source: str) -> str:
    """Remove /* */ blocks and whole-line // comments.

    Deliberately conservative: it only drops a line comment when the line is
    a comment in its entirety. Trailing comments stay, because a claim in a
    comment is exactly what these checks must not be confused by, and the
    checks below look for code shapes that only appear in real statements.
    """
    out = []
    in_block = False
    for line in source.splitlines():
        stripped = line.strip()
        if in_block:
            if "*/" in stripped:
                in_block = False
            continue
        if stripped.startswith("/*"):
            if "*/" not in stripped:
                in_block = True
            continue
        if stripped.startswith("//"):
            continue
        out.append(line)
    return "\n".join(out)


def _webui_sources():
    """Yield (relative path, text) for every WebUI asset.

    Covers webui/**.js|.html and the extension-injected page-head HTML, so the
    Headroom settings page and its stores get the same scrutiny as the Caveman
    dropdown. JS comments are stripped first so a route or claim quoted in a
    comment cannot be mistaken for shipped code.
    """
    bases = (os.path.join(HERE, "webui"), os.path.join(HERE, "extensions", "webui"))
    for base in bases:
        for dirpath, _dirnames, filenames in os.walk(base):
            if "__pycache__" in dirpath:
                continue
            for name in sorted(filenames):
                if not name.endswith((".js", ".html")):
                    continue
                full = os.path.join(dirpath, name)
                rel = os.path.relpath(full, HERE).replace(os.sep, "/")
                with open(full, "r", encoding="utf-8") as handle:
                    text = handle.read()
                if name.endswith(".js"):
                    text = _strip_js_comments(text)
                yield rel, text


def _bare_fetch_calls(js: str):
    """(line, args) for every fetch( call that does not route through the
    CSRF-aware helper.

    Per call site, not per file: a file that migrates one endpoint to fetchApi
    while leaving a bare fetch() on another passed the old file-level
    "fetchApi exists anywhere" short-circuit.
    """
    offenders = []
    for match in re.finditer(r"\bfetch\(", js):
        depth = 0
        end = None
        for i in range(match.end() - 1, len(js)):
            ch = js[i]
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    end = i
                    break
        args = js[match.end() : end if end is not None else match.end() + 120]
        if "fetchApi" not in args:
            line = js.count("\n", 0, match.start()) + 1
            offenders.append((line, args[:48]))
    return offenders


def check_webui(modules: dict) -> None:
    api = modules.get("api_state")
    if api is None:
        return

    import re

    js_path = os.path.join(HERE, "webui", "caveman-dropdown.js")
    with open(js_path, "r", encoding="utf-8") as handle:
        js = _strip_js_comments(handle.read())

    # Match any identifier shape, not just snake_case: a camelCase or
    # kebab-case action name would slip past a [a-z_]+ pattern and hide the
    # exact frontend/backend mismatch this check exists to catch.
    sent = set(re.findall(r'action:\s*"([A-Za-z0-9_-]+)"', js))
    sent |= set(re.findall(r"api\(\s*[\"']([A-Za-z0-9_-]+)[\"']", js))
    unknown = sorted(a for a in sent if a not in FRONTEND_ACTIONS)
    if unknown:
        fail(f"dropdown sends actions the API does not implement: {unknown}")

    bare = _bare_fetch_calls(js)
    for line_no, args in bare:
        fail(
            f"dropdown line {line_no} uses a bare fetch({args!r}...); "
            "it must use fetchApi, which attaches the CSRF token"
        )

    for match in re.finditer(r"setInterval\((?:[^()]|\([^()]*\))*?,\s*(\d+)\s*\)", js):
        if int(match.group(1)) < 5000:
            fail(f"dropdown polls every {match.group(1)}ms")

    # Clearing an element's innerHTML is what destroys the framework's
    # extension slot. Setting innerHTML on a node the script just created
    # (e.g. an icon span) is fine.
    if re.search(r"\.innerHTML\s*=\s*(\"\"|''|\"\s*\")", js):
        fail("dropdown clears an element's innerHTML, which destroys the host extension slot")

    # config.html must not bind keys the backend ignores.
    config_html = os.path.join(HERE, "webui", "config.html")
    with open(config_html, "r", encoding="utf-8") as handle:
        html = handle.read()
    bound = set(re.findall(r'x-model(?:\.number)?="config\.([A-Za-z0-9_]+)"', html))
    plugins_config = modules.get("plugins_config")
    if plugins_config is not None:
        dead = sorted(bound - set(plugins_config.DEFAULTS))
        if dead:
            fail(
                f"config.html binds keys no backend reads: {dead} "
                f"(known: {sorted(plugins_config.DEFAULTS)})"
            )
        else:
            ok(f"config.html binds only real settings: {sorted(bound)}")

    # The Headroom half ships its own settings page and stores. Its bindings
    # must name real headroom settings, and every plugin asset it references
    # must exist - the same frontend/backend contract config.html is held to,
    # which previously shipped a binding nothing read.
    headroom_defaults = {}
    if plugins_config is not None:
        section = plugins_config.DEFAULTS.get("headroom")
        if isinstance(section, dict):
            headroom_defaults = section
    bad_bindings = []
    missing_assets = []
    for rel, text in _webui_sources():
        for key_path in re.findall(
            r"config\.headroom\.([A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)*)", text
        ):
            node = headroom_defaults
            for part in key_path.split("."):
                if not isinstance(node, dict) or part not in node:
                    bad_bindings.append(f"{rel}: config.headroom.{key_path}")
                    break
                node = node[part]
        for match in re.finditer(
            r"[\"'(]/plugins/caveman/([A-Za-z0-9_./?=&-]+)", text
        ):
            tail = re.split(r"[?&]", match.group(1))[0]
            if not tail.endswith((".html", ".js", ".css", ".svg", ".png")):
                continue  # API route, not an asset
            if not os.path.isfile(os.path.join(HERE, *tail.split("/"))):
                missing_assets.append(f"{rel}: /plugins/caveman/{tail}")
    if bad_bindings:
        for entry in sorted(set(bad_bindings)):
            fail(f"WebUI binds a headroom setting no backend reads: {entry}")
    elif headroom_defaults:
        ok("headroom-config.html binds only real headroom settings")
    if missing_assets:
        for entry in missing_assets:
            fail(f"WebUI references a plugin asset that does not exist: {entry}")

    if not failures:
        ok("WebUI actions, CSRF, polling and settings bindings all agree")


# ---------------------------------------------------------------------------
# 9. Artifacts
# ---------------------------------------------------------------------------


def check_icon() -> None:
    path = os.path.join(HERE, "icon.svg")
    with open(path, "r", encoding="utf-8") as handle:
        body = handle.read()
    # A real element, not any occurrence of the substring: "<svg" inside a
    # comment or an attribute value is not an icon.
    if not re.search(r"<svg[\s>]", body):
        fail("icon.svg has no <svg> element")
    else:
        ok(f"icon.svg present ({len(body)} bytes)")


def check_agents() -> None:
    profiles = ("cavecrew-investigator", "cavecrew-builder", "cavecrew-reviewer")
    for name in profiles:
        base = os.path.join(HERE, "agents", name)
        yaml_path = os.path.join(base, "agent.yaml")
        specifics = os.path.join(base, "prompts", "agent.system.main.specifics.md")
        if not os.path.isfile(yaml_path) or not os.path.isfile(specifics):
            fail(f"subagent {name} is missing agent.yaml or its specifics prompt")
            continue
        # `agent.system.main.role.md` is the framework's inherited base-role
        # prompt. A profile that ships it replaces the base role wholesale;
        # the designated layering slot is `specifics.md`.
        if os.path.isfile(os.path.join(base, "prompts", "agent.system.main.role.md")):
            fail(
                f"subagent {name} ships agent.system.main.role.md; use "
                f"agent.system.main.specifics.md so the base role is layered, "
                f"not replaced"
            )
    ok("3 cavecrew subagent profiles use the specifics.md prompt slot")


# A profile cannot remove tools from a subordinate: SubAgent is
# title/description/context/prompts, with no tool field. So a role prompt that
# tells the model a tool is unavailable states something false, and the model
# may either waste turns looking for it or assume it is safely prevented.
TOOL_CLAIM_PATTERNS = (
    re.compile(r"`\w+`\s+(?:is\s+)?(?:not\s+available|unavailable)", re.I),
    re.compile(r"no\s+`\w+`\s+available", re.I),
)


def check_tool_claims() -> None:
    offenders = []
    base = os.path.join(HERE, "agents")
    for profile in sorted(os.listdir(base)) if os.path.isdir(base) else []:
        prompt = os.path.join(base, profile, "prompts", "agent.system.main.specifics.md")
        if not os.path.isfile(prompt):
            continue
        with open(prompt, "r", encoding="utf-8") as handle:
            lines = handle.read().splitlines()
        for index, line in enumerate(lines):
            window = "\n".join(lines[max(0, index - 1) : index + 2])
            if CLAIM_ALLOW in window:
                continue
            for pattern in TOOL_CLAIM_PATTERNS:
                match = pattern.search(line)
                if match:
                    rel = os.path.relpath(prompt, HERE).replace(os.sep, "/")
                    offenders.append(f"{rel}:{index + 1}: {match.group(0)!r}")
    if offenders:
        for line in offenders:
            fail(
                f"role prompt claims a tool is unavailable at {line}; "
                f"agent.yaml has no tool field, so state the limit as self-imposed"
            )
    else:
        ok("no role prompt claims a tool is unavailable")


# Unverifiable savings claims. Upstream retracted its fixed 65% ratio
# (docs/HONEST-NUMBERS.md), so no user-facing file in this plugin may assert a
# percentage reduction that no committed measurement supports.
#
# Both word orders are matched, and the words are kept loose on purpose. A
# narrow pattern such as `cuts?\s+\d+%` missed the published hub description
# "Cuts output tokens by roughly 65%", which is exactly the phrasing a store
# reviewer would read.
_REDUCE_WORD = r"(?:cut|cuts|cutting|reduc\w*|sav\w*|shorten\w*|shrink\w*|less|fewer|smaller|shorter|trim\w*)"
# The unit may be a % sign or spelled out; "cuts tokens by 65 percent" is the
# same unverifiable claim as "cuts tokens by 65%".
_PCT = r"(?:%|percent\b|pct\b)"
CLAIM_PATTERNS = (
    re.compile(rf"\b{_REDUCE_WORD}\b[^.]{{0,40}}?\b\d{{1,3}}\s?{_PCT}", re.I),
    re.compile(rf"\b\d{{1,3}}\s?{_PCT}[^.]{{0,40}}?\b{_REDUCE_WORD}\b", re.I),
    # A bare ratio table, e.g. "`full`: ~65%". A skill shipped exactly this and
    # the two patterns above missed it because the reduction word sat on a
    # different line from the number.
    re.compile(rf"^\s*(?:[-*]\s*)?`[a-z0-9_-]+`\s*[:=]\s*~?\d{{1,3}}\s?{_PCT}", re.I),
)

# A line may opt out with a trailing `claim-guard: allow` comment, or the line
# above it may carry one. Needed when a file has to *quote* the retracted
# number in order to explain that it was retracted. The marker is explicit so
# the exemption is auditable, rather than a heuristic that quietly allows any
# sentence containing a retraction keyword.
CLAIM_ALLOW = "claim-guard: allow"

# Directories whose text is executable or generated, not user-facing prose.
# ccr/ stats/ cache/ are runtime state created next to the plugin; .git holds
# history, none of it shipped prose.
CLAIM_SKIP_DIRS = ("__pycache__", "benchmarks", "tests", ".git", "ccr", "stats", "cache")

# User-facing text also ships inside .html and .js assets (settings pages,
# stores, the injected page-head), and inside the banner extensions' strings.
CLAIM_TEXT_EXTS = (".md", ".yaml", ".yml", ".json", ".html", ".js")


def check_claims() -> None:
    offenders = []

    def scan(rel: str, text: str) -> None:
        lines = text.splitlines()
        for index, line in enumerate(lines):
            # The marker may sit on the matched line, the one above it, or
            # the one below it (annotating a line you just quoted).
            window = "\n".join(lines[max(0, index - 1) : index + 2])
            if CLAIM_ALLOW in window:
                continue
            for pattern in CLAIM_PATTERNS:
                match = pattern.search(line)
                if match:
                    offenders.append(
                        f"{rel}:{index + 1}: {match.group(0)!r} "
                        f"(add '{CLAIM_ALLOW}' if this line explains the retraction)"
                    )

    for base, dirs, names in os.walk(HERE):
        dirs[:] = [d for d in dirs if d not in CLAIM_SKIP_DIRS]
        for name in names:
            if not name.endswith(CLAIM_TEXT_EXTS):
                continue
            rel = os.path.relpath(os.path.join(base, name), HERE).replace(os.sep, "/")
            with open(os.path.join(base, name), "r", encoding="utf-8") as handle:
                scan(rel, handle.read())

    # The banner extensions carry the user-visible strings in .py files, which
    # the extension filter above deliberately skips (helper docstrings are not
    # prose and must not be scanned). Only banners ship model- or user-facing
    # text in .py, so only they are read here.
    banner_dir = os.path.join(HERE, "extensions", "python", "banners")
    if os.path.isdir(banner_dir):
        for name in sorted(os.listdir(banner_dir)):
            if not name.endswith(".py"):
                continue
            rel = f"extensions/python/banners/{name}"
            with open(os.path.join(banner_dir, name), "r", encoding="utf-8") as handle:
                scan(rel, handle.read())

    if offenders:
        for line in offenders:
            fail(f"unverified savings claim in {line}")
    else:
        ok("no unverified percentage savings claim in user-facing files")


def check_prompts(modules: dict) -> None:
    """Every level must parse from the single ruleset, and only its own.

    The port used to keep one prompt file per level, and the copies drifted:
    the base style restated the level rules in its own words, `ultra` forbade
    abbreviations the base file described separately, and `wenyan-full`
    advertised a reduction range no measurement supported. One file, filtered
    per level, removes the failure mode, but only if every marker still parses
    and no block leaks another level's heading.
    """
    prompts = modules.get("prompts")
    if prompts is None:
        return

    defined = prompts.available_levels()
    if not defined:
        fail("no intensity levels parsed from caveman.intensity.md")
        return

    # Parse the shipped markers directly as well. available_levels() filters
    # whatever it parses against VALID_LEVELS, so a block whose level is
    # outside the contract never reaches this check through it - the exact
    # copy-drift this branch exists to catch.
    with open(
        os.path.join(HERE, "prompts", "caveman.intensity.md"), "r", encoding="utf-8"
    ) as handle:
        intensity_src = handle.read()
    shipped = [
        level.lower()
        for level in re.findall(
            r"^\[intensity:([a-z0-9-]+)\]\s*$", intensity_src, re.M
        )
    ]

    expected = ("lite", "full", "ultra", "wenyan-lite", "wenyan-full", "wenyan-ultra")
    missing = [level for level in expected if level not in defined]
    if missing:
        fail(f"intensity ruleset has no block for: {missing}")
    extra = sorted(set(shipped) - set(expected))
    if extra:
        fail(f"intensity ruleset defines unknown level(s): {extra}")

    for level in defined:
        block = prompts.load_intensity(level)
        if not block:
            fail(f"level {level} parsed but its block is empty")
            continue
        if level not in block:
            fail(f"level {level} block does not name its own level")
        stray = [
            line
            for line in block.splitlines()
            if line.startswith("### ") and level not in line
        ]
        if stray:
            fail(f"level {level} block leaks another level's heading: {stray}")

        prompt = prompts.build_system_prompt(level)
        if not prompt:
            fail(f"build_system_prompt({level!r}) is empty")
            continue
        if f"<active_level>{level}</active_level>" not in prompt:
            fail(f"build_system_prompt({level!r}) is missing the level tag")
        if prompts.MARKER not in prompt:
            fail(f"build_system_prompt({level!r}) is missing the style block")
        if "<!--" in prompt:
            fail(f"build_system_prompt({level!r}) ships maintainer HTML comments")

    if prompts.build_system_prompt("not-a-level"):
        fail("build_system_prompt accepted an unknown level instead of failing closed")

    if not failures:
        ok(
            f"intensity ruleset parses and filters correctly for "
            f"{len(defined)} level(s)"
        )


def check_module_contract(modules: dict) -> bool:
    """Assert the cross-module API the extensions actually call.

    A directory can pass every per-file check and still fail at runtime if it
    mixes versions: an extension from v0.5.0 beside a `helpers/state.py` from
    v0.4.0 compiles, parses, and imports, then raises
    `AttributeError: module ... has no attribute 'resolve'` from inside
    `get_system_prompt` and kills the agent turn. The same happens with a
    `helpers/plugins_config.py` from v0.4.0, which raises
    `TypeError: get_config() got an unexpected keyword argument 'agent'`.

    Per-file checks cannot see that. This one asks the loaded modules what they
    call on each other, which is the question a partial upgrade gets wrong.
    """
    compat = modules.get("compat")
    if compat is None:
        return True
    state = modules.get("state")
    config = modules.get("plugins_config")
    if state is None or config is None:
        return True

    missing = compat.missing_state_api(state)
    if missing:
        fail(
            "helpers/state.py does not provide the API the shipped modules "
            f"call: {', '.join(missing)}"
        )
        return False

    missing = compat.missing_config_api(config)
    if missing:
        fail(
            "helpers/plugins_config.py does not provide the API the shipped "
            f"modules call: {', '.join(missing)}"
        )
        return False

    # Each caller must actually go through the guard, so a stale install fails
    # soft instead of raising out of an extension point.
    guarded = []
    for alias in (
        "style",
        "validate",
        "observe",
        "shrink",
        "command",
        "api_state",
        "api_stats",
    ):
        module = modules.get(alias)
        if module is None:
            continue
        try:
            source = _read_source(module)
        except OSError:
            continue
        # Require the guard only when the module actually reaches for that
        # sibling: the command extension reads state but not config, and
        # caveman_stats reads state but not config. Demanding both strings in
        # every caller would fail a module that legitimately touches only one.
        if "caveman_state" in source and "compat.state_api(" not in source:
            fail(
                f"{alias} calls the state module without compat.state_api(); "
                f"a partial upgrade would raise out of an extension point"
            )
            return False
        if (
            re.search(r"\bplugins_config\b|\bplugin_cfg\b", source)
            and "compat.config_api(" not in source
        ):
            fail(
                f"{alias} calls the config module without compat.config_api(); "
                f"a partial upgrade would raise TypeError out of an "
                f"extension point and stop the agent"
            )
            return False
        guarded.append(alias)

    if guarded:
        ok(f"cross-module contract holds; guarded callers: {guarded}")
    return True


def _read_source(module) -> str:
    path = getattr(module, "__file__", None)
    if not path:
        return ""
    with open(path, "r", encoding="utf-8") as handle:
        return handle.read()


def check_toggle() -> str:
    if os.path.isfile(os.path.join(HERE, ".toggle-1")):
        state = "ON"
    elif os.path.isfile(os.path.join(HERE, ".toggle-0")):
        state = "OFF"
    else:
        state = "DEFAULT (disabled - set enabled: true in config.json)"
    print(f"[{PLUGIN_NAME}] toggle state: {state}")
    return state


def check_unit_tests() -> None:
    """Run the plugin's own test suite.

    The port shipped with no tests, which is why three non-functional features
    survived a health check that only verified files existed and compiled. The
    suite is invoked as a subprocess so a failure here is a real failure rather
    than an import error in this script.
    """
    suite = os.path.join(HERE, "tests", "test_caveman.py")
    if not os.path.isfile(suite):
        fail("tests/test_caveman.py is missing")
        return
    suites = [suite]
    combined = os.path.join(HERE, "tests", "test_headroom_integration.py")
    if os.path.isfile(combined):
        suites.append(combined)
    try:
        for suite in suites:
            proc = subprocess.run(
                [sys.executable, suite], capture_output=True, text=True, timeout=300
            )
            if proc.returncode != 0:
                tail = (proc.stdout + proc.stderr).strip().splitlines()[-12:]
                for line in tail:
                    print(f"    {line}")
                fail(
                    f"unit tests failed: {os.path.basename(suite)} "
                    f"(exit {proc.returncode})"
                )
                return
            summary = (proc.stdout.strip().splitlines() or [""])[-1]
            ok(f"unit tests pass [{os.path.basename(suite)}]: {summary}")
    except Exception as exc:
        fail(f"could not run the unit test suite: {type(exc).__name__}: {exc}")


# ---------------------------------------------------------------------------




# ---------------------------------------------------------------------------
# 9. Headroom half of the combined plugin (roadmap P1/P2/P4)
# ---------------------------------------------------------------------------


def _plugin_files(*subpaths):
    for rel in subpaths:
        base = os.path.join(HERE, *rel.split("/"))
        if not os.path.isdir(base):
            continue
        for dirpath, _dirnames, filenames in os.walk(base):
            if "__pycache__" in dirpath:
                continue
            for name in sorted(filenames):
                if name.endswith((".py", ".js")):
                    yield os.path.join(dirpath, name)


def _banned_import_sites(path: str):
    """(line, reason) for import statements that reach the standalone package.

    AST-based, because a line scan misses the canonical form the check exists
    for: `from usr.plugins import headroom_compress` contains no
    "usr.plugins.headroom_compress" literal, parenthesised multi-line imports
    split the name across lines, and a dynamically built module name
    (`import_module("usr.plugins." + mod)`) never spells the package out.
    """
    import ast

    banned = "usr.plugins.headroom_compress"
    try:
        with open(path, "r", encoding="utf-8") as handle:
            tree = ast.parse(handle.read())
    except (OSError, SyntaxError, ValueError, UnicodeDecodeError):
        return []  # check_syntax already reports unparsable files

    hits = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == banned or alias.name.startswith(banned + "."):
                    hits.append((node.lineno, f"import {alias.name}"))
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module == banned or module.startswith(banned + "."):
                names = ", ".join(a.name for a in node.names)
                hits.append((node.lineno, f"from {module} import {names}"))
            elif module == "usr.plugins":
                for alias in node.names:
                    if alias.name == "headroom_compress":
                        hits.append(
                            (
                                node.lineno,
                                f"from usr.plugins import {alias.name}",
                            )
                        )
        elif isinstance(node, ast.Call):
            func = node.func
            fname = getattr(func, "attr", None) or getattr(func, "id", None)
            if fname not in ("import_module", "__import__"):
                continue
            texts = [
                n.value
                for n in ast.walk(node)
                if isinstance(n, ast.Constant) and isinstance(n.value, str)
            ]
            joined = "\n".join(texts)
            has_non_literal = any(
                not isinstance(arg, ast.Constant) for arg in node.args
            )
            if "headroom_compress" in joined:
                hits.append(
                    (
                        node.lineno,
                        f"{fname}() with a name containing headroom_compress",
                    )
                )
            elif has_non_literal and "usr.plugins" in joined:
                hits.append(
                    (
                        node.lineno,
                        f"{fname}() builds a usr.plugins.* module name "
                        f"dynamically; verify it cannot resolve to the "
                        f"standalone plugin",
                    )
                )
    return hits


def check_headroom_isolation() -> None:
    """No runtime module may import the standalone plugin's package.

    The whole point of the port is that the combined plugin owns its code,
    config, CCR database and stats. An import of `usr.plugins.headroom_compress`
    would quietly bind this plugin's behaviour to the other plugin's mutable
    config and caches - and it would keep working right up until the user
    edited something over there.
    """
    offenders = []
    scan_paths = [
        path
        for path in _plugin_files(
            "helpers", "api", "extensions", "tools", "skills", "benchmarks"
        )
        if path.endswith(".py")
    ]
    # Root scripts are plugin code too. execute.py is excluded on purpose: it
    # carries the banned name because this check exists.
    scan_paths.extend(
        os.path.join(HERE, name)
        for name in ("hooks.py", "install.py", "headroom_setup.py")
    )
    for path in scan_paths:
        for lineno, reason in _banned_import_sites(path):
            offenders.append(f"{os.path.relpath(path, HERE)}:{lineno}: {reason}")
    if offenders:
        fail(
            "combined plugin imports the standalone plugin's package "
            f"(P1.2): {offenders}"
        )
        return

    # Same rule for the WebUI: a stale plugin segment in a URL 404s at runtime.
    # Literal segments, template literals and "/plugins/" + name concatenations
    # are all checked - a dynamic segment cannot be verified statically, so it
    # fails closed.
    for rel, src in _webui_sources():
        for plugin in re.findall(r"/plugins/([A-Za-z0-9_]+)/", src):
            if plugin != PLUGIN_NAME:
                fail(
                    f"{rel} calls /plugins/{plugin}/; "
                    "combined-plugin routes live under "
                    f"/plugins/{PLUGIN_NAME}/"
                )
                return
        if re.search(r"[\"'`]/plugins/[\"'`]\s*\+", src) or re.search(
            r"`/plugins/\$\{", src
        ):
            fail(
                f"{rel} builds a /plugins/ route dynamically; the plugin "
                "segment cannot be verified, use an explicit "
                f"/plugins/{PLUGIN_NAME}/ path"
            )
            return
    ok("no combined-plugin module or WebUI asset references the standalone plugin")



def check_headroom_config_parity() -> None:
    """default_config.yaml and DEFAULTS must describe the same settings.

    A key in the YAML that DEFAULTS does not list is a setting the user can
    change and nothing reads. A key in DEFAULTS that the YAML omits is a
    setting that exists only in code. Either one is a silent lie in the
    settings UI.
    """
    from usr.plugins.caveman.helpers import plugins_config as plugin_cfg

    defaults = plugin_cfg.DEFAULTS.get("headroom")
    if not isinstance(defaults, dict):
        fail("plugins_config.DEFAULTS has no 'headroom' section")
        return

    try:
        import yaml

        with open(os.path.join(HERE, "default_config.yaml"), "r", encoding="utf-8") as h:
            raw = yaml.safe_load(h) or {}
    except Exception as exc:
        fail(f"could not read default_config.yaml: {exc}")
        return

    section = raw.get("headroom")
    if not isinstance(section, dict):
        fail("default_config.yaml has no 'headroom' section")
        return

    # The Caveman top-level keys are held to the same standard as the headroom
    # section: a YAML key DEFAULTS does not list is a setting the user can
    # change and nothing reads.
    top_yaml = set(raw) - {"headroom"}
    top_code = set(plugin_cfg.DEFAULTS) - {"headroom"}
    if top_yaml != top_code:
        fail(
            "top-level settings disagree between default_config.yaml and "
            f"DEFAULTS: yaml-only={sorted(top_yaml - top_code)} "
            f"code-only={sorted(top_code - top_yaml)}"
        )
        return

    yaml_keys = set(section)
    code_keys = set(defaults)
    if yaml_keys != code_keys:
        fail(
            "headroom settings disagree between default_config.yaml and "
            f"DEFAULTS: yaml-only={sorted(yaml_keys - code_keys)} "
            f"code-only={sorted(code_keys - yaml_keys)}"
        )
        return

    yaml_proxy = set(section.get("proxy") or {})
    code_proxy = set(defaults.get("proxy") or {})
    if yaml_proxy != code_proxy:
        fail(
            "headroom.proxy settings disagree between default_config.yaml "
            f"and DEFAULTS: yaml-only={sorted(yaml_proxy - code_proxy)} "
            f"code-only={sorted(code_proxy - yaml_proxy)}"
        )
        return

    # Defaults must be safe: compression off, protections on.
    if defaults.get("enabled") is not False:
        fail("headroom.enabled must default to false (independent opt-in)")
    for key in ("protect_reads", "protect_code", "ccr_enabled"):
        if defaults.get(key) is not True:
            fail(f"headroom.{key} must default to true")

    ok(
        f"settings agree across YAML and DEFAULTS "
        f"(top level: {len(top_code)} keys; headroom: {len(code_keys)} keys)"
    )


def check_registration_uniqueness() -> None:
    """One Extension per file, one ApiHandler per file, unique banner ids.

    Agent Zero registers classes[0] per file, so a second class is dead code
    that looks alive; a duplicated banner id renders twice.
    """
    import ast

    def subclasses(path, base):
        with open(path, "r", encoding="utf-8") as handle:
            tree = ast.parse(handle.read())
        found = []
        for node in tree.body:
            if not isinstance(node, ast.ClassDef):
                continue
            for b in node.bases:
                name = b.id if isinstance(b, ast.Name) else getattr(b, "attr", "")
                if name == base:
                    found.append(node.name)
                    break
        return found

    problems = []
    for path in _plugin_files("extensions"):
        found = subclasses(path, "Extension")
        if len(found) != 1:
            problems.append(
                f"{os.path.relpath(path, HERE)} has {len(found)} Extension classes"
            )
    for path in _plugin_files("api"):
        found = subclasses(path, "ApiHandler")
        if len(found) != 1:
            problems.append(
                f"{os.path.relpath(path, HERE)} has {len(found)} ApiHandler classes"
            )
    if problems:
        fail("duplicate registration risk: " + "; ".join(problems))
        return

    # Banner ids may be literals in the banner file or constants defined in
    # helpers (the coexistence banner imports BANNER_ID from
    # helpers/headroom/coexistence.py). Collect both, statically, or a
    # duplicate constant is invisible.
    helper_constants: dict = {}
    for path in _plugin_files("helpers"):
        if not path.endswith(".py"):
            continue
        try:
            with open(path, "r", encoding="utf-8") as handle:
                tree = ast.parse(handle.read())
        except (OSError, SyntaxError, ValueError, UnicodeDecodeError):
            continue
        for node in tree.body:
            if (
                isinstance(node, ast.Assign)
                and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and node.targets[0].id in ("BANNER_ID", "CARD_ID")
                and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, str)
            ):
                helper_constants.setdefault(node.targets[0].id, set()).add(
                    node.value.value
                )

    ids = []
    for path in _plugin_files("extensions/python/banners"):
        with open(path, "r", encoding="utf-8") as handle:
            src = handle.read()
        ids.extend(re.findall(r'"id":\s*"([a-z0-9_-]+)"', src))
        ids.extend(
            re.findall(r'^(?:CARD_ID|BANNER_ID)\s*=\s*"([a-z0-9_-]+)"', src, re.M)
        )
        try:
            tree = ast.parse(src)
        except (SyntaxError, ValueError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Dict):
                continue
            for key, value in zip(node.keys, node.values):
                if not (
                    isinstance(key, ast.Constant)
                    and key.value == "id"
                    and isinstance(value, (ast.Name, ast.Attribute))
                ):
                    continue
                const_name = (
                    value.id if isinstance(value, ast.Name) else value.attr
                )
                if const_name in ("BANNER_ID", "CARD_ID"):
                    ids.extend(sorted(helper_constants.get(const_name, ())))
    duplicates = sorted({i for i in ids if ids.count(i) > 1})
    if duplicates:
        fail(f"duplicate banner ids: {duplicates}")
        return
    ok(
        f"one class per extension/API file; {len(ids)} banner ids are unique "
        f"({sorted(set(ids))})"
    )


def _swap_framework_modules():
    """Swap stub framework modules for the real ones when they import.

    Returns (mode, saved). "live" means the real helpers.* are now in
    sys.modules and the migration/coexistence checks read the real plugin
    registry; "stub" means the real framework could not be imported from this
    interpreter (standalone clone, or missing framework dependencies), the
    stubs were kept, and no claim about the install may be reported from them.
    """
    saved = {}
    for key in list(sys.modules):
        if key == "helpers" or key.startswith("helpers."):
            saved[key] = sys.modules.pop(key)
    try:
        import helpers.plugins  # noqa: F401 - the registry the checks read

        return "live", saved
    except Exception:
        # Drop any half-imported real helpers, then put the stubs back so the
        # rest of the process sees exactly the state it had before.
        for key in list(sys.modules):
            if (key == "helpers" or key.startswith("helpers.")) and key not in saved:
                del sys.modules[key]
        sys.modules.update(saved)
        return "stub", saved


def check_headroom_live() -> None:
    """Exercise the migration and coexistence modules for real.

    A preview must not write. This runs against the user's real config paths,
    which is why it stops at preview: an apply here would be exactly the
    surprise the roadmap forbids.

    The report says which mode ran. Stub data cannot tell whether the
    standalone plugin is installed, so in stub mode no presence claim is made
    at all - the old wording reported "standalone not installed" from a stub
    that hardcodes find_plugin_dir() -> None, which is a fabricated fact.
    """
    mode, saved_modules = _swap_framework_modules()
    try:
        _check_headroom_live(mode)
    finally:
        for key in list(sys.modules):
            if (key == "helpers" or key.startswith("helpers.")) and key not in (
                saved_modules
            ):
                del sys.modules[key]
        sys.modules.update(saved_modules)


def _check_headroom_live(mode: str) -> None:
    try:
        from usr.plugins.caveman.helpers.headroom import coexistence, migration
    except Exception as exc:
        fail(f"could not import the headroom helpers: {type(exc).__name__}: {exc}")
        return

    try:
        plan = migration.build_plan()
    except Exception as exc:
        fail(f"migration.build_plan() raised: {type(exc).__name__}: {exc}")
        return

    for key in ("sources", "to_import", "conflicts", "notes", "warnings"):
        if key not in plan:
            fail(f"migration plan is missing the {key!r} key")
            return

    dest = plan["destination"]
    if not dest.get("readable"):
        fail(f"plugin config.json is not readable: {dest.get('error')}")
        return

    try:
        report = coexistence.overlap()
    except Exception as exc:
        fail(f"coexistence.overlap() raised: {type(exc).__name__}: {exc}")
        return
    if "overlaps" not in report:
        fail("coexistence report has no 'overlaps' key")
        return

    if mode == "live":
        if report.get("overlaps"):
            print(
                f"[{PLUGIN_NAME}] note: the standalone headroom_compress "
                "plugin is enabled alongside this one. Nothing was changed - "
                "see the coexistence warning and the plugin README."
            )
        standalone = plan.get("standalone") or {}
        if standalone.get("present"):
            where = "present"
        elif standalone.get("lookup_error"):
            where = "presence unknown (registry unavailable)"
        else:
            where = "not installed"
        if standalone.get("lookup_error"):
            print(f"[{PLUGIN_NAME}] note: {standalone['lookup_error']}")
        detail = (
            f"live framework; standalone {where}; "
            f"{len(plan['sources'])} source scope(s), "
            f"{len(plan['to_import'])} importable key(s), "
            f"{len(plan['conflicts'])} conflict(s)"
        )
    else:
        print(
            f"[{PLUGIN_NAME}] note: framework helpers unavailable from this "
            "interpreter; migration and coexistence ran against stubs, so "
            "standalone-plugin detection is unavailable (no claim is made "
            "about whether the standalone plugin is installed)."
        )
        detail = "stub: framework unavailable; standalone detection unavailable"
    ok(f"migration preview and coexistence detection work ({detail})")


# ---------------------------------------------------------------------------


def finish(state: str) -> int:
    """Print the summary block and return the process exit code."""
    print()
    print("=" * 68)
    if failures:
        print(f" {PLUGIN_NAME} v{EXPECTED_VERSION} - health check FAILED")
        for line in failures:
            print(f"   - {line}")
        print("=" * 68)
        return 1
    print(f" {PLUGIN_NAME} v{EXPECTED_VERSION} - health check PASSED")
    print(f" toggle state: {state}")
    print(" stats are raw observations only; run benchmarks/run.py for a real A/B")
    print("=" * 68)
    return 0


def main() -> int:
    print(f"[{PLUGIN_NAME}] plugin dir: {HERE}")
    print(f"[{PLUGIN_NAME}] agent zero root: {ROOT}")

    check_files()
    check_manifest()
    check_syntax()
    modules = check_imports()

    # Must run before anything that touches the state module. A partial upgrade
    # used to crash this script with the very AttributeError it should have
    # explained, because check_state called state.resolve before the contract
    # was verified. A diagnostic cannot be allowed to fail the way its subject
    # fails.
    contract_ok = check_module_contract(modules)
    if not contract_ok:
        print()
        print(
            f"[{PLUGIN_NAME}] stopping: the remaining checks call the state "
            f"module directly and would fail the same way."
        )
        return finish(check_toggle())

    check_state(modules)
    check_api_contract(modules)
    check_commands(modules)
    check_webui(modules)
    check_icon()
    check_agents()
    check_tool_claims()
    check_prompts(modules)
    check_claims()
    check_headroom_isolation()
    check_headroom_config_parity()
    check_registration_uniqueness()
    check_headroom_live()
    check_unit_tests()
    return finish(check_toggle())


if __name__ == "__main__":
    sys.exit(main())
