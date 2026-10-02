"""Render pattern thumbnails (PNG) from the points read back from the laser.

Home Assistant independent; needs Pillow. Points are stored compactly as flat
integer lists ``[x, y, state, 0xRRGGBB, ...]`` (x/y in 0..254).
"""
from __future__ import annotations

import io

from PIL import Image, ImageDraw, ImageFont

BG = (17, 24, 32)
TILE = (28, 38, 50)
ACCENT = (80, 94, 217)       # the app's blue selection colour
TEXT = (230, 235, 245)
SS = 3                       # supersampling factor for smooth lines


def pack(points: list[tuple[int, int, int, int]]) -> list[int]:
    """Flatten ``(x, y, state, rgb)`` tuples for compact storage."""
    flat: list[int] = []
    for x, y, state, rgb in points:
        flat += [x, y, state, rgb]
    return flat


def unpack(flat: list[int]) -> list[tuple[int, int, int, int]]:
    return [(flat[i], flat[i + 1], flat[i + 2], flat[i + 3]) for i in range(0, len(flat) - 3, 4)]


def strokes(flat: list[int]) -> list[tuple[tuple[int, int, int], list[tuple[int, int]]]]:
    """Group points into coloured line segments.

    A new stroke starts at points flagged "start" (64). A segment is blank (not
    drawn) when its destination colour is black - that is how the laser's
    dashed/blanked patterns are encoded.
    """
    out: list[tuple[tuple[int, int, int], list[tuple[int, int]]]] = []
    prev: tuple[int, int] | None = None
    for x, y, state, rgb in unpack(flat):
        if state & 64:
            prev = None
        color = ((rgb >> 16) & 255, (rgb >> 8) & 255, rgb & 255)
        if prev is not None and color != (0, 0, 0) and (x, y) != prev:
            out.append((color, [prev, (x, y)]))
        prev = (x, y)
    return out


def _font(size: int):
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # very old Pillow
        return ImageFont.load_default()


def _draw_pattern(d: ImageDraw.ImageDraw, flat: list[int], box: tuple[int, int, int, int],
                  width: float) -> None:
    x0, y0, x1, y1 = box
    pad = (x1 - x0) * 0.07
    sx = (x1 - x0 - 2 * pad) / 254
    sy = (y1 - y0 - 2 * pad) / 254
    for color, seg in strokes(flat):
        pts = [(x0 + pad + px * sx, y0 + pad + py * sy) for px, py in seg]
        d.line(pts, fill=color, width=max(1, round(width)))


def render_pattern(flat: list[int] | None, size: int = 320, label: str | None = None) -> bytes:
    """One large preview. ``flat=None`` renders an empty placeholder."""
    big = size * SS
    im = Image.new("RGB", (big, big), TILE)
    d = ImageDraw.Draw(im)
    if flat:
        _draw_pattern(d, flat, (0, 0, big, big), width=1.6 * SS)
    im = im.resize((size, size), Image.LANCZOS)
    if label:
        ImageDraw.Draw(im).text((10, 8), label, fill=TEXT, font=_font(max(12, size // 14)))
    return _png(im)


def render_sheet(items: list[tuple[int, list[int] | None]], current: int | None, *,
                 cols: int = 5, cell: int = 150, title: str | None = None) -> bytes:
    """Grid of numbered thumbnails like the official app's library page.

    ``items`` is ``[(pattern_number, flat_points_or_None)]``; the tile for
    ``current`` gets the app's blue selection border.
    """
    rows = max(1, -(-len(items) // cols))
    gap = 8
    head = 34 if title else 0
    w = cols * cell + (cols + 1) * gap
    h = head + rows * cell + (rows + 1) * gap
    im = Image.new("RGB", (w, h), BG)
    d = ImageDraw.Draw(im)
    if title:
        d.text((gap + 2, 8), title, fill=TEXT, font=_font(18))
    font = _font(max(12, cell // 9))
    for i, (num, flat) in enumerate(items):
        r, c = divmod(i, cols)
        x = gap + c * (cell + gap)
        y = head + gap + r * (cell + gap)
        # draw the tile at supersampled size, then paste
        tile = Image.new("RGB", (cell * SS, cell * SS), TILE)
        if flat:
            _draw_pattern(ImageDraw.Draw(tile), flat, (0, 0, cell * SS, cell * SS), width=1.4 * SS)
        im.paste(tile.resize((cell, cell), Image.LANCZOS), (x, y))
        d.text((x + 6, y + 4), str(num), fill=TEXT, font=font)
        if not flat:
            d.text((x + cell // 2 - 4, y + cell // 2 - 6), "·", fill=(90, 100, 115), font=font)
        if num == current:
            d.rectangle([x, y, x + cell - 1, y + cell - 1], outline=ACCENT, width=3)
    return _png(im)


def _png(im: Image.Image) -> bytes:
    buf = io.BytesIO()
    im.save(buf, "PNG", optimize=True)
    return buf.getvalue()
