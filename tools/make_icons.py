#!/usr/bin/env python3
"""把生成好的原画裁成 App 图标与 TV 横幅。

用法：python3 tools/make_icons.py <icon.png> <banner.png>
生成的图带 "Qoder AI生成" 角标：图标按外框自动裁到徽章边界，横幅裁掉底部水印条，
所以源图必须留出水印所在的边缘空白。
"""
import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
RES = ROOT / "app" / "src" / "main" / "res"
LAUNCHER = {"hdpi": 72, "xhdpi": 96, "xxhdpi": 144, "xxxhdpi": 192}
BANNER = (320, 180)


def crop_badge(img: Image.Image) -> Image.Image:
    rgb = img.convert("RGB")
    w, h = rgb.size
    corner = rgb.getpixel((2, 2))
    px = rgb.load()
    xs, ys = [], []
    for y in range(h):
        for x in range(w):
            r, g, b = px[x, y]
            if abs(r - corner[0]) + abs(g - corner[1]) + abs(b - corner[2]) > 60:
                xs.append(x)
                ys.append(y)
    box = (min(xs), min(ys), max(xs) + 1, max(ys) + 1)
    side = min(box[2] - box[0], box[3] - box[1])
    cx, cy = (box[0] + box[2]) // 2, (box[1] + box[3]) // 2
    return rgb.crop((cx - side // 2, cy - side // 2, cx + side // 2, cy + side // 2))


def crop_banner(img: Image.Image) -> Image.Image:
    rgb = img.convert("RGB")
    w, h = rgb.size
    trimmed = rgb.crop((0, 0, w, int(h * 0.94)))
    tw, th = trimmed.size
    target_w = int(th * BANNER[0] / BANNER[1])
    if target_w > tw:
        target_w, target_h = tw, int(tw * BANNER[1] / BANNER[0])
    else:
        target_h = th
    left = (tw - target_w) // 2
    return trimmed.crop((left, 0, left + target_w, target_h))


def main() -> int:
    icon_src, banner_src = (Path(a) for a in sys.argv[1:3])
    badge = crop_badge(Image.open(icon_src))
    for dpi, size in LAUNCHER.items():
        out = RES / f"mipmap-{dpi}" / "ic_launcher.png"
        badge.resize((size, size), Image.LANCZOS).save(out, optimize=True)
        print(f"{out.relative_to(ROOT)} {size}x{size} {out.stat().st_size} bytes")
    banner = crop_banner(Image.open(banner_src)).resize(BANNER, Image.LANCZOS)
    out = RES / "drawable-nodpi" / "tv_banner.png"
    banner.save(out, optimize=True)
    print(f"{out.relative_to(ROOT)} {BANNER[0]}x{BANNER[1]} {out.stat().st_size} bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
