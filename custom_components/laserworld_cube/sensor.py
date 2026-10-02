"""Thumbnail build status."""
from __future__ import annotations

from homeassistant.components.sensor import SensorEntity
from homeassistant.const import EntityCategory

from .entity import CubeEntity


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    async_add_entities([CubeThumbStatus(entry.runtime_data, entry)])


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
