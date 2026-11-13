#!/usr/bin/env python3
"""Composite a WLED Remote app screenshot into the same case renders.

Usage:
  python3 tools/wledremote_composite.py [RENDERS_DIR]

With RENDERS_DIR (the uncommitted Blender renders described in
camperpower_composite.py) this runs exactly the Camper Power pipeline.

Without it, the committed Camper Power hero (assets/camperpower/hero-1240.jpg)
stands in for the render: its screen is known exactly (12-overview.png through
the same warp), so the glass reflection is solved for over the dark UI pixels,
the cyan glow is subtracted, and the new screen is lit, reflected and glowed
the same way. The visible window (the recess walls hide part of it in the 3/4
view) is refit from the base image. Inside it the new screen replaces the
pixels; on its anti-aliased edges and rounded corners only the screen-to-screen
difference is added, so the case, bezel and window shape come from the render.
Swapping 12-overview.png in for itself reproduces the base to within its JPEG
noise (mean 0.6 levels).

Screenshots (320x240) are read from, and web images written to,
assets/wledremote/. Needs Pillow (with WebP) and numpy.
"""
import os
import sys

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

import camperpower_composite as cp

ASSETS = os.path.join(cp.ROOT, "assets", "wledremote")
VIEW = "three_quarter"
SHOT = "02b-home-lights-on.png"
NAME = "hero"
GLOW = (255, 165, 41)       # UI accent amber

BASE_IMAGE = os.path.join(cp.ASSETS, "hero-1240.jpg")
BASE_SHOT = os.path.join(cp.ASSETS, "12-overview.png")
BASE_GLOW = cp.GLOW
RENDER_SIZE = (1920, 1200)
CORNER_PX = 16              # around each window corner, where the edge fit is least sure
WINDOW_RADIUS = 9           # rounded window corners, render pixels
DARK = 70                   # screen pixels darker than this give a clean reflection reading


def load_shot(path):
    shot = Image.open(path).convert("RGB")
    assert shot.size == (320, 240), path
    return shot


def quad_mask(quad_mm):
    """Anti-aliased mask of a mm-space rect on the window, in render pixels."""
    w, h = RENDER_SIZE[0] * cp.SS, RENDER_SIZE[1] * cp.SS
    quad_ss = [(x * cp.SS, y * cp.SS) for x, y in cp.QUADS[VIEW]]
    mm_rect = [(0, 0), (cp.WINDOW_MM[0], 0), cp.WINDOW_MM, (0, cp.WINDOW_MM[1])]
    h_mm = cp.homography(mm_rect, quad_ss)
    poly = cp.apply_h(h_mm, quad_mm)
    m = Image.new("L", (w, h), 0)
    ImageDraw.Draw(m).polygon(poly, fill=255)
    m = m.resize(RENDER_SIZE, Image.BOX)
    return np.asarray(m).astype(np.float32) / 255.0, [(x / cp.SS, y / cp.SS) for x, y in poly]


def glow(screen, a, rgb):
    src = Image.fromarray((screen * a[..., None]).astype(np.uint8))
    near = np.asarray(src.filter(ImageFilter.GaussianBlur(18))).astype(np.float32)
    far = np.asarray(src.filter(ImageFilter.GaussianBlur(70))).astype(np.float32)
    lum = (0.5 * near.mean(-1, keepdims=True) + 1.2 * far.mean(-1, keepdims=True)) / 255
    return lum * np.asarray(rgb, dtype=np.float32) * 0.55


def screen_blend(s, r):
    return 255 - (255 - s) * (255 - r) / 255


def gauss(v, sigma):
    k = np.exp(-0.5 * (np.arange(-3 * sigma, 3 * sigma + 1) / sigma) ** 2)
    k /= k.sum()
    v = np.apply_along_axis(np.convolve, 0, v, k, mode="same")
    return np.apply_along_axis(np.convolve, 1, v, k, mode="same")


def norm_blur(v, w, sigma):
    num = np.stack([gauss(v[..., c] * w, sigma) for c in range(3)], -1)
    return num / np.maximum(gauss(w, sigma), 1e-4)[..., None]


