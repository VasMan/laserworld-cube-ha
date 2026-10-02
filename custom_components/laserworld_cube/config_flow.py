"""Config flow for Laserworld Cube."""
from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.components.bluetooth import (BluetoothServiceInfoBleak,
                                                async_discovered_service_info)
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import callback

from .const import (CONF_BLE_NAME, CONF_IDLE_TIMEOUT, CONF_USER_ID,
                    DEFAULT_IDLE_TIMEOUT, DEFAULT_USER_ID, DOMAIN)
from .protocol import NAME_PREFIX


def _is_cube(info: BluetoothServiceInfoBleak) -> bool:
    return bool(info.name) and info.name.upper().startswith(NAME_PREFIX + "_")


class CubeConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._discovery: BluetoothServiceInfoBleak | None = None
        self._found: dict[str, BluetoothServiceInfoBleak] = {}

    async def async_step_bluetooth(self, discovery_info: BluetoothServiceInfoBleak) -> ConfigFlowResult:
        await self.async_set_unique_id(discovery_info.address)
        self._abort_if_unique_id_configured()
        self._discovery = discovery_info
        self.context["title_placeholders"] = {"name": discovery_info.name}
        return await self.async_step_confirm()

    async def async_step_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        assert self._discovery is not None
        if user_input is not None:
            return self._create(self._discovery)
        self._set_confirm_only()
        return self.async_show_form(
            step_id="confirm", description_placeholders={"name": self._discovery.name})

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            info = self._found[user_input[CONF_ADDRESS]]
            await self.async_set_unique_id(info.address)
            self._abort_if_unique_id_configured()
            return self._create(info)
        current = self._async_current_ids()
        self._found = {i.address: i for i in async_discovered_service_info(self.hass, False)
                       if _is_cube(i) and i.address not in current}
        if not self._found:
            return self.async_abort(reason="no_devices_found")
        return self.async_show_form(step_id="user", data_schema=vol.Schema({
            vol.Required(CONF_ADDRESS): vol.In(
                {a: f"{i.name} ({a})" for a, i in self._found.items()})}))

    def _create(self, info: BluetoothServiceInfoBleak) -> ConfigFlowResult:
        return self.async_create_entry(
            title=info.name, data={CONF_ADDRESS: info.address, CONF_BLE_NAME: info.name})

    @staticmethod
    @callback
    def async_get_options_flow(config_entry) -> OptionsFlow:
        return CubeOptionsFlow()


class CubeOptionsFlow(OptionsFlow):
    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)
        o = self.config_entry.options
        return self.async_show_form(step_id="init", data_schema=vol.Schema({
            vol.Required(CONF_IDLE_TIMEOUT, default=o.get(CONF_IDLE_TIMEOUT, DEFAULT_IDLE_TIMEOUT)):
                vol.All(vol.Coerce(int), vol.Range(min=0, max=3600)),
            vol.Required(CONF_USER_ID, default=o.get(CONF_USER_ID, DEFAULT_USER_ID)):
                vol.All(vol.Coerce(int), vol.Range(min=0, max=2**32 - 1)),
        }))
