"""分身干净启动：临时挂起用户的启动项。

原理
----
Windows 登录时会照着 **HKCU\\...\\Run** 和「启动」文件夹把第三方程序拉起来。
主桌面此时已经登录过了，**不会**再读这些项；只有新登录的会话（也就是桌面分身）
会读。所以——

    建分身之前：把 Run 项搬到备份键、把启动文件夹里的快捷方式挪走
    分身登录后：立刻搬回来

主桌面完全不受影响（它早就启动完了），分身则一个第三方启动项都不会跑。

安全性
----
- 备份写进注册表（`HKCU\\Software\\LuoXiaoyu\\SuspendedRun`）和清单文件，
  程序崩溃也能在下次启动时自动还原；
- 还原是幂等的：已经存在的项不会重复添加；
- 只动「用户级」启动项，不碰 HKLM（机器级）和系统服务。
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from . import paths

log = logging.getLogger(__name__)

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
BACKUP_KEY = r"Software\LuoXiaoyu\SuspendedRun"
MANIFEST = "startup-suspended.json"


def _manifest_path() -> Path:
    return paths.data_dir() / MANIFEST


# --------------------------------------------------------------------- 注册表
def _run_values() -> dict[str, tuple]:
    """读取 HKCU\\...\\Run 的 (name -> (value, type))。"""
    import winreg

    out: dict[str, tuple] = {}
    try:
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_READ | winreg.KEY_WRITE
        )
    except OSError:
        return out
    try:
        index = 0
        while True:
            try:
                name, value, vtype = winreg.EnumValue(key, index)
            except OSError:
                break
            out[name] = (value, vtype)
            index += 1
    finally:
        key.Close()
    return out


def _suspend_run_values() -> list[str]:
    """把 HKCU\\...\\Run 的值搬到备份键，返回搬走的项名。"""
    import winreg

    values = _run_values()
    if not values:
        return []
    moved: list[str] = []
    try:
        dst = winreg.CreateKeyEx(
            winreg.HKEY_CURRENT_USER, BACKUP_KEY, 0, winreg.KEY_READ | winreg.KEY_WRITE
        )
    except OSError:
        return moved
    try:
        for name, (value, vtype) in values.items():
            try:
                winreg.SetValueEx(dst, name, 0, vtype, value)
            except OSError:
                log.debug("备份启动项失败：%s", name, exc_info=True)
                continue
            moved.append(name)
    finally:
        dst.Close()

    # 备份写完后才删除原项：删除必须有 KEY_WRITE 权限
    try:
        src = winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_WRITE)
    except OSError:
        return []  # 删不掉就当没挂起，绝不半挂起
    try:
        for name in moved:
            try:
                winreg.DeleteValue(src, name)
            except OSError:
                log.debug("删除启动项失败：%s", name, exc_info=True)
    finally:
        src.Close()
    return moved


def _restore_run_values() -> list[str]:
    import winreg

    try:
        src = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, BACKUP_KEY, 0, winreg.KEY_READ | winreg.KEY_WRITE
        )
    except OSError:
        return []
    restored: list[str] = []
    try:
        values: dict[str, tuple] = {}
        index = 0
        while True:
            try:
                name, value, vtype = winreg.EnumValue(src, index)
            except OSError:
                break
            values[name] = (value, vtype)
            index += 1
        dst = winreg.CreateKeyEx(
            winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_READ | winreg.KEY_WRITE
        )
        try:
            for name, (value, vtype) in values.items():
                try:
                    winreg.SetValueEx(dst, name, 0, vtype, value)
                    restored.append(name)
                except OSError:
                    log.debug("恢复启动项失败：%s", name, exc_info=True)
        finally:
            dst.Close()
        for name in restored:
            try:
                winreg.DeleteValue(src, name)
            except OSError:
                pass
    finally:
        src.Close()
    try:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, BACKUP_KEY)
    except OSError:
        pass
    return restored

# ----------------------------------------------------------------- 启动文件夹
def _startup_folder() -> Path:
    return Path(
        __import__("os").environ.get(
            "APPDATA", str(Path.home() / "AppData" / "Roaming")
        )
    ) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"


def _suspend_startup_files() -> list[str]:
    folder = _startup_folder()
    if not folder.is_dir():
        return []
    stash = paths.data_dir() / "startup-suspended"
    moved: list[str] = []
    stash.mkdir(parents=True, exist_ok=True)
    for item in folder.iterdir():
        if item.name.lower() == "desktop.ini":
            continue
        try:
            item.rename(stash / item.name)
            moved.append(item.name)
        except OSError:
            log.debug("跳过启动文件：%s", item.name, exc_info=True)
    return moved


def _restore_startup_files() -> list[str]:
    stash = paths.data_dir() / "startup-suspended"
    if not stash.is_dir():
        return []
    folder = _startup_folder()
    folder.mkdir(parents=True, exist_ok=True)
    restored: list[str] = []
    for item in list(stash.iterdir()):
        try:
            item.rename(folder / item.name)
            restored.append(item.name)
        except OSError:
            log.debug("恢复启动文件失败：%s", item.name, exc_info=True)
    try:
        stash.rmdir()
    except OSError:
        pass
    return restored


# ------------------------------------------------------------------- 对外接口
def suspend() -> dict[str, list[str]]:
    """挂起用户启动项（建分身之前调用）。"""
    run_items = _suspend_run_values()
    files = _suspend_startup_files()
    info = {"run": run_items, "files": files}
    try:
        _manifest_path().write_text(
            json.dumps(info, ensure_ascii=False), encoding="utf-8"
        )
    except OSError:
        pass
    if run_items or files:
        log.info(
            "已挂起启动项：注册表 %d 项、启动文件夹 %d 个（分身登录后自动恢复）",
            len(run_items),
            len(files),
        )
    return info


def restore() -> dict[str, list[str]]:
    """恢复启动项（幂等，可在任意时刻重复调用）。"""
    run_items = _restore_run_values()
    files = _restore_startup_files()
    try:
        _manifest_path().unlink(missing_ok=True)
    except OSError:
        pass
    if run_items or files:
        log.info("已恢复启动项：注册表 %d 项、启动文件夹 %d 个", len(run_items), len(files))
    return {"run": run_items, "files": files}


def pending() -> dict[str, int]:
    """看还有多少项处于挂起状态（用于崩溃后自检）。"""
    import winreg

    count = 0
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, BACKUP_KEY, 0, winreg.KEY_READ)
        try:
            while True:
                try:
                    winreg.EnumValue(key, count)
                except OSError:
                    break
                count += 1
        finally:
            key.Close()
    except OSError:
        count = 0
    stash = paths.data_dir() / "startup-suspended"
    files = len(list(stash.iterdir())) if stash.is_dir() else 0
    return {"run": count, "files": files}
