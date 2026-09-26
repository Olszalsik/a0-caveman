"""
caveman plugin - unit tests.

    python usr/plugins/caveman/tests/test_caveman.py
    pytest usr/plugins/caveman/tests/test_caveman.py

Runs without pytest and without a full Agent Zero runtime: the few framework
modules these tests touch are stubbed at the top, because the real ones pull in
PIL, litellm and the settings file. The behaviour under test is plugin logic
that has no framework dependency worth exercising here.

This suite exists because the port shipped with none. Three of its features
could never execute - a slash command that could not match, a dropdown that
posted an action the API did not implement, and a response validator attached
to an extension point that carries no response text - and nothing caught any of
them. `test_healthcheck.py` covers the health check itself; this file covers the
logic the health check exercises.

Windows consoles default to the ANSI code page, which cannot encode the CJK
fixtures below. Replace rather than raise, or the tests crash while printing.
"""

import asyncio
import importlib.util
import json
import os
import sys
import tempfile
import types
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(errors="replace")
    except Exception:
        pass

PLUGIN = Path(__file__).resolve().parent.parent


def _find_agent_zero_root():
    """Walk up for a directory containing `usr/plugins/caveman/plugin.yaml`."""
    current = PLUGIN
    while True:
        parent = current.parent
        if parent == current:
            return None
        if (parent / "usr" / "plugins" / "caveman" / "plugin.yaml").is_file():
            return parent
        current = parent


# Installed the plugin lives at <a0>/usr/plugins/caveman/ and imports as
# `usr.plugins.caveman.helpers.*`. In a standalone clone - what a contributor or
# a Plugin Hub reviewer has - it is the repo root and there is no `usr/`
# package, so synthesise the chain with this directory as the search path.
A0_ROOT = _find_agent_zero_root()
ROOT = A0_ROOT or PLUGIN
STANDALONE = A0_ROOT is None

# In a standalone clone, keep the observation store out of the repository.
WORKDIR = (
    str(ROOT / "usr" / "workdir")
    if not STANDALONE
    else os.path.join(tempfile.gettempdir(), "caveman-tests-workdir")
)


# ---------------------------------------------------------------------------
# Bootstrap: import the plugin's modules by path, with framework stubs.
# ---------------------------------------------------------------------------


class _Extension:
    def __init__(self, agent=None, **kwargs):
        self.agent = agent


class _ApiHandler:
    def __init__(self, app, lock):
        self.app = app
        self.lock = lock


_CONFIG = {
    "enabled": True,
    "level": "full",
    "auto_clarity": True,
    "shrink_tools": False,
    "sanitize_responses": False,
}


def _stub_framework():
    helpers = types.ModuleType("helpers")
    helpers.__path__ = []

    ext = types.ModuleType("helpers.extension")
    ext.Extension = _Extension

    api = types.ModuleType("helpers.api")
    api.ApiHandler = _ApiHandler

    settings = types.ModuleType("helpers.settings")
    settings.get_settings = lambda: {"workdir_path": WORKDIR}

    files = types.ModuleType("helpers.files")
    files.get_abs_path = lambda *parts: os.path.join(ROOT, *parts)
    files.get_abs_path_dockerized = files.get_abs_path

    plugins = types.ModuleType("helpers.plugins")
    plugins.get_plugin_config = lambda name: dict(_CONFIG)

    for name, mod in {
        "helpers": helpers,
        "helpers.extension": ext,
        "helpers.api": api,
        "helpers.settings": settings,
        "helpers.files": files,
        "helpers.plugins": plugins,
    }.items():
        sys.modules.setdefault(name, mod)


def _register_package(name: str, path=None):
    if name in sys.modules:
        return
    module = types.ModuleType(name)
    module.__path__ = [path] if path is not None else []
    sys.modules[name] = module


def _bootstrap_imports():
    if A0_ROOT is not None:
        if str(A0_ROOT) not in sys.path:
            sys.path.insert(0, str(A0_ROOT))
        return
    _register_package("usr")
    _register_package("usr.plugins")
    _register_package("usr.plugins.caveman", str(PLUGIN))


_stub_framework()
_bootstrap_imports()

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _load(relative: str, alias: str):
    spec = importlib.util.spec_from_file_location(alias, PLUGIN / relative)
    module = importlib.util.module_from_spec(spec)
    sys.modules[alias] = module
    spec.loader.exec_module(module)
    return module


