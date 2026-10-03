"""Convert a picture into laser line paths, entirely locally (Pillow only).

Three modes:

* ``outline``    - Canny-style edge detection (blur, Sobel gradient, non-maximum
  suppression, hysteresis) followed by tracing the one-pixel-wide edges into
  polylines. Good for photos and line art.
* ``silhouette`` - threshold the picture (Otsu, or the alpha channel of a
  transparent PNG) and trace the outlines of the shapes. Good for logos.
* ``lines``      - threshold, thin the strokes to one-pixel centrelines (Zhang-Suen)
  and trace them. Good for drawings, handwriting and text (one line per stroke).

The official app sends the picture to the manufacturer's cloud for this step;
this module never leaves the machine. Home Assistant independent.
"""
from __future__ import annotations

import io
import math
from dataclasses import dataclass, field

from PIL import Image, ImageFilter, ImageOps

MAX_DIM = 220
MAX_PIXELS = 60_000_000          # refuse absurdly large images (decompression bombs)

Pt = tuple[float, float]
RGB = tuple[int, int, int]

# laser palette colours a path can be mapped to (white, red, yellow, green, cyan, blue, purple)
LASER_COLORS: tuple[RGB, ...] = ((255, 255, 255), (255, 0, 0), (255, 255, 0), (0, 255, 0),
                                 (0, 255, 255), (0, 0, 255), (255, 0, 255))
RAINBOW: tuple[RGB, ...] = LASER_COLORS[1:]


@dataclass
class Vectorized:
    paths: list[list[Pt]]
    colors: list[RGB]
    width: float
    height: float
    notes: list[str] = field(default_factory=list)

    @property
    def points(self) -> int:
        return sum(len(p) for p in self.paths)


# --------------------------------------------------------------------------- loading

def load_image(data: bytes, max_dim: int = MAX_DIM) -> Image.Image:
    """Decode, orient, flatten animations and downscale; returns an RGBA image."""
    try:
        img = Image.open(io.BytesIO(data))
        if img.width * img.height > MAX_PIXELS:
            raise ValueError("the picture is too large")
        img.draft("RGB", (max_dim * 2, max_dim * 2))      # fast JPEG downscale
        img = ImageOps.exif_transpose(img)
        img = img.convert("RGBA")
    except ValueError:
        raise
    except Exception as err:  # noqa: BLE001
        raise ValueError(f"cannot read the picture: {err}") from err
    img.thumbnail((max_dim, max_dim), Image.LANCZOS)
    if img.width < 8 or img.height < 8:
        raise ValueError("the picture is too small")
    return img


def _flatten(rgba: Image.Image, background: int = 255) -> tuple[Image.Image, Image.Image, bool]:
    """-> (RGB over a plain background, alpha, has_transparency)."""
    alpha = rgba.getchannel("A")
    bg = Image.new("RGB", rgba.size, (background,) * 3)
    bg.paste(rgba, mask=alpha)
    return bg, alpha, alpha.getextrema()[0] < 250


def _otsu(gray: Image.Image) -> int:
    hist = gray.histogram()[:256]
    total = sum(hist)
    sum_all = sum(i * h for i, h in enumerate(hist))
    best, best_t, w0, s0 = -1.0, 128, 0, 0
    for t in range(256):
        w0 += hist[t]
        if w0 == 0:
            continue
        w1 = total - w0
        if w1 == 0:
            break
        s0 += t * hist[t]
        m0, m1 = s0 / w0, (sum_all - s0) / w1
        var = w0 * w1 * (m0 - m1) ** 2
        if var > best:
            best, best_t = var, t
    return best_t


# ------------------------------------------------------------------- silhouette tracing

def _silhouette_loops(mask: bytearray, w: int, h: int) -> list[list[Pt]]:
    """Outline loops of the ink pixels (pixel-corner coordinates)."""
    W = w + 2

    def ink(x: int, y: int) -> bool:
        return mask[(y + 1) * W + (x + 1)] == 1

    out: dict[tuple[int, int], list[tuple[int, int]]] = {}
    for y in range(h):
        for x in range(w):
            if not ink(x, y):
                continue
            if not ink(x, y - 1):
                out.setdefault((x, y), []).append((x + 1, y))
            if not ink(x + 1, y):
                out.setdefault((x + 1, y), []).append((x + 1, y + 1))
            if not ink(x, y + 1):
                out.setdefault((x + 1, y + 1), []).append((x, y + 1))
            if not ink(x - 1, y):
                out.setdefault((x, y + 1), []).append((x, y))
    loops: list[list[Pt]] = []
    while out:
        start = next(iter(out))
        loop = [start]
        cur = start
        while True:
            nxt_list = out.get(cur)
            if not nxt_list:
                break
            nxt = nxt_list.pop()
            if not nxt_list:
                del out[cur]
            loop.append(nxt)
            cur = nxt
            if cur == start:
                break
        if len(loop) > 3:
            loops.append([(float(x), float(y)) for x, y in loop])
    return loops


