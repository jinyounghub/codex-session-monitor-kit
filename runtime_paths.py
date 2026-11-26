"""Resolve the local Codex installation and the collector's user data directory."""

import os
from pathlib import Path
import shutil
import sys


APP = Path(__file__).resolve().parent
CODEX_HOME = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")).expanduser().resolve()
LOCAL_DASHBOARD_OFF = "local-dashboard.disabled"


def runtime_dir():
    override = os.environ.get("CODEX_MONITOR_DATA_DIR")
    if override:
        return Path(override).expanduser().resolve()
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        return base / "CodexSessionMonitor"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "CodexSessionMonitor"
    base = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state"))
    return base / "codex-session-monitor"


def mac_gui():
    override = os.environ.get("CODEX_MONITOR_MAC_APP")
    if override:
        app = Path(override).expanduser().resolve()
        if not app.is_dir() or app.suffix != ".app":
            raise RuntimeError("Set CODEX_MONITOR_MAC_APP to the installed macOS application bundle.")
        return app
    for name in ("Codex.app", "ChatGPT.app"):
        for folder in (Path("/Applications"), Path.home() / "Applications"):
            app = folder / name
            if app.is_dir():
                return app
    return None


def codex_binary():
    candidate = os.environ.get("CODEX_MONITOR_REAL_CODEX")
    if not candidate and sys.platform == "darwin" and (app := mac_gui()):
        resources = app / "Contents" / "Resources"
        bundles = (
            resources / "codex-cli" / "CodexCLI.app" / "Contents" / "MacOS" / "codex",
            resources / "codex",
            resources / "codex-cli" / "bin" / "codex",
        )
        candidate = next((binary for binary in bundles if binary.is_file()), None)
    candidate = candidate or shutil.which("codex")
    if not candidate:
        raise RuntimeError("Set CODEX_MONITOR_REAL_CODEX to the Codex executable.")
    executable = Path(candidate).expanduser().resolve()
    if not executable.is_file() or executable == (APP / "codex-capture").resolve():
        raise RuntimeError("The configured Codex executable is unavailable.")
    return executable
