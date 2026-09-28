"""
Caveman + Headroom - cross-feature regression tests.

    python usr/plugins/caveman/tests/test_headroom_integration.py
    pytest usr/plugins/caveman/tests/test_headroom_integration.py

Roadmap P4.2. The two features are deliberately independent switches, and
the bugs that matter here are the ones where they are NOT:

  * a config read that silently falls back to defaults (an `agent=` argument
    mismatch does exactly this, and looks like a feature that is switched off),
  * two transformers running over the same message because the standalone
    plugin is still installed,
  * a destructive step that claims to be recoverable and is not,
  * a migration that overwrites a value the user set.

Everything here runs on stubbed framework modules and in `safe` mode, so it
needs neither headroom-ai nor a live Agent Zero runtime. The package being
absent must not change any result here - that is the point of the safe-mode
fallback, and a suite that only passed with the package installed would not
be testing this plugin.
"""

import asyncio
import json
import os
import sys
import tempfile
import time
import types
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(errors="replace")
    except Exception:
        pass

PLUGIN = Path(__file__).resolve().parent.parent


def _find_agent_zero_root():
    current = PLUGIN
    while True:
        parent = current.parent
        if parent == current:
            return None
        if (parent / "usr" / "plugins" / "caveman" / "plugin.yaml").is_file():
            return parent
        current = parent


ROOT = _find_agent_zero_root()

if ROOT is None:
    # Standalone clone: synthesise the usr.plugins.caveman package chain.
    for _name, _path in (
        ("usr", None),
        ("usr.plugins", None),
        ("usr.plugins.caveman", str(PLUGIN)),
    ):
        if _name in sys.modules:
            continue
        _mod = types.ModuleType(_name)
        _mod.__path__ = [_path] if _path else []
        sys.modules[_name] = _mod
else:
    sys.path.insert(0, str(ROOT))



# ---------------------------------------------------------------------------
# framework stubs - same approach as tests/test_caveman.py
# ---------------------------------------------------------------------------


class _Extension:
    def __init__(self, agent=None, **kwargs):
        self.agent = agent


class _Response:
    def __init__(self, message="", break_loop=False, **kwargs):
        self.message = message
        self.break_loop = break_loop


class _Tool:
    def __init__(self, agent=None, name="", args=None, **kwargs):
        self.agent = agent
        self.name = name
        self.args = args or {}


class _ApiHandler:
    def __init__(self, app=None, lock=None):
        self.app = app


def _stub_framework(config_provider):
    helpers = types.ModuleType("helpers")
    helpers.__path__ = []

    ext = types.ModuleType("helpers.extension")
    ext.Extension = _Extension

    tool = types.ModuleType("helpers.tool")
    tool.Tool = _Tool
    tool.Response = _Response

    api = types.ModuleType("helpers.api")
    api.ApiHandler = _ApiHandler
    api.Input = dict
    api.Output = dict
    api.Request = object
    api.Response = _Response

    settings = types.ModuleType("helpers.settings")
    settings.get_settings = lambda: {
        "workdir_path": os.path.join(tempfile.gettempdir(), "caveman-hr-tests")
    }

    files = types.ModuleType("helpers.files")
    files.get_abs_path = lambda *parts: os.path.join(ROOT or PLUGIN, *parts)
    files.get_abs_path_dockerized = files.get_abs_path

    plugins = types.ModuleType("helpers.plugins")
    # Accepts `agent=` and `**kwargs` on purpose: a narrower signature is
    # exactly what silently disabled half this plugin once already.
    plugins.get_plugin_config = (
        lambda name, agent=None, **kw: dict(config_provider(name, agent))
    )
    plugins.find_plugin_dir = lambda name: None
    plugins.get_toggle_state = lambda name: "disabled"
    plugins.find_plugin_assets = lambda *a, **k: []

    for name, mod in {
        "helpers": helpers,
        "helpers.extension": ext,
        "helpers.tool": tool,
        "helpers.api": api,
        "helpers.settings": settings,
        "helpers.files": files,
        "helpers.plugins": plugins,
    }.items():
        sys.modules.setdefault(name, mod)
    return plugins


_CONFIG = {"enabled": True, "level": "full", "auto_clarity": True}
PLUGINS = _stub_framework(lambda name, agent=None: _CONFIG)

from usr.plugins.caveman.helpers import plugins_config  # noqa: E402
from usr.plugins.caveman.helpers.headroom.ccr_cache import CcrCache  # noqa: E402
from usr.plugins.caveman.helpers.headroom import (  # noqa: E402
    clarity,
    coexistence,
    compressor,
    config as hr_config,
    migration,
)

_real_get_config = plugins_config.get_config
_real_destination_config_path = hr_config.destination_config_path


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _restore_config():
    plugins_config.get_config = _real_get_config


def _restore_destination():
    hr_config.destination_config_path = _real_destination_config_path


def _restore_plugins():
    PLUGINS.find_plugin_dir = lambda name: None
    PLUGINS.get_toggle_state = lambda name: "disabled"
    PLUGINS.find_plugin_assets = lambda *a, **k: []


def _load(dotted: str):
    """Import a plugin module by its usr.plugins path."""
    import importlib

    return importlib.import_module(f"usr.plugins.caveman.{dotted}")


def _dest_config(tmp: Path, data: dict) -> Path:
    """Point the migration module at a throwaway destination config."""
    dest = Path(tmp) / "config.json"
    dest.write_text(json.dumps(data), encoding="utf-8")
    hr_config.destination_config_path = lambda: dest
    return dest


def _fake_standalone(tmp: Path, config_data, per_chat=None) -> Path:
    root = Path(tmp) / "headroom_compress"
    root.mkdir(parents=True, exist_ok=True)
    if config_data is not None:
        (root / "config.json").write_text(
            config_data if isinstance(config_data, str) else json.dumps(config_data),
            encoding="utf-8",
        )
    if per_chat is not None:
        cache = root / "cache"
        cache.mkdir(exist_ok=True)
        (cache / "per_chat_overrides.json").write_text(
            json.dumps(per_chat), encoding="utf-8"
        )
    migration._standalone_dir = lambda: (root, "")
    PLUGINS.find_plugin_dir = (
        lambda name: str(root) if name == "headroom_compress" else None
    )
    PLUGINS.find_plugin_assets = lambda *a, **k: (
        []
        if config_data is None
        else [
            {
                "project_name": "",
                "agent_profile": "",
                "path": str(root / "config.json"),
            }
        ]
    )
    return root


def _no_standalone():
    migration._standalone_dir = lambda: (None, "")
    _restore_plugins()



# ---------------------------------------------------------------------------
# P0.2 - the combined settings model
# ---------------------------------------------------------------------------


def test_headroom_defaults_match_the_yaml():
    """A key in the YAML that no code reads is a setting that does nothing."""
    import yaml

    raw = yaml.safe_load((PLUGIN / "default_config.yaml").read_text(encoding="utf-8"))
    yaml_keys = set(raw.get("headroom", {}))
    code_keys = set(plugins_config.DEFAULTS["headroom"])
    assert yaml_keys == code_keys, (
        "default_config.yaml and DEFAULTS disagree: "
        f"yaml-only={sorted(yaml_keys - code_keys)} "
        f"code-only={sorted(code_keys - yaml_keys)}"
    )
    assert set(plugins_config.DEFAULTS["headroom"]["proxy"]) == set(
        raw["headroom"]["proxy"]
    )


def test_compression_is_off_by_default():
    assert plugins_config.DEFAULTS["headroom"]["enabled"] is False
    assert hr_config.is_enabled() is False


def test_the_two_switches_are_independent():
    """Turning one on must not be observable through the other."""
    for headroom_on in (True, False):
        plugins_config.get_config = lambda agent=None, on=headroom_on: {
            "enabled": True,
            "level": "full",
            "headroom": {**plugins_config.DEFAULTS["headroom"], "enabled": on},
        }
        try:
            cfg = plugins_config.get_config()
            assert cfg["enabled"] is True, "headroom switch leaked into style"
            assert cfg["headroom"]["enabled"] is headroom_on
        finally:
            _restore_config()


