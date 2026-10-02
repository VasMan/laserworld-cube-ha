"""A small original single-stroke ("one line") font for laser text.

Glyphs are polylines on a grid: baseline y=0, x-height 4, cap height 6,
descenders down to y=-2. y points *up* here; ``layout`` flips it for the laser
(canvas coordinates, y down).

This font was drawn for this project and is released under the same license as
the rest of the integration. It deliberately contains no data derived from the
fonts shipped with the official app.
"""
from __future__ import annotations

import math
import unicodedata
from collections.abc import Iterable

Point = tuple[float, float]
Stroke = list[Point]

GAP = 1.6          # space between glyphs
SPACE = 2.6        # width of a space
LINE_HEIGHT = 9.5  # baseline to baseline


def arc(cx: float, cy: float, rx: float, ry: float, a0: float, a1: float,
        step: float = 30.0) -> Stroke:
    """Elliptic arc from angle a0 to a1 (degrees, counter-clockwise positive)."""
    n = max(2, int(abs(a1 - a0) / step + 0.999))
    pts = []
    for i in range(n + 1):
        t = math.radians(a0 + (a1 - a0) * i / n)
        pts.append((round(cx + rx * math.cos(t), 3), round(cy + ry * math.sin(t), 3)))
    return pts


def ellipse(cx: float, cy: float, rx: float, ry: float, start: float = 90.0) -> Stroke:
    return arc(cx, cy, rx, ry, start, start + 360.0)


def _join(*parts: Iterable[Point]) -> Stroke:
    out: Stroke = []
    for part in parts:
        for p in part:
            if not out or out[-1] != p:
                out.append(p)
    return out


def _dot(x: float, y: float) -> Stroke:
    return [(x, y), (x, y + 0.5)]


# glyph name -> (width, strokes)
G: dict[str, tuple[float, list[Stroke]]] = {}


def _g(ch: str, width: float, *strokes: Stroke) -> None:
    G[ch] = (width, [list(s) for s in strokes])


# ----------------------------------------------------------------- upper case
_g("A", 4.4, [(0, 0), (2.2, 6), (4.4, 0)], [(0.8, 2), (3.6, 2)])
_g("B", 3.8,
   _join([(0, 0), (0, 6), (2.3, 6)], arc(2.3, 4.5, 1.4, 1.5, 90, -90), [(0, 3)]),
   _join([(0, 3), (2.5, 3)], arc(2.5, 1.5, 1.4, 1.5, 90, -90), [(0, 0)]))
_g("C", 4.2, arc(2.2, 3, 2.0, 3.0, 40, 320))
_g("D", 4.0, _join([(0, 0), (0, 6), (1.8, 6)], arc(1.8, 3, 2.2, 3, 90, -90), [(0, 0)]))
_g("E", 3.6, [(3.6, 6), (0, 6), (0, 0), (3.6, 0)], [(0, 3), (3.0, 3)])
_g("F", 3.6, [(3.6, 6), (0, 6), (0, 0)], [(0, 3), (3.0, 3)])
_g("G", 4.3, _join(arc(2.2, 3, 2.0, 3.0, 40, 360), [(2.4, 3)]))
_g("H", 4.0, [(0, 0), (0, 6)], [(4, 0), (4, 6)], [(0, 3), (4, 3)])
_g("I", 2.8, [(1.4, 0), (1.4, 6)], [(0.4, 6), (2.4, 6)], [(0.4, 0), (2.4, 0)])
_g("J", 3.6, _join([(3.4, 6), (3.4, 1.8)], arc(2.1, 1.8, 1.3, 1.8, 0, -180)))
_g("K", 4.0, [(0, 0), (0, 6)], [(4, 6), (0, 2.4)], [(1.2, 3.5), (4, 0)])
_g("L", 3.4, [(0, 6), (0, 0), (3.4, 0)])
_g("M", 5.2, [(0, 0), (0, 6), (2.6, 2.2), (5.2, 6), (5.2, 0)])
_g("N", 4.2, [(0, 0), (0, 6), (4.2, 0), (4.2, 6)])
_g("O", 4.4, ellipse(2.2, 3, 2.2, 3.0))
_g("P", 3.8, _join([(0, 0), (0, 6), (2.3, 6)], arc(2.3, 4.4, 1.5, 1.6, 90, -90), [(0, 2.8)]))
_g("Q", 4.4, ellipse(2.2, 3, 2.2, 3.0), [(2.5, 1.5), (4.4, -0.7)])
_g("R", 4.0, _join([(0, 0), (0, 6), (2.3, 6)], arc(2.3, 4.4, 1.5, 1.6, 90, -90), [(0, 2.8)]),
   [(1.9, 2.8), (4, 0)])
