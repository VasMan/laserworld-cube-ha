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
    link = entry.runtime_data
    async_add_entities([
        *(CubeNumber(link, entry, d) for d in NUMBERS),
        CubePatternNumber(link, entry),
        CubeLoopInterval(link, entry),
        CubeFlowNumber(link, entry, "color_flow", "mdi:water",
                       lambda l: l.flow_precision, lambda l, v: l.async_set_flow(precision=v)),
        CubeFlowNumber(link, entry, "color_speed", "mdi:speedometer-medium",
                       lambda l: l.run_params["runParaColorSpeed"], lambda l, v: l.async_set_flow(speed=v)),
    ])


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


class CubePatternNumber(CubeEntity, NumberEntity):
    """Pattern number within the selected library. Setting it plays the pattern."""
    _attr_mode = NumberMode.BOX
    _attr_native_step = 1
    _attr_native_min_value = 1
    _attr_translation_key = "pattern"
    _attr_icon = "mdi:numeric"

    def __init__(self, link, entry) -> None:
        super().__init__(link, entry, "pattern")

    @property
    def native_max_value(self) -> float:
        lib = self.link.current_library
        return float(lib.size) if lib else 1.0

    @property
    def native_value(self) -> float:
        return self.link.pattern_index

    async def async_set_native_value(self, value: float) -> None:
        await self.call(self.link.async_play_index(int(value)))


class CubeLoopInterval(CubeEntity, NumberEntity):
    _attr_mode = NumberMode.BOX
    _attr_native_step = 1
    _attr_native_min_value = 1
    _attr_native_max_value = 3600
    _attr_native_unit_of_measurement = "s"
    _attr_translation_key = "loop_interval"
    _attr_icon = "mdi:timer-outline"

    def __init__(self, link, entry) -> None:
        super().__init__(link, entry, "loop_interval")

    @property
    def native_value(self) -> float:
        return self.link.loop_interval

    async def async_set_native_value(self, value: float) -> None:
        await self.call(self.link.async_set_loop_interval(value))


class CubeFlowNumber(CubeEntity, NumberEntity):
    """Flowing-colour settings of the library page (disabled by default)."""
    _attr_mode = NumberMode.SLIDER
    _attr_native_step = 1
    _attr_native_min_value = 8
    _attr_native_max_value = 63
    _attr_entity_registry_enabled_default = False

    def __init__(self, link, entry, key: str, icon: str, getter, setter) -> None:
        super().__init__(link, entry, key)
        self._attr_translation_key = key
        self._attr_icon = icon
        self._get, self._set = getter, setter

    @property
    def native_value(self) -> float:
        return self._get(self.link)

    async def async_set_native_value(self, value: float) -> None:
        await self.call(self._set(self.link, int(value)))