def test_headroom_config_merges_a_partial_section():
    """A partial `headroom` block must not drop the other defaults."""
    plugins_config.get_config = lambda agent=None: {
        "headroom": {"enabled": True, "ccr_ttl_days": 3}
    }
    try:
        cfg = hr_config.get_config()
        assert cfg["enabled"] is True
        assert cfg["ccr_ttl_days"] == 3
        assert cfg["strategy"] == plugins_config.DEFAULTS["headroom"]["strategy"]
        assert (
            cfg["proxy"]["port"] == plugins_config.DEFAULTS["headroom"]["proxy"]["port"]
        )
    finally:
        _restore_config()


def test_caveman_call_sites_pass_the_agent():
    """Regression guard for the bug that made both gates look switched off.

    `get_plugin_config` is called with `agent=`; every reader must forward an
    agent, or a per-project / per-agent config silently resolves to the global
    one - or to defaults, when the reader's signature is narrower.
    """
    guard = [
        "extensions/python/system_prompt/_20_caveman_style.py",
        "extensions/python/message_loop_result/_50_caveman_validate.py",
        "extensions/python/message_loop_result/_60_caveman_observe.py",
        "extensions/python/chat_model_call_before/_60_caveman_shrink_tools.py",
        "extensions/python/banners/_10_caveman_discovery.py",
        "api/caveman_state.py",
    ]
    for rel in guard:
        src = (PLUGIN / rel).read_text(encoding="utf-8")
        for line in src.splitlines():
            if "plugin_cfg.get_config(" in line or "plugin_cfg.get_bool(" in line:
                assert "agent=" in line, f"{rel}: unscoped config read -> {line.strip()}"


def _query_db(db_path, sql, params=()):
    """Run one query and close the connection.

    `with sqlite3.connect(...)` commits but does NOT close, which leaks the
    handle; on Windows that makes the containing directory undeletable.
    """
    import sqlite3

    conn = sqlite3.connect(str(db_path))
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def _exec_db(db_path, sql, params=()):
    import sqlite3

    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(sql, params)
        conn.commit()
    finally:
        conn.close()


def _close_pools():
    """Release pooled SQLite handles.

    stats.py and ccr_cache.py keep one connection per database path so the
    ~25ms open/checkpoint cost stays off the hot path. On Windows an open
    handle makes its directory undeletable, which is a test-harness
    constraint, not a durability one: every write already autocommits.
    """
    from usr.plugins.caveman.helpers.headroom import ccr_cache as hr_ccr
    from usr.plugins.caveman.helpers.headroom import stats as hr_stats

    hr_stats.close_all_connections()
    hr_ccr.close_all_connections()


class _use_config:
    """Make the combined plugin read a specific headroom config.

    `compress_text` resolves its config through the plugin config reader, so
    the test has to patch that same seam rather than pass a config in. Temp
    CCR/stats paths keep the developer's real databases untouched.
    """

    def __init__(self, **overrides):
        self.overrides = overrides
        self.tmp = tempfile.mkdtemp(prefix="caveman-hr-cfg-")

    def __enter__(self):
        cfg = {**plugins_config.DEFAULTS["headroom"], "enabled": True, "mode": "safe"}
        cfg["ccr_path"] = str(Path(self.tmp) / "ccr.db")
        cfg["stats_path"] = str(Path(self.tmp) / "stats.db")
        cfg.update(self.overrides)
        plugins_config.get_config = lambda agent=None: {
            "enabled": True,
            "level": "full",
            "headroom": cfg,
        }
        return cfg

    def __exit__(self, *exc):
        _restore_config()
        return False


def _log_text(lines: int = 120) -> str:
    """Padding-heavy log text.

    Safe mode is whitespace normalisation, so a fixture with tidy single
    spaces would (correctly) save nothing. This one has the runs of spaces,
    tabs and blank lines real log dumps have, which is what safe mode can
    actually collapse.
    """
    return "\n\n".join(
        "\n".join(
            f"    {i:04d}  INFO   worker-{i % 7}    heartbeat ok    seq={i}   \t"
            for _ in range(4)
        )
        for i in range(lines)
    )



# ---------------------------------------------------------------------------
# P2.3 - compression safety: protections and recoverability
# ---------------------------------------------------------------------------


def test_compression_is_lossless_because_ccr_holds_the_original():
    with _use_config():
        text = _log_text()
        result = compressor.compress_text(text, source="tool:code_execution_tool")
        assert result["compressed"] is True
        assert result["saved_tokens"] > 0
        assert result["text"] != text
        key = result.get("ccr_key")
        assert key, "a lossy result with no retrieval key is unrecoverable"
        assert compressor.retrieve_original(key, agent=None) == text


def test_protected_file_reads_pass_through_byte_for_byte():
    text = "def f():\r\n\treturn 1  \n\n\n   # indented  \n"
    with _use_config():
        result = compressor.compress_text(text, source="tool:text_editor")
    assert result["text"] == text, "a protected read was rewritten"
    assert result.get("saved_tokens", 0) == 0


def test_detected_source_code_is_not_structurally_compressed():
    code = "def handler(req):\n    if req.method == 'GET':\n        return {'ok': True}\n" * 40
    with _use_config():
        result = compressor.compress_text(code, source="tool:code_execution")
    assert result["text"] == code
    assert result.get("saved_tokens", 0) == 0


def test_protections_can_be_turned_off_explicitly():
    """The guards default on, but the escape hatch still works."""
    with _use_config(protect_reads=False, protect_code=False):
        result = compressor.compress_text(_log_text(400), source="tool:text_editor")
    assert result.get("ccr_key"), "protect_reads=False should allow compression"


def test_disabled_compression_is_a_noop():
    with _use_config(enabled=False):
        result = compressor.compress_text(
            _log_text(), source="tool:code_execution_tool"
        )
    assert result["compressed"] is False
    assert result.get("skipped_reason") == "disabled"
    assert result["text"] == _log_text()


def test_small_outputs_are_left_alone():
    with _use_config():
        result = compressor.compress_text("ok", source="tool:code_execution_tool")
    assert result.get("skipped_reason") == "below_threshold"


def test_ccr_disabled_means_no_key_is_claimed():
    with _use_config(ccr_enabled=False):
        result = compressor.compress_text(
            _log_text(), source="tool:code_execution_tool"
        )
    if result.get("saved_tokens", 0) > 0:
        assert not result.get("ccr_key"), "claimed a key while CCR is disabled"


def test_normal_mode_without_the_package_fails_open():
    """Missing headroom-ai must degrade, never raise and never go lossy."""
    with _use_config(mode="normal", strategy="auto"):
        result = compressor.compress_text(
            _log_text(300), source="tool:code_execution_tool"
        )
    assert isinstance(result.get("text"), str) and result["text"]
    if result.get("saved_tokens", 0) > 0:
        assert result.get("ccr_key"), "lossy output without a retrieval key"



# ---------------------------------------------------------------------------
# P0.3 - old tool results are cleared only when recoverable
# ---------------------------------------------------------------------------


