"""Tests for the local picture-to-laser converter."""
import importlib.util
import io
import math
import pathlib
import random
import sys
import time

from PIL import Image, ImageDraw

ROOT = pathlib.Path(__file__).parent.parent / "custom_components" / "laserworld_cube"
_spec = importlib.util.spec_from_file_location("lt_vectorize", ROOT / "vectorize.py")
vz = importlib.util.module_from_spec(_spec)
sys.modules["lt_vectorize"] = vz
_spec.loader.exec_module(vz)


def png(im):
    b = io.BytesIO()
    im.save(b, "PNG")
    return b.getvalue()


def shapes_image():
    im = Image.new("RGB", (400, 300), "white")
    d = ImageDraw.Draw(im)
    d.ellipse([30, 40, 150, 160], fill="black")
    d.rectangle([200, 50, 340, 160], fill="black")
    d.polygon([(60, 280), (130, 180), (200, 280)], fill="black")
    return png(im)


def bbox(path):
    xs = [x for x, _ in path]; ys = [y for _, y in path]
    return min(xs), min(ys), max(xs), max(ys)


def test_silhouette_finds_each_shape_as_a_closed_outline():
    r = vz.vectorize(shapes_image(), mode="silhouette", color=1)
    assert len(r.paths) == 3
    assert all(p[0] == p[-1] for p in r.paths)                         # closed loops
    assert all(c == (255, 255, 255) for c in r.colors)
    # the circle (left) is round: its bounding box is roughly square and its points sit on one radius
    circle = min(r.paths, key=lambda p: bbox(p)[0])
    x0, y0, x1, y1 = bbox(circle)
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    radii = [math.hypot(x - cx, y - cy) for x, y in circle]
    assert abs((x1 - x0) - (y1 - y0)) < 4 and max(radii) - min(radii) < 4
    assert r.width <= vz.MAX_DIM and r.height <= vz.MAX_DIM and r.points < 200


def test_lines_mode_gives_one_centre_line_where_outline_gives_two_edges():
    im = Image.new("RGB", (300, 120), "white")
    ImageDraw.Draw(im).rectangle([20, 52, 280, 68], fill="black")          # a thick horizontal bar
    data = png(im)
    lines = vz.vectorize(data, mode="lines")
    outline = vz.vectorize(data, mode="outline")
    assert len(lines.paths) == 1
    ys = [y for _, y in lines.paths[0]]
    assert max(ys) - min(ys) < 3                                            # a single centre line ...
    scale = lines.height / 120
    assert abs(sum(ys) / len(ys) - 60 * scale) < 3                          # ... through the middle of the bar
    oy = [y for p in outline.paths for _, y in p]
    assert max(oy) - min(oy) > 3 * (max(ys) - min(ys)) and max(oy) - min(oy) > 5     # the two edges of the bar


def test_transparent_logo_works_in_every_mode_and_invert_matters():
    im = Image.new("RGBA", (300, 300), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.ellipse([40, 40, 260, 260], fill=(255, 255, 255, 255))               # white on transparent
    d.ellipse([100, 100, 200, 200], fill=(0, 0, 0, 0))
    data = png(im)
    for mode in ("outline", "silhouette", "lines"):
        assert vz.vectorize(data, mode=mode).points > 5, mode
    assert "transparency" in " ".join(vz.vectorize(data, mode="silhouette").notes)
    # white disc on a black photo background: inverting selects the disc instead of the frame
    im2 = Image.new("RGB", (300, 300), "black")
    ImageDraw.Draw(im2).ellipse([90, 90, 210, 210], fill="white")
    plain = vz.vectorize(png(im2), mode="silhouette")
    inverted = vz.vectorize(png(im2), mode="silhouette", invert=True)
    W = plain.width
    assert max(bbox(p)[2] - bbox(p)[0] for p in plain.paths) > 0.9 * W            # the whole frame
    assert max(bbox(p)[2] - bbox(p)[0] for p in inverted.paths) < 0.6 * W          # just the disc


def test_colors_single_rainbow_and_from_the_picture():
    im = Image.new("RGB", (400, 120), "white")
    d = ImageDraw.Draw(im)
    d.rectangle([10, 30, 90, 90], fill=(230, 20, 20))
    d.rectangle([160, 30, 240, 90], fill=(20, 200, 40))
    d.rectangle([310, 30, 390, 90], fill=(60, 60, 60))
    data = png(im)
    orig = vz.vectorize(data, mode="silhouette", color=0)
    by_x = sorted(zip([bbox(p)[0] for p in orig.paths], orig.colors))
    assert [c for _, c in by_x] == [(255, 0, 0), (0, 255, 0), (255, 255, 255)]      # red, green, grey->white
    rainbow = vz.vectorize(data, mode="silhouette", color=8)
    assert len({c for c in rainbow.colors}) == 3
    blue = vz.vectorize(data, mode="silhouette", color=6)
    assert set(blue.colors) == {(0, 255, 255)} or set(blue.colors) == {(0, 0, 255)}
    assert set(vz.vectorize(data, mode="silhouette", color=2).colors) == {(255, 0, 0)}


def test_point_budget_ordering_and_errors():
    rnd = random.Random(7)
    im = Image.new("RGB", (400, 400), "white")
    d = ImageDraw.Draw(im)
    for _ in range(120):
        x, y = rnd.randint(0, 380), rnd.randint(0, 380)
        d.line([x, y, x + rnd.randint(-60, 60), y + rnd.randint(-60, 60)], fill="black", width=2)
    data = png(im)
    big = vz.vectorize(data, mode="outline", detail=100, max_points=2000)
    small = vz.vectorize(data, mode="outline", detail=100, max_points=120)
    assert big.points > 300 and small.points <= 120 and small.notes or small.points <= 120
    # greedy ordering keeps blanked jumps short: four squares in a row are drawn left to right
    row = Image.new("RGB", (400, 60), "white")
    dr = ImageDraw.Draw(row)
    for x in (20, 120, 220, 320):
        dr.rectangle([x, 15, x + 50, 45], outline="black", width=3)
    xs = [bbox(p)[0] for p in vz.vectorize(png(row), mode="silhouette").paths]
    firsts = [xs[i] for i in range(0, len(xs)) if i == 0 or abs(xs[i] - xs[i - 1]) > 5]
    assert firsts == sorted(firsts)
    for bad, kw in ((png(Image.new("RGB", (200, 200), "white")), {}), (b"not an image", {}),
                    (png(Image.new("RGB", (4, 4), "white")), {})):
        try:
            vz.vectorize(bad, **kw); raise AssertionError("expected ValueError")
        except ValueError:
            pass
    try:
        vz.vectorize(shapes_image(), mode="spiral"); raise AssertionError("expected ValueError")
    except ValueError:
        pass
    old = vz.MAX_PIXELS
    vz.MAX_PIXELS = 100
    try:
        vz.vectorize(shapes_image()); raise AssertionError("expected ValueError")
    except ValueError as err:
        assert "too large" in str(err)
    finally:
        vz.MAX_PIXELS = old


def test_photo_like_picture_is_fast():
    im = Image.new("RGB", (800, 600), (40, 60, 110))
    d = ImageDraw.Draw(im)
    for i in range(40):
        d.ellipse([i * 18, 100 + (i % 7) * 20, i * 18 + 90, 300 + (i % 5) * 50], outline=(255, 200, 80), width=3)
    d.ellipse([300, 150, 520, 450], fill=(235, 190, 160))
    t = time.time()
    r = vz.vectorize(png(im), mode="outline", detail=70, max_points=900)
    assert time.time() - t < 8 and 20 < r.points <= 900
