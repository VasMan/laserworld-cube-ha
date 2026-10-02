"""Numeric run parameters (speed, size, position, rotation)."""
from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.const import DEGREE, PERCENTAGE, EntityCategory

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
        CubeTextSize(link, entry),
        CubeLoopInterval(link, entry),
        CubeFlowNumber(link, entry, "flow_zones", "mdi:water",
                       lambda l: l.flow_precision, lambda l, v: l.async_set_flow(precision=v)),
        CubeFlowNumber(link, entry, "flow_speed", "mdi:speedometer-medium",
                       lambda l: l.run_params["runParaColorSpeed"], lambda l, v: l.async_set_flow(speed=v)),
        CubeEffectSpeed(link, entry),
        CubeOverviewPage(link, entry),
        *(CubeSettingNumber(link, entry, d) for d in SETTING_NUMBERS),
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
    """Flowing-colour settings (the app's LaserZones / FlowSpeed)."""
    _attr_mode = NumberMode.SLIDER
    _attr_native_step = 1
    _attr_native_min_value = 8
    _attr_native_max_value = 63

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


class CubeTextSize(CubeEntity, NumberEntity):
    """Size of the displayed text relative to the laser's full frame."""
    _attr_mode = NumberMode.SLIDER
    _attr_native_step = 5
    _attr_native_min_value = 10
    _attr_native_max_value = 100
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_translation_key = "text_size"
    _attr_icon = "mdi:format-size"

    def __init__(self, link, entry) -> None:
        super().__init__(link, entry, "text_size")

    @property
    def native_value(self) -> float:
        return self.link.text_size

    async def async_set_native_value(self, value: float) -> None:
        await self.call(self.link.async_set_text_size(value))


class CubeEffectSpeed(CubeEntity, NumberEntity):
    """Speed of the motion effect (1 = slow, 100 = fast)."""
    _attr_mode = NumberMode.SLIDER
    _attr_native_step = 1
    _attr_native_min_value = 1
    _attr_native_max_value = 100
    _attr_translation_key = "effect_speed"
    _attr_icon = "mdi:speedometer"

    def __init__(self, link, entry) -> None:
        super().__init__(link, entry, "effect_speed")

    @property
    def native_value(self) -> float:
        return self.link.effect_speed

    async def async_set_native_value(self, value: float) -> None:
        await self.call(self.link.async_set_effect_speed(value))


class CubeOverviewPage(CubeEntity, NumberEntity):
    """Which page of the library overview image to show."""
    _attr_mode = NumberMode.BOX
    _attr_native_step = 1
    _attr_native_min_value = 1
    _attr_translation_key = "overview_page"
    _attr_icon = "mdi:book-open-page-variant"

    def __init__(self, link, entry) -> None:
        super().__init__(link, entry, "overview_page")

    @property
    def native_max_value(self) -> float:
        return float(self.link.overview_pages())

    @property
    def native_value(self) -> float:
        return self.link.current_overview_page()

    async def async_set_native_value(self, value: float) -> None:
        await self.call(self.link.async_set_overview_page(int(value)))


@dataclass(frozen=True)
class SettingNumberDef:
    key: str
    field: str            # field in the laser's settings block
    min: int
    max: int
    unit: str | None = None
    icon: str | None = None
    mode: str = "slider"
    enabled: bool = True


SETTING_NUMBERS = (
    SettingNumberDef("dmx_address", "deviceAddress", 1, 512, None, "mdi:numeric", "box"),
    SettingNumberDef("device_size_x", "deviceSizeX", 10, 100, PERCENTAGE, "mdi:arrow-expand-horizontal"),
    SettingNumberDef("device_size_y", "deviceSizeY", 10, 100, PERCENTAGE, "mdi:arrow-expand-vertical"),
    SettingNumberDef("device_position_x", "devicePositionX", 0, 255, None, "mdi:arrow-left-right"),
    SettingNumberDef("device_position_y", "devicePositionY", 0, 255, None, "mdi:arrow-up-down"),
    SettingNumberDef("red_max", "deviceRedMax", 0, 100, PERCENTAGE, "mdi:led-on", enabled=False),
    SettingNumberDef("green_max", "deviceGreenMax", 0, 100, PERCENTAGE, "mdi:led-on", enabled=False),
    SettingNumberDef("blue_max", "deviceBlueMax", 0, 100, PERCENTAGE, "mdi:led-on", enabled=False),
)


class CubeSettingNumber(CubeEntity, NumberEntity):
    """A persistent device setting from the app's "Laser device settings"."""
    _attr_entity_category = EntityCategory.CONFIG
    _attr_native_step = 1

    def __init__(self, link, entry, d: SettingNumberDef) -> None:
        super().__init__(link, entry, d.key)
        self._d = d
        self._attr_translation_key = d.key
        self._attr_native_min_value = d.min
        self._attr_native_max_value = d.max
        self._attr_native_unit_of_measurement = d.unit
        self._attr_icon = d.icon
        self._attr_mode = NumberMode.BOX if d.mode == "box" else NumberMode.SLIDER
        self._attr_entity_registry_enabled_default = d.enabled

    @property
    def native_value(self) -> float | None:
        return self.link.device_model.get(self._d.field)

    async def async_set_native_value(self, value: float) -> None:
        await self.call(self.link.async_set_device_settings(**{self._d.field: int(value)}))