state = _load("helpers/state.py", "cav_state")
compat = _load("helpers/compat.py", "cav_compat")
prompts = _load("helpers/prompts.py", "cav_prompts")
cav_compress = _load("helpers/compress.py", "cav_compress")
markdown = _load("helpers/markdown.py", "cav_markdown")
command = _load(
    "extensions/python/monologue_start/_30_caveman_command.py", "cav_command"
)
validate = _load(
    "extensions/python/message_loop_result/_50_caveman_validate.py", "cav_validate"
)
observe = _load(
    "extensions/python/message_loop_result/_60_caveman_observe.py", "cav_observe"
)
style = _load("extensions/python/system_prompt/_20_caveman_style.py", "cav_style")
shrink = _load(
    "extensions/python/chat_model_call_before/_60_caveman_shrink_tools.py", "cav_shrink"
)
api_state = _load("api/caveman_state.py", "cav_api_state")


def _fresh(chat_id: str):
    """Reset a chat's state so tests do not depend on each other or on reruns.

    The mode log is append-only, so it has to be cleared per chat rather than
    per run; otherwise a second run sees the first run's rows.
    """
    state.set_state(chat_id, level=None, enabled=None)
    state.reset_stats(chat_id)
    state.clear_mode_log(chat_id)
    return chat_id


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


class FakeMessage:
    """Stands in for history.Message, whose text lives behind output_text()."""

    def __init__(self, text: str):
        self._text = text

    def output_text(self):
        return self._text


class LoopData:
    def __init__(self, message):
        self.user_message = message


class Call:
    def __init__(self, name: str):
        self.name = name


class LLMResult:
    def __init__(self, response: str, calls=()):
        self.response = response
        self._calls = list(calls)

    @property
    def function_calls(self):
        return self._calls


class Log:
    def __init__(self):
        self.entries = []

    def log(self, **kwargs):
        self.entries.append(kwargs)


class Ctx:
    def __init__(self, chat_id: str):
        self.id = chat_id
        self.log = Log()


class Agent:
    agent_name = "A0"

    def __init__(self, chat_id: str):
        self.context = Ctx(chat_id)


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------


def test_state_path_is_inside_the_workdir():
    path = state.state_path()
    assert os.path.abspath(path).startswith(os.path.abspath(WORKDIR)), path


def test_resolve_defaults_come_from_config():
    chat = _fresh("_t_resolve")
    assert state.resolve(chat, {"enabled": False, "level": "lite"}) == {
        "enabled": False,
        "level": "lite",
    }
    assert state.resolve(chat, {"enabled": True, "level": "ultra"}) == {
        "enabled": True,
        "level": "ultra",
    }
    # An unknown configured level must not propagate.
    assert state.resolve(chat, {"enabled": True, "level": "bogus"})["level"] == "full"


def test_set_state_is_atomic_and_clears_cleanly():
    chat = _fresh("_t_atomic")
    assert state.set_state(chat, level="ultra", enabled=True)
    assert state.resolve(chat, {"enabled": False, "level": "lite"}) == {
        "enabled": True,
        "level": "ultra",
    }
    assert state.set_state(chat, level=None, enabled=None)
    assert state.get_entry(chat) == {}


def test_set_level_rejects_off_and_nonsense():
    chat = _fresh("_t_level")
    assert state.set_level(chat, "off") is False
    assert state.set_level(chat, "nope") is False
    assert state.set_level(chat, "lite") is True
    assert state.get_level(chat, "full") == "lite"


def test_set_state_rejects_a_bad_level_without_touching_enabled():
    chat = _fresh("_t_partial")
    state.set_state(chat, level="full", enabled=True)
    assert state.set_state(chat, level="nope", enabled=False) is False
    entry = state.get_entry(chat)
    assert entry.get("level") == "full"
    assert entry.get("enabled") is True


def test_mode_log_records_only_real_transitions():
    chat = _fresh("_t_log")
    for level, enabled in [
        ("full", True),
        ("ultra", True),
        ("ultra", True),  # duplicate: must not appear
        ("ultra", False),  # disable normalises to off
        (None, None),
    ]:
        state.set_state(chat, level=level, enabled=enabled)
    modes = [row["mode"] for row in state.mode_history(chat)]
    assert modes == ["full", "ultra", "off"], modes
    state.set_state(chat, level=None, enabled=None)


