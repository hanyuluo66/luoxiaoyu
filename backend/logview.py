"""Expose the on-disk run logs to the web console.

The console can list the log files, tail one of them, download it, or open the
folder in Explorer. Everything is local-only; nothing leaves the machine.
"""
from __future__ import annotations

import logging

from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

MAX_TAIL_LINES = 2000
MAX_DOWNLOAD_BYTES = 8 * 1024 * 1024

LABELS = {
    "app": "主程序日志（控制器 / 本机模式）",
    "keeper": "桌面分身引擎日志",
    "wechatauto": "微信自动化库日志",
}


def _session_label(path: Path) -> str:
    """data-session2/logs/app.log -> 分身实例日志（会话 2）"""
    if path.name == "childsession-keeper.log":
        return LABELS["keeper"]
    if path.parent.name == "wechatauto_logs":
        return LABELS["wechatauto"]
    for parent in path.parents:
        name = parent.name
        if name.startswith("data-session"):
            session = name.replace("data-session", "")
            return f"分身实例日志（会话 {session}）"
    if path.parent.name == "logs" and path.parent.parent.name == "data":
        return LABELS["app"]
    return "运行日志"


def list_logs(install_dir: Path, data_dir: Path) -> list[dict[str, Any]]:
    """Collect every log file that belongs to this installation."""
    items: list[dict[str, Any]] = []
    seen: set[Path] = set()

    candidates: list[tuple[str, Path]] = []

    app_log = data_dir / "logs" / "app.log"
    candidates.append(("app:" + str(app_log), app_log))
    # 控制器/本机模式的那份日志永远在 install_dir\data\logs 下；子会话实例的
    # data_dir 不是它，所以两个都列出来（按真实路径去重）。
    main_log = install_dir / "data" / "logs" / "app.log"
    candidates.append(("main:" + str(main_log), main_log))
    for extra in sorted(install_dir.glob("data-session*/logs/app.log")):
        candidates.append(("session:" + str(extra), extra))

    keeper = install_dir / "childsession-keeper.log"
    candidates.append(("keeper:" + str(keeper), keeper))

    for wx in sorted((install_dir / "wechatauto_logs").glob("*.log")):
        candidates.append(("wechatauto:" + str(wx), wx))

    for key, path in candidates:
        try:
            resolved = path.resolve()
        except OSError:
            continue
        if resolved in seen or not path.exists():
            continue
        seen.add(resolved)
        try:
            stat = path.stat()
        except OSError:
            continue
        items.append(
            {
                "key": key,
                "name": path.name,
                "label": _session_label(path),
                "dir": str(path.parent),
                "size": stat.st_size,
                "mtime": stat.st_mtime,
            }
        )
    items.sort(key=lambda item: item["mtime"], reverse=True)
    return items


def find_log(install_dir: Path, data_dir: Path, key: str) -> Path | None:
    for item in list_logs(install_dir, data_dir):
        if item["key"] == key:
            return Path(item["dir"]) / item["name"]
    return None


def tail(path: Path, lines: int = 200) -> str:
    """Return the last `lines` lines without loading a huge file into memory."""
    lines = max(10, min(int(lines or 200), MAX_TAIL_LINES))
    try:
        size = path.stat().st_size
    except OSError:
        return ""
    block = 64 * 1024
    with path.open("rb") as handle:
        data = b""
        pos = size
        while pos > 0 and data.count(b"\n") <= lines:
            step = min(block, pos)
            pos -= step
            handle.seek(pos)
            data = handle.read(step) + data
            if len(data) > MAX_DOWNLOAD_BYTES:
                break
    text = data.decode("utf-8", "replace")
    rows = text.splitlines()
    return "\n".join(rows[-lines:])


def open_folder(path: Path) -> bool:
    """Reveal a file (or open its folder) on the user's own desktop.

    The console is usually served from inside the clone session, so a plain
    ``startfile`` would open Explorer on the clone desktop — invisible to the
    user. ``childsession.open_on_console`` hands the job to the console session.
    """
    from . import childsession as _cs

    try:
        target = path.parent if path.is_file() else path
        ok, detail = _cs.open_on_console(target)
        if not ok:
            log.warning("打开目录失败：%s（%s）", target, detail)
        return bool(ok)
    except Exception:  # noqa: BLE001
        log.exception("打开目录异常：%s", path)
        return False
