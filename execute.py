"""
Caveman plugin - user-triggered maintenance script (v0.4.0).

Run from the Plugins UI or via `python /a0/usr/plugins/caveman/execute.py`.
Returns 0 on success, non-zero on failure.
"""

import os
import sys

PLUGIN_NAME = "caveman"
EXPECTED_VERSION = "0.4.0"


def _check_files(here):
    required = [
        "plugin.yaml",
        "default_config.yaml",
        "hooks.py",
        "LICENSE",
        "icon.svg",
        "install.py",
        # Prompts
        "prompts/caveman.system.style.md",
        "prompts/caveman.intensity.lite.md",
        "prompts/caveman.intensity.full.md",
        "prompts/caveman.intensity.ultra.md",
        "prompts/caveman.intensity.wenyan-lite.md",
        "prompts/caveman.intensity.wenyan-full.md",
        "prompts/caveman.intensity.wenyan-ultra.md",
        "prompts/caveman.auto_clarity.md",
        # Python extensions
        "extensions/python/system_prompt/_20_caveman_style.py",
        "extensions/python/banners/_10_caveman_discovery.py",
        "extensions/python/monologue_start/_30_caveman_command.py",
        "extensions/python/response_stream_end/_50_caveman_validate.py",
        "extensions/python/monologue_end/_70_caveman_stats.py",
        "extensions/python/message_loop_prompts_before/_60_caveman_shrink_tools.py",
        # Helpers + API
        "helpers/state.py",
        "api/caveman_state.py",
        "api/caveman_stats.py",
        # WebUI
        "extensions/webui/page-head/caveman-injector.html",
        # Sub-skills
        "skills/caveman-stats/SKILL.md",
        "skills/caveman-commit/SKILL.md",
        "skills/caveman-review/SKILL.md",
        "skills/caveman-compress/SKILL.md",
        "skills/caveman-help/SKILL.md",
        # Cavecrew subagents
        "agents/cavecrew-investigator/agent.yaml",
        "agents/cavecrew-investigator/prompts/agent.system.main.role.md",
        "agents/cavecrew-builder/agent.yaml",
        "agents/cavecrew-builder/prompts/agent.system.main.role.md",
        "agents/cavecrew-reviewer/agent.yaml",
        "agents/cavecrew-reviewer/prompts/agent.system.main.role.md",
        # Benchmarks (Plan C)
        "benchmarks/run.py",
        "benchmarks/prompts.json",
        "benchmarks/README.md",
    ]
    missing = [p for p in required if not os.path.isfile(os.path.join(here, p))]
    if missing:
        print(f"[{PLUGIN_NAME}] ERROR: missing files:")
        for m in missing:
            print(f"  - {m}")
        return 1
    print(f"[{PLUGIN_NAME}] OK: all {len(required)} required files present")
    return 0


def _check_manifest(here):
    try:
        import yaml
    except ImportError:
        yaml = None
    with open(os.path.join(here, "plugin.yaml"), "r", encoding="utf-8") as f:
        manifest = f.read()
    data = {}
    if yaml is not None:
        try:
            data = yaml.safe_load(manifest) or {}
        except Exception as e:
            print(f"[{PLUGIN_NAME}] ERROR: plugin.yaml is not valid YAML: {e}")
            return 2
    else:
        for line in manifest.splitlines():
            if ":" in line and not line.lstrip().startswith("#"):
                k, _, v = line.partition(":")
                data[k.strip()] = v.strip()
    name = (data.get("name") or "").strip()
    version = (data.get("version") or "").strip()
    if name != PLUGIN_NAME:
        print(f"[{PLUGIN_NAME}] ERROR: plugin name is {name!r}, expected {PLUGIN_NAME!r}")
        return 3
    if version not in ("0.1.0", "0.2.0", "0.3.0", EXPECTED_VERSION):
        print(f"[{PLUGIN_NAME}] WARN: version is {version!r}, expected {EXPECTED_VERSION!r}")
    else:
        print(f"[{PLUGIN_NAME}] OK: manifest version {version}")
    return 0


def _check_python_syntax(here):
    import py_compile
    py_files = [
        "hooks.py",
        "install.py",
        "extensions/python/system_prompt/_20_caveman_style.py",
        "extensions/python/banners/_10_caveman_discovery.py",
        "extensions/python/monologue_start/_30_caveman_command.py",
        "extensions/python/response_stream_end/_50_caveman_validate.py",
        "extensions/python/monologue_end/_70_caveman_stats.py",
        "extensions/python/message_loop_prompts_before/_60_caveman_shrink_tools.py",
        "helpers/state.py",
        "api/caveman_state.py",
        "api/caveman_stats.py",
        "benchmarks/run.py",
    ]
    for rel in py_files:
        full = os.path.join(here, rel)
        try:
            py_compile.compile(full, doraise=True)
            print(f"[{PLUGIN_NAME}] OK: {rel} compiles cleanly")
        except py_compile.PyCompileError as e:
            print(f"[{PLUGIN_NAME}] ERROR: {rel} has syntax errors: {e}")
            return 4
    return 0