def test_observations_store_no_estimate():
    chat = _fresh("_t_obs")
    state.record_turn(chat, "full", 100)
    state.record_turn(chat, "full", 50)
    entry = state.get_stats(chat)
    assert entry["turns"] == 2
    assert entry["chars"] == 150
    assert "est_tokens_saved" not in entry
    assert entry["by_level"]["full"] == {"turns": 2, "chars": 150}
    state.reset_stats(chat)


def test_record_turn_ignores_negative_lengths():
    chat = _fresh("_t_negative")
    assert state.record_turn(chat, "full", -1) is False
    assert state.get_stats(chat) == {}


# ---------------------------------------------------------------------------
# Slash commands
# ---------------------------------------------------------------------------


def test_command_classification():
    cases = [
        ("/caveman", "on_default"),
        ("/caveman lite", "set_level"),
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
        ("caveman disabled", "off"),
        ("/Caveman ULTRA", "set_level"),
        ("  /caveman lite  ", "set_level"),
        ("/caveman nonsense", None),
        ("please fix the failing test", None),
        ("caveman", None),
        ("the caveman plugin is nice", None),
    ]
    for text, expected in cases:
        result = command._classify(text)
        action = result[0] if result else None
        assert action == expected, f"{text!r} -> {result!r}, expected {expected!r}"


def test_user_text_is_read_from_a_message_object():
    """The original bug: the probe required a str and LoopData never has one."""
    loop = LoopData(FakeMessage("/caveman ultra"))
    assert command._get_latest_user_text(loop) == "/caveman ultra"
    assert command._get_latest_user_text(LoopData(None)) == ""


def test_user_text_falls_back_to_plain_string_content():
    class StrContent:
        content = "/caveman off"

    assert command._get_latest_user_text(LoopData(StrContent())) == "/caveman off"


def test_slash_command_persists_the_level():
    chat = _fresh("_t_cmd")
    agent = Agent(chat)
    asyncio.run(
        command.CavemanCommand(agent).execute(
            loop_data=LoopData(FakeMessage("/caveman ultra"))
        )
    )
    assert state.resolve(chat, {"enabled": False, "level": "lite"}) == {
        "enabled": True,
        "level": "ultra",
    }
    state.set_state(chat, level=None, enabled=None)


def test_slash_command_off_disables():
    chat = _fresh("_t_cmd_off")
    agent = Agent(chat)
    asyncio.run(
        command.CavemanCommand(agent).execute(
            loop_data=LoopData(FakeMessage("/caveman off"))
        )
    )
    assert state.resolve(chat, {"enabled": True, "level": "lite"})["enabled"] is False
    state.set_state(chat, level=None, enabled=None)


def test_non_command_message_changes_nothing():
    chat = _fresh("_t_cmd_noop")
    before = state.get_entry(chat)
    asyncio.run(
        command.CavemanCommand(Agent(chat)).execute(
            loop_data=LoopData(FakeMessage("please run the tests"))
        )
    )
    assert state.get_entry(chat) == before


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------


def test_every_level_parses_from_one_ruleset():
    assert prompts.available_levels() == list(state.VALID_LEVELS)


def test_each_level_block_is_self_contained():
    for level in prompts.available_levels():
        block = prompts.load_intensity(level)
        assert block, level
        assert level in block, level
        stray = [
            line
            for line in block.splitlines()
            if line.startswith("### ") and level not in line
        ]
        assert not stray, f"{level} leaks {stray}"


def test_assembled_prompt_is_complete_and_comment_free():
    prompt = prompts.build_system_prompt("full")
    assert prompts.MARKER in prompt
    assert "<active_level>full</active_level>" in prompt
    assert "Auto-Clarity" in prompt
    assert "<!--" not in prompt, "maintainer comments ship to the model"


def test_auto_clarity_can_be_disabled():
    assert "Auto-Clarity" not in prompts.build_system_prompt(
        "full", auto_clarity=False
    )


def test_unknown_level_fails_closed():
    assert prompts.load_intensity("nope") == ""
    assert prompts.build_system_prompt("nope") == ""


# ---------------------------------------------------------------------------
# Compressor
# ---------------------------------------------------------------------------


