# Caveman plugin - health check self-test (P0.5 verification)
#
# A health check that cannot fail is the defect this file exists to prevent.
# Each case below breaks one thing, runs execute.py, and asserts it failed
# for the RIGHT reason (matched on the message, not just the exit code).
#
#   python usr/plugins/caveman/tests/test_healthcheck.py
#
# Run from anywhere; paths are derived from this file's location.

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN = os.path.abspath(os.path.join(HERE, ".."))
EXECUTE = os.path.join(PLUGIN, "execute.py")

# Keys of the files we mutate, so every case can restore them.
MUTATED = {
    "config": os.path.join(PLUGIN, "config.json"),
    "dropdown": os.path.join(PLUGIN, "webui", "caveman-dropdown.js"),
    "config_html": os.path.join(PLUGIN, "webui", "config.html"),
    "readme": os.path.join(PLUGIN, "README.md"),
    "execute": os.path.join(PLUGIN, "execute.py"),
    "command": os.path.join(
        PLUGIN, "extensions", "python", "monologue_start", "_30_caveman_command.py"
    ),
    "state": os.path.join(PLUGIN, "helpers", "state.py"),
    "investigator_prompt": os.path.join(
        PLUGIN,
        "agents",
        "cavecrew-investigator",
        "prompts",
        "agent.system.main.specifics.md",
    ),
    "builder_prompt": os.path.join(
        PLUGIN,
        "agents",
        "cavecrew-builder",
        "prompts",
        "agent.system.main.specifics.md",
    ),
    "intensity": os.path.join(PLUGIN, "prompts", "caveman.intensity.md"),
    "help_skill": os.path.join(PLUGIN, "skills", "caveman-help", "SKILL.md"),
    "style_extension": os.path.join(
        PLUGIN,
        "extensions",
        "python",
        "system_prompt",
        "_20_caveman_style.py",
    ),
    "plugins_config": os.path.join(PLUGIN, "helpers", "plugins_config.py"),
    "coexistence": os.path.join(
        PLUGIN, "helpers", "headroom", "coexistence.py"
    ),
    "default_yaml": os.path.join(PLUGIN, "default_config.yaml"),
}


def run_check():
    proc = subprocess.run(
        [sys.executable, EXECUTE], capture_output=True, text=True, cwd=PLUGIN
    )
    return proc.returncode, proc.stdout + proc.stderr


def read(path):
    with open(path, "r", encoding="utf-8") as handle:
        return handle.read()


def write(path, text):
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)


class Case:
    """Mutate one file, assert the check fails, always restore."""

    def __init__(self, name, key, mutate, expect):
        self.name = name
        self.key = key
        self.mutate = mutate
        self.expect = expect

    def run(self):
        path = MUTATED[self.key]
        original = read(path)
        try:
            write(path, self.mutate(original))
            code, out = run_check()
            if code == 0:
                return False, f"{self.name}: check PASSED but should have failed"
            if self.expect not in out:
                return False, (
                    f"{self.name}: failed for the wrong reason; "
                    f"expected {self.expect!r} in output"
                )
            return True, f"{self.name}: correctly detected"
        finally:
            write(path, original)


# --- mutations -------------------------------------------------------------


def add_dead_config_key(text):
    data = json.loads(text)
    data["risk_floor_pct"] = 25
    return json.dumps(data)


def break_action_name(text):
    # The exact defect that shipped: frontend sends an action the API lacks.
    return text.replace('action: "set"', 'action: "setLevel"')


def bind_unknown_setting(text):
    return text.replace(
        'x-model="config.auto_clarity"', 'x-model="config.risk_floor_pct"', 1
    )


def break_command_regex(text):
    # Collapse the level alternation so no level can ever match.
    target = "_LEVELS = " + '"' + "|".join("") + '"'  # placeholder, unused
    original = '_LEVELS = "|".join(re.escape(level) for level in caveman_state.VALID_LEVELS)'
    assert original in text, "command module no longer has the expected _LEVELS line"
    return text.replace(original, "_LEVELS = 'nomatch'")


def break_state_path(text):
    # Reintroduce the machine-global fallback instead of the workdir.
    original = 'workdir = (get_settings() or {}).get("workdir_path")'
    assert original in text, "state module no longer resolves the workdir"
    return text.replace(
        original,
        "workdir = os.path.join(os.path.expanduser('~'), '.cache', 'agent0', 'caveman')",
    )


def bare_fetch(text):
    return text.replace("await fetchApi(STATE_URL, {", "await fetch(STATE_URL, {").replace(
        'import { fetchApi } from "/js/api.js";', ""
    )


