#!/usr/bin/env python3
"""Composite Camper Power app screenshots into the Blender case renders.

Usage:
  python3 tools/camperpower_composite.py RENDERS_DIR

RENDERS_DIR holds the 1920x1200 Cycles renders (not committed, they are large):
  front_three_quarter.png, front_three_quarter_chroma.png,
  front_ortho.png, front_ortho_chroma.png
Screenshots (320x240) are read from, and web images written to,
assets/camperpower/. Needs Pillow (with WebP) and numpy.
"""
import os
import sys

import numpy as np
from PIL import Image, ImageFilter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ASSETS = os.path.join(ROOT, "assets", "camperpower")

# Screen window: the sharp 61 x 48 mm LCD rect, corners in render pixels,
# ordered top-left, top-right, bottom-right, bottom-left as seen in the frame
# (USB end on the left, SD side on top). From screen_corners.json.
WINDOW_MM = (61.0, 48.0)
CONTENT_MM = (0.0, 1.125, 61.0, 46.875)  # uncropped 4:3 shot, letterboxed
QUADS = {
    "ortho": [(541.714, 267.429), (1378.286, 267.429), (1378.286, 925.714), (541.714, 925.714)],
    "three_quarter": [(684.428, 406.547), (1260.339, 329.873), (1248.522, 792.555), (696.114, 902.005)],
}
# Output crops (x, y, w, h) in render pixels, and the widths to export.
CROPS = {
    "ortho": ((200, 120, 1520, 950), (1520, 960)),
    "three_quarter": ((300, 200, 1240, 776), (1240, 960)),
}
JOBS = [
    ("three_quarter", "12-overview.png", "hero"),
    ("ortho", "08-ecoworthy-battery.png", "battery"),
    ("ortho", "09-renogy-bt2-dc-dc.png", "solar"),
    ("ortho", "10-diesel-heater.png", "heater"),
    ("ortho", "04-auto-detect-added.png", "setup"),
]

SS = 2                      # supersampling factor for the warp
LETTERBOX = (4, 7, 7)       # unlit LCD strips above and below the 4:3 image
BG_LEVEL = 10               # render background (#0a0a0a) is mapped to #000
REFLECTION = 0.45           # share of the glossy glass reflection kept
GLOW = (24, 208, 224)       # UI accent cyan


def homography(src, dst):
    a = []
    for (x, y), (u, v) in zip(src, dst):
        a.append([x, y, 1, 0, 0, 0, -u * x, -u * y, -u])
        a.append([0, 0, 0, x, y, 1, -v * x, -v * y, -v])
    _, _, vt = np.linalg.svd(np.asarray(a, dtype=float))
    h = vt[-1].reshape(3, 3)
    return h / h[2, 2]


def apply_h(h, pts):
    out = []
    for x, y in pts:
        p = h @ np.array([x, y, 1.0])
        out.append((p[0] / p[2], p[1] / p[2]))
    return out


def screen_layer(quad, shot, size):
    """Warp the window (letterbox + screenshot) into the frame at SS x res."""
    w, h = size[0] * SS, size[1] * SS
    quad_ss = [(x * SS, y * SS) for x, y in quad]
    mm_rect = [(0, 0), (WINDOW_MM[0], 0), WINDOW_MM, (0, WINDOW_MM[1])]
    h_mm = homography(mm_rect, quad_ss)

    # Nearest-neighbour upscale keeps every LCD pixel a hard-edged square.
    scale = 12
    up = shot.resize((shot.width * scale, shot.height * scale), Image.NEAREST)
    x0, y0, x1, y1 = CONTENT_MM
    content_quad = apply_h(h_mm, [(x0, y0), (x1, y0), (x1, y1), (x0, y1)])
    src_rect = [(0, 0), (up.width, 0), (up.width, up.height), (0, up.height)]
    inv = homography(content_quad, src_rect)
    coeffs = (inv / inv[2, 2]).flatten()[:8]

    layer = Image.new("RGB", (w, h), LETTERBOX)
    warped = up.transform((w, h), Image.PERSPECTIVE, tuple(coeffs), Image.BICUBIC)
    inside = Image.new("L", up.size, 255).transform((w, h), Image.PERSPECTIVE, tuple(coeffs), Image.BICUBIC)
    layer.paste(warped, (0, 0), inside)
    return layer.resize(size, Image.LANCZOS)


def chroma_mask(chroma):
    c = np.asarray(chroma.convert("RGB")).astype(np.float32)
    g = c[..., 1] - np.maximum(c[..., 0], c[..., 2])
    return np.clip(g / 255.0, 0.0, 1.0)


def composite(view, renders, shot):
    base = Image.open(os.path.join(renders, f"front_{view}.png")).convert("RGB")
    chroma = Image.open(os.path.join(renders, f"front_{view}_chroma.png"))
    a = chroma_mask(chroma)[..., None]

    glossy = np.asarray(base).astype(np.float32)
    screen = np.asarray(screen_layer(QUADS[view], shot, base.size)).astype(np.float32)

    # Glass reflection: the glossy screen minus its darkest level, screened on.
    glass_floor = np.percentile(glossy[a[..., 0] > 0.99], 2)
    refl = np.clip(glossy - glass_floor, 0, 255) * REFLECTION
    lit = 255 - (255 - screen) * (255 - refl) / 255

    out = glossy * (1 - a) + lit * a

    # Glow: light from the lit screen spilling onto the bezel and beyond.
    glow_src = Image.fromarray((screen * a).astype(np.uint8))
    near = np.asarray(glow_src.filter(ImageFilter.GaussianBlur(18))).astype(np.float32)
    far = np.asarray(glow_src.filter(ImageFilter.GaussianBlur(70))).astype(np.float32)
    lum = (0.5 * near.mean(-1, keepdims=True) + 1.2 * far.mean(-1, keepdims=True)) / 255
    glow = lum * np.asarray(GLOW, dtype=np.float32) * 0.55
    out = out + glow * (1 - a)

    # Map the render's #0a0a0a background to the page's #000.
    out = (out - BG_LEVEL) * 255 / (255 - BG_LEVEL)
    return Image.fromarray(np.clip(out + 0.5, 0, 255).astype(np.uint8))


def export(img, view, name):
    (x, y, w, h), widths = CROPS[view]
    img = img.crop((x, y, x + w, y + h))
    for width in widths:
        size = (width, round(h * width / w))
        im = img if size == img.size else img.resize(size, Image.LANCZOS)
        stem = os.path.join(ASSETS, f"{name}-{width}")
        im.save(stem + ".webp", "WEBP", quality=86, method=6)
        im.save(stem + ".jpg", "JPEG", quality=86, optimize=True, progressive=True, subsampling=0)
        print(f"{name}-{width}: {size[0]}x{size[1]}",
              f"webp {os.path.getsize(stem + '.webp') // 1024} KB,",
              f"jpg {os.path.getsize(stem + '.jpg') // 1024} KB")


def main():
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    renders = sys.argv[1]
    for view, shot_name, name in JOBS:
        shot = Image.open(os.path.join(ASSETS, shot_name)).convert("RGB")
        assert shot.size == (320, 240), shot_name
        export(composite(view, renders, shot), view, name)


if __name__ == "__main__":
    main()
