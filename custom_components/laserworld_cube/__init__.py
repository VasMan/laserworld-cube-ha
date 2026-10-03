"""Laserworld Cube Laser (Bluetooth) integration."""
from __future__ import annotations

import logging

from bleak_retry_connector import BleakClientWithServiceCache, establish_connection
from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ADDRESS, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.storage import Store

from . import media
from .client import CubeError, CubeLink
from .const import (DOMAIN, CONF_BLE_NAME, CONF_IDLE_TIMEOUT, CONF_USER_ID,
                    DEFAULT_IDLE_TIMEOUT, DEFAULT_USER_ID)

_LOGGER = logging.getLogger(__name__)
PLATFORMS = [Platform.SWITCH, Platform.SELECT, Platform.NUMBER, Platform.BUTTON,
             Platform.TEXT, Platform.IMAGE, Platform.SENSOR]

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

    # thumbnails read from the laser are cached so they are only read once
    store = Store(hass, 1, f"{DOMAIN}.thumbs_{address.replace(':', '').lower()}")
    link.store = store
    cached = await store.async_load()
    if isinstance(cached, dict):
        link.thumbs.update({k: v for k, v in (cached.get("thumbs") or {}).items() if isinstance(v, list)})
    link.on_thumbs_changed = lambda: store.async_delay_save(lambda: {"thumbs": link.thumbs}, 15)

    # playlists are saved so they survive restarts
    pstore = Store(hass, 1, f"{DOMAIN}.playlists_{address.replace(':', '').lower()}")
    link.playlist_store = pstore
    saved = await pstore.async_load()
    if isinstance(saved, dict):
        link.playlists.update({k: v for k, v in (saved.get("playlists") or {}).items() if isinstance(v, list)})
        active = saved.get("active")
        link.playlist_name = active if active in link.playlists else next(iter(link.playlists), None)
        link.playlist_seconds = float(saved.get("seconds", link.playlist_seconds))
        link.playlist_repeat = bool(saved.get("repeat", True))
    link.on_playlists_changed = lambda: pstore.async_delay_save(
        lambda: {"playlists": link.playlists, "active": link.playlist_name,
                 "seconds": link.playlist_seconds, "repeat": link.playlist_repeat}, 3)

    # pictures: the select lists image files from your media folder; they are read on demand
    link.picture_loader = media.make_picture_loader(hass, link)
    link.refresh_pictures = media.make_picture_refresher(hass, link)
    entry.async_create_background_task(hass, link.refresh_pictures(), f"{ble_name} picture scan")

    async def _initial_read() -> None:
        try:
            await link.async_connect()
        except CubeError as err:
            _LOGGER.warning("Initial read from %s failed: %s", ble_name, err)

    entry.async_create_background_task(hass, _initial_read(), f"{ble_name} initial read")
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    dev_reg = dr.async_get(hass)
    ent_reg = er.async_get(hass)
    # the colour-flow numbers were renamed (Flow zones / Flow speed); drop the old, disabled ones
    for ent in er.async_entries_for_config_entry(ent_reg, entry.entry_id):
        if ent.unique_id.endswith(("_color_flow", "_color_speed")):
            ent_reg.async_remove(ent.entity_id)
    device = dev_reg.async_get_device(identifiers={(DOMAIN, address)})
    if device is not None:
        # Safety net: make sure every entity of this entry hangs under the device
        # (also repairs entries created by version 0.1.0).
        for ent in er.async_entries_for_config_entry(ent_reg, entry.entry_id):
            if ent.device_id != device.id:
                _LOGGER.debug("linking %s to device", ent.entity_id)
                ent_reg.async_update_entity(ent.entity_id, device_id=device.id)

    def _sync_firmware() -> None:
        fw = link.info.firmware_version
        dev = dev_reg.async_get_device(identifiers={(DOMAIN, address)})
        if dev is not None and fw and dev.sw_version != fw:
            dev_reg.async_update_device(dev.id, sw_version=fw)

    entry.async_on_unload(link.add_listener(_sync_firmware))
    entry.async_on_unload(entry.add_update_listener(_reload))
    return True


async def _reload(hass: HomeAssistant, entry: CubeConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: CubeConfigEntry) -> bool:
    ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if ok:
        link = entry.runtime_data
        await link.disconnect()
        if link.thumbs and link.store is not None:
            await link.store.async_save({"thumbs": link.thumbs})
        pstore = getattr(link, "playlist_store", None)
        if pstore is not None:
            await pstore.async_save({"playlists": link.playlists, "active": link.playlist_name,
                                     "seconds": link.playlist_seconds, "repeat": link.playlist_repeat})
    return ok
