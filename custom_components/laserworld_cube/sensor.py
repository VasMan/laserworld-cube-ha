"""Thumbnail build status."""
from __future__ import annotations

from homeassistant.components.sensor import SensorEntity
from homeassistant.const import EntityCategory

from .entity import CubeEntity


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    async_add_entities([CubeThumbStatus(entry.runtime_data, entry), CubeDmxChannels(entry.runtime_data, entry)])


class CubeThumbStatus(CubeEntity, SensorEntity):
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_translation_key = "thumbnail_status"
    _attr_icon = "mdi:image-multiple"

    def __init__(self, link, entry) -> None:
        super().__init__(link, entry, "thumbnail_status")

    @property
    def native_value(self) -> str:
        return self.link.thumb_status or "Idle"

    @property
    def extra_state_attributes(self) -> dict:
        return {"cached_patterns": len(self.link.thumbs)}


class CubeDmxChannels(CubeEntity, SensorEntity):
    """The laser's own channel counts - useful to decide the hardware effect layout."""
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_translation_key = "dmx_channels"
    _attr_icon = "mdi:tune-variant"

    def __init__(self, link, entry) -> None:
        super().__init__(link, entry, "dmx_channels")

    @property
    def native_value(self) -> str | None:
        m = self.link.device_model
        if "deviceStdChannleTotal" not in m:
            return None
        return f"{m['deviceStdChannleTotal']} / {m.get('deviceProChannleTotal')} / {m.get('deviceSCEChannleTotal')}"

    @property
    def extra_state_attributes(self) -> dict:
        m = self.link.device_model
        try:
            base = self.link.effect_layout_base()
        except Exception as err:  # noqa: BLE001
            base = str(err)
        return {"standard_channels": m.get("deviceStdChannleTotal"),
                "professional_channels": m.get("deviceProChannleTotal"),
                "scene_channels": m.get("deviceSCEChannleTotal"),
                "effect_array_starts_at_channel": base}
