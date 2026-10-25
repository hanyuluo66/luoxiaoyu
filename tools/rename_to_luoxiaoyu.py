"""一次性改名脚本：落小鱼 → 落小鱼

规则：
  * 先替换 "落小鱼" → "落小鱼"（含窗口标题、日志、文档）
  * 再把剩下的 "小鱼" → "小鱼"（人设自称、托盘文案等）
  * 互斥体/常量名 AI_DA_FEI_YU → LUO_XIAOYU
  * 桌面分身引擎文件名 落小鱼引擎.exe → 落小鱼引擎.exe
只处理源码与文档；数据目录里的运行数据另行迁移。
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(r"E:\微信小鱼")
SKIP_DIRS = {
    "data", "data-session2", "data-session3", "vendor", ".venv", "node_modules",
    "package", ".setup", ".tmp", "dist", "build", "__pycache__", "wechatauto_logs",
}
SUFFIXES = {".py", ".js", ".html", ".md", ".cs", ".txt", ".bat", ".ps1", ".json", ".css"}

FORCE_INCLUDE = [
    ROOT / "release" / "README.md",
    ROOT / "release" / "微信版本说明.md",
]

REPLACEMENTS = [
    ("落小鱼", "落小鱼"),
    ("小鱼", "小鱼"),
    ("LUO_XIAOYU_BOT_SINGLETON", "LUO_XIAOYU_BOT_SINGLETON"),
    ("落小鱼引擎.exe", "落小鱼引擎.exe"),
    ("落小鱼引擎", "落小鱼引擎"),
]


def iter_files():
    for path in ROOT.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix.lower() not in SUFFIXES:
            continue
        parts = {p.lower() for p in path.parts}
        if parts & SKIP_DIRS:
            continue
        yield path
    for extra in FORCE_INCLUDE:
        if extra.exists():
            yield extra


def main() -> int:
    changed: list[tuple[Path, int]] = []
    for path in iter_files():
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        origin = text
        for old, new in REPLACEMENTS:
            text = text.replace(old, new)
        if text != origin:
            hits = sum(origin.count(old) for old, _ in REPLACEMENTS)
            path.write_text(text, encoding="utf-8")
            changed.append((path, hits))
    for path, hits in sorted(changed, key=lambda x: -x[1]):
        print(f"  {hits:>3} 处  {path.relative_to(ROOT)}")
    print(f"\n共改动 {len(changed)} 个文件")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