def _check_state_roundtrip():
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        sys.path.insert(0, "/a0")
        from usr.plugins.caveman.helpers import state as caveman_state
    except Exception as e:
        print(f"[{PLUGIN_NAME}] WARN: could not import helpers.state: {e}")
        return 0
    test_id = "_caveman_healthcheck_" + str(os.getpid())
    try:
        ok1 = caveman_state.set_level(test_id, "ultra")
        got1 = caveman_state.get_level(test_id, default="full")
        if not ok1 or got1 != "ultra":
            print(f"[{PLUGIN_NAME}] ERROR: state roundtrip failed (set={ok1}, get={got1!r})")
            return 5
        caveman_state.set_level(test_id, None)
        got2 = caveman_state.get_level(test_id, default="full")
        if got2 != "full":
            print(f"[{PLUGIN_NAME}] ERROR: state clear failed (get={got2!r})")
            return 6
        print(f"[{PLUGIN_NAME}] OK: per-chat state roundtrip works (set, get, clear)")
    finally:
        try:
            caveman_state.set_level(test_id, None)
        except Exception:
            pass
    return 0


def _check_subagent_files(here):
    agents = ("cavecrew-investigator", "cavecrew-builder", "cavecrew-reviewer")
    for name in agents:
        agent_yaml = os.path.join(here, "agents", name, "agent.yaml")
        role_md = os.path.join(here, "agents", name, "prompts", "agent.system.main.role.md")
        if not os.path.isfile(agent_yaml) or not os.path.isfile(role_md):
            print(f"[{PLUGIN_NAME}] ERROR: subagent {name} missing yaml or role.md")
            return 7
    print(f"[{PLUGIN_NAME}] OK: 3 cavecrew subagents (investigator, builder, reviewer) present")
    return 0


def _check_benchmark_files(here):
    for rel in ("benchmarks/run.py", "benchmarks/prompts.json", "benchmarks/README.md"):
        if not os.path.isfile(os.path.join(here, rel)):
            print(f"[{PLUGIN_NAME}] ERROR: missing {rel}")
            return 8
    print(f"[{PLUGIN_NAME}] OK: 3 benchmark files present")
    return 0


def _check_icon(here):
    icon = os.path.join(here, "icon.svg")
    if not os.path.isfile(icon):
        print(f"[{PLUGIN_NAME}] ERROR: icon.svg missing")
        return 9
    with open(icon, "r", encoding="utf-8") as f:
        body = f.read()
    if "<svg" not in body or "caveman" not in body.lower():
        print(f"[{PLUGIN_NAME}] ERROR: icon.svg invalid or missing 'caveman' marker")
        return 10
    print(f"[{PLUGIN_NAME}] OK: icon.svg present ({len(body)} bytes)")
    return 0


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    print(f"[{PLUGIN_NAME}] Plugin dir: {here}")
    rc = _check_files(here)
    if rc:
        return rc
    rc = _check_manifest(here)
    if rc:
        return rc
    toggle_on = os.path.isfile(os.path.join(here, ".toggle-1"))
    toggle_off = os.path.isfile(os.path.join(here, ".toggle-0"))
    if toggle_on:
        state = "ON"
    elif toggle_off:
        state = "OFF"
    else:
        state = "DEFAULT (disabled - set enabled: true in default_config.yaml)"
    print(f"[{PLUGIN_NAME}] Toggle state: {state}")
    rc = _check_python_syntax(here)
    if rc:
        return rc
    rc = _check_state_roundtrip()
    if rc:
        return rc
    rc = _check_subagent_files(here)
    if rc:
        return rc
    rc = _check_benchmark_files(here)
    if rc:
        return rc
    rc = _check_icon(here)
    if rc:
        return rc
    print()
    print("=" * 60)
    print(f" {PLUGIN_NAME} v{EXPECTED_VERSION} (Plan C COMPLETE) - health check PASSED")
    print(f" Toggle state: {state}")
    print(f" To enable: set 'enabled: true' in default_config.yaml")
    print(f" Or use Settings -> Developer -> Caveman")
    print(f" WebUI: reload to see the caveman selector in the topbar")
    print(f" Benchmarks: python benchmarks/run.py --dry-run")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
