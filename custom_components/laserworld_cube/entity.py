"""Shared entity base."""
from __future__ import annotations

from collections.abc import Awaitable

from homeassistant.const import CONF_ADDRESS
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity

from .client import CubeError, CubeLink
from .const import CONF_BLE_NAME, DOMAIN


class CubeEntity(Entity):
    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, link: CubeLink, entry, key: str) -> None:
        self.link = link
        self._attr_unique_id = f"{entry.data[CONF_ADDRESS]}_{key}"
        address = entry.data[CONF_ADDRESS]
        parts = entry.data[CONF_BLE_NAME].split("_")
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, address)},
            connections={(dr.CONNECTION_BLUETOOTH, address)},
            name=f"Laserworld Cube {parts[1] if len(parts) > 1 else parts[0]}",
            manufacturer="Laserworld",
            model="Cube Laser",
        )

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(self.link.add_listener(self.async_write_ha_state))

    @staticmethod
    async def call(coro: Awaitable) -> None:
        try:
            await coro
        except CubeError as err:
            raise HomeAssistantError(str(err)) from err
