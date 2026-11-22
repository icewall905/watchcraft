"""Clean the raw Hub logo artwork into brand/hub_logo.png.

The generated artwork had a lumpy, off-centre edge, a slightly translucent interior
(alpha 252) and an outer rim 4-16 px wide. This fits a circle to the edge, crops a
clean anti-aliased disc inside every dent, makes it opaque, recentres it on a
1024 px canvas and repaints a uniform rim from the sampled rim colours.

Run: python3 brand/clean_logo.py raw.png brand/hub_logo.png
Then regenerate icons: cd frontend && npm run icons
"""
import math
import sys

import numpy as np
from PIL import Image

OUT_SIZE, RIM_W = 1024, 14.0


def fit_disc(im):
    a = im[..., 3]
    h, w = a.shape
    ys, xs = np.nonzero(a > 128)
    cx, cy = xs.mean(), ys.mean()
    pts = []
    for k in range(720):
        t = 2 * math.pi * k / 720
        r = 0
        for rr in range(int(min(h, w) * 0.3), int(max(h, w) * 0.6)):
            x, y = int(cx + rr * math.cos(t)), int(cy + rr * math.sin(t))
            if 0 <= x < w and 0 <= y < h and a[y, x] > 128:
                r = rr
        pts.append((cx + r * math.cos(t), cy + r * math.sin(t)))
    p = np.array(pts)
    (fx, fy, c), *_ = np.linalg.lstsq(np.c_[2 * p, np.ones(len(p))], (p ** 2).sum(1), rcond=None)
    edge = np.hypot(p[:, 0] - fx, p[:, 1] - fy)
    return fx, fy, edge.min() - 3  # radius inside every dent


def crop_disc(im, fx, fy, r, ss=4):
    size = int(math.ceil(2 * r)) + 16
    yy, xx = np.mgrid[:size * ss, :size * ss]
    dd = np.hypot((xx + 0.5) / ss - size / 2, (yy + 0.5) / ss - size / 2)
    mask = (dd <= r).reshape(size, ss, size, ss).mean((1, 3))
    x0, y0 = int(round(fx - size / 2)), int(round(fy - size / 2))
    h, w = im.shape[:2]
    crop = np.zeros((size, size, 3))
    sy0, sx0, sy1, sx1 = max(0, y0), max(0, x0), min(h, y0 + size), min(w, x0 + size)
    crop[sy0 - y0:sy1 - y0, sx0 - x0:sx1 - x0] = im[sy0:sy1, sx0:sx1, :3]
    out = np.dstack([crop, mask * 255]).round().clip(0, 255).astype(np.uint8)
    img = Image.fromarray(out, 'RGBA').resize((OUT_SIZE, OUT_SIZE), Image.LANCZOS)
    return np.array(img).astype(float), r * OUT_SIZE / size


def repaint_rim(im, r):
    n = im.shape[0]
    c = n / 2
    yy, xx = np.mgrid[:n, :n]
    d = np.hypot(xx + 0.5 - c, yy + 0.5 - c)
    th = np.arctan2(yy + 0.5 - c, xx + 0.5 - c)
    bins_n = 360
    bins = ((th + math.pi) / (2 * math.pi) * bins_n).astype(int) % bins_n
    rgb, lum = im[..., :3], im[..., :3].sum(-1)
    band = (d > r - 22) & (d < r - 1.5)
    cols = np.zeros((bins_n, 3))
    for k in range(bins_n):
        m = band & (bins == k)
        v, l = rgb[m], lum[m]
        cols[k] = v[l >= np.percentile(l, 60)].mean(0)
    ker = np.exp(-0.5 * (np.arange(-20, 21) / 7) ** 2)
    ker /= ker.sum()
    cols = np.stack([np.convolve(np.r_[cols[-20:, i], cols[:, i], cols[:20, i]], ker, 'valid') for i in range(3)], 1)
    t = np.clip((d - (r - RIM_W)) / RIM_W, 0, 1)[..., None]
    shade = cols[bins] * (0.88 + 0.2 * t)  # slightly brighter toward the outside
    blend = np.clip((d - (r - RIM_W)) / 2.0, 0, 1)[..., None]
    out = np.dstack([rgb * (1 - blend) + shade * blend, im[..., 3]])
    return out.round().clip(0, 255).astype(np.uint8)


def main(src, dst):
    raw = np.array(Image.open(src).convert('RGBA')).astype(float)
    disc, r = crop_disc(raw, *fit_disc(raw))
    Image.fromarray(repaint_rim(disc, r), 'RGBA').save(dst, optimize=True)


if __name__ == '__main__':
    main(*sys.argv[1:3])
