"""Pattern thumbnails: a preview of the current pattern and an overview sheet."""
from __future__ import annotations

from homeassistant.components.image import ImageEntity
from homeassistant.util import dt as dt_util

from . import thumbs
from .entity import CubeEntity

PAGE = 20  # patterns per overview sheet (5 x 4)


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    link = entry.runtime_data
    async_add_entities([CubePatternPreview(hass, link, entry), CubeLibraryOverview(hass, link, entry)])


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
        start = (self.link.pattern_index - 1) // PAGE * PAGE + 1
        return lib, start, min(start + PAGE - 1, lib.size)

    def _signature(self):
        lib, a, b = self._page()
        if lib is None:
            return None
        have = tuple(len(self.link.get_thumb(lib, n) or ()) if self.link.get_thumb(lib, n) is not None else -1
                     for n in range(a, b + 1))
        return (self.link.selected_library, a, self.link.pattern_index, have)

    def _render(self) -> bytes:
        lib, a, b = self._page()
        if lib is None:
            return thumbs.render_pattern(None, 320, "No library loaded")
        items = [(n, self.link.get_thumb(lib, n)) for n in range(a, b + 1)]
        missing = sum(1 for _, f in items if f is None)
        title = f"{lib.name}  {a}-{b} of {lib.size}" + ("   (press 'Build thumbnails')" if missing == len(items) else "")
        return thumbs.render_sheet(items, self.link.pattern_index, title=title)