def window_alpha(out, lit, window_poly, offset):
    """The render's own window mask, as a crop-space alpha.

    In the 3/4 view the recess walls hide part of the geometric window. Where
    the old screen and the bezel differ clearly, out = G + a (lit - G) gives a
    per pixel, with the bezel G extrapolated inward from outside the window.
    Each window side is then refit as a straight line through the a = 0.5
    crossings, and the polygon is rasterised with the window's rounded corners.
    """
    poly = [(x - offset[0], y - offset[1]) for x, y in window_poly]
    h, w = out.shape[:2]
    geo = Image.new("L", (w * cp.SS, h * cp.SS), 0)
    ImageDraw.Draw(geo).polygon([(x * cp.SS, y * cp.SS) for x, y in poly], fill=255)
    a_geo = np.asarray(geo.resize((w, h), Image.BOX)).astype(np.float32) / 255

    outside = a_geo <= 0.0
    ring = outside & (gauss(1 - outside.astype(np.float32), 6) > 0.02)
    g = norm_blur(out, ring.astype(np.float32), 8)
    d = lit - g
    den = (d * d).sum(-1)
    a = ((out - g) * d).sum(-1) / np.maximum(den, 1e-3)
    ok = (den > 25.0 ** 2 * 3) & (gauss(ring.astype(np.float32), 8) > 0.05)
    crossing = ok & (a > 0.3) & (a < 0.7)
    # UI pixels say directly whether a spot is seen or hidden. Where no clean
    # crossing exists (a wall in shadow), the edge goes at the outermost spot
    # where even dim UI is still seen; dim "hidden" readings are skewed by the
    # glass reflection, so only bright ones bound the edge from inside.
    def evidence(floor, seen_tol, hidden_gap):
        ui = lit.max(-1) > floor
        return ui & (np.abs(out - lit).max(-1) < seen_tol), ui & ((lit - out).max(-1) > hidden_gap)

    strict = evidence(90, 40, 0.5 * lit.max(-1))
    dim = evidence(30, 10, 15)

    sides = []
    for i in range(4):
        p0, p1 = np.asarray(poly[i]), np.asarray(poly[(i + 1) % 4])
        L = np.linalg.norm(p1 - p0)
        t_hat = (p1 - p0) / L
        n_hat = np.array([t_hat[1], -t_hat[0]])
        if n_hat @ (np.mean(poly, 0) - p0) < 0:
            n_hat = -n_hat

        def along(mask):
            ys, xs = np.where(mask)
            rel = np.stack([xs, ys], -1) - p0
            t, n = rel @ t_hat, rel @ n_hat
            k = (t > 2 * CORNER_PX) & (t < L - 2 * CORNER_PX) & (n > -3) & (n < 30)
            return t[k], n[k]

        def bounds(seen, hidden):
            _, n_seen = along(seen)
            _, n_hidden = along(hidden)
            lo = np.percentile(n_hidden, 98) if len(n_hidden) else 0.0
            hi = np.percentile(n_seen, 0.5) if len(n_seen) else np.inf
            return max(lo, 0.0), hi

        t, n = along(crossing)
        if len(t) >= 20:
            k1, k0 = np.polyfit(t, n, 1)
            lo, hi = bounds(*strict)
            ends = [float(np.clip(e, lo, max(hi, lo))) for e in (k0, k0 + k1 * L)]
        else:
            lo, _ = bounds(*strict)
            _, hi = bounds(*dim)
            ends = [max(hi, lo) if np.isfinite(hi) else lo] * 2
        sides.append((p0 + n_hat * ends[0], p1 + n_hat * ends[1]))

    vis = [cross(sides[i - 1], sides[i]) for i in range(4)]
    return rounded_mask(vis, (w, h)), vis


def cross(s1, s2):
    (a1, b1), (a2, b2) = s1, s2
    d1, d2 = b1 - a1, b2 - a2
    u = np.linalg.solve(np.array([d1, -d2]).T, a2 - a1)[0]
    return a1 + u * d1


def rounded_mask(poly, size):
    """Anti-aliased convex polygon with its corners rounded to WINDOW_RADIUS."""
    poly = np.asarray(poly, dtype=float) * cp.SS
    r = WINDOW_RADIUS * cp.SS
    c = poly.mean(0)
    sides = []
    for i in range(4):
        p0, p1 = poly[i], poly[(i + 1) % 4]
        t_hat = (p1 - p0) / np.linalg.norm(p1 - p0)
        n_hat = np.array([t_hat[1], -t_hat[0]])
        if n_hat @ (c - p0) < 0:
            n_hat = -n_hat
        sides.append((p0 + n_hat * r, p1 + n_hat * r))
    inner = [tuple(cross(sides[i - 1], sides[i])) for i in range(4)]
    m = Image.new("L", (size[0] * cp.SS, size[1] * cp.SS), 0)
    draw = ImageDraw.Draw(m)
    draw.polygon(inner, fill=255)
    for i in range(4):
        draw.line([inner[i], inner[(i + 1) % 4]], fill=255, width=int(2 * r))
        x, y = inner[i]
        draw.ellipse((x - r, y - r, x + r, y + r), fill=255)
    return np.asarray(m.resize(size, Image.BOX)).astype(np.float32) / 255