def _smooth_closed(pts: list[Pt]) -> list[Pt]:
    """One pass of corner cutting to take the staircase out of pixel outlines."""
    n = len(pts) - 1                      # closed: last == first
    if n < 4:
        return pts
    res = []
    for i in range(n):
        a, b, c = pts[i - 1], pts[i], pts[(i + 1) % n]
        res.append((0.25 * a[0] + 0.5 * b[0] + 0.25 * c[0], 0.25 * a[1] + 0.5 * b[1] + 0.25 * c[1]))
    res.append(res[0])
    return res


# ------------------------------------------------------------------------------ ink / thinning

def _ink_mask(gray: Image.Image, alpha: Image.Image, has_alpha: bool, invert: bool) -> tuple[Image.Image, str]:
    """White (255) where there is "ink": dark pixels, or the opaque part of a transparent picture."""
    if has_alpha:
        m = alpha.point(lambda v: 255 if v > 128 else 0)
        note = "shape from transparency"
    else:
        t = _otsu(gray)
        m = gray.point(lambda v, t=t: 255 if v <= t else 0)       # dark = ink
        note = f"threshold {t}"
    if invert:
        m = ImageOps.invert(m)
    return m.filter(ImageFilter.MedianFilter(3)), note


def _thin(pixels: set[tuple[int, int]], max_iter: int = 60) -> set[tuple[int, int]]:
    """Zhang-Suen thinning: reduce strokes to one-pixel centrelines."""
    px = set(pixels)
    for _ in range(max_iter):
        changed = False
        for step in (0, 1):
            doomed = []
            for (x, y) in px:
                n = [(x, y - 1) in px, (x + 1, y - 1) in px, (x + 1, y) in px, (x + 1, y + 1) in px,
                     (x, y + 1) in px, (x - 1, y + 1) in px, (x - 1, y) in px, (x - 1, y - 1) in px]
                b = sum(n)
                if not 2 <= b <= 6:
                    continue
                if sum(1 for i in range(8) if not n[i] and n[(i + 1) % 8]) != 1:
                    continue
                if step == 0:
                    if (n[0] and n[2] and n[4]) or (n[2] and n[4] and n[6]):
                        continue
                elif (n[0] and n[2] and n[6]) or (n[0] and n[4] and n[6]):
                    continue
                doomed.append((x, y))
            if doomed:
                px.difference_update(doomed)
                changed = True
        if not changed:
            break
    return px


# ----------------------------------------------------------------------- edge detection

