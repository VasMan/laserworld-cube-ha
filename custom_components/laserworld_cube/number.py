"""Numeric run parameters (speed, size, position, rotation)."""
from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.const import DEGREE, PERCENTAGE

from .entity import CubeEntity


@dataclass(frozen=True)
class NumberDef:
    key: str
    field: str
    min: int
    max: int
    unit: str | None = None
    icon: str | None = None


NUMBERS = (
    NumberDef("auto_speed", "runAutoSpeed", 0, 100, PERCENTAGE, "mdi:speedometer"),
    NumberDef("voice_sensitivity", "runMusicDb", 0, 100, PERCENTAGE, "mdi:microphone"),
    NumberDef("size_x", "runsizeX", 10, 100, PERCENTAGE, "mdi:arrow-expand-horizontal"),
    NumberDef("size_y", "runsizeY", 10, 100, PERCENTAGE, "mdi:arrow-expand-vertical"),
    NumberDef("position_x", "runPositionX", 0, 255, None, "mdi:arrow-left-right"),
    NumberDef("position_y", "runPositionY", 0, 255, None, "mdi:arrow-up-down"),
    NumberDef("rotation", "runRotateZ", 0, 360, DEGREE, "mdi:rotate-right"),
)


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    async_add_entities(CubeNumber(entry.runtime_data, entry, d) for d in NUMBERS)


class CubeNumber(CubeEntity, NumberEntity):
    _attr_mode = NumberMode.SLIDER
    _attr_native_step = 1

    def __init__(self, link, entry, d: NumberDef) -> None:
        super().__init__(link, entry, d.key)
        self._d = d
        self._attr_translation_key = d.key
        self._attr_native_min_value = d.min
        self._attr_native_max_value = d.max
        self._attr_native_unit_of_measurement = d.unit
        self._attr_icon = d.icon

    @property
    def native_value(self) -> float:
        return self.link.run_params[self._d.field]

    async def async_set_native_value(self, value: float) -> None:
        await self.call(self.link.async_set_params(**{self._d.field: int(value)}))
