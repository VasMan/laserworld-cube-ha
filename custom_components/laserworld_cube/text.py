"""Text input: shows the message on the laser."""
from __future__ import annotations

from homeassistant.components.text import TextEntity, TextMode

from .entity import CubeEntity


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    async_add_entities([CubeTextMessage(entry.runtime_data, entry)])


class CubeTextMessage(CubeEntity, TextEntity):
    """Setting the value plays the text on the laser (like the app's Text page)."""
    _attr_mode = TextMode.TEXT
    _attr_native_min = 0
    _attr_native_max = 60
    _attr_translation_key = "message"
    _attr_icon = "mdi:format-text"

    def __init__(self, link, entry) -> None:
        super().__init__(link, entry, "message")

    @property
    def native_value(self) -> str:
        return self.link.text

    async def async_set_value(self, value: str) -> None:
        await self.call(self.link.async_play_text(value))
