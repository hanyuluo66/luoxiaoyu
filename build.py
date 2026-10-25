"""Build 落小鱼.exe with PyInstaller.

Run:  .venv\\Scripts\\python.exe build.py
Output: release\\落小鱼.exe  (single file, tray + web console)
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
APP_NAME = "落小鱼"
ENTRY = ROOT / "main.py"
DIST = ROOT / "release"
BUILD = ROOT / "build"
ASSETS = ROOT / "assets"


def make_icon() -> Path:
    """Use the 落小鱼 artwork as the exe icon (falls back to the drawn fish)."""
    ASSETS.mkdir(exist_ok=True)
    ico = ASSETS / "icon.ico"
    try:
        from PIL import Image
    except ImportError:
        print("! 没有 Pillow，跳过图标")
        return ico

    source = ASSETS / "luoxiaoyu-source.png"
    if source.exists():
        try:
            image = Image.open(source).convert("RGB")
            w, h = image.size
            art = image.crop((0, 0, w, int(h * 0.872)))  # 去掉底部黑色标题带
            side = min(art.size)
            left = (art.width - side) // 2
            top = int((art.height - side) * 0.12)
            face = art.crop((left, top, left + side, top + side))
            image = face.resize((256, 256), Image.LANCZOS)
            image.save(ico, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
            print(f"+ 图标已生成（落小鱼）{ico}")
            return ico
        except Exception as exc:  # noqa: BLE001
            print(f"! 用原图生成图标失败（{exc}），改用画的")

    size = 256
    image = Image.new("RGB", (size, size), (15, 20, 32))
    draw = ImageDraw.Draw(image)
    draw.ellipse((24, 56, 232, 200), fill=(255, 196, 87))
    draw.polygon([(184, 176), (248, 232), (160, 208)], fill=(255, 196, 87))
    draw.ellipse((88, 104, 112, 128), fill=(40, 30, 10))
    draw.ellipse((150, 92, 190, 122), fill=(255, 255, 255))
    draw.ellipse((162, 98, 184, 118), fill=(30, 24, 8))
    image.save(ico, sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print(f"+ 图标已生成 {ico}")
    return ico


def build(console: bool = False) -> int:
    icon = make_icon()

    if not (ROOT / "web" / "index.html").exists():
        print("! web/index.html 不存在，打出来的 exe 会没有控制台界面")

    args = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onefile",
        "--console" if console else "--noconsole",
        "--name",
        APP_NAME + ("-console" if console else ""),
        "--distpath",
        str(DIST / ("console" if console else ".")),
        "--workpath",
        str(BUILD),
        "--specpath",
        str(BUILD),
        "--add-data",
        f"{ROOT / 'web'}{';'}web",
        "--collect-submodules",
        "wxauto",
        "--collect-submodules",
        "wechatauto",
        "--collect-submodules",
        "uiautomation",
        "--collect-submodules",
        "uvicorn",
        "--collect-submodules",
        "pystray",
        "--hidden-import",
        "comtypes",
        "--hidden-import",
        "comtypes.client",
        "--hidden-import",
        "pywinauto",
        "--hidden-import",
        "win32gui",
        "--hidden-import",
        "win32api",
        "--hidden-import",
        "win32con",
        "--hidden-import",
        "win32clipboard",
        "--hidden-import",
        "pythoncom",
        "--hidden-import",
        "pywintypes",
        "--hidden-import",
        "PIL.Image",
        "--hidden-import",
        "PIL.ImageDraw",
        "--hidden-import",
        "numpy",
        "--hidden-import",
        "cryptography.hazmat.primitives.ciphers",
        "--hidden-import",
        "zstandard",
        "--hidden-import",
        "psutil",
        "--hidden-import",
        "pyperclip",
        # 控制台独立窗口（WebView2）
        "--hidden-import",
        "webview",
        "--hidden-import",
        "webview.platforms.edgechromium",
        "--hidden-import",
        "webview.platforms.winforms",
        "--hidden-import",
        "clr",
        "--collect-submodules",
        "webview",
        "--collect-submodules",
        "pythonnet",
        "--collect-data",
        "webview",
    ]
    if icon.exists():
        args += ["--icon", str(icon)]
    args.append(str(ENTRY))

    print("+ 开始打包，首次会比较慢（1~3 分钟）…")
    result = subprocess.run(args, cwd=str(ROOT))
    if result.returncode != 0:
        print("! 打包失败")
        return result.returncode

    out_dir = DIST / ("console" if console else ".")
    exe = out_dir / f"{APP_NAME}{'-console' if console else ''}.exe"
    if exe.exists():
        size_mb = exe.stat().st_size / 1024 / 1024
        print(f"\n✅ 打包完成：{exe}  ({size_mb:.1f} MB)")
        if console:
            print("   这是诊断用的控制台版本，会显示报错信息。")
        else:
            print("   双击即可运行，控制台会自动在浏览器打开。")
    else:
        print("! 没找到输出的 exe")
        return 1

    if console:
        return 0

    # Ship the docs next to the exe so the downgrade guide travels with it.
    for doc in ("README.md", "docs/微信版本说明.md"):
        src = ROOT / doc
        if src.exists():
            target = DIST / Path(doc).name
            shutil.copy2(src, target)
            print(f"   + {target.name}")

    # The desktop-clone keeper is a separate small .NET executable: it has to
    # outlive our process and host the RDP ActiveX control.
    keeper = ROOT / "tools" / "落小鱼引擎.exe"
    if keeper.exists():
        target = DIST / "落小鱼引擎.exe"
        shutil.copy2(keeper, target)
        print(f"   + {target.name}")
    else:
        print("! 缺少 tools/落小鱼引擎.exe（桌面分身功能将不可用）")
    return 0


if __name__ == "__main__":
    raise SystemExit(build(console="--console" in sys.argv))