def add_fast_poll(text):
    return text.replace("}, 15000);", "}, 2000);")


def reinstate_claim(text):
    # The exact retracted headline, asserted as fact.
    original = "squeezes the assistant's own wording"
    assert original in text, "README no longer has the expected headline"
    return text.replace(
        original,
        "cuts 65% of output tokens (measured) by squeezing the assistant's own wording",
        1,
    )


def wrong_prompt_slot(text):
    # Reintroduce the original, false claim: agent.yaml has no tool field, so
    # `Bash` is present and only self-restraint prevents its use.
    original = "No `Bash`: do not shell out"
    assert original in text, "builder prompt no longer has the expected limit line"
    return text.replace(
        original, "No `Bash` available - cannot shell out", 1
    )


def add_wrong_slot_file(text):
    # Restore the old role.md slot name in the required-file list.
    return text.replace("agent.system.main.specifics.md", "agent.system.main.role.md")


def break_intensity_fence(text):
    # Reintroduce HTML-comment fences, which the loader strips along with the
    # maintainer notes, so every level silently disappears.
    return text.replace("[intensity:", "<!-- intensity:").replace(
        "lite]\n", "lite -->\n", 1
    ).replace("[/intensity]", "<!-- /intensity -->")


def strip_state_resolve(text):
    # Simulate a partial upgrade: a v0.4.0-shaped state.py that lacks the v0.5.0
    # API, sitting beside v0.5.0 callers. Every per-file check still passes, so
    # only the cross-module contract check can catch this.
    marker = "def resolve(chat_id: Optional[str], config: Optional[dict] = None) -> dict:"
    assert marker in text, "state.py no longer has the expected resolve()"
    return text.replace(marker, "def _removed_resolve(chat_id=None, config=None) -> dict:")


def remove_compat_guard(text):
    # Bypass the guard in the extension that produced the reported traceback.
    original = "state = compat.state_api(caveman_state, self.agent)"
    assert original in text, "style extension no longer has the expected guard"
    return text.replace(original, "state = caveman_state  # guard removed", 1)


def remove_config_guard(text):
    # The same bypass for the config module, which is guarded independently
    # because a partial upgrade reaches it as easily as the state module.
    original = "config_mod = compat.config_api(plugin_cfg, self.agent)"
    assert original in text, "style extension no longer has the config guard"
    return text.replace(original, "config_mod = plugin_cfg  # guard removed", 1)


def narrow_get_config(text):
    # Simulate the second reported partial upgrade: plugins_config.py from
    # v0.4.0, whose get_config predates the `agent` parameter. Every symbol the
    # shipped modules call is still present and the file still compiles, so only
    # a signature-aware contract check can catch this.
    original = "def get_config(agent: Any = None) -> dict[str, Any]:"
    assert original in text, "plugins_config.py no longer has the expected get_config"
    text = text.replace(
        original, "def get_config() -> dict[str, Any]:", 1
    )
    return text.replace(
        "plugins_helper.get_plugin_config(PLUGIN_NAME, agent=agent)",
        "plugins_helper.get_plugin_config(PLUGIN_NAME)",
        1,
    )


def add_estimator_back(text):
    # Reintroduce the fabricated ratio into the observation store.
    return text.replace(
        'DEFAULT_LEVEL = "full"',
        'DEFAULT_LEVEL = "full"\nREDUCTION_FRACTION = {"full": 0.65}',
    )


def add_ratio_table(text):
    # The shape a skill shipped: a bare per-level ratio list with no reduction
    # word on the same line as the number.
    anchor = "| **caveman-commit** |"
    assert anchor in text, "caveman-help table no longer has the expected row"
    return text.replace(anchor, "- `full`: ~65%\n" + anchor, 1)


def add_standalone_from_import(text):
    # The canonical from-import form the old line-based scan missed: the line
    # carries no "usr.plugins.headroom_compress" literal. Wrapped in a function
    # so importing the mutated module does not execute it (the AST scan still
    # sees the node).
    assert "def resolve(" in text, "state.py shape changed"
    return text + (
        "\n\ndef _healthcheck_probe_import():\n"
        "    from usr.plugins import headroom_compress\n"
    )


def collide_banner_constant(text):
    # The coexistence banner's id is not a literal in the banner file; it is
    # BANNER_ID in helpers/headroom/coexistence.py. Point the constant at the
    # literal id the other banner ships, now that helpers constants are
    # resolved, and the collision must fail the uniqueness check.
    original = 'BANNER_ID = "caveman_headroom_standalone_overlap"'
    assert original in text, "coexistence helper no longer defines BANNER_ID"
    return text.replace(original, 'BANNER_ID = "caveman_headroom_setup_hint"', 1)