def _canny_pixels(gray: Image.Image, detail: int) -> set[tuple[int, int]]:
    """Thin edge pixels: blur, Sobel, non-maximum suppression, hysteresis."""
    w, h = gray.size
    g = list(gray.filter(ImageFilter.GaussianBlur(1.1)).getdata())
    mag = [0.0] * (w * h)
    sector = bytearray(w * h)             # 0: gradient horizontal ... 3
    for y in range(1, h - 1):
        row = y * w
        for x in range(1, w - 1):
            i = row + x
            a, b, c = g[i - w - 1], g[i - w], g[i - w + 1]
            d, f = g[i - 1], g[i + 1]
            gg, hh, k = g[i + w - 1], g[i + w], g[i + w + 1]
            gx = (c + 2 * f + k) - (a + 2 * d + gg)
            gy = (gg + 2 * hh + k) - (a + 2 * b + c)
            m = math.hypot(gx, gy)
            if m < 1:
                continue
            mag[i] = m
            ang = math.degrees(math.atan2(gy, gx)) % 180
            sector[i] = 0 if ang < 22.5 or ang >= 157.5 else 1 if ang < 67.5 else 2 if ang < 112.5 else 3
    offsets = {0: (1, 0), 1: (1, 1), 2: (0, 1), 3: (-1, 1)}
    nms: dict[int, float] = {}
    for y in range(1, h - 1):
        for x in range(1, w - 1):
            i = y * w + x
            m = mag[i]
            if m < 25:                          # flat areas / noise floor
                continue
            dx, dy = offsets[sector[i]]
            if m >= mag[i + dy * w + dx] and m >= mag[i - dy * w - dx]:
                nms[i] = m
    if not nms:
        return set()
    keep = 0.15 + 0.6 * (detail / 100)         # more detail -> keep weaker edges as seeds
    values = sorted(nms.values())
    high = values[min(len(values) - 1, int(len(values) * (1 - keep)))]
    low = max(25.0, 0.45 * high)
    strong = [i for i, m in nms.items() if m >= high]
    edge = set(strong)
    stack = list(strong)
    while stack:
        i = stack.pop()
        y, x = divmod(i, w)
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                j = i + dy * w + dx
                if (dx or dy) and j not in edge and nms.get(j, 0) >= low and 0 < x + dx < w - 1:
                    edge.add(j)
                    stack.append(j)
    return {(i % w, i // w) for i in edge}


def _trace_pixels(pixels: set[tuple[int, int]]) -> list[list[Pt]]:
    """Walk 8-connected one-pixel-wide edges into polylines."""
    n8 = ((-1, 0), (1, 0), (0, -1), (0, 1), (-1, -1), (1, -1), (-1, 1), (1, 1))
    adj = {p: [q for dx, dy in n8 if (q := (p[0] + dx, p[1] + dy)) in pixels] for p in pixels}
    used: set[tuple] = set()

    def edge(a, b):
        return (a, b) if a < b else (b, a)

    def walk(start, first):
        chain = [start, first]
        used.add(edge(start, first))
        prev, cur = start, first
        while True:
            cands = [q for q in adj[cur] if edge(cur, q) not in used]
            if not cands:
                break
            d0 = (cur[0] - prev[0], cur[1] - prev[1])

            def score(q):
                v = (q[0] - cur[0], q[1] - cur[1])
                return (d0[0] * v[0] + d0[1] * v[1]) / (math.hypot(*d0) * math.hypot(*v)) - 0.01 * math.hypot(*v)
            best = max(cands, key=score)
            used.add(edge(cur, best))
            chain.append(best)
            prev, cur = cur, best
        return chain

    chains = []
    starts = sorted(pixels, key=lambda p: (len(adj[p]) != 1, p))      # endpoints first
    for p in starts:
        for q in adj[p]:
            if edge(p, q) not in used:
                chains.append([(float(x), float(y)) for x, y in walk(p, q)])
    return chains


# ----------------------------------------------------------------------- path utilities

def _rdp(pts: list[Pt], eps: float) -> list[Pt]:
    n = len(pts)
    if n < 3:
        return pts
    keep = [False] * n
    keep[0] = keep[-1] = True
    stack = [(0, n - 1)]
    while stack:
        i, j = stack.pop()
        ax, ay = pts[i]
        bx, by = pts[j]
        dx, dy = bx - ax, by - ay
        L2 = dx * dx + dy * dy
        dmax, k = 0.0, -1
        for m in range(i + 1, j):
            px, py = pts[m]
            if L2 == 0:
                d = math.hypot(px - ax, py - ay)
            else:
                t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / L2))
                d = math.hypot(px - (ax + t * dx), py - (ay + t * dy))
            if d > dmax:
                dmax, k = d, m
        if dmax > eps and k > 0:
            keep[k] = True
            stack.append((i, k))
            stack.append((k, j))
    return [p for p, kp in zip(pts, keep) if kp]


def _length(pts: list[Pt]) -> float:
    return sum(math.hypot(pts[i + 1][0] - pts[i][0], pts[i + 1][1] - pts[i][1]) for i in range(len(pts) - 1))


def _order(paths: list[list[Pt]]) -> list[list[Pt]]:
    """Greedy nearest-neighbour ordering (reversing paths) to keep blanked jumps short."""
    remaining = [p for p in paths]
    out: list[list[Pt]] = []
    cur: Pt = (0.0, 0.0)
    while remaining:
        best_i, best_d, best_rev = 0, 1e18, False
        for i, p in enumerate(remaining):
            d0 = (p[0][0] - cur[0]) ** 2 + (p[0][1] - cur[1]) ** 2
            d1 = (p[-1][0] - cur[0]) ** 2 + (p[-1][1] - cur[1]) ** 2
            if d0 < best_d:
                best_i, best_d, best_rev = i, d0, False
            if d1 < best_d:
                best_i, best_d, best_rev = i, d1, True
        p = remaining.pop(best_i)
        if best_rev:
            p = p[::-1]
        out.append(p)
        cur = p[-1]
    return out


