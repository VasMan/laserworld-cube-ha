"""Pattern thumbnails: a preview of the current pattern and an overview sheet."""
from __future__ import annotations

from homeassistant.components.image import ImageEntity
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_platform
from homeassistant.util import dt as dt_util
import voluptuous as vol

from . import protocol, thumbs
from .entity import CubeEntity

PAGE = protocol.OVERVIEW_PAGE  # patterns per overview sheet (5 x 4)


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    link = entry.runtime_data
    async_add_entities([CubePatternPreview(hass, link, entry), CubeLibraryOverview(hass, link, entry),
                        CubePlaylistOverview(hass, link, entry), CubeDisplayPreview(hass, link, entry)])
    # services used by dashboard cards (see dashboard/library_browser.yaml)
    platform = entity_platform.async_get_current_platform()
    platform.async_register_entity_service(
        "play_overview_tile",
        {vol.Required("tile"): vol.All(vol.Coerce(int), vol.Range(min=1, max=PAGE))},
        "async_play_tile")
    platform.async_register_entity_service(
        "playlist_add_tile",
        {vol.Required("tile"): vol.All(vol.Coerce(int), vol.Range(min=1, max=PAGE)),
         vol.Optional("duration"): vol.All(vol.Coerce(float), vol.Range(min=0.5, max=3600))},
        "async_playlist_add_tile")
    platform.async_register_entity_service("overview_next_page", {}, "async_page_next")
    platform.async_register_entity_service("overview_previous_page", {}, "async_page_previous")


class _CubeImage(CubeEntity, ImageEntity):
    _attr_content_type = "image/png"

    def __init__(self, hass, link, entry, key: str) -> None:
        ImageEntity.__init__(self, hass)
        CubeEntity.__init__(self, link, entry, key)
        self._attr_translation_key = key
        self._sig = None
        self._attr_image_last_updated = dt_util.utcnow()

    def _signature(self):
        raise NotImplementedError

    def _render(self) -> bytes:
        raise NotImplementedError

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(self.link.add_listener(self._changed))

    def _changed(self) -> None:
        sig = self._signature()
        if sig != self._sig:
            self._sig = sig
            self._attr_image_last_updated = dt_util.utcnow()
        self.async_write_ha_state()

    async def async_image(self) -> bytes | None:
        return await self.hass.async_add_executor_job(self._render)


class CubePatternPreview(_CubeImage):
    """The pattern that is currently selected / playing."""

    def __init__(self, hass, link, entry) -> None:
        super().__init__(hass, link, entry, "pattern_preview")

    def _signature(self):
        lib = self.link.current_library
        flat = self.link.get_thumb(lib, self.link.pattern_index) if lib else None
        return (self.link.selected_library, self.link.pattern_index, len(flat) if flat is not None else -1)

    def _render(self) -> bytes:
        lib = self.link.current_library
        if lib is None:
            return thumbs.render_pattern(None, 320, "No library loaded")
        n = self.link.pattern_index
        return thumbs.render_pattern(self.link.get_thumb(lib, n), 320, f"{lib.name} {n}")


class CubeLibraryOverview(_CubeImage):
    """A numbered grid of the patterns around the current one, like the app's library page."""

    def __init__(self, hass, link, entry) -> None:
        super().__init__(hass, link, entry, "library_overview")

    def _page(self):
        lib = self.link.current_library
        if lib is None:
            return None, 0, 0
        start = (self.link.current_overview_page() - 1) * PAGE + 1
        return lib, start, min(start + PAGE - 1, lib.size)

    def _signature(self):
        lib, a, b = self._page()
        if lib is None:
            return None
        have = tuple(len(self.link.get_thumb(lib, n) or ()) if self.link.get_thumb(lib, n) is not None else -1
                     for n in range(a, b + 1))
        return (self.link.selected_library, a, self.link.pattern_index, have)  # a = first pattern of the page

    def _render(self) -> bytes:
        lib, a, b = self._page()
        if lib is None:
            return thumbs.render_pattern(None, 320, "No library loaded")
        items = [(n, self.link.get_thumb(lib, n)) for n in range(a, b + 1)]
        missing = sum(1 for _, f in items if f is None)
        pages = self.link.overview_pages()
        title = f"{lib.name}  {a}-{b} of {lib.size}" + (
            "   - press 'Build thumbnails'" if missing == len(items) else "")
        return thumbs.render_sheet(items, self.link.pattern_index, title=title,
                                   page=self.link.current_overview_page(), pages=pages)

    async def async_play_tile(self, tile: int) -> None:
        """Play the pattern shown in tile ``tile`` (1..20) of the current page."""
        lib, a, b = self._page()
        n = a + tile - 1
        if lib is None or n > b:
            raise HomeAssistantError("There is no pattern in that slot")
        await self.call(self.link.async_play_index(n))

    async def async_playlist_add_tile(self, tile: int, duration: float | None = None) -> None:
        """Add the pattern in tile ``tile`` of the current page to the active playlist."""
        lib, a, b = self._page()
        n = a + tile - 1
        if lib is None or n > b:
            raise HomeAssistantError("There is no pattern in that slot")
        await self.call(self.link.async_playlist_add(lib.key, n, duration))

    async def async_page_next(self) -> None:
        await self.call(self.link.async_overview_step(1))

    async def async_page_previous(self) -> None:
        await self.call(self.link.async_overview_step(-1))


class CubePlaylistOverview(_CubeImage):
    """The active playlist: thumbnails with group, pattern number and on-time."""

    def __init__(self, hass, link, entry) -> None:
        super().__init__(hass, link, entry, "playlist_overview")

    def _entries(self):
        out = []
        for it in self.link.current_playlist() or []:
            lib = self.link.find_library(it["lib"])
            flat = self.link.get_thumb(lib, it["n"]) if lib and 1 <= it["n"] <= lib.size else None
            out.append({"label": f"{it['name']} {it['n']}", "seconds": it["seconds"], "flat": flat})
        return out

    def _signature(self):
        items = self.link.current_playlist() or []
        have = sum(1 for e in self._entries() if e["flat"] is not None)
        return (self.link.playlist_name, tuple((i["lib"], i["n"], i["seconds"]) for i in items),
                self.link.playlist_index if self.link.playlist_on else None, have)

    def _render(self) -> bytes:
        entries = self._entries()
        name = self.link.playlist_name
        title = "No playlist - press 'New playlist'" if name is None else (
            f"{name}  -  {len(entries)} items, {self.link.playlist_total_seconds():g} s"
            + ("  (playing)" if self.link.playlist_on else ""))
        return thumbs.render_playlist(entries, self.link.playlist_index if self.link.playlist_on else None,
                                      title=title)


class CubeDisplayPreview(_CubeImage):
    """What the laser is drawing right now when it shows text or a picture."""

    def __init__(self, hass, link, entry) -> None:
        super().__init__(hass, link, entry, "display_preview")

    def _signature(self):
        flat = self.link.content_preview
        return (self.link.content, len(flat) if flat else 0, self.link.picture_name, self.link.text)

    def _render(self) -> bytes:
        link = self.link
        if not link.content or not link.content_preview:
            return thumbs.render_pattern(None, 320, "Nothing showing")
        label = f"Picture: {link.picture_name}" if link.content == "picture" else f"Text: {link.text[:24]}"
        return thumbs.render_pattern(link.content_preview, 320, label)