_g("S", 4.0, _join(arc(2.0, 4.5, 1.8, 1.5, 40, 270), arc(2.0, 1.5, 1.9, 1.5, 90, -140)))
_g("T", 4.0, [(0, 6), (4, 6)], [(2, 6), (2, 0)])
_g("U", 4.0, _join([(0, 6), (0, 1.9)], arc(2.0, 1.9, 2.0, 1.9, 180, 360), [(4, 6)]))
_g("V", 4.4, [(0, 6), (2.2, 0), (4.4, 6)])
_g("W", 6.0, [(0, 6), (1.5, 0), (3.0, 4.2), (4.5, 0), (6.0, 6)])
_g("X", 4.2, [(0, 6), (4.2, 0)], [(4.2, 6), (0, 0)])
_g("Y", 4.2, [(0, 6), (2.1, 3), (4.2, 6)], [(2.1, 3), (2.1, 0)])
_g("Z", 4.0, [(0, 6), (4, 6), (0, 0), (4, 0)])

# ----------------------------------------------------------------- lower case
_g("a", 3.8, ellipse(1.9, 2, 1.9, 2.0, 0), [(3.8, 4), (3.8, 0)])
_g("b", 3.8, [(0, 6), (0, 0)], ellipse(1.9, 2, 1.9, 2.0, 180))
_g("c", 3.6, arc(1.9, 2, 1.8, 2.0, 45, 315))
_g("d", 3.8, [(3.8, 6), (3.8, 0)], ellipse(1.9, 2, 1.9, 2.0, 0))
_g("e", 3.8, _join([(0.1, 2), (3.7, 2)], arc(1.9, 2, 1.8, 2.0, 0, 320)))
_g("f", 3.0, _join([(1.3, 0), (1.3, 5)], arc(2.5, 5, 1.2, 1.0, 180, 60)), [(0, 4), (2.9, 4)])
_g("g", 3.8, ellipse(1.9, 2, 1.9, 2.0, 0), _join([(3.8, 4), (3.8, -0.8)], arc(1.9, -0.8, 1.9, 1.2, 0, -150)))
_g("h", 3.8, [(0, 0), (0, 6)], _join([(0, 2.6)], arc(1.9, 2.6, 1.9, 1.4, 180, 0), [(3.8, 0)]))
_g("i", 1.4, [(0.7, 0), (0.7, 4)], _dot(0.7, 5.2))
_g("j", 2.4, _join([(1.5, 4), (1.5, -0.6)], arc(0.4, -0.6, 1.1, 1.2, 0, -110)), _dot(1.5, 5.2))
_g("k", 3.6, [(0, 0), (0, 6)], [(3.5, 4), (0, 1.6)], [(1.1, 2.5), (3.7, 0)])
_g("l", 1.4, [(0.7, 6), (0.7, 0)])
_g("m", 6.0, [(0, 0), (0, 4)],
   _join([(0, 2.6)], arc(1.5, 2.6, 1.5, 1.4, 180, 0), [(3.0, 0)]),
   _join([(3.0, 2.6)], arc(4.5, 2.6, 1.5, 1.4, 180, 0), [(6.0, 0)]))
_g("n", 3.8, [(0, 0), (0, 4)], _join([(0, 2.6)], arc(1.9, 2.6, 1.9, 1.4, 180, 0), [(3.8, 0)]))
_g("o", 3.8, ellipse(1.9, 2, 1.9, 2.0))
_g("p", 3.8, [(0, 4), (0, -2)], ellipse(1.9, 2, 1.9, 2.0, 180))
_g("q", 3.8, [(3.8, 4), (3.8, -2)], ellipse(1.9, 2, 1.9, 2.0, 0))
_g("r", 3.0, [(0, 0), (0, 4)], _join([(0, 2.6)], arc(1.8, 2.6, 1.8, 1.4, 180, 55)))
_g("s", 3.4, _join(arc(1.7, 3.1, 1.5, 0.9, 40, 270), arc(1.7, 0.9, 1.6, 0.9, 90, -140)))
_g("t", 3.0, _join([(1.2, 5.6), (1.2, 1.0)], arc(2.2, 1.0, 1.0, 1.0, 180, 290)), [(0, 4), (2.8, 4)])
_g("u", 3.8, _join([(0, 4), (0, 1.8)], arc(1.9, 1.8, 1.9, 1.8, 180, 360)), [(3.8, 4), (3.8, 0)])
_g("v", 4.0, [(0, 4), (2, 0), (4, 4)])
_g("w", 5.6, [(0, 4), (1.4, 0), (2.8, 3.2), (4.2, 0), (5.6, 4)])
_g("x", 3.8, [(0, 4), (3.8, 0)], [(3.8, 4), (0, 0)])
_g("y", 3.8, [(0, 4), (1.9, 0.2)], _join([(3.8, 4), (1.4, -1.3), (0.5, -2)]))
_g("z", 3.6, [(0, 4), (3.6, 4), (0, 0), (3.6, 0)])