def test_hyphenated_compound_survives():
    """Upstream #1055: \\b matches inside just-in-time and leaves a dangling -in-time."""
    out, _, _ = cav_compress.compress(
        "The just-in-time cache expires the just-in-time token early."
    )
    assert out.lower().count("just-in-time") == 2, out
    # The bug's signature: after removing every intact compound, a leftover
    # "-in-time" would mean "just" had been eaten out of one of them.
    import re

    residual = re.sub(r"just-in-time", "", out, flags=re.IGNORECASE)
    assert "-in-time" not in residual, residual


def test_collocated_sure_keeps_its_verb():
    """Upstream #1073: 'Make sure the file exists' is a check, not a create."""
    out, _, _ = cav_compress.compress(
        "Make sure the file exists before you run the migrator."
    )
    assert "Make sure" in out


def test_bare_sure_interjection_is_removed():
    out, _, _ = cav_compress.compress("Sure, this returns the value you asked for.")
    assert not out.lower().startswith("sure")


def test_ordinary_sure_is_kept():
    out, _, _ = cav_compress.compress("Are you sure that this is the right path?")
    assert "sure" in out


def test_cjk_is_returned_untouched():
    text = "組件頻重繪，以每繪新生對象參照故。Please wrap the object in useMemo."
    out, before, after = cav_compress.compress(text)
    assert out == text
    assert before == after


def test_protected_segments_survive():
    cases = [
        ("Here:\n\n```js\nconst a = 1; // the quick brown fox\n```\n\nDone.", "the quick brown fox"),
        ("Wrap it in `useMemo` for stability.", "useMemo"),
        ("See https://example.com/a/b?q=1 now.", "https://example.com/a/b?q=1"),
        ("Edit ./config/settings.toml now.", "./config/settings.toml"),
        ("Run /usr/local/bin/deploy first.", "/usr/local/bin/deploy"),
        ("Check MAX_RETRY_COUNT here.", "MAX_RETRY_COUNT"),
        ("Call foo.bar(1) now.", "foo.bar(1)"),
        ("Pin version 1.2.3 exactly.", "1.2.3"),
    ]
    for text, must_survive in cases:
        out, _, _ = cav_compress.compress(text)
        assert must_survive in out, f"{must_survive!r} lost from {text!r} -> {out!r}"


def test_leaders_hedges_and_fillers_go():
    out, _, _ = cav_compress.compress(
        "I'll just maybe actually do the thing that you can do."
    )
    lowered = out.lower()
    for gone in ("i'll", "just", "maybe", "actually"):
        assert gone not in lowered, gone


def test_empty_and_non_string_input_is_safe():
    assert cav_compress.compress("")[0] == ""
    assert cav_compress.compress(None)[0] == ""


# ---------------------------------------------------------------------------
# Markdown validation
# ---------------------------------------------------------------------------


DOC = """# Deploy Runbook

Please just actually verify the rollout before you continue.

## Steps

Run the migration with `migrate.py --dry-run` and then check
/usr/local/bin/deploy and the config at ./config/settings.toml.

Reference: https://example.com/deploy/runbook

```bash
# the quick brown fox jumps
deploy --profile prod
```

Pros/cons are documented in the Node/browser matrix.
"""


def test_faithful_compression_validates():
    out, _, _ = cav_compress.compress(DOC)
    assert markdown.validate(DOC, out).is_valid


def test_validator_catches_each_loss():
    out, _, _ = cav_compress.compress(DOC)
    cases = {
        "inline code lost": out.replace("migrate.py --dry-run", "migrate"),
        "URL lost": out.replace("https://example.com/deploy/runbook", "https://example.com"),
        "path lost": out.replace("./config/settings.toml", ""),
        "heading lost": out.replace("## Steps", "Some steps"),
        "code block modified": out.replace("deploy --profile prod", "deploy"),
    }
    for label, broken in cases.items():
        result = markdown.validate(DOC, broken)
        assert not result.is_valid, label
        assert result.errors, label


def test_validator_ignores_prose_pairs_and_urls_in_path_check():
    paths = markdown._definite_paths(DOC)
    assert "./config/settings.toml" in paths
    assert "/usr/local/bin/deploy" in paths
    assert not any("example.com" in p for p in paths), "URL counted as a path"
    assert not any(p in ("pros/cons", "Node/browser") for p in paths)


