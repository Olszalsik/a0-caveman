"""
Caveman plugin - Markdown compression validator.

The A0 port's `caveman-compress` sub-skill was prose only: it told the model to
squeeze `.md` memory files with no check that the squeeze preserved anything.
Upstream ships `skills/caveman-compress/scripts/validate.py` and refuses to
write when validation fails. This is that safety net, scoped to the invariants
`helpers/compress.py` can actually violate.

Invariants checked
------------------
1. Fenced code blocks: same count, same content, same document order.
2. Inline code spans: same multiset of contents.
3. URLs: same multiset.
4. Definite paths: same multiset.
5. Heading sequence: unchanged, so document structure survives.
6. CJK round-trip: text containing CJK must come back byte-identical.

On path detection: a crude path regex also matches ordinary prose pairs like
"pros/cons", "Node/browser" and "state/lifecycle". Caveman prose adds those
constructions freely, so treating them as hard losses produces noise that
trains the caller to ignore the check. Following upstream, only an unambiguous
path counts: a leading ./, ../, / or drive letter, or a dotted filename in the
final component.
"""

import re
from collections import Counter
from typing import Dict, List, Tuple

from usr.plugins.caveman.helpers.compress import CJK

MAX_REPORTED_SPAN = 60

FENCE_OPEN = re.compile(r"^(\s{0,3})(`{3,}|~{3,})(.*)$")
HEADING = re.compile(r"^(#{1,6})\s+(.*)", re.MULTILINE)
URL = re.compile(r"https?://[^\s)]+")
PATH = re.compile(r"(?:\./|\.\./|/|[A-Za-z]:\\)[\w\-/\\.]+|[\w\-.]+[/\\][\w\-/\\.]+")

# A PATH match is a definite path when it starts with ./ ../ / or a drive
# letter, or when its last component has a file extension.
_DEFINITE_PREFIX = re.compile(r"^(?:\./|\.\./|/|[A-Za-z]:\\)")
_DOTTED_LAST = re.compile(r"^[^/\\]*\.[A-Za-z0-9]{1,8}$")

# The path character class includes ".", so a sentence-final period is swallowed
# into the match ("./config/settings.toml." rather than "./config/settings.toml").
# Whether the trailing dot survives compression is incidental to the path
# being intact, so it is trimmed before the two sides are compared.
_TRAILING_PUNCT = ".,;:!?)]'\"`"


def _clip(text: str) -> str:
    text = text.strip()
    if len(text) <= MAX_REPORTED_SPAN:
        return text
    return text[:MAX_REPORTED_SPAN] + "..."


def extract_code_blocks(text: str) -> List[str]:
    """Fenced blocks in document order.

    Closed-fence rules follow CommonMark: the closing fence must use the same
    character and be at least as long as the opening one. An unterminated block
    runs to the end of the document.
    """
    blocks: List[str] = []
    lines = text.split("\n")
    index = 0
    total = len(lines)
    while index < total:
        match = FENCE_OPEN.match(lines[index])
        if not match:
            index += 1
            continue
        fence_char = match.group(2)[0]
        fence_len = len(match.group(2))
        body: List[str] = []
        cursor = index + 1
        closed = False
        while cursor < total:
            closing = FENCE_OPEN.match(lines[cursor])
            if (
                closing
                and closing.group(2)[0] == fence_char
                and len(closing.group(2)) >= fence_len
                and not closing.group(3).strip()
            ):
                closed = True
                break
            body.append(lines[cursor])
            cursor += 1
        blocks.append("\n".join(body))
        index = cursor + 1 if closed else total
    return blocks


def extract_inline_code(text: str) -> List[str]:
    """Inline code spans, with fenced blocks removed first.

    A fence marker left inline would otherwise consume the rest of the line as
    a code span, which hides a real inline-code loss.
    """
    without_blocks = FENCE_OPEN.sub("", text)
    return re.findall(r"`([^`\n]+)`", without_blocks)


def _definite_paths(text: str) -> Counter:
    """Unambiguous paths, excluding any span already claimed by a URL.

    A URL is also a `/`-containing token, so without this the same string is
    counted twice and a URL change shows up as both a URL error and a path
    error. URLs are compared separately, so mask them out first.
    """
    keep: Counter = Counter()
    spans = [m.span() for m in URL.finditer(text)]
    for match in PATH.finditer(text):
        if any(start <= match.start() < end for start, end in spans):
            continue
        candidate = match.group(0).rstrip(_TRAILING_PUNCT)
        if not candidate:
            continue
        if _DEFINITE_PREFIX.match(candidate) or _DOTTED_LAST.search(candidate):
            keep[candidate] += 1
    return keep


class ValidationResult:
    def __init__(self) -> None:
        self.is_valid = True
        self.errors: List[str] = []
        self.warnings: List[str] = []

    def add_error(self, message: str) -> None:
        self.is_valid = False
        self.errors.append(message)

    def add_warning(self, message: str) -> None:
        self.warnings.append(message)

    def report(self) -> str:
        lines = [f"valid={self.is_valid}"]
        lines += [f"  error: {e}" for e in self.errors]
        lines += [f"  warn : {w}" for w in self.warnings]
        return "\n".join(lines)


def _compare_multiset(
    result: ValidationResult, label: str, before: Counter, after: Counter
) -> None:
    for item, count in (before - after).items():
        result.add_error(
            f"{label} lost ({count} occurrence(s)): {_clip(item)}"
        )
    for item, count in (after - before).items():
        result.add_error(
            f"{label} appeared unexpectedly ({count} occurrence(s)): {_clip(item)}"
        )


def validate(original: str, compressed: str) -> ValidationResult:
    """Check that `compressed` preserved everything `original` relied on."""
    result = ValidationResult()

    if not isinstance(original, str) or not isinstance(compressed, str):
        result.add_error("both inputs must be strings")
        return result

    # The compressor short-circuits on CJK, so any difference is a bug.
    if CJK.search(original):
        if compressed != original:
            result.add_error(
                "text contains CJK and must round-trip byte-identically"
            )
        return result

    before_blocks = extract_code_blocks(original)
    after_blocks = extract_code_blocks(compressed)
    if len(before_blocks) != len(after_blocks):
        result.add_error(
            f"fenced code block count changed: {len(before_blocks)} -> "
            f"{len(after_blocks)}"
        )
    else:
        for position, (want, got) in enumerate(zip(before_blocks, after_blocks)):
            if want != got:
                result.add_error(
                    f"fenced code block {position} was modified "
                    f"(expected {_clip(want)!r}, got {_clip(got)!r})"
                )

    _compare_multiset(
        result,
        "inline code",
        Counter(extract_inline_code(original)),
        Counter(extract_inline_code(compressed)),
    )
    _compare_multiset(
        result, "URL", Counter(URL.findall(original)), Counter(URL.findall(compressed))
    )
    _compare_multiset(
        result,
        "path",
        _definite_paths(original),
        _definite_paths(compressed),
    )

    before_headings = [title.strip() for _, title in HEADING.findall(original)]
    after_headings = [title.strip() for _, title in HEADING.findall(compressed)]
    if before_headings != after_headings:
        missing = [h for h in before_headings if h not in after_headings]
        if missing:
            result.add_error(f"headings lost: {[ _clip(h) for h in missing ]}")
        else:
            result.add_error("heading order or level changed")

    if not compressed.strip() and original.strip():
        result.add_error("compression emptied a non-empty document")

    return result
