#!/usr/bin/env python3
"""
Caveman plugin - community installer for Agent Zero v2.2+.

Usage:
 python /a0/usr/plugins/caveman/install.py [--prefix /a0] [--no-deps]

Copies the plugin into <prefix>/usr/plugins/caveman/, prints a short
health check, and (unless --no-deps) installs framework deps that the
plugin transitively relies on (langchain-community for the framework's
own _memory plugin loader).

Run from the Plugins UI's "execute.py" button, or from a terminal.

Returns 0 on success, non-zero on failure.
"""

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path


PLUGIN_NAME = "caveman"
EXPECTED_VERSION = "0.4.0"


def main() -> int:
    parser = argparse.ArgumentParser(description=f"Install {PLUGIN_NAME} plugin")
    parser.add_argument("--prefix", default="/a0", help="Agent Zero root (default /a0)")
    parser.add_argument("--no-deps", action="store_true", help="Skip pip install of framework deps")
    parser.add_argument("--force", action="store_true", help="Overwrite existing install")
    args = parser.parse_args()

    src = Path(__file__).resolve().parent
    dst = Path(args.prefix) / "usr" / "plugins" / PLUGIN_NAME

    if not (src / "plugin.yaml").is_file():
        print(f"ERROR: source plugin.yaml not found in {src}", file=sys.stderr)
        return 1

    if dst.exists() and not args.force:
        print(f"ERROR: {dst} already exists. Use --force to overwrite.", file=sys.stderr)
        return 2

    print(f"[{PLUGIN_NAME}] Copying {src} -> {dst}")
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns("__pycache__", ".cache", "*.pyc"))
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
