"""Generate the README banner for 落小鱼."""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(r"E:\微信大肥鱼")
ASSETS = ROOT / "assets"

W, H = 1200, 340
BG_TOP = (15, 20, 32)
BG_BOTTOM = (26, 38, 66)
ACCENT = (96, 165, 250)
TEXT = (240, 246, 255)
MUTED = (150, 168, 196)

FONT_BOLD = Path(r"C:\Windows\Fonts\msyhbd.ttc")
FONT_REG = Path(r"C:\Windows\Fonts\msyh.ttc")


def load_font(path: Path, size: int) -> ImageFont.FreeTypeFont:
    try:
        return ImageFont.truetype(str(path), size)
    except Exception:  # noqa: BLE001
        return ImageFont.load_default()


def gradient(size: tuple[int, int]) -> Image.Image:
    img = Image.new("RGB", size)
    draw = ImageDraw.Draw(img)
    for y in range(size[1]):
        t = y / max(1, size[1] - 1)
        color = tuple(int(a + (b - a) * t) for a, b in zip(BG_TOP, BG_BOTTOM))
        draw.line([(0, y), (size[0], y)], fill=color)
    return img


def main() -> int:
    canvas = gradient((W, H)).convert("RGBA")
    draw = ImageDraw.Draw(canvas)

    # 右下角的柔光
    glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    gdraw = ImageDraw.Draw(glow)
    gdraw.ellipse((W - 460, -160, W + 160, 460), fill=(*ACCENT, 26))
    canvas = Image.alpha_composite(canvas, glow)
    draw = ImageDraw.Draw(canvas)

    # 左侧头像
    art = Image.open(ASSETS / "luoxiaoyu-source.png").convert("RGB")
    w, h = art.size
    art = art.crop((0, 0, w, int(h * 0.872)))
    side = min(art.size)
    left = (art.width - side) // 2
    top = int((art.height - side) * 0.12)
    face = art.crop((left, top, left + side, top + side)).resize((220, 220), Image.LANCZOS)

    mask = Image.new("L", face.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, 219, 219), radius=48, fill=255)
    canvas.paste(face, (60, 60), mask)

    # 文字
    title_font = load_font(FONT_BOLD, 78)
    sub_font = load_font(FONT_REG, 30)
    tag_font = load_font(FONT_REG, 24)

    draw.text((320, 78), "落小鱼", font=title_font, fill=TEXT)
    draw.text((324, 178), "住在你电脑里的微信分身", font=sub_font, fill=ACCENT)
    draw.text(
        (324, 226),
        "在独立桌面里替你回消息 · 你的鼠标键盘不受影响",
        font=tag_font,
        fill=MUTED,
    )

    out = ASSETS / "banner.png"
    canvas.convert("RGB").save(out, quality=95)
    print("banner:", out, out.stat().st_size)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
