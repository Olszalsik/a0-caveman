"""
Caveman plugin - prose compressor.

Python port of upstream `src/mcp-servers/caveman-shrink/compress.js`, used for
tool descriptions and for the `caveman-compress` sub-skill.

What is never touched
---------------------
Fenced code blocks, inline code, URLs, filesystem paths, CONST_CASE,
dotted.path / pkg.fn(), function calls, and version numbers. Those are
substituted out with sentinels, the prose around them is compressed, and the
originals are spliced back in.

Why this exists in the A0 port
-----------------------------
The previous `message_loop_prompts_before` shrinker never ran (it read
`loop_data.tools`, which does not exist on `LoopData`), and its regexes
reproduced two bugs upstream had already fixed and shipped as incidents:

* `rf"\\b{re.escape(f)}\\b"` -- `\\b` treats `-` as a word boundary, so
  `\\bjust\\b` matched inside `just-in-time` and the removal left a dangling
  `-in-time`. Upstream #1055: the proxy rewrites MCP tool descriptions in
  place, so the corruption landed in the model's tool list.
* `make sure to` / `be sure to` were in a filler list. "Make sure the file
  exists" is a *check*; deleting `make sure` yields "the file exists", which
  reads as a *create*. Upstream #1073: a word that is filler on its own is
  not filler inside a collocation.

Both fixes are carried over. Word boundaries are `(?<![\\w-])` / `(?![\\w-])`,
and `sure` is matched as a position-initial interjection rather than by an
ever-growing collocation-head list.

CJK guard
---------
English deletion rules are not valid for mixed CJK prose: English articles and
intent phrases may qualify embedded technical terms (upstream #575). When a CJK
script is present the input is returned unchanged, spacing included.

Known upstream quirk, preserved here
-----------------------------------
`ARTICLES` is case-insensitive, and that flag applies inside the character
class too, so its `(?=[a-z])` lookahead also accepts an uppercase letter.
"A React app" therefore compresses to "React app" instead of keeping the
article as the lookahead appears to intend. This matches upstream
`compress.js` exactly, so it is preserved rather than silently changed; a
divergence from upstream would be a harder bug to notice than a documented
quirk.
"""

import re
from typing import Callable, List, Tuple

# Han, Hiragana, Katakana, Hangul, Bopomofo. Python's `re` has no
# \p{Script=...} support, so the ranges are spelled out.
CJK = re.compile(
    "["
    "㐀-䶿"  # Han extension A
    "一-鿿"  # CJK unified ideographs
    "豈-﫿"  # compatibility ideographs
    "぀-ゟ"  # Hiragana
    "゠-ヿ"  # Katakana
    "㄀-ㄯ"  # Bopomofo
    "가-힯"  # Hangul syllables
    "ᄀ-ᇿ"  # Hangul jamo
    "]"
)

# (?![\w-]) rather than \b: \b fires on "-", so \bjust\b matches "just-in-time".
FILLERS = re.compile(
    r"(?<![\w-])(?:just|really|basically|actually|simply|quite|very|essentially"
    r"|literally)(?![\w-])",
    re.IGNORECASE,
)

# Trailing punctuation and following space are part of the match so that
# removing the pleasantry does not leave a stray ", " behind.
PLEASANTRIES = re.compile(
    r"(?<![\w-])(?:please|kindly|thank you|thanks|certainly|of course"
    r"|happy to|i'?d be happy)(?![\w-])[,.]?\s*",
    re.IGNORECASE,
)

# `sure` is a pleasantry only as a bare, punctuated, prompt- or
# sentence-initial interjection ("Sure, this returns the value"). In
# "make sure" / "be sure" / "not sure" it is the complement of a verb, and
# deleting it changes the meaning of the sentence (#1073). Matching by
# position is what makes this safe: a collocation-head list would have to grow
# forever, whereas an acknowledgment is reliably initial AND punctuated.
SURE_INTERJECTION = re.compile(
    r"(?:^|(?<=[.!?]\s))sure\s*[,.!]\s*",
    re.IGNORECASE | re.MULTILINE,
)

HEDGES = re.compile(
    r"(?<![\w-])(?:perhaps|maybe|might|could potentially|would like to"
    r"|i think|in my opinion|it seems|it appears)(?![\w-])\s*",
    re.IGNORECASE,
)

# Matched per line, so `m` is required.
LEADERS = re.compile(
    r"^(?:i'?ll|i will|i can|i'?d|you can|we will|we can|let me|let'?s)\s+",
    re.IGNORECASE | re.MULTILINE,
)

# Faithful to upstream, including its quirk: the case-insensitive flag also
# applies inside the character class, so `[a-z]` matches an uppercase letter
# and "A React app" becomes "React app" rather than keeping the article. The
# lookahead therefore does not exclude a following proper noun, which reads
# like the original intent. Behaviour is left as upstream ships it; see the
# module docstring.
ARTICLES = re.compile(r"(?<![\w-])(?:a|an|the)\s+(?=[a-z])", re.IGNORECASE)

