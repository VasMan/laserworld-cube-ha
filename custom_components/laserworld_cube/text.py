"""Text input: shows the message on the laser."""
from __future__ import annotations

import voluptuous as vol
from homeassistant.components.text import TextEntity, TextMode
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_platform

from . import media, protocol

from .entity import CubeEntity


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    async_add_entities([CubeTextMessage(entry.runtime_data, entry)])
    platform = entity_platform.async_get_current_platform()
    platform.async_register_entity_service(
        "send_effect",
        {
            vol.Required("channels"): [vol.All(vol.Coerce(int), vol.Range(min=0, max=255))],
            vol.Optional("step", default=0): vol.All(vol.Coerce(int), vol.Range(min=0, max=255)),
            vol.Optional("steps", default=1): vol.All(vol.Coerce(int), vol.Range(min=1, max=255)),
            vol.Optional("duration", default=5.0): vol.All(vol.Coerce(float), vol.Range(min=0.05, max=3000)),
        },
        "async_send_effect")
    platform.async_register_entity_service(
        "show_image",
        {
            vol.Optional("media"): vol.Any(str, dict),
            vol.Optional("path"): str,
            vol.Optional("mode"): vol.In([m.lower() for m in protocol.PICTURE_MODES.values()]),
            vol.Optional("detail"): vol.All(vol.Coerce(int), vol.Range(min=1, max=100)),
            vol.Optional("color"): vol.In(["original"] + [c.lower() for k, c in protocol.PICTURE_COLORS.items() if k]),
            vol.Optional("size"): vol.All(vol.Coerce(int), vol.Range(min=10, max=100)),
            vol.Optional("invert"): bool,
        },
        "async_show_image")


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

    async def async_send_effect(self, channels: list[int], step: int = 0, steps: int = 1,
                                duration: float = 5.0) -> None:
        """Experimental: send raw effect channel values with the text (see README)."""
        await self.call(self.link.async_send_effect(channels, step, steps, duration))

    async def async_show_image(self, media=None, path=None, mode=None, detail=None, color=None,
                               size=None, invert=None) -> None:
        """Convert a picture (HA media item, path or URL) to laser lines and show it."""
        source = media.get("media_content_id") if isinstance(media, dict) else media
        source = source or path
        if not source:
            raise HomeAssistantError("Choose a media item or give a path")
        data, name = await _read(self.hass, source)
        modes = {v.lower(): k for k, v in protocol.PICTURE_MODES.items()}
        colors = {"original": 0, **{v.lower(): k for k, v in protocol.PICTURE_COLORS.items() if k}}
        await self.call(self.link.async_show_picture(
            data, name, mode=modes[mode] if mode else None, detail=detail,
            color=colors[color] if color else None, size=size, invert=invert))


async def _read(hass, source):
    return await media.async_read_image(hass, source)
