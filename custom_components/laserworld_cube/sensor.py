"""Thumbnail build status."""
from __future__ import annotations

from homeassistant.components.sensor import SensorEntity
from homeassistant.const import EntityCategory

from .entity import CubeEntity


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    async_add_entities([CubeThumbStatus(entry.runtime_data, entry), CubeDmxChannels(entry.runtime_data, entry),
                        CubePlaylistSensor(entry.runtime_data, entry)])


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


class CubePlaylistSensor(CubeEntity, SensorEntity):
    """Summary of the active playlist; the items are in the attributes."""
    _attr_translation_key = "playlist_summary"
    _attr_icon = "mdi:format-list-numbered"

    def __init__(self, link, entry) -> None:
        super().__init__(link, entry, "playlist_summary")

    @property
    def native_value(self) -> str:
        items = self.link.current_playlist()
        if items is None:
            return "No playlist"
        total = self.link.playlist_total_seconds()
        return f"{len(items)} items, {total:g} s"

    @property
    def extra_state_attributes(self) -> dict:
        items = self.link.current_playlist() or []
        return {
            "playlist": self.link.playlist_name,
            "playing": self.link.playlist_on,
            "current_item": self.link.playlist_index + 1 if self.link.playlist_on else None,
            "items": [{"position": i + 1, "library": it["name"], "pattern": it["n"], "seconds": it["seconds"]}
                      for i, it in enumerate(items)],
        }