def erode(mask, px):
    im = Image.fromarray((mask * 255).astype(np.uint8))
    return np.asarray(im.filter(ImageFilter.MinFilter(2 * px + 1))) > 0


def reflection(out, s, weight_region):
    """The glass reflection r in out = screen(s, r), read over dark, flat UI."""
    r_raw = 255 - (255 - out) * 255 / np.maximum(255 - s, 1)
    bright = Image.fromarray(((s.max(-1) >= DARK) * 255).astype(np.uint8))
    near_bright = np.asarray(bright.filter(ImageFilter.MaxFilter(7))) > 0
    w = (weight_region & ~near_bright).astype(np.float32)
    return np.clip(norm_blur(np.clip(r_raw, 0, 255).astype(np.float32), w, 10), 0, 255), w.mean()


def rescreen(new_shot, glow_rgb=GLOW):
    (cx, cy, cw, ch), _ = cp.CROPS[VIEW]
    crop = lambda arr: arr[cy:cy + ch, cx:cx + cw]

    final = np.asarray(Image.open(BASE_IMAGE).convert("RGB")).astype(np.float32)
    assert final.shape[:2] == (ch, cw), BASE_IMAGE
    out_old = final * (255 - cp.BG_LEVEL) / 255 + cp.BG_LEVEL

    s_old_full = np.asarray(cp.screen_layer(cp.QUADS[VIEW], load_shot(BASE_SHOT), RENDER_SIZE)).astype(np.float32)
    s_new_full = np.asarray(cp.screen_layer(cp.QUADS[VIEW], new_shot, RENDER_SIZE)).astype(np.float32)
    x0, y0, x1, y1 = cp.CONTENT_MM
    a_content_full, content_poly = quad_mask([(x0, y0), (x1, y0), (x1, y1), (x0, y1)])
    a_geo_full, window_poly = quad_mask([(0, 0), (cp.WINDOW_MM[0], 0), cp.WINDOW_MM, (0, cp.WINDOW_MM[1])])
    s_old, s_new, a_c = crop(s_old_full), crop(s_new_full), crop(a_content_full)

    # Two passes: a rough reflection from deep inside the window finds the
    # visible window, then the reflection is re-read over all of it.
    r, _ = reflection(out_old, s_old, erode(a_c >= 0.999, 20))
    a, vis = window_alpha(out_old, screen_blend(s_old, r), window_poly, (cx, cy))
    r, coverage = reflection(out_old, s_old, erode(a_c * a >= 0.999, 3))
    lit_old = screen_blend(s_old, r)
    lit_new = screen_blend(s_new, r)

    a_full = np.zeros(RENDER_SIZE[::-1], np.float32)
    a_full[cy:cy + ch, cx:cx + cw] = a
    d_glow = crop(glow(s_new_full, a_full, glow_rgb) - glow(s_old_full, a_full, BASE_GLOW))

    # Direct replacement in the clean interior, exact difference elsewhere.
    yy, xx = np.mgrid[0:ch, 0:cw]
    near_corner = np.zeros((ch, cw), bool)
    for px, py in vis:
        near_corner |= (xx - px) ** 2 + (yy - py) ** 2 < CORNER_PX ** 2
    a_s = a * a_c
    direct = erode(a_s >= 0.999, 2) & ~near_corner
    out = np.where(direct[..., None], lit_new, out_old + a_s[..., None] * (lit_new - lit_old))

    # The hidden strip of the window should show only the recess wall; clear
    # any hairline the old screen left there.
    wall = (crop(a_geo_full) > 0) & (a < 0.02)
    opened = np.stack([np.asarray(Image.fromarray(np.clip(out[..., c], 0, 255).astype(np.uint8))
                                  .filter(ImageFilter.MinFilter(5)).filter(ImageFilter.MaxFilter(5)))
                       for c in range(3)], -1).astype(np.float32)
    out = np.where(wall[..., None], opened, out)
    out = out + d_glow * (1 - a[..., None])

    out = (out - cp.BG_LEVEL) * 255 / (255 - cp.BG_LEVEL)
    return Image.fromarray(np.clip(out + 0.5, 0, 255).astype(np.uint8)), coverage


def main():
    shot = load_shot(os.path.join(ASSETS, SHOT))
    if len(sys.argv) == 2:
        img = cp.composite(VIEW, sys.argv[1], shot, glow_rgb=GLOW)
        cp.export(img, VIEW, NAME, assets=ASSETS)
    elif len(sys.argv) == 1:
        img, coverage = rescreen(shot)
        print(f"reflection read from {coverage:.1%} of the frame")
        cp.export(img, VIEW, NAME, assets=ASSETS, cropped=True)
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
