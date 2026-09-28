#!/usr/bin/env python3
"""
Caveman plugin - community installer for Agent Zero v2.2+.

Usage:
  python usr/plugins/caveman/install.py [--prefix <a0-root>] [--no-deps]
                                         [--force] [--check-only]

Copies the plugin into <prefix>/usr/plugins/caveman/ and runs the health
check on the result. With no --prefix, the Agent Zero root is derived from
this file's own location, so the script works from any checkout.

--check-only runs the health check in place without copying anything, which
is what you want when the script is already executing from inside the
installed plugin.

Returns 0 on success, non-zero on failure.
"""

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path


PLUGIN_NAME = "caveman"
EXPECTED_VERSION = "0.5.3"


def main() -> int:
    parser = argparse.ArgumentParser(description=f"Install {PLUGIN_NAME} plugin")
    parser.add_argument(
        "--prefix",
        default="",
        help="Agent Zero root (default: auto-detect from this file's location)",
    )
    parser.add_argument(
        "--no-deps", action="store_true", help="Skip pip install of framework deps"
    )
    parser.add_argument("--force", action="store_true", help="Overwrite existing install")
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="Run the health check in place and exit without copying",
    )
    args = parser.parse_args()

    src = Path(__file__).resolve().parent

    if not (src / "plugin.yaml").is_file():
        print(f"ERROR: source plugin.yaml not found in {src}", file=sys.stderr)
        return 1

    if args.check_only:
        return subprocess.run([sys.executable, str(src / "execute.py")]).returncode

    # Default to the root three levels up: <root>/usr/plugins/<name>/install.py.
    prefix = Path(args.prefix) if args.prefix else src.parents[2]
    dst = (prefix / "usr" / "plugins" / PLUGIN_NAME).resolve()
    src_resolved = src.resolve()

    # Copying a directory onto itself would rmtree the source (which is the
    # running plugin, since this script executes from inside it) and then fail.
    if dst == src_resolved or src_resolved in dst.parents:
        print(
            f"ERROR: refusing to install onto the source directory.\n"
            f"       source: {src_resolved}\n"
            f"       target: {dst}\n"
            f"       Pass --prefix <agent-zero-root> to install somewhere else, "
            f"or --check-only to just run the health check.",
            file=sys.stderr,
        )
        return 2

    if dst.exists() and not args.force:
        print(f"ERROR: {dst} already exists. Use --force to overwrite.", file=sys.stderr)
        return 3

    print(f"[{PLUGIN_NAME}] Copying {src_resolved} -> {dst}")
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src_resolved, dst, ignore=shutil.ignore_patterns("__pycache__", ".cache", "*.pyc"))
    print(f"[{PLUGIN_NAME}] Installed v{EXPECTED_VERSION} to {dst}")

    if not args.no_deps:
        print(f"[{PLUGIN_NAME}] Installing framework deps (langchain-community, GitPython, nest_asyncio)")
        candidates = [
            "/opt/venv-a0/bin/pip",
            "/opt/venv/bin/pip",
            shutil.which("pip") or "pip",
        ]
        pip = next((p for p in candidates if p and os.path.isfile(p)), None)
        if pip:
            for pkg in ("GitPython", "nest_asyncio", "langchain-community"):
                subprocess.run([pip, "install", pkg], check=False)
        else:
            print(f"[{PLUGIN_NAME}] WARN: no pip found, skipped dep install", file=sys.stderr)

    print(f"[{PLUGIN_NAME}] Running health check...")
    rc = subprocess.run([sys.executable, str(dst / "execute.py")]).returncode
    if rc == 0:
        print(f"[{PLUGIN_NAME}] DONE. Reload the WebUI to activate the caveman selector.")
    return rc


if __name__ == "__main__":
    sys.exit(main())
