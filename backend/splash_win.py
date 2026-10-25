"""启动进度窗口（独立进程版）。

不用 tkinter：Tk 在非主线程里创建/销毁很容易 `Tcl_AsyncDelete` 直接把进程干掉。
这里用同一个 WebView2（pywebview）开一个无边框小窗，进度通过一个 JSON 文件传递：

    主程序写好  state.json  →  本窗口每 150ms 读一次  →  用 evaluate_js 更新界面

所以启动窗口是**独立进程**（`落小鱼.exe --splash <state.json>`），主程序忙它的，
窗口关掉也只是这个进程退出。
"""
from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path

log = logging.getLogger(__name__)

HTML = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<style>
  html,body{margin:0;height:100%;background:#0b1220;color:#e2e8f0;
    font-family:"Microsoft YaHei UI","Segoe UI",sans-serif;overflow:hidden;user-select:none}
  .wrap{padding:20px 22px 18px}
  .title{font-size:17px;font-weight:700;letter-spacing:.5px}
  .step{margin-top:10px;font-size:12.5px;color:#93c5fd;min-height:18px}
  .track{margin-top:12px;height:7px;border-radius:99px;background:rgba(148,163,184,.2);overflow:hidden}
  .fill{height:100%;width:2%;border-radius:99px;
    background:linear-gradient(90deg,#38bdf8,#6366f1);transition:width .35s ease}
  .row{display:flex;justify-content:space-between;margin-top:7px;font-size:11px;color:#64748b}
  .detail{margin-top:6px;font-size:11px;color:#64748b;min-height:14px;
    white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
</style></head>
<body><div class="wrap">
  <div class="title">落小鱼</div>
  <div class="step" id="step">正在启动…</div>
  <div class="track"><div class="fill" id="fill"></div></div>
  <div class="row"><span id="pct">0%</span><span id="detail"></span></div>
</div>
<script>
function setProgress(p, step, detail) {
  const f = document.getElementById('fill');
  if (f) f.style.width = Math.max(2, Math.min(100, p)) + '%';
  document.getElementById('pct').textContent = Math.round(p) + '%';
  if (step) document.getElementById('step').textContent = step;
  document.getElementById('detail').textContent = detail || '';
}
</script></body></html>
"""


def write_state(path: Path, percent: float, step: str = "", detail: str = "",
                done: bool = False, failed: bool = False) -> None:
    """主程序调用：把进度写进状态文件（原子替换，避免读到半个 JSON）。"""
    payload = {
        "percent": float(percent),
        "step": step,
        "detail": detail,
        "done": bool(done),
        "failed": bool(failed),
        "ts": time.time(),
    }
    tmp = path.with_suffix(".tmp")
    try:
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)
    except OSError:
        pass


def _read_state(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def run_splash_window(state_file: str, width: int = 460, height: int = 168) -> int:
    """本进程就是启动窗口：读状态文件更新界面，完成后自行退出。"""
    try:
        import webview
    except Exception:  # noqa: BLE001
        log.warning("pywebview 不可用，启动窗口跳过")
        return 0

    path = Path(state_file)
    window = webview.create_window(
        "落小鱼 · 启动中",
        html=HTML,
        width=width,
        height=height,
        resizable=False,
        frameless=True,
        easy_drag=True,
        on_top=True,
        background_color="#0b1220",
    )

    stop = threading.Event()

    def pump() -> None:
        deadline = time.time() + 600  # 最多盯 10 分钟
        while not stop.is_set() and time.time() < deadline:
            state = _read_state(path)
            if state:
                try:
                    window.evaluate_js(
                        "setProgress(%s, %s, %s)"
                        % (
                            float(state.get("percent") or 0),
                            json.dumps(str(state.get("step") or "")),
                            json.dumps(str(state.get("detail") or "")),
                        )
                    )
                except Exception:  # noqa: BLE001
                    pass
                if state.get("failed"):
                    time.sleep(2.4)
                    break
                if state.get("done"):
                    time.sleep(0.5)
                    break
            time.sleep(0.15)
        try:
            window.destroy()
        except Exception:  # noqa: BLE001
            pass

    threading.Thread(target=pump, name="splash-pump", daemon=True).start()
    try:
        webview.start(gui="edgechromium")
    except Exception:  # noqa: BLE001
        log.debug("启动窗口异常退出", exc_info=True)
    finally:
        stop.set()
    return 0
