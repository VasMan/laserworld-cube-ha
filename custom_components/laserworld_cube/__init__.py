"""Laserworld Cube Laser (Bluetooth) integration."""
from __future__ import annotations

import logging

from bleak_retry_connector import BleakClientWithServiceCache, establish_connection
from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ADDRESS, Platform
from homeassistant.core import HomeAssistant

from .client import CubeError, CubeLink
from .const import (CONF_BLE_NAME, CONF_IDLE_TIMEOUT, CONF_USER_ID,
                    DEFAULT_IDLE_TIMEOUT, DEFAULT_USER_ID)

_LOGGER = logging.getLogger(__name__)
PLATFORMS = [Platform.SWITCH, Platform.SELECT, Platform.NUMBER, Platform.BUTTON]

CubeConfigEntry = ConfigEntry[CubeLink]


async def async_setup_entry(hass: HomeAssistant, entry: CubeConfigEntry) -> bool:
    address: str = entry.data[CONF_ADDRESS]
    ble_name: str = entry.data[CONF_BLE_NAME]

    def _device():
        return bluetooth.async_ble_device_from_address(hass, address, connectable=True)

    async def _connect(disconnected_cb):
        device = _device()
        if device is None:
            raise CubeError(f"{ble_name} is not in Bluetooth range of any adapter/proxy")
        return await establish_connection(
            BleakClientWithServiceCache, device, ble_name,
            disconnected_callback=disconnected_cb, ble_device_callback=_device)

    link = CubeLink(
        ble_name, _connect,
        user_id=int(entry.options.get(CONF_USER_ID, DEFAULT_USER_ID)),
        idle_timeout=float(entry.options.get(CONF_IDLE_TIMEOUT, DEFAULT_IDLE_TIMEOUT)))
    entry.runtime_data = link

    async def _initial_read() -> None:
        try:
            await link.async_connect()
        except CubeError as err:
            _LOGGER.warning("Initial read from %s failed: %s", ble_name, err)

    entry.async_create_background_task(hass, _initial_read(), f"{ble_name} initial read")
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_reload))
    return True


async def _reload(hass: HomeAssistant, entry: CubeConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: CubeConfigEntry) -> bool:
    ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if ok:
        await entry.runtime_data.disconnect()
    return ok