def add_unread_top_level_key(text):
    # A caveman (not headroom) setting shipped in the YAML that DEFAULTS does
    # not list: only the top-level parity comparison can catch it.
    assert "enabled: false" in text, "default_config.yaml shape changed"
    return text.replace("enabled: false", "enabled: false\nrisk_floor_pct: 25", 1)


def reinstate_claim_percent_word(text):
    # Same retracted headline with the unit spelled out - the variant the
    # %-only patterns let through.
    original = "squeezes the assistant's own wording"
    assert original in text, "README no longer has the expected headline"
    return text.replace(
        original,
        "cuts tokens by 65 percent (measured) by squeezing the assistant's own wording",
        1,
    )


CASES = [
    Case(
        "config.json key nothing reads",
        "config",
        add_dead_config_key,
        "no backend reads",
    ),
    Case(
        "dropdown sends an unknown action",
        "dropdown",
        break_action_name,
        "actions the API does not implement",
    ),
    Case(
        "config.html binds an unknown setting",
        "config_html",
        bind_unknown_setting,
        "binds keys no backend reads",
    ),
    Case(
        "slash-command regex matches nothing",
        "command",
        break_command_regex,
        "command misclassified",
    ),
    Case(
        "state escapes the workdir",
        "state",
        break_state_path,
        "outside the Agent Zero workdir",
    ),
    Case(
        "dropdown drops CSRF helper",
        "dropdown",
        bare_fetch,
        "bare fetch",
    ),
    Case(
        "dropdown polls every 2s again",
        "dropdown",
        add_fast_poll,
        "polls every 2000ms",
    ),
    Case(
        "retracted savings claim returns",
        "readme",
        reinstate_claim,
        "unverified savings claim",
    ),
    Case(
        "profile claims a tool is unavailable",
        "builder_prompt",
        wrong_prompt_slot,
        "claims a tool is unavailable",
    ),
    Case(
        "health check expects the wrong prompt slot",
        "execute",
        add_wrong_slot_file,
        "missing file",
    ),
    Case(
        "intensity fences become HTML comments",
        "intensity",
        break_intensity_fence,
        "intensity levels parsed",
    ),
    Case(
        "fabricated estimator returns to the store",
        "state",
        add_estimator_back,
        "REDUCTION_FRACTION",
    ),
    Case(
        "a bare per-level ratio table returns",
        "help_skill",
        add_ratio_table,
        "unverified savings claim",
    ),
    Case(
        "state.py loses the v0.5.0 API (partial upgrade)",
        "state",
        strip_state_resolve,
        "does not provide the API",
    ),
    Case(
        "an extension drops the compat guard",
        "style_extension",
        remove_compat_guard,
        "without compat.state_api",
    ),
    Case(
        "an extension drops the config guard",
        "style_extension",
        remove_config_guard,
        "without compat.config_api",
    ),
    Case(
        "plugins_config.py loses its agent parameter (partial upgrade)",
        "plugins_config",
        narrow_get_config,
        "does not provide the API",
    ),
    Case(
        "combined plugin from-imports the standalone package",
        "state",
        add_standalone_from_import,
        "imports the standalone plugin's package",
    ),
    Case(
        "banner id constant collides with a shipped literal id",
        "coexistence",
        collide_banner_constant,
        "duplicate banner ids",
    ),
    Case(
        "YAML ships a caveman key DEFAULTS lacks",
        "default_yaml",
        add_unread_top_level_key,
        "top-level settings disagree",
    ),
    Case(
        "savings claim returns with the unit spelled out",
        "readme",
        reinstate_claim_percent_word,
        "unverified savings claim",
    ),
]

def main() -> int:
    print("=" * 68)
    print(" caveman health-check self-test")
    print("=" * 68)

    # Baseline: the unmodified plugin must pass.
    code, out = run_check()
    if code != 0:
        print("FAIL: baseline health check did not pass:\n" + out)
        return 1
    print("OK   baseline health check passes")

    failures = 0
    for case in CASES:
        passed, message = case.run()
        print(("OK   " if passed else "FAIL ") + message)
        if not passed:
            failures += 1

    # And it must still pass after everything is restored.
    code, out = run_check()
    if code != 0:
        print("FAIL: health check broken after restore:\n" + out)
        return 1
    print("OK   health check passes again after restore")

    print("=" * 68)
    if failures:
        print(f" {failures} of {len(CASES)} self-test cases FAILED")
        print("=" * 68)
        return 1
    print(f" all {len(CASES)} self-test cases passed")
    print("=" * 68)
    return 0


if __name__ == "__main__":
    sys.exit(main())
