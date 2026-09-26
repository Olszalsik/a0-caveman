#!/usr/bin/env python3
"""
caveman-compress - compress a Markdown file, but never at the cost of content.

    python compress.py <file> [...]           # compress in place
    python compress.py --check <file>         # report only, write nothing
    python compress.py --stdout <file>        # print, write nothing
    python compress.py --force <file>         # write even if validation fails

Validation runs before the write, and a failed validation aborts it. A false
pass would overwrite the user's file with unvalidated output, which is worse
than refusing, so the check is the gate rather than a warning.

Exit codes: 0 ok, 1 validation failed, 2 usage or IO error.

Ported from upstream skills/caveman-compress/scripts/. The A0 port previously
shipped that skill as prose only, with no script and no validation at all.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

def _find_agent_zero_root(start: Path) -> Path | None:
    """Walk up from this script until a directory containing `usr/` is found.

    Counting `parents[n]` is fragile: this file sits six levels below the
    Agent Zero root, and the depth changes if the script is ever copied.
    """
    for candidate in [start, *start.parents]:
        if (candidate / "usr").is_dir():
            return candidate
    return None


_HERE = Path(__file__).resolve().parent
_ROOT = _find_agent_zero_root(_HERE)
if _ROOT is not None and str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
elif str(_HERE.parents[3]) not in sys.path:
    # Best effort when running from a relocated copy; the import below reports
    # a clear error if this does not resolve either.
    sys.path.insert(0, str(_HERE.parents[3]))

from usr.plugins.caveman.helpers import markdown as caveman_markdown  # noqa: E402
from usr.plugins.caveman.helpers.compress import compress  # noqa: E402

# Config and source are never rewritten by a prose squeeze.
SKIP_SUFFIXES = {".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".lock"}


def process(path: Path, write: bool, force: bool, backup: bool = True) -> int:
    try:
        original = path.read_text(encoding="utf-8")
    except OSError as exc:
        print(f"ERROR: cannot read {path}: {exc}", file=sys.stderr)
        return 2

    compressed, before, after = compress(original)

    if compressed == original:
        print(f"{path}: no change ({before} chars)")
        return 0

    result = caveman_markdown.validate(original, compressed)
    saved = before - after

    if not result.is_valid:
        print(f"{path}: VALIDATION FAILED ({before} -> {after} chars, -{saved})")
        for error in result.errors:
            print(f"  error: {error}")
        if write and not force:
            print("  refusing to write. Re-run with --force to override.")
            return 1

    if not write:
        print(f"{path}: ok ({before} -> {after} chars, -{saved})")
        for warning in result.warnings:
            print(f"  warn: {warning}")
        return 0

    # Write through a temp file so an interrupted run cannot truncate the input.
    tmp = path.with_suffix(path.suffix + ".caveman-tmp")
    try:
        tmp.write_text(compressed, encoding="utf-8")
        tmp.replace(path)
    except OSError as exc:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass
        print(f"ERROR: cannot write {path}: {exc}", file=sys.stderr)
        return 2

    # Back up the pre-compression text. Never clobber an existing backup, so
    # running twice cannot destroy the only pristine copy.
    if backup:
        backup_path = path.with_name(f"{path.name}.original.md")
        try:
            if not backup_path.exists():
                backup_path.write_text(original, encoding="utf-8")
        except OSError as exc:
            print(f"WARN: could not write backup {backup_path}: {exc}", file=sys.stderr)

    status = "ok" if result.is_valid else "written with --force despite errors"
    print(f"{path}: {status} ({before} -> {after} chars, -{saved})")
    return 0 if result.is_valid else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="caveman-compress")
    parser.add_argument("files", nargs="+", help="Markdown files to compress")
    parser.add_argument(
        "--check", action="store_true", help="report only, never write"
    )
    parser.add_argument(
        "--stdout", action="store_true", help="print the result, never write"
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="write even when validation fails (the original is unchanged on disk "
        "until this is passed, so this is a deliberate override)",
    )
    parser.add_argument(
        "--no-backup",
        action="store_true",
        help="do not write <name>.original.md before overwriting",
    )
    args = parser.parse_args()

    write = not (args.check or args.stdout)
    worst = 0
    for raw in args.files:
        path = Path(raw)
        if not path.is_file():
            print(f"ERROR: no such file: {path}", file=sys.stderr)
            worst = max(worst, 2)
            continue
        if path.suffix.lower() in SKIP_SUFFIXES:
            print(f"{path}: skipped ({path.suffix} is not prose)")
            continue
        if args.stdout:
            text = path.read_text(encoding="utf-8")
            print(compress(text)[0])
            continue
        if path.name.endswith(".original.md"):
            print(f"{path}: skipped (this is a backup)")
            continue
        worst = max(worst, process(path, write=write, force=args.force, backup=not args.no_backup))

    return worst


if __name__ == "__main__":
    sys.exit(main())
