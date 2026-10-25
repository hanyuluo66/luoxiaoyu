"""启动进度窗口。

主程序在「建桌面分身 → 等微信 → 投递机器人 → 等控制台」这段要花几十秒到几分钟，
这期间用户什么都看不到，会以为程序没反应。这里用 tkinter 弹一个小窗口显示进度，
主流程每一步调用 ``update(percent, step, detail)``，就绪后 ``close()``。

注意：这个窗口只出现在**控制台会话**（用户能看见的那个桌面）。分身里的实例
不需要它，调用方应当跳过。
"""
from __future__ import annotations

import logging
import queue
import threading

log = logging.getLogger(__name__)

_app: "Splash | None" = None


class Splash:
    def __init__(self, title: str = "落小鱼", subtitle: str = "正在启动…") -> None:
        self._queue: queue.Queue = queue.Queue()
        self._thread: threading.Thread | None = None
        self._root = None
        self._bar = None
        self._step_var = None
        self._detail_var = None
        self._title = title
        self._subtitle = subtitle

    # ---------------------------------------------------------------- 生命周期
    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="splash", daemon=True)
        self._thread.start()

    def update(self, percent: float, step: str = "", detail: str = "") -> None:
        try:
            self._queue.put(("update", float(percent), step, detail))
        except Exception:  # noqa: BLE001
            pass

    def close(self, delay_ms: int = 0) -> None:
        try:
            self._queue.put(("close", delay_ms, "", ""))
        except Exception:  # noqa: BLE001
            pass

    def fail(self, text: str, hold_ms: int = 2500) -> None:
        try:
            self._queue.put(("fail", hold_ms, text, ""))
        except Exception:  # noqa: BLE001
            pass

    # -------------------------------------------------------------------- 内部
    def _run(self) -> None:
        try:
            import tkinter as tk
            from tkinter import ttk
        except Exception:  # noqa: BLE001
            log.debug("tkinter 不可用，跳过启动进度窗口")
            return

        try:
            root = tk.Tk()
        except Exception:  # noqa: BLE001
            log.debug("无法创建进度窗口", exc_info=True)
            return

        self._root = root
        root.title(f"{self._title} · 启动中")
        root.resizable(False, False)
        root.configure(bg="#0b1220")
        # 居中偏上，避免挡住任务栏
        width, height = 460, 190
        try:
            sw = root.winfo_screenwidth()
            sh = root.winfo_screenheight()
            root.geometry(f"{width}x{height}+{(sw - width) // 2}+{(sh - height) // 3}")
        except Exception:  # noqa: BLE001
            pass
        try:
            root.attributes("-topmost", True)
        except Exception:  # noqa: BLE001
            pass

        tk.Label(
            root, text=self._title, bg="#0b1220", fg="#e2e8f0",
            font=("Microsoft YaHei UI", 15, "bold"),
        ).pack(anchor="w", padx=22, pady=(18, 0))
        self._step_var = tk.StringVar(value=self._subtitle)
        tk.Label(
            root, textvariable=self._step_var, bg="#0b1220", fg="#93c5fd",
            font=("Microsoft YaHei UI", 10),
        ).pack(anchor="w", padx=22, pady=(6, 0))

        self._bar = ttk.Progressbar(root, length=width - 44, mode="determinate", maximum=100)
        self._bar.pack(padx=22, pady=(14, 0))

        self._detail_var = tk.StringVar(value="")
        tk.Label(
            root, textvariable=self._detail_var, bg="#0b1220", fg="#64748b",
            font=("Microsoft YaHei UI", 9), wraplength=width - 44, justify="left",
        ).pack(anchor="w", padx=22, pady=(8, 0))

        def pump() -> None:
            try:
                while True:
                    kind, value, step, detail = self._queue.get_nowait()
                    if kind == "update":
                        if self._bar is not None:
                            self._bar["value"] = max(0.0, min(100.0, value))
                        if step and self._step_var is not None:
                            self._step_var.set(step)
                        if detail is not None and self._detail_var is not None:
                            self._detail_var.set(detail)
                    elif kind == "close":
                        root.after(max(0, int(value)), root.destroy)
                        return
                    elif kind == "fail":
                        if self._step_var is not None:
                            self._step_var.set("启动没有完成")
                        if self._detail_var is not None:
                            self._detail_var.set(step)
                        if self._bar is not None:
                            self._bar["value"] = 100
                        root.after(max(0, int(value)), root.destroy)
                        return
            except queue.Empty:
                pass
            except Exception:  # noqa: BLE001
                pass
            root.after(120, pump)

        root.after(120, pump)
        try:
            root.mainloop()
        except Exception:  # noqa: BLE001
            log.debug("进度窗口异常退出", exc_info=True)


def show(title: str = "落小鱼", subtitle: str = "正在启动…") -> Splash | None:
    """Open the splash (best effort — never raises)."""
    global _app
    try:
        splash = Splash(title=title, subtitle=subtitle)
        splash.start()
        _app = splash
        return splash
    except Exception:  # noqa: BLE001
        log.debug("进度窗口启动失败", exc_info=True)
        return None


def current() -> Splash | None:
    return _app
