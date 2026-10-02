"""Laser output switch."""
from __future__ import annotations

from homeassistant.components.switch import SwitchEntity

from .entity import CubeEntity


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    async_add_entities([CubeLaserSwitch(entry.runtime_data, entry)])


class CubeLaserSwitch(CubeEntity, SwitchEntity):
    """Laser output on/off.

    The device does not report this state back, so it is assumed. It is
    deliberately never restored as "on" after a restart.
    """
    _attr_assumed_state = True
    _attr_translation_key = "laser_output"
    _attr_icon = "mdi:laser-pointer"

    def __init__(self, link, entry) -> None:
        super().__init__(link, entry, "laser_output")

    @property
    def is_on(self) -> bool:
        return self.link.laser_on

    async def async_turn_on(self, **kwargs) -> None:
        await self.call(self.link.async_set_laser(True))

    async def async_turn_off(self, **kwargs) -> None:
        await self.call(self.link.async_set_laser(False))
