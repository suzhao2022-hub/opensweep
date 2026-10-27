"""Small OS helpers: platform name, admin detection, app data folders."""

from __future__ import annotations

import ctypes
import os
import subprocess
import sys
from pathlib import Path

from opensweep import APP_NAME

IS_WINDOWS = sys.platform == "win32"


def platform_key() -> str:
    """Return the key used in rule files: windows, darwin or linux."""
    if IS_WINDOWS:
        return "windows"
    if sys.platform == "darwin":
        return "darwin"
    return "linux"


def is_admin() -> bool:
    if IS_WINDOWS:
        try:
            return bool(ctypes.windll.shell32.IsUserAnAdmin())
        except OSError:
            return False
    return os.geteuid() == 0


def relaunch_as_admin() -> bool:
    """Start a new elevated copy of the app. Returns True if the UAC prompt was accepted."""
    if not IS_WINDOWS:
        return False
    if getattr(sys, "frozen", False):
        exe, params = sys.executable, ""
    else:
        exe, params = sys.executable, "-m opensweep"
    rc = ctypes.windll.shell32.ShellExecuteW(None, "runas", exe, params, None, 1)
    return rc > 32


def config_dir() -> Path:
    if IS_WINDOWS:
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    path = base / APP_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def log_dir() -> Path:
    path = config_dir() / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def user_rules_dir() -> Path:
    path = config_dir() / "rules"
    path.mkdir(parents=True, exist_ok=True)
    return path


def open_in_file_manager(path: str | Path) -> None:
    path = str(path)
    if IS_WINDOWS:
        if os.path.isfile(path):
            subprocess.Popen(["explorer", "/select,", path])
        else:
            os.startfile(path)  # noqa: S606 - opening a folder the user asked for
    elif sys.platform == "darwin":
        subprocess.Popen(["open", "-R" if os.path.isfile(path) else "", path])
    else:
        subprocess.Popen(["xdg-open", os.path.dirname(path) if os.path.isfile(path) else path])


def resource_path(*parts: str) -> Path:
    """Locate bundled data both from source and from a PyInstaller build."""
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    if getattr(sys, "frozen", False):
        base = base / "opensweep"
    return base.joinpath(*parts)