_WHITESPACE_RUN = re.compile(r"[ \t]{2,}")
_SPACE_BEFORE_PUNCT = re.compile(r"\s+([,.;:!?])")
_BLANK_LINES = re.compile(r"\n{3,}")
_SENTENCE_START = re.compile(r"(^|[.!?]\s+)([a-z])")

# Upper bound on sentinel-restore passes. Protected patterns can nest (the
# path rule swallows `STARTER/BUSINESS`, then the function-call rule swallows
# the result), so a single pass can leave an inner sentinel unresolved. The
# pass count grows with nesting depth, never with input size.
MAX_RESTORE_PASSES = 8

# A sentinel that cannot collide with real text: a private-use codepoint plus
# the index plus a delimiter. The upstream JS version uses a bare number,
# which can be produced by its own transform pass; this cannot.
_SENTINEL = "{}"

# Order matters. Paths and calls are substituted before inline code so that a
# path inside backticks is captured as one segment.
PROTECTED_PATTERNS = [
    re.compile(r"```[\s\S]*?```"),  # fenced code
    re.compile(r"`[^`\n]+`"),  # inline code
    re.compile(r"\bhttps?://\S+", re.IGNORECASE),  # URLs
    # Paths. Two deviations from upstream, both fixing real corruption:
    #  - an optional leading ./ or ../ is captured. Upstream's pattern starts
    #    with \b, which cannot match a leading dot, so "./config/x.toml"
    #    protected only "config/x.toml" and left a bare "." in the prose. The
    #    following whitespace-before-punctuation cleanup then glued it to the
    #    previous word ("at." + restored path), so the path came back as
    #    "at./config/x.toml" and any comparison against the original failed.
    #  - the match must end on a word character or a slash, so a sentence-final
    #    period is not swallowed into the path.
    re.compile(r"(?:\.\.?[/\\])?[\w.-]*[/\\][\w/\\-]*[\w/-]"),
    re.compile(r"\b[A-Z][A-Za-z0-9]*(?:_[A-Z][A-Za-z0-9]*)+\b"),  # CONST_CASE
    re.compile(r"\b\w+\.\w+(?:\.\w+)*\(\)?"),  # dotted.path or pkg.fn()
    # function calls: no space before "(", else "word (aside)" reads as a call
    re.compile(r"\b[A-Za-z_][A-Za-z0-9_]*\([^)]*\)"),
    re.compile(r"\b\d+\.\d+\.\d+\b"),  # version numbers
]

_SENTINEL_RE = re.compile(_SENTINEL.format(r"(\d+)"))


def _with_protected_segments(text: str, transform: Callable[[str], str]) -> str:
    """Substitute protected segments, transform the rest, restore the originals."""
    segments: List[str] = []
    working = text
    for pattern in PROTECTED_PATTERNS:
        working = pattern.sub(lambda m: _store(m.group(0), segments), working)

    out = transform(working)

    # Restore iteratively so nested sentinels resolve.
    for _ in range(MAX_RESTORE_PASSES):
        if not _SENTINEL_RE.search(out):
            break
        out = _SENTINEL_RE.sub(lambda m: _restore(m, segments), out)
    return out


def _store(match_text: str, segments: List[str]) -> str:
    index = len(segments)
    segments.append(match_text)
    return _SENTINEL.format(index)


def _restore(match: "re.Match[str]", segments: List[str]) -> str:
    index = int(match.group(1))
    return segments[index] if 0 <= index < len(segments) else match.group(0)


def _compress_prose(text: str) -> str:
    out = text
    out = LEADERS.sub("", out)
    out = PLEASANTRIES.sub("", out)
    out = SURE_INTERJECTION.sub("", out)
    out = HEDGES.sub("", out)
    out = FILLERS.sub("", out)
    out = ARTICLES.sub("", out)
    # Collapse the whitespace the removals introduced.
    out = _WHITESPACE_RUN.sub(" ", out)
    out = _SPACE_BEFORE_PUNCT.sub(r"\1", out)
    out = _BLANK_LINES.sub("\n\n", out)
    # Removing a leading interjection can leave a sentence starting lowercase.
    out = _SENTENCE_START.sub(lambda m: m.group(1) + m.group(2).upper(), out)
    return out.strip()


def compress(text: str) -> Tuple[str, int, int]:
    """Compress prose. Returns (compressed, before_chars, after_chars).

    Input is returned unchanged when it is empty, not a string, or contains CJK.
    """
    if not isinstance(text, str) or not text:
        return (text if isinstance(text, str) else "", 0, 0)
    if CJK.search(text):
        return (text, len(text), len(text))
    compressed = _with_protected_segments(text, _compress_prose)
    return (compressed, len(text), len(compressed))


def compression_ratio(before: int, after: int) -> float:
    if before <= 0:
        return 0.0
    return 1.0 - (after / before)
