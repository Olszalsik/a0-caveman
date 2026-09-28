#!/usr/bin/env python3
"""
Headroom Context Compression - user-triggered setup / maintenance script.

Run from the Plugins UI button, or directly:
    python /a0/usr/plugins/caveman/headroom_setup.py

What it does (idempotent, safe to re-run):
  1. Verifies Python >= 3.10 (headroom-ai requirement).
  2. Installs the adapter-tested `headroom-ai` version without dependencies
     into the Agent Zero venv using the current `sys.executable`, avoiding
     changes to shared framework packages.
  3. Rejects optional extras because their dependency resolution is not
     validated against Agent Zero's framework pins.
  4. Sanity-checks the import and version.
  5. Optionally pre-creates the CCR SQLite and stats SQLite files with the
     expected schema.

Exit codes: 0 on success, non-zero on failure (so the UI shows a red toast).
"""

from __future__ import annotations

import argparse
import importlib
import shutil
import subprocess
import sys
import sqlite3
from pathlib import Path

PLUGIN_DIR = Path(__file__).resolve().parent
CCR_DIR = PLUGIN_DIR / "ccr"
STATS_DIR = PLUGIN_DIR / "stats"

MIN_PY = (3, 10)
HEADROOM_VERSION = "0.38.0"


def _print(msg: str) -> None:
    print(f"[caveman/headroom] {msg}", flush=True)


def _error(msg: str) -> None:
    print(f"[caveman/headroom] ERROR: {msg}", file=sys.stderr, flush=True)


def check_python() -> bool:
    if sys.version_info < MIN_PY:
        _error(
            f"headroom-ai requires Python >= {'.'.join(map(str, MIN_PY))}, "
            f"current is {sys.version.split()[0]}"
        )
        return False
    return True


def load_default_config() -> dict:
    config_path = PLUGIN_DIR / "default_config.yaml"
    if not config_path.exists():
        return {}
    try:
        import yaml  # Agent Zero ships PyYAML
    except ImportError:
        _print("PyYAML not available; skipping default_config.yaml inspection")
        return {}

    try:
        with config_path.open("r", encoding="utf-8") as fh:
            loaded = yaml.safe_load(fh) or {}
            if isinstance(loaded, dict) and isinstance(loaded.get("headroom"), dict):
                return loaded["headroom"]
            return loaded
    except Exception as exc:  # noqa: BLE001
        _print(f"could not parse default_config.yaml: {exc}")
        return {}


def run_pip_install(extras: list[str], upgrade: bool) -> bool:
    if extras:
        _error(
            "optional Headroom extras are not installed into the shared Agent Zero "
            "runtime because their dependency resolution can change framework packages. "
            "Use the core adapter; optional strategies are not wired by this plugin."
        )
        return False
    spec = f"headroom-ai=={HEADROOM_VERSION}"

    cmd = [sys.executable, "-m", "pip", "install", "--no-deps"]
    if upgrade:
        cmd.append("--upgrade")
    cmd.append(spec)

    _print(f"installing {spec} ...")
    result = subprocess.run(cmd, text=True)
    if result.returncode != 0:
        _error(f"pip install failed for {spec} (exit {result.returncode})")
        return False
    return True


def verify_import() -> bool:
    try:
        headroom = importlib.import_module("headroom")
    except Exception as exc:  # noqa: BLE001
        _error(f"headroom import failed: {exc}")
        return False

    version = getattr(headroom, "__version__", "unknown")
    _print(f"headroom-ai imported OK, version={version}")
    return True


def ensure_dirs() -> None:
    CCR_DIR.mkdir(parents=True, exist_ok=True)
    STATS_DIR.mkdir(parents=True, exist_ok=True)
    (PLUGIN_DIR / "cache").mkdir(parents=True, exist_ok=True)


def init_ccr_db(path: Path) -> None:
    if path.exists():
        return
    _print(f"creating CCR SQLite at {path}")
    with sqlite3.connect(path) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS ccr (
                key TEXT PRIMARY KEY,
                original BLOB NOT NULL,
                original_tokens INTEGER NOT NULL,
                compressed_tokens INTEGER NOT NULL,
                created_at REAL NOT NULL,
                source TEXT
            );
            CREATE INDEX IF NOT EXISTS ccr_created_at ON ccr(created_at);
            """
        )
        conn.commit()


def init_stats_db(path: Path) -> None:
    if path.exists():
        return
    _print(f"creating stats SQLite at {path}")
    with sqlite3.connect(path) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts REAL NOT NULL,
                kind TEXT NOT NULL,
                source TEXT,
                input_tokens INTEGER NOT NULL DEFAULT 0,
                output_tokens INTEGER NOT NULL DEFAULT 0,
                saved_tokens INTEGER NOT NULL DEFAULT 0,
                duration_ms REAL NOT NULL DEFAULT 0,
                repeat_output INTEGER NOT NULL DEFAULT 0,
                details TEXT
            );
            CREATE INDEX IF NOT EXISTS events_ts ON events(ts);
            CREATE INDEX IF NOT EXISTS events_kind ON events(kind);
            """
        )
        conn.commit()


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Headroom context compression - plugin setup",
    )
    p.add_argument(
        "--upgrade",
        action="store_true",
        help="Reinstall headroom-ai even if already present",
    )
    p.add_argument(
        "--extras",
        nargs="*",
        default=None,
        help="Deprecated and rejected: optional extras are not supported in the shared runtime",
    )
    p.add_argument(
        "--no-install",
        action="store_true",
        help="Skip pip install (init DBs and verify import only)",
    )
    p.add_argument(
        "--uninstall-package",
        action="store_true",
        help="pip uninstall headroom-ai (full plugin reset)",
    )
    return p.parse_args()


def main() -> int:
    args = parse_args()

    if not check_python():
        return 2

    ensure_dirs()
    init_ccr_db(CCR_DIR / "ccr.db")
    init_stats_db(STATS_DIR / "stats.db")

    if args.uninstall_package:
        _print("uninstalling headroom-ai ...")
        subprocess.run([sys.executable, "-m", "pip", "uninstall", "-y", "headroom-ai"])
        _print("done. Plugin can stay installed but will be a no-op until reinstalled.")
        return 0

    if args.no_install:
        if not verify_import():
            return 3
        return 0

    if shutil.which("ast-grep") is None:
        _print("note: 'ast-grep' binary not found on PATH. Required only by headroom-ai[code] extra; safe to ignore otherwise.")

    # The current adapter uses only the core per-content API. Optional extras
    # are deliberately not installed into Agent Zero's shared runtime.
    extras = list(args.extras or [])

    if not run_pip_install(extras, args.upgrade):
        return 4

    if not verify_import():
        return 5

    _print("setup complete. Plugin is ready (still OFF until you toggle it in Plugins UI).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
