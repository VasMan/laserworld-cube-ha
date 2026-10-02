"""Utility buttons."""
from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.const import EntityCategory

from .entity import CubeEntity


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    link = entry.runtime_data
    async_add_entities([CubeRefreshButton(link, entry), CubeDisconnectButton(link, entry)])


class CubeRefreshButton(CubeEntity, ButtonEntity):
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_translation_key = "read_settings"
    _attr_icon = "mdi:refresh"

    def __init__(self, link, entry) -> None:
        super().__init__(link, entry, "read_settings")

    async def async_press(self) -> None:
        await self.call(self.link.async_connect(refresh=True))


class CubeDisconnectButton(CubeEntity, ButtonEntity):
    """Free the Bluetooth link right away so the phone app can connect."""
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_translation_key = "disconnect"
    _attr_icon = "mdi:bluetooth-off"

    def __init__(self, link, entry) -> None:
        super().__init__(link, entry, "disconnect")

    async def async_press(self) -> None:
        await self.link.disconnect()