class _Msg:
    def __init__(self, content, ai=False, metadata=None):
        self.content = content
        self.ai = ai
        # helpers/history.py Message carries a metadata dict; the system-prompt
        # guard reads it, so the stub has to expose one.
        self.metadata = metadata if isinstance(metadata, dict) else {}
        self.tokens = 0
        self.summary = False

    def calculate_tokens(self):
        if isinstance(self.content, str):
            text = self.content
        elif isinstance(self.content, dict):
            text = str(self.content.get("tool_result") or "")
        else:
            text = ""
        return max(1, len(text) // 4)


class _Log:
    def __init__(self):
        self.entries = []

    def log(self, type="info", content="", **kw):
        self.entries.append({"type": type, "content": content})


class _Agent:
    agent_name = "tester"

    def __init__(self, messages):
        self.history = types.SimpleNamespace(all_messages=lambda: messages)
        self.context = types.SimpleNamespace(id="ctx-test", log=_Log())


def _tool_result(text):
    return _Msg({"tool_name": "code_execution", "tool_result": text})


def _cleared(messages):
    return [m for m in messages if "cleared by headroom" in str(m.content)]


def _run_clear(messages, **cfg_overrides):
    mod = _load(
        "extensions.python.message_loop_prompts_before._07_clear_old_tool_results"
    )
    with _use_config(**cfg_overrides):
        mod.ClearOldToolResults(_Agent(messages)).execute(data={})


def test_old_results_clear_and_recent_ones_survive():
    messages = [_Msg("task")] + [_tool_result(_log_text(300)) for _ in range(6)]
    _run_clear(messages, clear_keep_recent=3, clear_min_tokens=100)
    assert len(_cleared(messages)) == 3, "expected the 3 oldest results to be cleared"
    for msg in messages[4:]:
        assert "cleared by headroom" not in str(msg.content), "recent result cleared"


def test_cleared_results_are_recoverable_from_ccr():
    """The losslessness claim, end to end, through the same config the hook used.

    Retrieval resolves the CCR path from the plugin config, so the lookup has
    to happen inside the same config context as the clear - exactly as it does
    at runtime, where both read one config.
    """
    messages = [_Msg("task")] + [_tool_result(_log_text(120)) for _ in range(4)]
    restored = None
    with _use_config(clear_keep_recent=1, clear_min_tokens=100):
        mod = _load(
            "extensions.python.message_loop_prompts_before._07_clear_old_tool_results"
        )
        mod.ClearOldToolResults(_Agent(messages)).execute(data={})
        cleared = _cleared(messages)
        assert cleared, "nothing was cleared"
        key = str(cleared[0].content).split("CCR key ")[1].split(" ")[0]
        restored = compressor.retrieve_original(key, agent=None)
    assert restored and "INFO" in restored, "original not recoverable"


def test_nothing_is_cleared_when_ccr_is_unavailable():
    """The losslessness claim depends on this: no store, no clearing."""
    messages = [_Msg("task")] + [_tool_result(_log_text(300)) for _ in range(5)]
    original = [str(m.content) for m in messages]
    _run_clear(messages, clear_keep_recent=1, clear_min_tokens=100, ccr_enabled=False)
    assert [str(m.content) for m in messages] == original


def test_clearing_is_idempotent():
    messages = [_Msg("task")] + [_tool_result(_log_text(300)) for _ in range(5)]
    _run_clear(messages, clear_keep_recent=1, clear_min_tokens=100)
    after_first = [str(m.content) for m in messages]
    _run_clear(messages, clear_keep_recent=1, clear_min_tokens=100)
    assert [str(m.content) for m in messages] == after_first


def test_exempt_tools_are_never_cleared():
    messages = [_tool_result(_log_text(300)) for _ in range(5)]
    _run_clear(
        messages,
        clear_keep_recent=0,
        clear_min_tokens=100,
        clear_exempt_tools=["code_execution"],
    )
    assert not _cleared(messages)


def test_clearing_respects_the_minimum_size():
    messages = [_tool_result("tiny result") for _ in range(5)]
    _run_clear(messages, clear_keep_recent=0, clear_min_tokens=100000)
    assert not _cleared(messages)


def test_token_counts_are_refreshed_after_clearing():
    """A stale per-message token count makes the context budget a lie."""
    messages = [_Msg("task")] + [_tool_result(_log_text(300)) for _ in range(4)]
    for msg in messages:
        msg.tokens = msg.calculate_tokens()
    _run_clear(messages, clear_keep_recent=1, clear_min_tokens=100)
    for msg in _cleared(messages):
        assert msg.tokens < 200, "token count was not recomputed after clearing"

# ---------------------------------------------------------------------------
# Auto-clarity must protect the destructive stage too
# ---------------------------------------------------------------------------


def test_auto_clarity_prevents_destructive_clearing():
    """Regression: the safety flag used to protect only compression.

    `_05_auto_clarity` sets a per-context skip flag when the user asks for
    something destructive. `_10_compress_history` honoured it, but `_07` -
    the hook that actually deletes live tool output from history - did not,
    so the turn that most needed protecting was the one that lost its
    results anyway.
    """
    clarity.set_skip("ctx-test", "rm_rf")
    try:
        messages = [_Msg("task")] + [_tool_result(_log_text(300)) for _ in range(5)]
        original = [str(m.content) for m in messages]
        _run_clear(messages, clear_keep_recent=1, clear_min_tokens=100)
        assert [str(m.content) for m in messages] == original, (
            "a destructive-command turn had its history cleared anyway"
        )
    finally:
        clarity.clear("ctx-test")


def test_clearing_still_works_when_no_safety_flag_is_set():
    """The guard must not disable the feature it is guarding."""
    assert clarity.peek_skip("ctx-test") is None
    messages = [_Msg("task")] + [_tool_result(_log_text(300)) for _ in range(4)]
    _run_clear(messages, clear_keep_recent=1, clear_min_tokens=100)
    assert _cleared(messages), "clearing stopped working for ordinary turns"


def test_peek_skip_does_not_consume_the_flag():
    """Several stages read the flag; only the last one may consume it.

    `_07` peeks so that `_10_compress_history`, which runs later in the same
    extension point, still sees the flag and protects its own pass.
    """
    clarity.set_skip("ctx-peek", "rm_rf")
    try:
        assert clarity.peek_skip("ctx-peek") == "rm_rf"
        assert clarity.peek_skip("ctx-peek") == "rm_rf", "peek consumed the flag"
        assert clarity.consume_skip("ctx-peek") == "rm_rf"
        assert clarity.consume_skip("ctx-peek") is None, "consume is not once-only"
    finally:
        clarity.clear("ctx-peek")


def test_expired_safety_flag_protects_nothing():
    """A flag older than its TTL must not silently disable the feature."""
    clarity.set_skip("ctx-stale", "rm_rf")
    try:
        store = json.loads(clarity.FLAGS_PATH.read_text(encoding="utf-8"))
        store["flags"]["ctx-stale"]["set_at"] = (
            int(time.time()) - clarity.FLAG_TTL_SECONDS - 60
        )
        clarity.FLAGS_PATH.write_text(json.dumps(store), encoding="utf-8")
        assert clarity.peek_skip("ctx-stale") is None
        assert clarity.consume_skip("ctx-stale") is None
    finally:
        clarity.clear("ctx-stale")


# ---------------------------------------------------------------------------
# Bounded growth: retention and byte budget
# ---------------------------------------------------------------------------


def test_stats_retention_bounds_an_unbounded_log():
    """The events log must not grow forever.

    One row is written per compression *and* per skip, so a long-lived
    install reaches tens of thousands of rows. Retention is what keeps the
    dashboard queries cheap and the file from growing without limit.
    """
    from usr.plugins.caveman.helpers.headroom import stats as hr_stats

    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "stats.db"
        cfg = {
            **plugins_config.DEFAULTS["headroom"],
            "stats_enabled": True,
            "stats_path": str(db),
            "stats_max_rows": 25,
            "stats_retention_days": 0,
        }
        for _ in range(120):
            rec = hr_stats.StatsRecorder(cfg)
            rec.record(
                kind="compress_text", source="tool:x", input_tokens=4, output_tokens=2
            )
            rec.close()
        # Release the pooled handle inside the `with`, or Windows refuses to
        # delete the directory it is holding open.
        _close_pools()
        total = _query_db(db, "SELECT COUNT(*) FROM events")[0][0]
        newest = _query_db(db, "SELECT MAX(id) FROM events")[0][0]
    assert total <= 25, f"retention did not bound the log: {total} rows"
    assert newest is not None, "retention deleted everything"


def test_stats_age_retention_drops_only_expired_rows():
    import time as _time

    from usr.plugins.caveman.helpers.headroom import stats as hr_stats

    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "stats.db"
        base = {
            **plugins_config.DEFAULTS["headroom"],
            "stats_enabled": True,
            "stats_path": str(db),
            "stats_max_rows": 0,
        }
        rec = hr_stats.StatsRecorder({**base, "stats_retention_days": 0})
        for _ in range(5):
            rec.record(kind="ancient", source="s", input_tokens=1, output_tokens=1)
        rec.close()

        _exec_db(db, "UPDATE events SET ts = ?", (_time.time() - 400 * 86400,))

        rec = hr_stats.StatsRecorder({**base, "stats_retention_days": 30})
        rec.record(kind="fresh", source="s", input_tokens=1, output_tokens=1)
        rec.close()
        _close_pools()

        ancient = _query_db(db, "SELECT COUNT(*) FROM events WHERE kind='ancient'")[0][0]
        fresh = _query_db(db, "SELECT COUNT(*) FROM events WHERE kind='fresh'")[0][0]
    assert ancient == 0, "expired rows survived retention"
    assert fresh == 1, "retention deleted a row inside the window"


# ---------------------------------------------------------------------------
# K1-K4 - the remaining audit findings
# ---------------------------------------------------------------------------


def test_expose_compress_tool_actually_gates_the_tool():
    """K1: the setting was a checkbox that no backend read.

    Agent Zero registers plugin tools by directory and has no runtime hook to
    withdraw one, so the honest implementation is to refuse the call and say
    why - not to keep working while the UI claims it is off.
    """
    from usr.plugins.caveman.tools.compress_text import CompressText

    async def _call(action, expose):
        with _use_config(expose_compress_tool=expose):
            tool = CompressText(agent=None, args={"action": action, "text": "x"})
            return await tool.execute()

    # One loop for both calls. Creating a fresh loop per call and never
    # closing it leaks a thread and hangs interpreter shutdown.
    async def _both():
        return await _call("compress", False), await _call("toggle_info", False)

    loop = asyncio.new_event_loop()
    try:
        off, info = loop.run_until_complete(_both())
    finally:
        loop.close()

    assert "disabled" in str(off.message).lower(), "off did not refuse the call"
    # Diagnostics stay available, or the setting becomes undebuggable.
    assert "disabled" not in str(info.message).lower(), "toggle_info was refused"


def test_never_compress_system_prompts_is_enforced_not_assumed():
    """K2: the guarantee now has an explicit, tested guard behind it.

    System prompts never enter history in Agent Zero, so this predicate is
    structurally unreachable today - the point is that it is *enforced*
    rather than resting on that assumption.
    """
    from usr.plugins.caveman.helpers.plugins_config import DEFAULTS

    assert DEFAULTS["headroom"]["never_compress_system_prompts"] is True

    mod = _load(
        "extensions.python.message_loop_prompts_before._10_compress_history"
    )
    for msg in (
        _Msg("hi", metadata={"role": "system"}),
        _Msg("hi", metadata={"is_system_prompt": True}),
        _Msg({"role": "system", "content": "hi"}),
    ):
        assert mod._is_system_prompt(msg) is True, f"missed: {msg!r}"
    for msg in (_Msg("hi"), _Msg({"content": "hi"}), _Msg("hi", ai=True)):
        assert mod._is_system_prompt(msg) is False, f"false positive: {msg!r}"


def test_per_chat_lock_survives_concurrent_writers():
    """K3: the override store is guarded across processes, not just threads."""
    from usr.plugins.caveman.helpers.headroom import per_chat

    # A held lock must be released cleanly, or every later write would wait.
    with per_chat._FileLock(per_chat.LOCK_PATH):
        assert per_chat.LOCK_PATH.exists(), "lock was not taken"
    assert not per_chat.LOCK_PATH.exists(), "lock was not released"

    # A stale lock left by a dead process must be broken, not waited on.
    per_chat.LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    per_chat.LOCK_PATH.write_text(str(time.time()), encoding="utf-8")
    import os as _os

    old = time.time() - per_chat._LOCK_WAIT_SECONDS * 10
    _os.utime(per_chat.LOCK_PATH, (old, old))
    with per_chat._FileLock(per_chat.LOCK_PATH):
        pass
    assert not per_chat.LOCK_PATH.exists(), "stale lock was not cleaned up"


def test_per_chat_concurrent_writers_lose_nothing():
    """K3: the read-modify-write must not drop entries under contention.

    The threading.Lock alone only serialises threads inside one process, which
    is exactly why the file lock was added. This asserts the observable
    property rather than the mechanism: every write is still in the file.
    """
    import json
    import threading
    from pathlib import Path

    from usr.plugins.caveman.helpers.headroom import per_chat

    with tempfile.TemporaryDirectory() as tmp:
        store = Path(tmp) / "overrides.json"
        lock = Path(tmp) / "overrides.lock"
        orig_store, orig_lock = per_chat.STORE_PATH, per_chat.LOCK_PATH
        per_chat.STORE_PATH, per_chat.LOCK_PATH = store, lock
        try:
            def _write(worker):
                for j in range(15):
                    per_chat.set_enabled(f"ctx-{worker}-{j}", True)

            threads = [threading.Thread(target=_write, args=(i,)) for i in range(5)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

            data = json.loads(store.read_text(encoding="utf-8"))
            assert len(data) == 75, f"lost updates: wrote 75, kept {len(data)}"
            assert not lock.exists(), "lock file was left behind"
        finally:
            per_chat.STORE_PATH, per_chat.LOCK_PATH = orig_store, orig_lock


def test_per_chat_lock_accepts_a_string_path():
    """The lock must not raise on the str paths the tests hand it."""
    from usr.plugins.caveman.helpers.headroom.per_chat import _FileLock

    with tempfile.TemporaryDirectory() as tmp:
        with _FileLock(str(Path(tmp) / "s.lock")):
            pass


def test_proxy_refuses_to_signal_a_recycled_pid():
    """K4: a stale PID must not be able to kill an unrelated process.

    This is the dangerous case because stop() signals a process *group*.

    The liveness probe is stubbed rather than exercised for real:
    `os.kill(pid, 0)` against this test process hangs on some Windows hosts,
    which is a platform quirk rather than anything the plugin controls. What
    matters here is the decision each liveness answer produces.
    """
    from usr.plugins.caveman.helpers.headroom import proxy_manager as pm_mod
    from usr.plugins.caveman.helpers.headroom.proxy_manager import ProxyManager

    pm = ProxyManager(
        {"proxy": {"binary": "/opt/venv-a0/bin/headroom", "port": 8787}}
    )
    original_kill = pm_mod.os.kill
    try:
        # Alive, but its command line is someone else's.
        pm_mod.os.kill = lambda pid, sig: None
        pm._read_cmdline = lambda pid: "/usr/bin/some-other-service --daemon"
        owned, why, could_verify = pm._verify_ownership(4242)
        assert owned is False, "an unrelated process was accepted as the proxy"
        assert could_verify is True
        assert "headroom" in why

        # Same binary name, but not the proxy subcommand.
        pm._read_cmdline = lambda pid: "/opt/venv-a0/bin/headroom version"
        owned, why, _ = pm._verify_ownership(4242)
        assert owned is False, "a non-proxy headroom process was accepted"
        assert "subcommand" in why

        # The real thing: both markers present.
        pm._read_cmdline = lambda pid: "/opt/venv-a0/bin/headroom proxy --port 8787"
        owned, why, _ = pm._verify_ownership(4242)
        assert owned is True, "the genuine proxy was rejected"

        # A dead pid is reported without consulting the command line at all.
        def _dead(pid, sig):
            raise ProcessLookupError()

        pm_mod.os.kill = _dead
        owned, why, could_verify = pm._verify_ownership(4242)
        assert owned is False and could_verify is True
        assert "no such process" in why
    finally:
        pm_mod.os.kill = original_kill

    # An invalid pid is rejected before any probe.
    owned, why, could_verify = pm._verify_ownership(0)
    assert owned is False and could_verify is True
    assert "not a valid pid" in why


def test_proxy_ownership_is_conservative_when_unverifiable():
    """Unverifiable must be reported as unknown, never as someone else's."""
    from usr.plugins.caveman.helpers.headroom.proxy_manager import ProxyManager

    from usr.plugins.caveman.helpers.headroom import proxy_manager as pm_mod

    pm = ProxyManager({"proxy": {"binary": "/opt/venv-a0/bin/headroom"}})
    original_kill = pm_mod.os.kill
    try:
        pm_mod.os.kill = lambda pid, sig: None
        pm._read_cmdline = lambda pid: None
        owned, why, could_verify = pm._verify_ownership(4242)
        assert owned is False
        assert could_verify is False, "an unreadable cmdline claimed certainty"
        assert "not readable" in why
    finally:
        pm_mod.os.kill = original_kill


def test_ccr_respects_its_byte_budget_under_pressure():
    """A bounded cache is the contract; eviction must actually hold it."""
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "ccr.db"
        cfg = {
            **plugins_config.DEFAULTS["headroom"],
            "ccr_enabled": True,
            "ccr_path": str(db),
            "ccr_max_bytes": 20000,
            "ccr_ttl_days": 0,
        }
        cache = CcrCache(cfg)
        for i in range(40):
            cache.put(f"key-{i}", "z" * 3000, 750, 200, source="tool:x")
        cache.close()
        _close_pools()

        used = _query_db(db, "SELECT COALESCE(SUM(length(original)),0) FROM ccr")[0][0]
    assert used <= 20000, f"cache exceeded ccr_max_bytes: {used}"

# ---------------------------------------------------------------------------
# P0.4 - coexistence with the standalone plugin
# ---------------------------------------------------------------------------


def _both_active(standalone_on=True, combined_on=True):
    for on in (standalone_on, combined_on):
        pass
    return None


def test_no_standalone_plugin_means_no_conflict():
    _no_standalone()
    try:
        report = coexistence.overlap()
        assert report["present"] is False
        assert report["overlaps"] is False
        assert report["error"] == ""
    finally:
        _restore_plugins()


def test_present_but_disabled_is_not_a_conflict():
    with tempfile.TemporaryDirectory() as tmp:
        _fake_standalone(tmp, {"enabled": False})
        PLUGINS.get_toggle_state = lambda name: "disabled"
        try:
            assert coexistence.overlap()["overlaps"] is False
        finally:
            _restore_plugins()


def test_both_active_is_reported_as_a_conflict():
    with tempfile.TemporaryDirectory() as tmp:
        _fake_standalone(tmp, {"enabled": True})
        PLUGINS.get_toggle_state = lambda name: "enabled"
        plugins_config.get_config = lambda agent=None: {
            "enabled": True,
            "level": "full",
            "headroom": {**plugins_config.DEFAULTS["headroom"], "enabled": True},
        }
        try:
            report = coexistence.overlap()
            assert report["overlaps"] is True
            assert "Plugins UI" in report["guidance"]
        finally:
            _restore_config()
            _restore_plugins()


def test_detection_never_writes_to_the_other_plugin():
    """A report that changed the other plugin's state would be a bug."""
    with tempfile.TemporaryDirectory() as tmp:
        root = _fake_standalone(tmp, {"enabled": True})
        before = (root / "config.json").read_text(encoding="utf-8")
        PLUGINS.get_toggle_state = lambda name: "enabled"
        plugins_config.get_config = lambda agent=None: {
            "enabled": True,
            "level": "full",
            "headroom": {**plugins_config.DEFAULTS["headroom"], "enabled": True},
        }
        try:
            coexistence.overlap()
            assert (root / "config.json").read_text(encoding="utf-8") == before
            assert not list(root.glob(".disabled"))
            assert not list(root.glob(".enabled"))
        finally:
            _restore_config()
            _restore_plugins()


def test_detection_survives_a_broken_framework_helper():
    def boom(name):
        raise RuntimeError("framework exploded")

    PLUGINS.find_plugin_dir = boom
    try:
        report = coexistence.standalone_state()
        assert report["present"] is False
        assert "framework exploded" in report["error"]
    finally:
        _restore_plugins()


def test_no_runtime_module_imports_the_standalone_package():
    """P1.2 acceptance: no accidental import of the other plugin's code.

    Parsed rather than grepped: naming the other plugin in a comment or a
    string constant is legitimate (the coexistence and migration modules do
    exactly that), importing it is not.
    """
    import ast

    banned = "usr.plugins.headroom_compress"
    offenders = []
    this_file = Path(__file__).resolve()
    for path in sorted(PLUGIN.rglob("*.py")):
        if "__pycache__" in str(path) or path.resolve() == this_file:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            for name in names:
                if name.startswith(banned):
                    rel = path.relative_to(PLUGIN)
                    offenders.append(f"{rel}:{node.lineno}: {name}")
    assert not offenders, "standalone imports found: " + "; ".join(offenders)



# ---------------------------------------------------------------------------
# P3.1 / P3.2 - migration: preview, apply, rollback
# ---------------------------------------------------------------------------


def test_preview_writes_nothing():
    with tempfile.TemporaryDirectory() as tmp:
        dest = _dest_config(tmp, {"enabled": True, "level": "full"})
        before = dest.read_text(encoding="utf-8")
        _fake_standalone(tmp, {"enabled": True, "ccr_ttl_days": 30})
        try:
            plan = migration.build_plan()
            # headroom.enabled is not set in the destination, so it is
            # importable; the top-level "enabled" belongs to Caveman and is
            # not the same key.
            assert plan["to_import"] == {"enabled": True, "ccr_ttl_days": 30}
            assert dest.read_text(encoding="utf-8") == before
        finally:
            _restore_plugins()


def test_apply_requires_an_explicit_confirm():
    with tempfile.TemporaryDirectory() as tmp:
        dest = _dest_config(tmp, {"enabled": True, "level": "full"})
        _fake_standalone(tmp, {"ccr_ttl_days": 30})
        try:
            plan = migration.build_plan()
            outcome = migration.apply_plan(plan, confirm=False)
            assert outcome["applied"] is False
            assert "confirm" in outcome["error"]
            assert "headroom" not in json.loads(dest.read_text(encoding="utf-8"))
        finally:
            _restore_plugins()


def test_apply_imports_missing_keys_and_backs_up():
    with tempfile.TemporaryDirectory() as tmp:
        dest = _dest_config(tmp, {"enabled": True, "level": "full"})
        _fake_standalone(tmp, {"ccr_ttl_days": 30, "mode": "normal"})
        try:
            plan = migration.build_plan()
            outcome = migration.apply_plan(plan, confirm=True)
            assert outcome["applied"] is True and outcome["changed"] is True
            assert outcome["written_keys"] == ["ccr_ttl_days", "mode"]
            written = json.loads(dest.read_text(encoding="utf-8"))
            assert written["headroom"]["ccr_ttl_days"] == 30
            assert written["headroom"]["mode"] == "normal"
            # Caveman's own keys are untouched.
            assert written["enabled"] is True and written["level"] == "full"
            assert Path(outcome["backup"]).exists(), "no backup was written"
        finally:
            _restore_plugins()


def test_apply_never_overwrites_a_value_set_here():
    with tempfile.TemporaryDirectory() as tmp:
        dest = _dest_config(tmp, {"headroom": {"ccr_ttl_days": 7, "mode": "safe"}})
        _fake_standalone(tmp, {"ccr_ttl_days": 30, "mode": "normal"})
        try:
            plan = migration.build_plan()
            assert {c["key"] for c in plan["conflicts"]} == {"ccr_ttl_days", "mode"}
            outcome = migration.apply_plan(plan, confirm=True)
            assert outcome["changed"] is False
            written = json.loads(dest.read_text(encoding="utf-8"))
            assert written["headroom"] == {"ccr_ttl_days": 7, "mode": "safe"}
        finally:
            _restore_plugins()


def test_apply_is_idempotent():
    with tempfile.TemporaryDirectory() as tmp:
        _dest_config(tmp, {"enabled": True})
        _fake_standalone(tmp, {"ccr_ttl_days": 30})
        try:
            first = migration.apply_plan(migration.build_plan(), confirm=True)
            assert first["changed"] is True
            second = migration.apply_plan(migration.build_plan(), confirm=True)
            assert second["applied"] is True and second["changed"] is False
            assert second["written_keys"] == []
        finally:
            _restore_plugins()


def test_unknown_keys_are_preserved_not_dropped():
    """A setting this plugin does not implement must survive the round trip."""
    with tempfile.TemporaryDirectory() as tmp:
        dest = _dest_config(tmp, {"headroom": {"future_option": "keep-me"}})
        _fake_standalone(tmp, {"future_option": "keep-me", "ccr_ttl_days": 30})
        try:
            plan = migration.build_plan()
            assert "future_option" in plan["unknown_keys"]
            migration.apply_plan(plan, confirm=True)
            written = json.loads(dest.read_text(encoding="utf-8"))
            assert written["headroom"]["future_option"] == "keep-me"
        finally:
            _restore_plugins()


def test_malformed_source_config_is_reported_not_raised():
    with tempfile.TemporaryDirectory() as tmp:
        _dest_config(tmp, {"enabled": True})
        _fake_standalone(tmp, "{not json at all")
        try:
            plan = migration.build_plan()
            assert plan["to_import"] == {}
            assert plan["warnings"], "a malformed source must be surfaced"
            outcome = migration.apply_plan(plan, confirm=True)
            assert outcome["error"] == "" and outcome["changed"] is False
        finally:
            _restore_plugins()


def test_broken_destination_blocks_apply():
    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp) / "config.json"
        dest.write_text("{ broken", encoding="utf-8")
        hr_config.destination_config_path = lambda: dest
        _fake_standalone(tmp, {"ccr_ttl_days": 30})
        try:
            plan = migration.build_plan()
            assert plan["destination"]["readable"] is False
            outcome = migration.apply_plan(plan, confirm=True)
            assert outcome["applied"] is False and outcome["error"]
            assert dest.read_text(encoding="utf-8") == "{ broken"
        finally:
            _restore_plugins()


def test_unavailable_registry_is_not_reported_as_not_installed():
    """"The plugin registry is down" and "it is not installed" are different
    problems with different fixes, so the plan must not conflate them."""
    with tempfile.TemporaryDirectory() as tmp:
        _dest_config(tmp, {"enabled": True})
        migration._standalone_dir = lambda: (
            None,
            "could not query the plugin registry: RuntimeError: down",
        )
        try:
            plan = migration.build_plan()
            assert plan["standalone"]["present"] is False
            assert "down" in plan["standalone"]["lookup_error"]
            assert any("down" in w for w in plan["warnings"])
            assert not any("not installed" in n for n in plan["notes"])
        finally:
            _restore_plugins()


def test_nothing_to_migrate_is_reported_clearly():
    with tempfile.TemporaryDirectory() as tmp:
        _dest_config(tmp, {"enabled": True})
        _no_standalone()
        try:
            plan = migration.build_plan()
            assert plan["standalone"]["present"] is False
            assert plan["to_import"] == {}
            assert any("not installed" in n for n in plan["notes"])
        finally:
            _restore_plugins()


def test_source_files_are_never_modified():
    with tempfile.TemporaryDirectory() as tmp:
        _dest_config(tmp, {"enabled": True})
        root = _fake_standalone(
            tmp, {"ccr_ttl_days": 30}, per_chat={"ctx1": {"enabled": False}}
        )
        before = {
            p.relative_to(root).as_posix(): p.read_text(encoding="utf-8")
            for p in root.rglob("*")
            if p.is_file()
        }
        try:
            migration.apply_plan(migration.build_plan(), confirm=True)
            after = {
                p.relative_to(root).as_posix(): p.read_text(encoding="utf-8")
                for p in root.rglob("*")
                if p.is_file()
            }
            assert before == after, "the standalone plugin was modified"
        finally:
            _restore_plugins()


def test_chat_overrides_are_reported_but_not_imported():
    with tempfile.TemporaryDirectory() as tmp:
        dest = _dest_config(tmp, {"enabled": True})
        _fake_standalone(
            tmp, {"ccr_ttl_days": 30}, per_chat={"ctx1": {"enabled": False}}
        )
        try:
            plan = migration.build_plan()
            assert plan["chat_overrides"]["count"] == 1
            assert "headroom" not in json.loads(dest.read_text(encoding="utf-8"))
        finally:
            _restore_plugins()


def test_scoped_sources_are_reported_as_manual_work():
    with tempfile.TemporaryDirectory() as tmp:
        _dest_config(tmp, {"enabled": True})
        _fake_standalone(tmp, {"ccr_ttl_days": 30})
        scoped = Path(tmp) / "project.json"
        scoped.write_text(json.dumps({"ccr_ttl_days": 99}), encoding="utf-8")
        PLUGINS.find_plugin_assets = lambda *a, **k: [
            {"project_name": "proj", "agent_profile": "", "path": str(scoped)}
        ]
        try:
            plan = migration.build_plan()
            assert any(m["scope"] == "project" for m in plan["manual"])
        finally:
            _restore_plugins()



# ---------------------------------------------------------------------------
# P4.3 - nothing is registered twice
# ---------------------------------------------------------------------------


def _subclasses(path, base_name):
    import ast

    tree = ast.parse(path.read_text(encoding="utf-8"))
    found = []
    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue
        for b in node.bases:
            name = b.id if isinstance(b, ast.Name) else getattr(b, "attr", "")
            if name == base_name:
                found.append(node.name)
                break
    return found


def test_each_extension_file_defines_exactly_one_extension():
    for path in sorted((PLUGIN / "extensions" / "python").rglob("*.py")):
        found = _subclasses(path, "Extension")
        rel = path.relative_to(PLUGIN)
        assert len(found) == 1, f"{rel} defines {len(found)} Extension classes: {found}"


def test_each_api_file_defines_exactly_one_handler():
    for path in sorted((PLUGIN / "api").rglob("*.py")):
        found = _subclasses(path, "ApiHandler")
        rel = path.relative_to(PLUGIN)
        assert len(found) == 1, f"{rel} defines {len(found)} ApiHandler classes: {found}"


def test_banner_ids_are_unique():
    import re

    ids = []
    for path in sorted((PLUGIN / "extensions" / "python" / "banners").rglob("*.py")):
        src = path.read_text(encoding="utf-8")
        ids.extend(re.findall(r'"id":\s*"([a-z0-9_]+)"', src))
        ids.extend(re.findall(r'^CARD_ID\s*=\s*"([a-z0-9_]+)"', src, re.M))
        ids.extend(re.findall(r'^BANNER_ID\s*=\s*"([a-z0-9_]+)"', src, re.M))
    assert len(ids) == len(set(ids)), f"duplicate banner ids: {ids}"


def _strip_js_comments(src: str) -> str:
    """Drop // and /* */ comments.

    The dashboard store documents a previously wrong URL in a comment; that is
    history, not a live call, and flagging it would be a false positive.
    """
    import re

    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return re.sub(r"//[^\n]*", "", src)


def test_webui_calls_stay_inside_this_plugin():
    """A leftover /plugins/headroom_compress/ URL would 404 at runtime."""
    import re

    for path in sorted((PLUGIN / "webui").rglob("*.js")):
        src = _strip_js_comments(path.read_text(encoding="utf-8"))
        for plugin in re.findall(r"/plugins/([a-z_]+)/", src):
            assert plugin == "caveman", f"{path.name} calls /plugins/{plugin}/"


def test_config_html_opens_only_modals_that_exist():
    import re

    src = (PLUGIN / "webui" / "config.html").read_text(encoding="utf-8")
    for modal in re.findall(r"openModal\('([^']+)'\)", src):
        rel = modal.lstrip("/").replace("plugins/caveman/", "")
        target = PLUGIN / rel
        assert target.exists(), f"config.html opens missing modal {modal}"


# ---------------------------------------------------------------------------
# 2026-09-28 remediation - API/WebUI/tools fixes
# ---------------------------------------------------------------------------


def test_webui_module_scripts_point_at_real_files():
    """A <script src> that 404s leaves the modal stuck on 'Loading...' forever
    (importComponent has no .catch), so every /plugins/caveman/ reference must
    resolve to a file that actually ships."""

    import re

    for path in sorted((PLUGIN / "webui").rglob("*.html")):
        src = path.read_text(encoding="utf-8")
        for ref in re.findall(r'<script[^>]*\ssrc="([^"]+)"', src):
            if not ref.startswith("/plugins/caveman/"):
                continue  # framework assets (/js/...) are served by A0 itself
            rel = ref[len("/plugins/"):]  # caveman/webui/...
            assert (PLUGIN.parent / rel).is_file(), f"{path.name} references missing {ref}"


def test_dashboard_modal_uses_the_real_store_path():
    src = (PLUGIN / "webui" / "headroom-dashboard.html").read_text(encoding="utf-8")
    assert "headroom-dashboard-store.js" in src, (
        "dashboard must load its own store, not the (renamed-away) dashboard-store.js"
    )


def _json_blob(message: str) -> dict:
    """Extract the first ```json block a tool/API message contains."""
    blob = str(message).split("```json\n", 1)[1].split("\n```", 1)[0]
    return json.loads(blob)


def _run_tool(tool_cls, args, **cfg_overrides):
    tool = tool_cls(agent=None, args=args)
    loop = asyncio.new_event_loop()
    try:
        with _use_config(**cfg_overrides):
            return loop.run_until_complete(tool.execute())
    finally:
        loop.close()


# --- headroom_config API: standalone settings modal get/set -----------------


def test_headroom_config_get_returns_effective_config():
    mod = _load("api.headroom_config")
    handler = mod.HeadroomConfig()
    loop = asyncio.new_event_loop()
    try:
        res = loop.run_until_complete(handler.process({"action": "get"}, None))
    finally:
        loop.close()
    assert res["ok"] is True
    assert isinstance(res["config"], dict)
    assert "headroom" in res["config"], "the modal binds against config.headroom"
    assert res["coexistence"]["present"] in (True, False)


def test_headroom_config_set_writes_whitelisted_section_only():
    mod = _load("api.headroom_config")
    handler = mod.HeadroomConfig()
    loop = asyncio.new_event_loop()
    try:
        with tempfile.TemporaryDirectory() as tmp:
            dest = _dest_config(
                tmp, {"enabled": True, "level": "full", "headroom": {"future_key": "keep"}}
            )
            try:
                res = loop.run_until_complete(
                    handler.process(
                        {
                            "action": "set",
                            "section": {
                                "enabled": True,
                                "ccr_ttl_days": 9,
                                "hacker_key": "x",
                                "proxy": {"port": 9000, "evil": 1},
                            },
                        },
                        None,
                    )
                )
                written = json.loads(dest.read_text(encoding="utf-8"))
            finally:
                _restore_destination()
    finally:
        loop.close()

    assert res["ok"] is True
    assert res["changed"] is True
    assert sorted(res["written_keys"]) == ["ccr_ttl_days", "enabled", "proxy"]
    # Caveman's own top-level keys survive.
    assert written["enabled"] is True and written["level"] == "full"
    section = written["headroom"]
    assert section["enabled"] is True and section["ccr_ttl_days"] == 9
    # Unknown incoming keys are dropped; unknown existing keys survive.
    assert "hacker_key" not in section
    assert section["future_key"] == "keep"
    # The proxy subsection is filtered against the documented proxy keys.
    assert section["proxy"]["port"] == 9000
    assert "evil" not in section["proxy"]


def test_headroom_config_set_rejects_bad_requests_without_raising():
    mod = _load("api.headroom_config")
    handler = mod.HeadroomConfig()
    loop = asyncio.new_event_loop()
    try:
        with tempfile.TemporaryDirectory() as tmp:
            _dest_config(tmp, {"enabled": True})
            try:
                bad_section = loop.run_until_complete(
                    handler.process({"action": "set", "section": "nope"}, None)
                )
                unknown = loop.run_until_complete(
                    handler.process({"action": "explode"}, None)
                )
                empty = loop.run_until_complete(
                    handler.process({"action": "set", "section": {"nope": 1}}, None)
                )
            finally:
                _restore_destination()
    finally:
        loop.close()

    assert bad_section.break_loop == 400
    assert bad_section.message["ok"] is False
    assert unknown.break_loop == 400
    # Nothing whitelisted -> refuse to write, but still a structured error.
    assert empty.break_loop == 500
    assert empty.message["ok"] is False


def test_headroom_config_set_refuses_a_broken_destination():
    mod = _load("api.headroom_config")
    handler = mod.HeadroomConfig()
    loop = asyncio.new_event_loop()
    try:
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "config.json"
            dest.write_text("{ broken", encoding="utf-8")
            hr_config.destination_config_path = lambda: dest
            try:
                res = loop.run_until_complete(
                    handler.process(
                        {"action": "set", "section": {"enabled": True}}, None
                    )
                )
                # Assert inside the tmpdir's lifetime (Windows deletes it with us).
                assert dest.read_text(encoding="utf-8") == "{ broken", (
                    "unusable file overwritten"
                )
            finally:
                _restore_destination()
    finally:
        loop.close()

    assert res.break_loop == 500
    assert "unusable" in res.message["error"]


# --- headroom_stats: window_hours=0 means lifetime --------------------------


def _run_stats(input, **cfg_overrides):
    mod = _load("api.headroom_stats")
    handler = mod.HeadroomStats()
    loop = asyncio.new_event_loop()
    try:
        with _use_config(**cfg_overrides):
            res = loop.run_until_complete(handler.process(input, None))
        _close_pools()
        return res
    finally:
        loop.close()


def test_stats_window_hours_zero_means_lifetime():
    res = _run_stats({"window_hours": 0})
    assert res["ok"] is True
    assert res["events"]["window_hours"] == 0
    assert res["events"]["summary_window"] == res["events"]["summary_lifetime"], (
        "window 0 must equal the lifetime summary, not the 24h default"
    )


def test_stats_missing_window_defaults_to_24h():
    assert _run_stats({})["events"]["window_hours"] == 24


def test_stats_invalid_window_defaults_to_24h():
    assert _run_stats({"window_hours": "abc"})["events"]["window_hours"] == 24


def test_stats_string_zero_is_lifetime_not_the_default():
    assert _run_stats({"window_hours": "0"})["events"]["window_hours"] == 0


def test_stats_explicit_window_is_honoured():
    assert _run_stats({"window_hours": 1})["events"]["window_hours"] == 1


# --- headroom_execute: reap the killed child --------------------------------


def test_execute_timeout_kills_and_reaps_the_child():
    ex_mod = _load("api.headroom_execute")
    handler = ex_mod.HeadroomExecute()

    class _FakeProc:
        def __init__(self):
            self.killed = False
            self.waited = False

        def kill(self):
            self.killed = True

        async def wait(self):
            self.waited = True

        async def communicate(self):
            await asyncio.sleep(3600)  # never finishes on its own

    created = {}

    async def _fake_exec(*cmd, **kw):
        created["cmd"] = cmd
        created["proc"] = _FakeProc()
        return created["proc"]

    async def _fake_wait_for(coro, timeout=None):
        coro.close()  # silence "coroutine never awaited"
        raise asyncio.TimeoutError()

    real_exec = ex_mod.asyncio.create_subprocess_exec
    real_wait_for = ex_mod.asyncio.wait_for
    ex_mod.asyncio.create_subprocess_exec = _fake_exec
    ex_mod.asyncio.wait_for = _fake_wait_for
    loop = asyncio.new_event_loop()
    try:
        res = loop.run_until_complete(handler.process({"args": ["--no-install"]}, None))
    finally:
        ex_mod.asyncio.create_subprocess_exec = real_exec
        ex_mod.asyncio.wait_for = real_wait_for
        loop.close()

    assert created["proc"].killed, "the timed-out child was never killed"
    assert created["proc"].waited, "the killed child was never reaped (zombie)"
    assert res.break_loop == 504
    assert res.message["ok"] is False
    assert "timed out" in res.message["error"]


# --- headroom_proxy / headroom_per_chat: blocking calls off the event loop --


def test_proxy_handler_dispatches_and_reports_errors():
    mod = _load("api.headroom_proxy")
    handler = mod.HeadroomProxy()
    calls = []

    class _FakePM:
        def __init__(self, cfg):
            calls.append("init")

        def status(self):
            calls.append("status")
            return {"running": False, "mode": "token"}

        def start(self):
            calls.append("start")
            return {"ok": True, "pid": 1, "url": "http://127.0.0.1:8787"}

    real_pm = mod.ProxyManager
    mod.ProxyManager = _FakePM
    loop = asyncio.new_event_loop()
    try:
        started = loop.run_until_complete(handler.process({"action": "start"}, None))
        status = loop.run_until_complete(handler.process({"action": "status"}, None))
        unknown = loop.run_until_complete(handler.process({"action": "bogus"}, None))
    finally:
        mod.ProxyManager = real_pm
        loop.close()

    assert "start" in calls, "start did not reach the manager"
    assert started.break_loop == 200 and started.message["ok"] is True
    assert "status" in calls and status["ok"] is True
    assert unknown.break_loop == 400


def test_per_chat_handler_set_and_clear_run_off_the_loop():
    mod = _load("api.headroom_per_chat")
    handler = mod.HeadroomPerChat()
    from usr.plugins.caveman.helpers.headroom import per_chat

    with tempfile.TemporaryDirectory() as tmp:
        store = Path(tmp) / "overrides.json"
        lock = Path(tmp) / "lock"
        orig_store, orig_lock = per_chat.STORE_PATH, per_chat.LOCK_PATH
        per_chat.STORE_PATH, per_chat.LOCK_PATH = store, lock
        loop = asyncio.new_event_loop()
        try:
            set_res = loop.run_until_complete(
                handler.process(
                    {"action": "set", "context_id": "ctx-1", "enabled": False}, None
                )
            )
            clear_res = loop.run_until_complete(
                handler.process({"action": "clear", "context_id": "ctx-1"}, None)
            )
            bad = loop.run_until_complete(
                handler.process({"action": "set", "context_id": ""}, None)
            )
        finally:
            loop.close()
            per_chat.STORE_PATH, per_chat.LOCK_PATH = orig_store, orig_lock

    assert set_res["ok"] is True and set_res["entry"]["enabled"] is False
    assert clear_res["ok"] is True
    assert bad.break_loop == 400


# --- headroom_retrieve: paging through large originals ----------------------


def test_retrieve_get_pages_large_originals():
    from usr.plugins.caveman.tools.headroom_retrieve import HeadroomRetrieve

    loop = asyncio.new_event_loop()
    try:
        with _use_config() as cfg:
            cache = CcrCache(cfg)
            original = "".join(f"line-{i:05d}\n" for i in range(1000))  # 11k chars
            key = "a" * 32
            cache.put(key, original, 3000, 100, source="tool:x")
            cache.close()

            first = loop.run_until_complete(
                HeadroomRetrieve(agent=None, args={"action": "get", "ccr_key": key}).execute()
            )
            rest = loop.run_until_complete(
                HeadroomRetrieve(
                    agent=None,
                    args={"action": "get", "ccr_key": key, "offset": 4000, "max_chars": 100000},
                ).execute()
            )
            oversized = loop.run_until_complete(
                HeadroomRetrieve(
                    agent=None,
                    args={"action": "get", "ccr_key": key, "max_chars": 999999},
                ).execute()
            )
            past_end = loop.run_until_complete(
                HeadroomRetrieve(
                    agent=None,
                    args={"action": "get", "ccr_key": key, "offset": len(original) + 10},
                ).execute()
            )
    finally:
        loop.close()

    first_msg = str(first.message)
    assert first_msg.count("line-") == len(original[:4000].rstrip().splitlines()), (
        "default page is not 4000 chars"
    )
    assert "[TRUNCATED" in first_msg and "offset=4000" in first_msg, (
        "the response must say more text exists and how to get it"
    )
    rest_msg = str(rest.message)
    assert "[TRUNCATED" not in rest_msg
    assert "line-00999" in rest_msg, "the remainder was not returned"

    over_len = len(str(oversized.message))
    assert over_len < 110_000, "max_chars cap not enforced"
    assert "past the end" in str(past_end.message)


def test_retrieve_docstring_documents_the_real_marker_format():
    doc = _load("tools.headroom_retrieve").__doc__ or ""
    assert "original saved as CCR key" in doc, (
        "the stale '[headroom: ... CCR key=...]' example must be the real marker"
    )
    assert "CCR key=" not in doc


# --- compress_text: force semantics + toggle_info state ---------------------


def test_compress_text_bypasses_min_tokens_when_enabled():
    """Explicit on-demand intent: force=True stops below_threshold rejections.

    force deliberately does NOT bypass the enabled gate - the next test pins
    the documented no-op contract that must survive this change.
    """
    from usr.plugins.caveman.tools.compress_text import CompressText

    text = _log_text(300)
    res = _run_tool(
        CompressText,
        {"action": "compress", "text": text},
        enabled=True,
        auto_compress_tool_outputs_min_tokens=100000,
        dry_run=False,
    )
    result = _json_blob(res.message)
    assert result["compressed"] is True, (
        f"force did not bypass the min_tokens gate: {result.get('skipped_reason')}"
    )
    assert result["text"] != text


def test_compress_text_stays_a_noop_when_disabled():
    """force is gated on the enabled flag, so 'off' remains a true no-op."""
    from usr.plugins.caveman.tools.compress_text import CompressText

    text = _log_text()
    res = _run_tool(CompressText, {"action": "compress", "text": text}, enabled=False)
    result = _json_blob(res.message)
    assert result["compressed"] is False
    assert result.get("skipped_reason") == "disabled"
    assert result["text"] == text


def test_toggle_info_reports_the_real_toggle_state():
    """get_toggle_state returns a plain string - the .value access read ''."""
    from usr.plugins.caveman.tools.compress_text import CompressText

    PLUGINS.get_toggle_state = lambda name: "enabled"
    try:
        res = _run_tool(CompressText, {"action": "toggle_info"}, enabled=True)
    finally:
        _restore_plugins()

    data = _json_blob(res.message)
    assert data["plugin_toggle"] == "on", data


# --- hooks: fire-and-forget proxy auto-start --------------------------------


def test_hooks_install_dispatches_proxy_auto_start_only_when_configured():
    import threading

    hooks = _load("hooks")
    from usr.plugins.caveman.helpers.headroom import proxy_manager as pm_mod

    calls = []
    event = threading.Event()

    def _fake_auto_start():
        calls.append("start")
        event.set()

    real = pm_mod.auto_start_if_configured
    pm_mod.auto_start_if_configured = _fake_auto_start
    try:
        with _use_config(enabled=True, proxy={"auto_start": True}):
            hooks.install()
            assert event.wait(timeout=5), "auto-start thread never ran"

        with _use_config(enabled=False, proxy={"auto_start": True}):
            before = len(calls)
            hooks.install()
            time.sleep(0.2)
            assert len(calls) == before, "auto-start ran while headroom is disabled"

        with _use_config(enabled=True, proxy={"auto_start": False}):
            hooks.install()
            time.sleep(0.2)
            assert len(calls) == before, "auto-start ran with proxy.auto_start off"
    finally:
        pm_mod.auto_start_if_configured = real


# ---------------------------------------------------------------------------
# runner (pytest is optional)
# ---------------------------------------------------------------------------


def _main() -> int:
    tests = [
        (name, obj)
        for name, obj in sorted(globals().items())
        if name.startswith("test_") and callable(obj)
    ]
    failed = []
    for name, fn in tests:
        try:
            fn()
            print(f"    ok    {name}")
        except Exception as exc:  # noqa: BLE001
            import traceback

            failed.append(name)
            print(f"    FAIL  {name}: {exc}")
            traceback.print_exc()
    print()
    print(f"    {len(tests) - len(failed)}/{len(tests)} passed")
    if failed:
        print(f"    failed: {', '.join(failed)}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(_main())