# --------------------------------------------------------------------- digits
_g("0", 4.0, ellipse(2, 3, 2.0, 3.0))
_g("1", 2.8, [(0.2, 4.7), (1.6, 6), (1.6, 0)])
_g("2", 4.0, _join(arc(2, 4.2, 2.0, 1.8, 165, -25), [(0, 0), (4, 0)]))
_g("3", 4.0, _join(arc(2, 4.5, 1.8, 1.5, 150, -90), arc(2, 1.5, 2.0, 1.5, 90, -150)))
_g("4", 4.2, [(3.1, 0), (3.1, 6), (0, 1.8), (4.2, 1.8)])
_g("5", 4.0, _join([(3.8, 6), (0.4, 6), (0.1, 3.3)], arc(2, 1.9, 2.0, 1.9, 120, -155)))
_g("6", 4.0, _join([(3.4, 6), (1.4, 5.2), (0.4, 3.6), (0, 1.9)], ellipse(2, 1.9, 2.0, 1.9, 180)))
_g("7", 4.0, [(0, 6), (4, 6), (1.4, 0)])
_g("8", 4.0, ellipse(2, 4.5, 1.6, 1.5, 270), ellipse(2, 1.5, 1.9, 1.5, 90))
_g("9", 4.0, ellipse(2, 4.1, 2.0, 1.9, 0), _join([(4, 4.1), (3.6, 2), (2.6, 0.8), (0.6, 0)]))

# ---------------------------------------------------------------- punctuation
_g(".", 1.0, _dot(0.5, 0))
_g(",", 1.2, [(0.7, 0.4), (0.2, -1.1)])
_g(":", 1.0, _dot(0.5, 0), _dot(0.5, 3.2))
_g(";", 1.2, [(0.7, 0.4), (0.2, -1.1)], _dot(0.7, 3.2))
_g("!", 1.0, [(0.5, 6), (0.5, 1.9)], _dot(0.5, 0))
_g("?", 3.6, _join(arc(1.8, 4.3, 1.8, 1.7, 170, -50), [(1.8, 2.3), (1.8, 1.7)]), _dot(1.8, 0))
_g("-", 3.0, [(0.2, 3), (2.8, 3)])
_g("_", 4.0, [(0, -1), (4, -1)])
_g("+", 3.6, [(0.3, 3), (3.3, 3)], [(1.8, 1.5), (1.8, 4.5)])
_g("=", 3.6, [(0.3, 2.2), (3.3, 2.2)], [(0.3, 3.8), (3.3, 3.8)])
_g("/", 3.0, [(0, -0.5), (3, 6.5)])
_g("\\", 3.0, [(0, 6.5), (3, -0.5)])
_g("|", 1.0, [(0.5, -1.5), (0.5, 7)])
_g("'", 1.0, [(0.5, 6), (0.5, 4.4)])
_g('"', 2.2, [(0.4, 6), (0.4, 4.4)], [(1.8, 6), (1.8, 4.4)])
_g("`", 1.6, [(0.3, 6), (1.2, 4.9)])
_g("(", 2.0, arc(2.2, 2.5, 2.0, 4.0, 120, 240))
_g(")", 2.0, arc(-0.2, 2.5, 2.0, 4.0, 60, -60))
_g("[", 2.0, [(1.8, 6.5), (0.3, 6.5), (0.3, -1.5), (1.8, -1.5)])
_g("]", 2.0, [(0.2, 6.5), (1.7, 6.5), (1.7, -1.5), (0.2, -1.5)])
_g("{", 2.4, _join([(2.2, 6.5), (1.4, 6.3), (1.2, 5.4), (1.2, 3.5), (0.3, 2.5), (1.2, 1.5), (1.2, -0.4), (1.4, -1.3), (2.2, -1.5)]))
_g("}", 2.4, _join([(0.2, 6.5), (1.0, 6.3), (1.2, 5.4), (1.2, 3.5), (2.1, 2.5), (1.2, 1.5), (1.2, -0.4), (1.0, -1.3), (0.2, -1.5)]))
_g("<", 3.0, [(3.0, 5), (0, 2.5), (3.0, 0)])
_g(">", 3.0, [(0, 5), (3.0, 2.5), (0, 0)])
_g("*", 3.2, [(1.6, 5.2), (1.6, 1.8)], [(0.1, 4.6), (3.1, 2.4)], [(3.1, 4.6), (0.1, 2.4)])
_g("#", 4.4, [(1.3, 0), (1.9, 6)], [(2.9, 0), (3.5, 6)], [(0.3, 2), (4.3, 2)], [(0.3, 4), (4.3, 4)])
_g("^", 3.2, [(0, 3.6), (1.6, 6), (3.2, 3.6)])
_g("~", 4.0, _join(arc(1.0, 3.2, 1.0, 0.8, 180, 0), arc(3.0, 3.2, 1.0, 0.8, 180, 360)))
_g("&", 4.6, [(4.4, 0), (1.0, 4.2), (0.9, 5.4), (1.6, 6), (2.3, 5.4), (2.0, 4.4), (0.4, 2.3),
              (0.3, 0.9), (1.2, 0), (2.4, 0.2), (3.4, 1.2), (4.0, 2.6)])