def test_validator_requires_cjk_round_trip():
    text = "組件重繪。Please wrap it in useMemo."
    assert not markdown.validate(text, text.replace("Please ", "")).is_valid


# ---------------------------------------------------------------------------
# Response validation
# ---------------------------------------------------------------------------


FILLED = "Sure! I'd be happy to help with that. The token check fails."


def _run_validate(agent, result_data):
    asyncio.run(validate.CavemanValidate(agent).execute(result_data=result_data))


def test_filler_is_warned_but_not_stripped_by_default():
    chat = _fresh("_t_val")
    agent = Agent(chat)
    result_data = {"llm_result": LLMResult(FILLED, [Call("response")])}
    _run_validate(agent, result_data)
    assert result_data["llm_result"].response == FILLED
    assert any(e.get("type") == "warning" for e in agent.context.log.entries)
    state.set_state(chat, level=None, enabled=None)


def test_strip_requires_opt_in_and_a_strip_level():
    chat = _fresh("_t_strip")
    _CONFIG["sanitize_responses"] = True
    state.set_state(chat, level="ultra", enabled=True)

    # full is not a strip level
    state.set_state(chat, level="full", enabled=True)
    rd = {"llm_result": LLMResult(FILLED, [Call("response")])}
    _run_validate(Agent(chat), rd)
    assert rd["llm_result"].response == FILLED

    # ultra strips on a final answer turn
    state.set_state(chat, level="ultra", enabled=True)
    rd = {"llm_result": LLMResult(FILLED, [Call("response")])}
    _run_validate(Agent(chat), rd)
    assert rd["llm_result"].response != FILLED
    assert "happy to" not in rd["llm_result"].response

    _CONFIG["sanitize_responses"] = False
    state.set_state(chat, level=None, enabled=None)


def test_strip_never_touches_a_tool_turn():
    chat = _fresh("_t_toolturn")
    _CONFIG["sanitize_responses"] = True
    state.set_state(chat, level="ultra", enabled=True)
    for calls in ([Call("read_file")], [Call("response"), Call("read_file")]):
        rd = {"llm_result": LLMResult(FILLED, calls)}
        _run_validate(Agent(chat), rd)
        assert rd["llm_result"].response == FILLED, calls
    _CONFIG["sanitize_responses"] = False
    state.set_state(chat, level=None, enabled=None)


def test_strip_will_not_empty_a_response():
    chat = _fresh("_t_empty")
    _CONFIG["sanitize_responses"] = True
    state.set_state(chat, level="ultra", enabled=True)
    rd = {"llm_result": LLMResult("Sure! Of course!", [Call("response")])}
    _run_validate(Agent(chat), rd)
    assert rd["llm_result"].response.strip() != ""
    _CONFIG["sanitize_responses"] = False
    state.set_state(chat, level=None, enabled=None)


def test_validate_respects_skip_default_processing():
    chat = _fresh("_t_skip")
    agent = Agent(chat)
    rd = {
        "llm_result": LLMResult(FILLED, [Call("response")]),
        "skip_default_processing": True,
    }
    _run_validate(agent, rd)
    assert rd["llm_result"].response == FILLED
    assert not agent.context.log.entries


# ---------------------------------------------------------------------------
# Observation
# ---------------------------------------------------------------------------


def test_observer_records_real_output_length():
    chat = _fresh("_t_observe")
    state.set_state(chat, level="lite", enabled=True)
    rd = {"llm_result": LLMResult("x" * 42, [Call("response")])}
    asyncio.run(observe.CavemanObserve(Agent(chat)).execute(result_data=rd))
    assert state.get_stats(chat)["chars"] == 42
    state.set_state(chat, level=None, enabled=None)


def test_observer_is_silent_when_disabled():
    chat = _fresh("_t_observe_off")
    state.set_state(chat, level="lite", enabled=False)
    rd = {"llm_result": LLMResult("x" * 42, [Call("response")])}
    asyncio.run(observe.CavemanObserve(Agent(chat)).execute(result_data=rd))
    assert state.get_stats(chat) == {}
    state.set_state(chat, level=None, enabled=None)


# ---------------------------------------------------------------------------
# Tool description compression
# ---------------------------------------------------------------------------


