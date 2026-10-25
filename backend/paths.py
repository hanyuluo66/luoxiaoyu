"""Filesystem layout helpers.

Portable design:
  * frozen (PyInstaller onefile) -> data/ lives next to the exe
  * development                   -> data/ lives in the project root
Bundled resources (web/) are read from the PyInstaller temp dir when frozen.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

APP_NAME = "落小鱼"
APP_ID = "ai-dafeiyu"


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def project_root() -> Path:
    """Dev project root (parent of the backend package)."""
    return Path(__file__).resolve().parent.parent


def resource_dir() -> Path:
    """Directory containing bundled read-only resources (web assets)."""
    if is_frozen():
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            return Path(meipass)
        return Path(sys.executable).resolve().parent
    return project_root()


def install_dir() -> Path:
    """Directory the executable lives in."""
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return project_root()


def _writable(candidate: Path) -> bool:
    try:
        candidate.mkdir(parents=True, exist_ok=True)
        probe = candidate / ".write-probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        return True
    except OSError:
        return False


def data_dir() -> Path:
    """Writable data directory (config, database, logs)."""
    override = os.environ.get("DAFEIYU_DATA_DIR")
    if override:
        custom = Path(override).expanduser()
        custom.mkdir(parents=True, exist_ok=True)
        return custom

    portable = install_dir() / "data"
    if _writable(portable):
        return portable

    local = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    fallback = Path(local) / APP_ID / "data"
    fallback.mkdir(parents=True, exist_ok=True)
    return fallback


def logs_dir() -> Path:
    d = data_dir() / "logs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def config_path() -> Path:
    return data_dir() / "config.json"


def db_path() -> Path:
    return data_dir() / "bot.sqlite3"


def web_dir() -> Path:
    return resource_dir() / "web"
