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
"""

import importlib.util
import json
import os
import re
import subprocess
import sys
import types

PLUGIN_NAME = "caveman"
EXPECTED_VERSION = "0.5.0"

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
    "config.json",
    "hooks.py",
    "LICENSE",
    "icon.svg",
    "install.py",
    # helpers
    "helpers/state.py",
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
    if "helpers.plugins" not in sys.modules:
        plugins_mod = types.ModuleType("helpers.plugins")
        plugins_mod.get_plugin_config = lambda name: None
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
        return

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
    if not os.path.abspath(path).startswith(expected_root):
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

    if re.search(r"(?<!fetchApi\()\bfetch\(", js) and "fetchApi" not in js:
        fail("dropdown uses a bare fetch(); it must use fetchApi for the CSRF token")

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

    if not failures:
        ok("WebUI actions, CSRF, polling and settings bindings all agree")


# ---------------------------------------------------------------------------
# 9. Artifacts
# ---------------------------------------------------------------------------


def check_icon() -> None:
    path = os.path.join(HERE, "icon.svg")
    with open(path, "r", encoding="utf-8") as handle:
        body = handle.read()
    if "<svg" not in body:
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
CLAIM_PATTERNS = (
    re.compile(rf"\b{_REDUCE_WORD}\b[^.]{{0,40}}?\b\d{{1,3}}\s?%", re.I),
    re.compile(rf"\b\d{{1,3}}\s?%[^.]{{0,40}}?\b{_REDUCE_WORD}\b", re.I),
    # A bare ratio table, e.g. "`full`: ~65%". A skill shipped exactly this and
    # the two patterns above missed it because the reduction word sat on a
    # different line from the number.
    re.compile(r"^\s*(?:[-*]\s*)?`[a-z0-9_-]+`\s*[:=]\s*~?\d{1,3}\s?%", re.I),
)

# A line may opt out with a trailing `claim-guard: allow` comment, or the line
# above it may carry one. Needed when a file has to *quote* the retracted
# number in order to explain that it was retracted. The marker is explicit so
# the exemption is auditable, rather than a heuristic that quietly allows any
# sentence containing a retraction keyword.
CLAIM_ALLOW = "claim-guard: allow"

# Directories whose text is executable or generated, not user-facing prose.
CLAIM_SKIP_DIRS = ("__pycache__", "benchmarks", "tests")


def check_claims() -> None:
    offenders = []
    for base, dirs, names in os.walk(HERE):
        dirs[:] = [d for d in dirs if d not in CLAIM_SKIP_DIRS]
        for name in names:
            if not name.endswith((".md", ".yaml", ".yml", ".json")):
                continue
            rel = os.path.relpath(os.path.join(base, name), HERE).replace(os.sep, "/")
            with open(os.path.join(base, name), "r", encoding="utf-8") as handle:
                lines = handle.read().splitlines()
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

    expected = ("lite", "full", "ultra", "wenyan-lite", "wenyan-full", "wenyan-ultra")
    missing = [level for level in expected if level not in defined]
    if missing:
        fail(f"intensity ruleset has no block for: {missing}")
    extra = [level for level in defined if level not in expected]
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
    try:
        proc = subprocess.run(
            [sys.executable, suite], capture_output=True, text=True, timeout=300
        )
    except Exception as exc:
        fail(f"could not run the unit test suite: {type(exc).__name__}: {exc}")
        return
    if proc.returncode != 0:
        tail = (proc.stdout + proc.stderr).strip().splitlines()[-12:]
        for line in tail:
            print(f"    {line}")
        fail(f"unit tests failed (exit {proc.returncode})")
        return
    summary = (proc.stdout.strip().splitlines() or [""])[-1]
    ok(f"unit tests pass: {summary}")


# ---------------------------------------------------------------------------


def main() -> int:
    print(f"[{PLUGIN_NAME}] plugin dir: {HERE}")
    print(f"[{PLUGIN_NAME}] agent zero root: {ROOT}")

    check_files()
    check_manifest()
    check_syntax()
    modules = check_imports()
    check_state(modules)
    check_api_contract(modules)
    check_commands(modules)
    check_webui(modules)
    check_icon()
    check_agents()
    check_tool_claims()
    check_prompts(modules)
    check_claims()
    check_unit_tests()
    state = check_toggle()

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


if __name__ == "__main__":
    sys.exit(main())
