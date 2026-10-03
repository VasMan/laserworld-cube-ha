"""Laser output switch."""
from __future__ import annotations

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import EntityCategory

from .entity import CubeEntity


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    link = entry.runtime_data
    async_add_entities([CubeLaserSwitch(link, entry), CubeLoopSwitch(link, entry),
                        CubePlaylistSwitch(link, entry), CubePlaylistRepeat(link, entry),
                        *(CubeSettingSwitch(link, entry, *d) for d in SETTING_SWITCHES)])


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


class CubeLoopSwitch(CubeEntity, SwitchEntity):
    """Cycle through the selected library from Home Assistant.

    The official app also times this on the phone: the laser itself plays one
    pattern until told otherwise.
    """
    _attr_translation_key = "loop_play"
    _attr_icon = "mdi:play-box-multiple"

    def __init__(self, link, entry) -> None:
        super().__init__(link, entry, "loop_play")

    @property
    def is_on(self) -> bool:
        return self.link.loop_on

    async def async_turn_on(self, **kwargs) -> None:
        await self.call(self.link.async_set_loop(True))

    async def async_turn_off(self, **kwargs) -> None:
        await self.call(self.link.async_set_loop(False))


# key, settings field, icon, enabled by default
SETTING_SWITCHES = (
    ("safety", "devicesafety", "mdi:shield-check", False),
    ("master", "deviceMasterFunc", "mdi:account-supervisor", True),
    ("invert_x", "deviceInvertX", "mdi:flip-horizontal", True),
    ("invert_y", "deviceInvertY", "mdi:flip-vertical", True),
    ("swap_xy", "deviceSwapXY", "mdi:swap-horizontal-variant", True),
)


class CubeSettingSwitch(CubeEntity, SwitchEntity):
    """A persistent on/off device setting from the app's "Laser device settings"."""
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, link, entry, key: str, field: str, icon: str, enabled: bool) -> None:
        super().__init__(link, entry, key)
        self._field = field
        self._attr_translation_key = key
        self._attr_icon = icon
        self._attr_entity_registry_enabled_default = enabled

    @property
    def is_on(self) -> bool | None:
        value = self.link.device_model.get(self._field)
        return None if value is None else value == 1

    async def async_turn_on(self, **kwargs) -> None:
        await self.call(self.link.async_set_device_settings(**{self._field: 1}))

    async def async_turn_off(self, **kwargs) -> None:
        await self.call(self.link.async_set_device_settings(**{self._field: 0}))


class CubePlaylistSwitch(CubeEntity, SwitchEntity):
    """Play the active playlist: each pattern for its own time."""
    _attr_translation_key = "play_playlist"
    _attr_icon = "mdi:playlist-play"

    def __init__(self, link, entry) -> None:
        super().__init__(link, entry, "play_playlist")

    @property
    def is_on(self) -> bool:
        return self.link.playlist_on

    async def async_turn_on(self, **kwargs) -> None:
        await self.call(self.link.async_playlist_play())

    async def async_turn_off(self, **kwargs) -> None:
        await self.call(self.link.async_playlist_stop())


class CubePlaylistRepeat(CubeEntity, SwitchEntity):
    _attr_translation_key = "repeat_playlist"
    _attr_icon = "mdi:repeat"

    def __init__(self, link, entry) -> None:
        super().__init__(link, entry, "repeat_playlist")

    @property
    def is_on(self) -> bool:
        return self.link.playlist_repeat

    async def async_turn_on(self, **kwargs) -> None:
        await self.call(self.link.async_set_playlist_repeat(True))

    async def async_turn_off(self, **kwargs) -> None:
        await self.call(self.link.async_set_playlist_repeat(False))