def _nearest_laser_color(rgb: RGB) -> RGB:
    r, g, b = rgb
    if max(rgb) - min(rgb) < 48:                         # grey / black / white -> white
        return LASER_COLORS[0]
    return min(LASER_COLORS[1:], key=lambda c: (c[0] - r) ** 2 + (c[1] - g) ** 2 + (c[2] - b) ** 2)


def _ink_color(img: Image.Image, x: float, y: float) -> RGB:
    """The colour of the line itself near (x, y): on a boundary the exact pixel is blended with the
    background, so look at a small window and take the most saturated (else darkest) pixel."""
    w, h = img.size
    best, best_score = (255, 255, 255), -1.0
    for dy in range(-2, 3):
        for dx in range(-2, 3):
            px = img.getpixel((min(w - 1, max(0, int(x) + dx)), min(h - 1, max(0, int(y) + dy))))
            score = (max(px) - min(px)) * 3 + (255 - sum(px) / 3)
            if score > best_score:
                best, best_score = px, score
    return best


def _assign_colors(paths: list[list[Pt]], color: int, rgb_img: Image.Image) -> list[RGB]:
    """color: 0 = from the picture, 1-7 = a single laser colour, 8 = rainbow left to right."""
    w, h = rgb_img.size
    colors: list[RGB] = []
    for p in paths:
        if 1 <= color <= 7:
            colors.append(LASER_COLORS[color - 1])
        elif color == 8:
            cx = sum(x for x, _ in p) / len(p)
            colors.append(RAINBOW[min(len(RAINBOW) - 1, int(cx / max(1, w) * len(RAINBOW)))])
        else:
            samples = [p[int(i * (len(p) - 1) / 4)] for i in range(5)]
            px = [_ink_color(rgb_img, x, y) for x, y in samples]
            avg = tuple(sum(c[i] for c in px) // len(px) for i in range(3))
            colors.append(_nearest_laser_color(avg))
    return colors


# ------------------------------------------------------------------------------- main

def vectorize(data: bytes, *, mode: str = "outline", detail: int = 50, invert: bool = False,
              color: int = 0, max_points: int = 700) -> Vectorized:
    """Convert picture bytes to coloured laser paths (coordinates: x right, y down)."""
    if mode not in ("outline", "silhouette", "lines"):
        raise ValueError("mode must be 'outline', 'silhouette' or 'lines'")
    detail = min(100, max(1, int(detail)))
    rgba = load_image(data)
    rgb, alpha, has_alpha = _flatten(rgba)
    w, h = rgb.size
    gray = rgb.convert("L")
    notes: list[str] = []

    if mode in ("silhouette", "lines"):
        m, note = _ink_mask(gray, alpha, has_alpha, invert)
        notes.append(note)
        pix = m.getdata()
        if mode == "silhouette":
            buf = bytearray((w + 2) * (h + 2))
            for y in range(h):
                row = (y + 1) * (w + 2) + 1
                for x in range(w):
                    if pix[y * w + x] > 127:
                        buf[row + x] = 1
            raw = [_smooth_closed(p) for p in _silhouette_loops(buf, w, h)]
            min_len = 8.0
        else:
            ink = {(x, y) for y in range(h) for x in range(w) if pix[y * w + x] > 127}
            raw = _trace_pixels(_thin(ink))
            min_len = 5.0
    else:
        if has_alpha:        # white-on-transparent logos vanish on white: use a mid-grey background
            gray = _flatten(rgba, 128)[0].convert("L")
        if invert:
            gray = ImageOps.invert(gray)
        raw = _trace_pixels(_canny_pixels(ImageOps.autocontrast(gray, cutoff=1), detail))
        min_len = 5.0

    eps = 0.5 + (100 - detail) / 100 * 2.5            # more detail -> finer polylines
    paths: list[list[Pt]] = []
    for _ in range(9):
        paths = []
        for p in raw:
            s = _rdp(p, eps)
            if len(s) >= 2 and _length(s) >= min_len:
                paths.append(s)
        if sum(len(p) for p in paths) <= max_points:
            break
        eps *= 1.4
    else:
        paths.sort(key=_length, reverse=True)           # still too many: keep the longest paths
        kept, total = [], 0
        for p in paths:
            if total + len(p) <= max_points:
                kept.append(p)
                total += len(p)
        paths = kept
        notes.append("reduced to the longest lines")
    if not paths:
        raise ValueError("no lines found in the picture - try the other mode, 'Invert', or a higher detail")
    paths = _order(paths)
    return Vectorized(paths, _assign_colors(paths, color, rgb), float(w), float(h), notes)
