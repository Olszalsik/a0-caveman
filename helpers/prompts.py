"""
Caveman plugin - prompt fragment loader.

Single owner of `prompts/`, shared by the `system_prompt` extension and the
benchmark harness so both measure and inject the same text.

Level filtering
---------------
`caveman.intensity.md` holds all six levels in one file, delimited by
`[intensity: <level>]` / `[/intensity]`. `load_intensity()` returns only the
active level's block.

The previous port kept one file per level. That is what upstream replaced with
`loadFilteredRuleset()`, and the reason is not tidiness: a single ruleset that
is *filtered* means the model can never see the levels it is not running. Seven
near-identical files drifted instead — the base style restated the level rules
in its own words, and the copies diverged.

Upstream also drops the whole `<h2>` wrapper when only one level is present and
keeps it when several are, so the model is not told it has a choice. Here the
filtered block is always exactly one level, so the wrapper is always kept: it
labels which level is in force.

The fences are square brackets rather than HTML comments because HTML comments
are stripped from everything that ships, so a comment-delimited marker would
delete itself along with the maintainer notes.
"""

import os
import re
from typing import Dict, List, Optional

from usr.plugins.caveman.helpers.state import VALID_LEVELS


HERE = os.path.dirname(os.path.abspath(__file__))


def _find_plugin_root() -> str:
    """Walk up until a directory with both `plugin.yaml` and `prompts/` is found.

    Counting `dirname` levels is fragile: the previous loader in
    `extensions/python/system_prompt/` sat three levels below the plugin root
    while this one sits one, and both are easy to get wrong.
    """
    current = HERE
    while True:
        parent = os.path.dirname(current)
        if parent == current:
            return HERE
        if os.path.isfile(os.path.join(parent, "plugin.yaml")) and os.path.isdir(
            os.path.join(parent, "prompts")
        ):
            return parent
        current = parent


PLUGIN_ROOT = _find_plugin_root()
PROMPTS_DIR = os.path.join(PLUGIN_ROOT, "prompts")

STYLE_FILE = "caveman.system.style.md"
STYLE_LEAN_FILE = "caveman.system.style.lean.md"
INTENSITY_FILE = "caveman.intensity.md"
CLARITY_FILE = "caveman.auto_clarity.md"

MARKER = '<style name="caveman"'
LEVEL_TAG_OPEN = "<active_level>"
LEVEL_TAG_CLOSE = "</active_level>"

_BLOCK_START = re.compile(r"^\[intensity:([a-z0-9-]+)\]$", re.IGNORECASE)
_BLOCK_END = "[/intensity]"

_INTENSITY_HEADER_END = "# Intensity"


# Maintainer notes are HTML comments at the top of each shipped fragment. They
# are stripped before injection: the model does not need the file's edit
# history, and the fragments are re-sent on every turn.
_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)


def read_prompt(filename: str) -> str:
    """Read one prompt fragment, minus HTML comments. Missing yields ""."""
    path = os.path.join(PROMPTS_DIR, filename)
    try:
        with open(path, "r", encoding="utf-8") as handle:
            text = handle.read()
    except (OSError, UnicodeDecodeError):
        return ""
    return _COMMENT.sub("", text).strip()


_INTENSITY_CACHE: Optional[Dict[str, str]] = None


def _parse_intensity() -> Dict[str, str]:
    """Parse the ruleset into {level: block}. Cached; the file ships with the plugin."""
    global _INTENSITY_CACHE
    if _INTENSITY_CACHE is not None:
        return _INTENSITY_CACHE

    blocks: Dict[str, str] = {}
    text = read_prompt(INTENSITY_FILE)
    if text:
        # Everything before the first block marker is the file header, which is
        # documentation for maintainers and must not reach the model.
        start_of_rules = text.find(_INTENSITY_HEADER_END)
        if start_of_rules != -1:
            text = text[start_of_rules + len(_INTENSITY_HEADER_END) :]

        current: Optional[str] = None
        collected: List[str] = []
        for line in text.splitlines():
            opening = _BLOCK_START.match(line.strip())
            if opening:
                if current and collected:
                    blocks[current] = "\n".join(collected).strip()
                current = opening.group(1).lower()
                collected = []
                continue
            if current and line.strip() == _BLOCK_END:
                blocks[current] = "\n".join(collected).strip()
                current = None
                collected = []
                continue
            if current:
                collected.append(line)
        if current and collected:
            # Unterminated final block: keep it rather than silently dropping a
            # level the user may have selected.
            blocks[current] = "\n".join(collected).strip()

    _INTENSITY_CACHE = blocks
    return blocks


def available_levels() -> List[str]:
    """Levels the shipped ruleset actually defines, in canonical order."""
    defined = _parse_intensity()
    return [level for level in VALID_LEVELS if level in defined]


def load_intensity(level: str) -> str:
    """Return only the active level's block, or "" if it is not defined."""
    block = _parse_intensity().get(level)
    if not block:
        return ""
    return f"## Intensity\n\n{block}"


def build_system_prompt(
    level: str, auto_clarity: bool = True, lean: bool = False
) -> str:
    """Assemble the exact text injected for a level.

    Used by both the `system_prompt` extension and `benchmarks/run.py`, so the
    benchmark cannot drift from what is actually sent.

    `lean=True` swaps the base style for the compact variant (and condenses
    auto-clarity to its one-line summary). The level's own ruleset block is
    unchanged: that block is the operative contract, and trimming it would
    change behaviour, not just cost. Measured input cost 2026-09-28:
    standard ~800 tok/turn, lean ~300 tok/turn.

    Fails closed: an unknown level yields "", never a style-only prompt. A
    prompt with no intensity block would apply `full`-shaped rules to a level
    that has none, which is the kind of quiet wrongness this plugin has had
    enough of.
    """
    intensity = load_intensity(level)
    if not intensity:
        return ""
    if lean:
        style = read_prompt(STYLE_LEAN_FILE) or read_prompt(STYLE_FILE)
        parts = [style, intensity]
        if auto_clarity:
            parts.append(
                "## Auto-clarity\n\nDrop the style for security warnings, "
                "irreversible actions, and ambiguous multi-step sequences; "
                "resume after."
            )
    else:
        parts = [read_prompt(STYLE_FILE), intensity]
        if auto_clarity:
            parts.append(read_prompt(CLARITY_FILE))
    body = "\n\n".join(part for part in parts if part)
    if not body:
        return ""
    return f"{body}\n\n{LEVEL_TAG_OPEN}{level}{LEVEL_TAG_CLOSE}"


def fragments_present(level: str) -> bool:
    """True when the base style and the level's block both exist."""
    return bool(read_prompt(STYLE_FILE)) and bool(load_intensity(level))
