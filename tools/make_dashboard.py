"""Generate dashboard/library_browser.yaml for a given device.

Usage:  python3 tools/make_dashboard.py [device_slug]      (default: laserworld_cube_847e)

The tap zones are computed from the geometry the overview image is drawn with
(custom_components/laserworld_cube/thumbs.py), so they always line up with it.
"""
from __future__ import annotations

import importlib.util
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "cube_thumbs", ROOT / "custom_components" / "laserworld_cube" / "thumbs.py")
thumbs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(thumbs)

# 1x1 transparent PNG: used as an invisible tap target (no files needed)
TRANSPARENT = ("data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII=")
TILES = thumbs.COLS * thumbs.ROWS


def zones(slug: str) -> list[dict]:
    """Tap zones as percentages of the overview image: ``left/top`` are centres."""
    w, h = thumbs.sheet_size()
    out = []
    for i in range(TILES):
        x, y = thumbs.tile_origin(i)
        out.append({"kind": "tile", "tile": i + 1,
                    "left": (x + thumbs.CELL / 2) / w * 100, "top": (y + thumbs.CELL / 2) / h * 100,
                    "width": thumbs.CELL / w * 100})
    nav = thumbs.nav_centers(w)
    for name in ("previous", "next"):
        cx, cy = nav[name]
        out.append({"kind": name, "left": cx / w * 100, "top": cy / h * 100, "width": 40 / w * 100})
    return out


def build(slug: str = "laserworld_cube_847e") -> str:
    img = f"image.{slug}_library_overview"
    lines = [
        "# Library browser for the Laserworld Cube integration.",
        "# Tap a thumbnail to play it, long-press it to add it to the playlist;",
        "# tap the arrows on the image to change page.",
        "# Dashboard > Edit > Add card > Manual, then paste this. Adjust the entity ids if yours differ.",
        "type: vertical-stack",
        "cards:",
        "  - type: picture-elements",
        f"    image_entity: {img}",
        "    elements:",
    ]
    for z in zones(slug):
        if z["kind"] == "tile":
            action = ["          perform_action: laserworld_cube.play_overview_tile",
                      "          target:", f"            entity_id: {img}",
                      "          data:", f"            tile: {z['tile']}"]
            hold = ["        hold_action:", "          action: perform-action",
                    "          perform_action: laserworld_cube.playlist_add_tile",
                    "          target:", f"            entity_id: {img}",
                    "          data:", f"            tile: {z['tile']}"]
            label = f"tile {z['tile']}"
        else:
            svc = "overview_next_page" if z["kind"] == "next" else "overview_previous_page"
            action = [f"          perform_action: laserworld_cube.{svc}",
                      "          target:", f"            entity_id: {img}"]
            hold = []
            label = f"{z['kind']} page"
        lines += [
            f"      - type: image  # {label}",
            f'        image: "{TRANSPARENT}"',
            "        tap_action:", "          action: perform-action", *action,
            *hold,
            "        style:",
            f"          left: {z['left']:.2f}%", f"          top: {z['top']:.2f}%",
            f"          width: {z['width']:.2f}%",
        ]
    lines += [
        "  - type: entities",
        "    entities:",
        f"      - select.{slug}_pattern_library",
        f"      - number.{slug}_pattern_number",
        f"      - text.{slug}_text",
        f"      - select.{slug}_text_color",
        "  - type: entities",
        "    title: Playlist",
        "    entities:",
        f"      - select.{slug}_playlist",
        f"      - number.{slug}_playlist_item_duration",
        f"      - button.{slug}_add_to_playlist",
        f"      - switch.{slug}_play_playlist",
    ]
    return "\n".join(lines) + "\n"


def build_playlist(slug: str = "laserworld_cube_847e") -> str:
    """A card for managing and playing playlists."""
    lines = [
        "# Playlist card for the Laserworld Cube integration.",
        "# Dashboard > Edit > Add card > Manual, then paste this.",
        "type: vertical-stack",
        "cards:",
        "  - type: picture-entity",
        f"    entity: image.{slug}_playlist_overview",
        "    show_name: false",
        "    show_state: false",
        "  - type: entities",
        "    entities:",
        f"      - select.{slug}_playlist",
        f"      - switch.{slug}_play_playlist",
        f"      - switch.{slug}_repeat_playlist",
        f"      - number.{slug}_playlist_item_duration",
        f"      - button.{slug}_add_to_playlist",
        f"      - button.{slug}_remove_last_playlist_item",
        f"      - button.{slug}_clear_playlist",
        f"      - button.{slug}_new_playlist",
        f"      - button.{slug}_delete_playlist",
        f"      - sensor.{slug}_playlist_summary",
    ]
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    slug = sys.argv[1] if len(sys.argv) > 1 else "laserworld_cube_847e"
    out = ROOT / "dashboard" / "library_browser.yaml"
    out.write_text(build(slug))
    out2 = ROOT / "dashboard" / "playlist.yaml"
    out2.write_text(build_playlist(slug))
    print(f"wrote {out} ({TILES} tiles + 2 page controls) and {out2}")
