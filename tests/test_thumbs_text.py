"""Unit tests for the thumbnail renderer and the stroke font."""
import importlib.util
import pathlib
import sys
import types

ROOT = pathlib.Path(__file__).parent.parent / "custom_components" / "laserworld_cube"


def _load(name):
    spec = importlib.util.spec_from_file_location(f"lt_{name}", ROOT / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[f"lt_{name}"] = mod
    spec.loader.exec_module(mod)
    return mod


th = _load("thumbs")
sf = _load("stroke_font")


def test_strokes_follow_start_and_black_rules():
    flat = th.pack([(10, 10, 64, 0xFF0000), (50, 10, 0, 0xFF0000),      # red segment
                    (90, 10, 0, 0x000000),                              # black = blank (not drawn)
                    (90, 50, 0, 0x00FF00),                              # green segment
                    (200, 200, 64, 0xFFFFFF), (200, 220, 0, 0xFFFFFF)]) # new stroke, no link to previous
    segs = th.strokes(flat)
    assert [c for c, _ in segs] == [(255, 0, 0), (0, 255, 0), (255, 255, 255)]
    assert segs[0][1] == [(10, 10), (50, 10)] and segs[2][1] == [(200, 200), (200, 220)]


def test_pack_unpack_roundtrip_and_render():
    pts = [(2, 4, 64, 0x123456), (254, 254, 128, 0xFFFFFF)]
    assert th.unpack(th.pack(pts)) == pts
    for png in (th.render_pattern(None), th.render_pattern(th.pack(pts), 200, "x"),
                th.render_sheet([(1, th.pack(pts)), (2, None), (3, None)], current=1, cols=2, cell=90, title="t")):
        assert png.startswith(b"\x89PNG")


def test_font_covers_ascii_and_layout_is_y_down():
    for code in range(33, 127):
        assert chr(code) in sf.G, chr(code)
    out = sf.layout("Ab")
    ys = [y for _, s in out for _, y in s]
    xs = [x for _, s in out for x, _ in s]
    assert min(xs) == 0 and min(ys) == 0          # origin at the top-left of the text block
    a_strokes = [s for i, s in out if i == 0]
    top = min(y for s in a_strokes for _, y in s); bottom = max(y for s in a_strokes for _, y in s)
    assert bottom - top == 6                       # cap height, apex above the baseline (y down)
    # the apex of "A" is at the top
    assert min(a_strokes[0], key=lambda pt: pt[1])[1] == top


def test_font_accents_unknown_and_multiline():
    assert sf.layout("é") == sf.layout("e")
    assert sf.layout("☃") == sf.layout("?")
    two = sf.layout("A\nA")
    assert max(y for _, s in two for _, y in s) > 6 + sf.LINE_HEIGHT - 1
    assert sf.layout("   ") == [] and sf.layout("") == []
    assert len({i for i, _ in sf.layout("a b")}) == 2      # spaces do not count as characters


def test_all_strokes_have_two_points():
    for ch, (_, strokes) in sf.G.items():
        for s in strokes:
            assert len(s) >= 2, ch


def test_sheet_size_is_constant_and_matches_geometry():
    import io
    from PIL import Image
    pts = th.pack([(2, 4, 64, 0xFFFFFF), (200, 200, 0, 0xFFFFFF)])
    want = th.sheet_size()
    for items, pages in (([(1, pts)], 1), ([(n, pts) for n in range(1, 21)], 7), ([(n, None) for n in range(101, 109)], 7)):
        png = th.render_sheet(items, current=1, title="x", page=3, pages=pages)
        assert Image.open(io.BytesIO(png)).size == want        # same size whatever the page holds


def test_dashboard_tap_zones_line_up_with_the_image():
    import importlib.util as iu
    spec = iu.spec_from_file_location("mk", ROOT.parent.parent / "tools" / "make_dashboard.py")
    mk = iu.module_from_spec(spec); sys.modules["mk"] = mk; spec.loader.exec_module(mk)
    w, h = th.sheet_size()
    zs = mk.zones("x")
    tiles = [z for z in zs if z["kind"] == "tile"]
    assert len(tiles) == 20 and {z["kind"] for z in zs} == {"tile", "previous", "next"}
    for z in tiles:
        i = z["tile"] - 1
        x, y = th.tile_origin(i)
        cx, cy = z["left"] / 100 * w, z["top"] / 100 * h
        assert x < cx < x + th.CELL and y < cy < y + th.CELL           # centre of the tile
        assert abs(cx - (x + th.CELL / 2)) < 0.6 and abs(cy - (y + th.CELL / 2)) < 0.6
        assert abs(z["width"] / 100 * w - th.CELL) < 0.6               # same size as the tile
    nav = th.nav_centers(w)
    prev = next(z for z in zs if z["kind"] == "previous"); nxt = next(z for z in zs if z["kind"] == "next")
    assert abs(prev["left"] / 100 * w - nav["previous"][0]) < 0.6 and abs(nxt["left"] / 100 * w - nav["next"][0]) < 0.6
    assert prev["top"] / 100 * h < th.HEAD                              # in the header strip
    # the shipped file is exactly what the generator produces
    assert (ROOT.parent.parent / "dashboard" / "library_browser.yaml").read_text() == mk.build("laserworld_cube_847e")