def test_shrink_touches_descriptions_only():
    tools = [
        {
            "type": "function",
            "name": "caveman_read_file",
            "description": "Please just read the file and return the quick contents.",
            "parameters": {"type": "object", "properties": {"path": {"type": "string"}}},
        }
    ]
    before_name = tools[0]["name"]
    before_params = json.dumps(tools[0]["parameters"], sort_keys=True)
    touched, saved = shrink.compress_tool_descriptions(tools)
    assert touched == 1 and saved > 0
    assert tools[0]["name"] == before_name, "tool name must never change"
    assert json.dumps(tools[0]["parameters"], sort_keys=True) == before_params
    assert "Please" not in tools[0]["description"]


def test_shrink_leaves_cjk_and_bad_input_alone():
    tools = [{"description": "組件重繪。Please wrap it."}, {"description": ""}, "nope"]
    touched, _ = shrink.compress_tool_descriptions(tools)
    assert touched == 0
    assert shrink.compress_tool_descriptions(None) == (0, 0)


def test_shrinker_respects_its_config_gate():
    chat = _fresh("_t_shrink_gate")
    state.set_state(chat, level="ultra", enabled=True)
    tools = [{"description": "Please just read the file."}]
    _CONFIG["shrink_tools"] = False
    asyncio.run(
        shrink.CavemanShrinkTools(Agent(chat)).execute(
            call_data={"a0_responses_function_tools": tools}
        )
    )
    assert tools[0]["description"] == "Please just read the file."
    _CONFIG["shrink_tools"] = True
    asyncio.run(
        shrink.CavemanShrinkTools(Agent(chat)).execute(
            call_data={"a0_responses_function_tools": tools}
        )
    )
    assert tools[0]["description"] != "Please just read the file."
    _CONFIG["shrink_tools"] = False
    state.set_state(chat, level=None, enabled=None)


# ---------------------------------------------------------------------------
# State API
# ---------------------------------------------------------------------------


HANDLER = api_state.CavemanState(None, None)


def _call(**payload):
    return asyncio.run(HANDLER.process(payload, None))


def test_api_set_implies_enable_and_off_implies_disable():
    chat = _fresh("_t_api")
    result = _call(action="set", chat_id=chat, level="ultra")
    assert result["ok"] and result["enabled"] is True and result["level"] == "ultra"
    result = _call(action="set", chat_id=chat, level="off")
    assert result["ok"] and result["enabled"] is False
    state.set_state(chat, level=None, enabled=None)


def test_api_validates_before_writing():
    chat = _fresh("_t_api_invalid")
    state.set_state(chat, level="full", enabled=True)
    assert _call(action="set", chat_id=chat, level="offf")["ok"] is False
    assert _call(action="set", chat_id=chat, enabled="yes")["ok"] is False
    assert _call(action="set_level", chat_id=chat, level="off")["ok"] is False
    assert state.get_entry(chat).get("level") == "full"
    state.set_state(chat, level=None, enabled=None)


def test_api_unknown_action_and_missing_chat_id():
    chat = _fresh("_t_api_bad")
    bad = _call(action="nope", chat_id=chat)
    assert bad["ok"] is False and "unknown action" in bad["error"]
    assert _call(action="get")["ok"] is False
    assert _call(action="get", chat_id=chat)["ok"] is True


def test_api_clear_and_list():
    chat = _fresh("_t_api_clear")
    state.set_state(chat, level="lite", enabled=True)
    assert _call(action="clear", chat_id=chat)["ok"] is True
    assert state.get_entry(chat) == {}
    listing = _call(action="list")
    assert listing["ok"] and chat not in listing["chats"]


# ---------------------------------------------------------------------------
# Partial upgrade / stale state module
#
# Reproduces the reported production failure:
#   AttributeError: module 'usr.plugins.caveman.helpers.state'
#                   has no attribute 'resolve'
# raised from _20_caveman_style.py:55, inside Agent.get_system_prompt, which
# killed the agent turn. An extension from v0.5.0 sat beside a v0.4.0
# helpers/state.py.
# ---------------------------------------------------------------------------