_g("@", 6.4, arc(3.2, 2.4, 3.2, 3.6, 10, 350), ellipse(3.0, 2.3, 1.2, 1.5, 0))
_g("%", 5.0, [(0.2, 0), (4.6, 6)], ellipse(1.0, 4.9, 0.9, 1.1), ellipse(4.0, 1.1, 0.9, 1.1))
_g("$", 4.0, _join(arc(2.0, 4.5, 1.8, 1.5, 40, 270), arc(2.0, 1.5, 1.9, 1.5, 90, -140)), [(2.0, -0.5), (2.0, 6.5)])
_g("€", 4.4, _join(arc(2.5, 3, 2.0, 3.0, 40, 320)), [(0, 3.7), (3.4, 3.7)], [(0, 2.3), (3.4, 2.3)])
_g("£", 4.0, _join(arc(2.4, 4.6, 1.6, 1.4, 20, 180), [(0.8, 3), (0.8, 1.2), (0, 0), (3.8, 0)]), [(0, 3), (2.6, 3)])
_g("°", 1.8, ellipse(0.9, 5.2, 0.8, 0.8))


def _strip(ch: str) -> str:
    """Drop accents so 'é' -> 'e'; unknown characters become '?'."""
    if ch in G:
        return ch
    base = "".join(c for c in unicodedata.normalize("NFD", ch) if not unicodedata.combining(c))
    if base in G:
        return base
    return {"ß": "s", "æ": "a", "Æ": "A", "ø": "o", "Ø": "O", "œ": "o", "Œ": "O",
            "–": "-", "—": "-", "‘": "'", "’": "'", "“": '"', "”": '"'}.get(ch, "?")


def layout(text: str, vertical: bool = False) -> list[tuple[int, Stroke]]:
    """Lay out text. Returns ``[(char_index, polyline)]`` in laser canvas
    coordinates (x right, y DOWN, origin top-left of the text block).

    ``char_index`` counts visible characters so callers can colour per letter.
    With ``vertical`` the characters are stacked top to bottom, each centred.
    """
    if vertical:
        return _layout_vertical(text)
    lines = text.split("\n")
    out: list[tuple[int, Stroke]] = []
    visible = 0
    for row, line in enumerate(lines):
        x = 0.0
        base = row * LINE_HEIGHT
        for raw in line:
            if raw == " ":
                x += SPACE
                continue
            ch = _strip(raw)
            width, strokes = G[ch]
            for stroke in strokes:
                out.append((visible, [(round(x + px, 3), round(base - py, 3)) for px, py in stroke]))
            x += width + GAP
            visible += 1
    if not out:
        return []
    minx = min(px for _, s in out for px, _ in s)
    miny = min(py for _, s in out for _, py in s)
    return [(i, [(round(px - minx, 3), round(py - miny, 3)) for px, py in s]) for i, s in out]


def _layout_vertical(text: str) -> list[tuple[int, Stroke]]:
    chars = [c for c in text.replace("\n", " ") if c != " "]
    if not chars:
        return []
    widest = max(G[_strip(c)][0] for c in chars)
    out: list[tuple[int, Stroke]] = []
    for row, raw in enumerate(chars):
        width, strokes = G[_strip(raw)]
        dx = (widest - width) / 2
        base = row * (LINE_HEIGHT - 1.5)
        for stroke in strokes:
            out.append((row, [(round(dx + px, 3), round(base - py, 3)) for px, py in stroke]))
    minx = min(px for _, s in out for px, _ in s)
    miny = min(py for _, s in out for _, py in s)
    return [(i, [(round(px - minx, 3), round(py - miny, 3)) for px, py in s]) for i, s in out]
