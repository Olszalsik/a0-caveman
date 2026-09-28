"""Proxy mode manager for the Headroom plugin.

Starts and stops the `headroom proxy` subprocess on the local machine. When
the proxy is running, Agent Zero's model providers can be re-pointed to
`http://127.0.0.1:<port>` to transparently compress every LLM request.

The proxy is a separate process so it does not block the agent loop. We
track its PID in a file and clean it up on uninstall / pre_update.

Usage:
    from usr.plugins.caveman.helpers.headroom.proxy_manager import ProxyManager
    pm = ProxyManager(cfg)
    await pm.start()
    status = pm.status()
    pm.stop()
"""

from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from usr.plugins.caveman.helpers.headroom import config as _config

PLUGIN_NAME = _config.PLUGIN_NAME
PLUGIN_DIR = _config.PLUGIN_DIR
PID_FILE = PLUGIN_DIR / "proxy.pid"
LOG_FILE = PLUGIN_DIR / "proxy.log"


def _print(msg: str) -> None:
    sys.stderr.write(f"[caveman/headroom/proxy] {msg}\n")
    sys.stderr.flush()


class ProxyManager:
    """Manage the local headroom proxy subprocess."""

    def __init__(self, cfg: dict[str, Any] | None = None):
        self.cfg = cfg or _config.get_config(agent=None)
        proxy_cfg = self.cfg.get("proxy", {}) or {}
        self.host: str = proxy_cfg.get("host", "127.0.0.1")
        self.port: int = int(proxy_cfg.get("port", 8787))
        self.mode: str = proxy_cfg.get("mode", "token")
        self.auto_start: bool = bool(proxy_cfg.get("auto_start", False))
        self.headroom_bin: str = proxy_cfg.get("binary", "/opt/venv-a0/bin/headroom")
        self.env_overrides: dict[str, str] = dict(proxy_cfg.get("env", {}) or {})

    # -----------------------------------------------------------------
    # Lifecycle
    # -----------------------------------------------------------------
    def is_running(self) -> bool:
        """Check if the proxy process is alive and listening."""
        pid = self._read_pid()
        if pid is None:
            return False
        try:
            os.kill(pid, 0)  # signal 0 = check existence
        except (OSError, ProcessLookupError):
            self._clear_pid()
            return False
        # Also verify the port is actually accepting connections.
        try:
            with socket.create_connection((self.host, self.port), timeout=1.0):
                return True
        except (OSError, socket.timeout):
            return False

    def status(self) -> dict[str, Any]:
        """Return a JSON-serializable status snapshot for the UI."""
        pid = self._read_pid()
        running = self.is_running()
        return {
            "running": running,
            "host": self.host,
            "port": self.port,
            "url": f"http://{self.host}:{self.port}",
            "mode": self.mode,
            "pid": pid if running else None,
            "auto_start": self.auto_start,
            "binary": self.headroom_bin,
            "log_file": str(LOG_FILE),
        }

    def start(self, wait_seconds: float = 10.0) -> dict[str, Any]:
        """Start the proxy as a background subprocess. Returns status dict."""
        if self.is_running():
            return {"ok": True, "already_running": True, **self.status()}

        if not os.path.exists(self.headroom_bin):
            return {
                "ok": False,
                "error": f"headroom binary not found at {self.headroom_bin}. "
                         f"Run Install / upgrade headroom-ai first.",
            }

        cmd = [
            self.headroom_bin,
            "proxy",
            "--host", self.host,
            "--port", str(self.port),
            "--mode", self.mode,
        ]
        env = os.environ.copy()
        env.update(self.env_overrides)
        env["HEADROOM_HOST"] = self.host
        env["HEADROOM_PORT"] = str(self.port)
        env["HEADROOM_MODE"] = self.mode

        try:
            log_fh = open(LOG_FILE, "ab")
            proc = subprocess.Popen(
                cmd,
                stdout=log_fh,
                stderr=subprocess.STDOUT,
                env=env,
                cwd=str(PLUGIN_DIR),
                start_new_session=True,  # detach from parent process group
            )
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": f"failed to spawn proxy: {exc}"}

        self._write_pid(proc.pid)
        _print(f"started headroom proxy pid={proc.pid} on http://{self.host}:{self.port}")

        # Wait for the port to start accepting connections.
        deadline = time.time() + wait_seconds
        while time.time() < deadline:
            try:
                with socket.create_connection((self.host, self.port), timeout=1.0):
                    return {"ok": True, "started": True, **self.status()}
            except (OSError, socket.timeout):
                time.sleep(0.3)

        # If we timed out, the process may still be starting; report partial success.
        return {
            "ok": True,
            "started": True,
            "port_not_yet_listening": True,
            **self.status(),
        }

    def stop(self) -> dict[str, Any]:
        """Stop the proxy subprocess if running. Returns status dict.

        K4 (audit 2026-09-27): the PID file alone is not proof of ownership.
        If the proxy died and the OS later recycled its PID, `stop()` would
        signal an unrelated process - and because it signals the process
        *group*, potentially several. `_verify_ownership()` now confirms the
        PID really is our headroom proxy before anything is signalled.

        On a platform where the command line cannot be read, verification is
        reported as "unknown" rather than "someone else's", and the previous
        behaviour is kept: the user asked to stop the proxy, and the default
        binary is a Linux path, so a recycled PID is a remote possibility
        there. The result always states which of the two happened.
        """
        pid = self._read_pid()
        if pid is None:
            return {"ok": True, "already_stopped": True, **self.status()}

        owned, why, could_verify = self._verify_ownership(pid)
        if not owned and could_verify:
            # Positively identified as NOT our proxy. Drop the stale record so
            # the next start() is not blocked by it, and report rather than
            # signalling someone else's process.
            self._clear_pid()
            return {
                "ok": False,
                "stopped": False,
                "verified": True,
                "pid": pid,
                "error": (
                    f"refusing to signal pid {pid}: {why}. The recorded proxy is "
                    "gone and that pid now belongs to another process, so it was "
                    "left alone; the stale pid file has been removed."
                ),
            }

        try:
            os.killpg(os.getpgid(pid), signal.SIGTERM)
        except (OSError, ProcessLookupError, PermissionError):
            try:
                os.kill(pid, signal.SIGTERM)
            except (OSError, ProcessLookupError):
                pass

        # Wait up to 5s for graceful exit, then force-kill.
        for _ in range(50):
            try:
                os.kill(pid, 0)
                time.sleep(0.1)
            except (OSError, ProcessLookupError):
                self._clear_pid()
                return {
                    "ok": True,
                    "stopped": True,
                    "verified": bool(owned),
                    **self.status(),
                }

        try:
            os.kill(pid, signal.SIGKILL)
        except (OSError, ProcessLookupError):
            pass
        self._clear_pid()
        return {"ok": True, "stopped": True, "force_killed": True, **self.status()}

    def restart(self) -> dict[str, Any]:
        """Stop then start. Used after config changes."""
        self.stop()
        return self.start()

    # -----------------------------------------------------------------
    # Internal helpers
    # -----------------------------------------------------------------
    def _read_pid(self) -> int | None:
        if not PID_FILE.exists():
            return None
        try:
            return int(PID_FILE.read_text().strip())
        except (ValueError, OSError):
            return None

    def _verify_ownership(self, pid: int) -> tuple[bool, str, bool]:
        """Return (is_our_proxy, reason, could_verify).

        Conservative where verification is possible, and honest where it is
        not. A false "not ours" costs the user one manual kill; a false "ours"
        would signal an unrelated process *group*.

        `could_verify` is False on platforms with no way to read another
        process's command line. Callers must treat that as "unknown", not as
        "someone else's": refusing to stop on Windows would be a regression,
        since the default binary is a Linux path and the proxy rarely runs
        there at all.
        """
        if pid <= 0:
            return False, "not a valid pid", True

        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False, "no such process", True
        except PermissionError:
            # Alive, but owned by another user. Not ours to signal.
            return False, "process belongs to another user", True
        except OSError as exc:
            return False, f"process not reachable ({exc})", True

        cmdline = self._read_cmdline(pid)
        if cmdline is None:
            return False, "command line is not readable on this platform", False

        if not cmdline.strip():
            return False, "process has an empty command line", True

        # The proxy is always started as `<binary> proxy --host ... --port ...`,
        # so both the binary name and the subcommand must be present. Matching
        # a bare "headroom" would also match an unrelated tool.
        basename = os.path.basename(self.headroom_bin)
        if basename and basename not in cmdline:
            return False, f"command line does not contain {basename!r}", True
        if "proxy" not in cmdline.split():
            return False, "process is not running the headroom proxy subcommand", True

        return True, "verified as the headroom proxy", True

    def _read_cmdline(self, pid: int) -> str | None:
        """Best-effort read of a process's full command line.

        Linux exposes /proc/<pid>/cmdline. Elsewhere this needs a third-party
        dependency, so None is returned and the caller treats the result as
        unverifiable rather than assuming the process is safe to signal.
        """
        proc = Path("/proc") / str(pid) / "cmdline"
        try:
            if proc.exists():
                return proc.read_bytes().replace(b"\x00", b" ").decode(
                    "utf-8", errors="replace"
                )
        except OSError:
            return None
        return None
        return None

    def _write_pid(self, pid: int) -> None:
        try:
            PID_FILE.write_text(str(pid))
        except OSError as exc:  # noqa: BLE001
            _print(f"could not write pid file: {exc}")

    def _clear_pid(self) -> None:
        try:
            PID_FILE.unlink(missing_ok=True)
        except OSError:
            pass


# ---------------------------------------------------------------------
# Convenience functions used by hooks.py
# ---------------------------------------------------------------------
def auto_start_if_configured() -> bool:
    """If the plugin's config has proxy.auto_start=true, start the proxy.
    Called from hooks.py install() so the proxy comes up automatically
    whenever the plugin is enabled."""
    try:
        cfg = _config.get_config(agent=None)
        if not cfg.get("proxy", {}).get("auto_start", False):
            return False
        pm = ProxyManager(cfg)
        result = pm.start()
        if result.get("ok"):
            _print("proxy auto-started on plugin activation")
        else:
            _print(f"proxy auto-start failed: {result.get('error')}")
        return result.get("ok", False)
    except Exception as exc:  # noqa: BLE001
        _print(f"auto_start_if_configured error: {exc}")
        return False