class StaleState:
    """A v0.4.0-shaped state module: no resolve, no set_state, no record_turn."""

    VALID_LEVELS = ("lite", "full", "ultra", "wenyan-lite", "wenyan-full", "wenyan-ultra")

    @staticmethod
    def get_level(chat_id, default="full"):
        return default

    @staticmethod
    def is_enabled(chat_id, default_enabled):
        return default_enabled

    @staticmethod
    def set_level(chat_id, level):
        return False

    @staticmethod
    def set_enabled(chat_id, enabled):
        return False

    @staticmethod
    def get_all_chats():
        return {}


def test_stale_state_is_detected():
    compat.reset_warnings()
    missing = compat.missing_state_api(StaleState)
    assert "resolve" in missing
    assert "set_state" in missing
    assert compat.has_state_api(state) is True
    assert compat.missing_state_api(state) == []


def test_state_api_returns_none_and_warns_once():
    compat.reset_warnings()
    agent = Agent("_t_stale")
    assert compat.state_api(StaleState, agent) is None
    assert compat.state_api(state, agent) is state
    assert len(agent.context.log.entries) == 1, "must warn exactly once"
    message = agent.context.log.entries[0]["content"]
    assert "partial upgrade" in message
    assert "replace the whole plugin" in message.lower()
    # Second call with the same message must not log again.
    compat.state_api(StaleState, agent)
    assert len(agent.context.log.entries) == 1


def test_style_extension_degrades_instead_of_raising():
    """The exact traceback the user hit must not happen any more."""
    # The extension resolved its own `compat` through the package path, which
    # is a different module object from the alias-loaded one above, so the
    # warn-once registry has to be reset on that instance.
    style.compat.reset_warnings()
    agent = Agent("_t_stale_style")
    original = style.caveman_state
    try:
        style.caveman_state = StaleState()
        system_prompt = ["existing prompt"]
        asyncio.run(
            style.CavemanStyle(agent).execute(system_prompt=system_prompt)
        )
    finally:
        style.caveman_state = original
    assert system_prompt == ["existing prompt"], "must not inject anything"
    assert any(
        e.get("type") == "warning" and "partial upgrade" in e.get("content", "")
        for e in agent.context.log.entries
    ), agent.context.log.entries


def test_observer_degrades_instead_of_raising():
    observe.compat.reset_warnings()
    agent = Agent("_t_stale_observe")
    original = observe.caveman_state
    try:
        observe.caveman_state = StaleState()
        result_data = {"llm_result": LLMResult("x" * 30, [Call("response")])}
        asyncio.run(observe.CavemanObserve(agent).execute(result_data=result_data))
    finally:
        observe.caveman_state = original
    # No exception, and nothing recorded into the real store.
    assert state.get_stats("_t_stale_observe") == {}


def test_validator_degrades_instead_of_raising():
    validate.compat.reset_warnings()
    agent = Agent("_t_stale_validate")
    original = validate.caveman_state
    text = "Sure! I'd be happy to help."
    try:
        validate.caveman_state = StaleState()
        result_data = {"llm_result": LLMResult(text, [Call("response")])}
        _run_validate(agent, result_data)
    finally:
        validate.caveman_state = original
    assert result_data["llm_result"].response == text, "must not mutate"


def test_state_api_reads_still_work_over_http():
    compat.reset_warnings()
    chat = _fresh("_t_stale_api")
    original = api_state.caveman_state
    try:
        api_state.caveman_state = StaleState()
        result = asyncio.run(api_state.CavemanState(None, None).process(
            {"action": "get", "chat_id": chat}, None
        ))
        assert result["ok"] is True, "reads must not 500"
        assert result["level"] == "full", "falls back to the configured default"

        write = asyncio.run(api_state.CavemanState(None, None).process(
            {"action": "set", "chat_id": chat, "level": "ultra"}, None
        ))
        assert write["ok"] is False
        assert "older version" in write["error"]
    finally:
        api_state.caveman_state = original


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


def _main() -> int:
    tests = [
        (name, obj)
        for name, obj in sorted(globals().items())
        if name.startswith("test_") and callable(obj)
    ]
    failures = []
    for name, fn in tests:
        try:
            fn()
            print(f"ok    {name}")
        except Exception as exc:  # noqa: BLE001 - a test runner reports everything
            failures.append(name)
            print(f"FAIL  {name}: {type(exc).__name__}: {exc}")
    print()
    print(f"{len(tests) - len(failures)}/{len(tests)} passed")
    if failures:
        print("failed: " + ", ".join(failures))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(_main())
