"""Run mode / work mode / colour mode selects."""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from homeassistant.components.select import SelectEntity
import voluptuous as vol
from homeassistant.const import EntityCategory
from homeassistant.helpers import entity_platform
from homeassistant.helpers.restore_state import RestoreEntity

from . import protocol as p
from .client import CubeLink
from .entity import CubeEntity


@dataclass(frozen=True)
class SelectDef:
    key: str
    options: dict[int, str]
    get: Callable[[CubeLink], int]
    set: Callable[[CubeLink, int], Awaitable[None]]
    restore: Callable[[CubeLink, int], None] | None = None
    icon: str | None = None
    config: bool = False


def _restore_run_mode(link: CubeLink, v: int) -> None:
    link.run_mode = v


def _restore_work_mode(link: CubeLink, v: int) -> None:
    link.run_params["runWorkMode"] = v


SELECTS = (
    SelectDef("run_mode", p.RUN_MODES, lambda l: l.run_mode,
              lambda l, v: l.async_set_run_mode(v), _restore_run_mode, "mdi:tune"),
    SelectDef("work_mode", {p.WORK_MODE_AUTO: "Automatic", p.WORK_MODE_VOICE: "Voice"},
              lambda l: l.run_params["runWorkMode"],
              lambda l, v: l.async_set_params(runWorkMode=v), _restore_work_mode, "mdi:music"),
    SelectDef("color_mode", p.COLOR_MODES, lambda l: l.run_params["runColorMode"],
              lambda l, v: l.async_set_params(runColorMode=v), None, "mdi:palette"),
    SelectDef("pattern_color", p.PATTERN_COLORS, lambda l: _pattern_color(l),
              lambda l, v: l.async_set_pattern_color(v), None, "mdi:palette-swatch"),
    SelectDef("text_color", p.TEXT_COLORS, lambda l: l.text_color,
              lambda l, v: l.async_set_text_color(v), None, "mdi:format-color-text"),
    SelectDef("text_orientation", p.TEXT_ORIENTATIONS, lambda l: l.text_orientation,
              lambda l, v: l.async_set_text_orientation(v), None, "mdi:format-text-rotation-none"),
    SelectDef("text_direction", p.TEXT_DIRECTIONS, lambda l: 1 if l.text_reverse else 0,
              lambda l, v: l.async_set_text_direction(v), None, "mdi:swap-horizontal"),
    SelectDef("effect", p.EFFECTS, lambda l: l.effect,
              lambda l, v: l.async_set_effect(v), None, "mdi:animation-play"),
    SelectDef("picture_mode", p.PICTURE_MODES, lambda l: l.picture_mode,
              lambda l, v: l.async_set_picture_options(mode=v), None, "mdi:vector-polyline"),
    SelectDef("picture_color", p.PICTURE_COLORS, lambda l: l.picture_color,
              lambda l, v: l.async_set_picture_options(color=v), None, "mdi:palette-swatch-variant"),
    SelectDef("hw_effect", p.HW_EFFECTS, lambda l: l.hw_effect,
              lambda l, v: l.async_set_hw_effect(v), None, "mdi:auto-fix"),
    SelectDef("hw_layout", p.HW_LAYOUTS, lambda l: l.hw_layout,
              lambda l, v: l.async_set_hw_layout(v), None, "mdi:table-column", True),
    SelectDef("loop_mode", p.LOOP_MODES, lambda l: l.loop_mode,
              lambda l, v: l.async_set_loop_mode(v), None, "mdi:repeat"),
)


def _pattern_color(link: CubeLink) -> int:
    mode = link.run_params["runParaColorMode"]
    return mode if mode <= 7 else 8


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    link = entry.runtime_data
    async_add_entities([
        *(CubeSelect(link, entry, d) for d in SELECTS),
        CubeLibrarySelect(link, entry),
        *(CubeSettingSelect(link, entry, d) for d in SETTING_SELECTS),
        CubePlaylistSelect(link, entry),
        CubePictureSelect(link, entry),
    ])
    platform = entity_platform.async_get_current_platform()
    secs = vol.All(vol.Coerce(float), vol.Range(min=0.5, max=3600))
    platform.async_register_entity_service("playlist_add", {
        vol.Optional("library"): str,
        vol.Optional("pattern"): vol.All(vol.Coerce(int), vol.Range(min=1, max=255)),
        vol.Optional("duration"): secs,
        vol.Optional("playlist"): str,
        vol.Optional("position"): vol.All(vol.Coerce(int), vol.Range(min=1, max=999)),
    }, "async_service_add")
    platform.async_register_entity_service("playlist_rename", {
        vol.Required("name"): str,
        vol.Optional("playlist"): str,
    }, "async_service_rename")
    platform.async_register_entity_service("playlist_create", {
        vol.Optional("name"): str,
    }, "async_service_create")
    platform.async_register_entity_service("playlist_remove", {
        vol.Optional("index"): vol.All(vol.Coerce(int), vol.Range(min=1, max=999)),
        vol.Optional("playlist"): str,
    }, "async_service_remove")
    platform.async_register_entity_service("playlist_set_duration", {
        vol.Required("index"): vol.All(vol.Coerce(int), vol.Range(min=1, max=999)),
        vol.Required("duration"): secs,
        vol.Optional("playlist"): str,
    }, "async_service_set_duration")
    platform.async_register_entity_service("playlist_move", {
        vol.Required("index"): vol.All(vol.Coerce(int), vol.Range(min=1, max=999)),
        vol.Required("to"): vol.All(vol.Coerce(int), vol.Range(min=1, max=999)),
        vol.Optional("playlist"): str,
    }, "async_service_move")


class CubeSelect(CubeEntity, SelectEntity, RestoreEntity):
    def __init__(self, link: CubeLink, entry, d: SelectDef) -> None:
        super().__init__(link, entry, d.key)
        self._d = d
        self._attr_translation_key = d.key
        self._attr_icon = d.icon
        self._attr_options = list(d.options.values())
        self._by_name = {v: k for k, v in d.options.items()}
        if d.config:
            self._attr_entity_category = EntityCategory.CONFIG

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if self._d.restore and (last := await self.async_get_last_state()):
            if last.state in self._by_name and not self.link.settings_loaded:
                self._d.restore(self.link, self._by_name[last.state])

    @property
    def current_option(self) -> str | None:
        return self._d.options.get(self._d.get(self.link))

    async def async_select_option(self, option: str) -> None:
        await self.call(self._d.set(self.link, self._by_name[option]))


class CubeLibrarySelect(CubeEntity, SelectEntity):
    """Which built-in pattern library the pattern number refers to.

    Libraries come from the laser's own catalog, so they appear after the first
    successful connection (press "Read settings" to re-read).
    """
    _attr_translation_key = "library"
    _attr_icon = "mdi:folder-multiple-image"

    def __init__(self, link: CubeLink, entry) -> None:
        super().__init__(link, entry, "library")

    @property
    def options(self) -> list[str]:
        return [lib.label for lib in self.link.libraries]

    @property
    def current_option(self) -> str | None:
        lib = self.link.current_library
        return lib.label if lib else None

    async def async_select_option(self, option: str) -> None:
        await self.call(self.link.async_select_library(self.options.index(option)))


@dataclass(frozen=True)
class SettingSelectDef:
    key: str
    field: str                                   # field in the laser's settings block
    options: Callable[[CubeLink], dict[int, str]]
    icon: str | None = None
    enabled: bool = True


def _dmx_modes(link: CubeLink) -> dict[int, str]:
    m = link.device_model
    std, pro = m.get("deviceStdChannleTotal"), m.get("deviceProChannleTotal")
    return {0: f"DMX mode {std}CH" if std else "DMX mode (standard)",
            1: f"DMX mode {pro}CH" if pro else "DMX mode (pro)"}


SETTING_SELECTS = (
    SettingSelectDef("dmx_mode", "deviceChannelMode", _dmx_modes, "mdi:dip-switch"),
    SettingSelectDef("functional_mode", "deviceRunWorkMode", lambda l: p.FUNCTION_MODES, "mdi:cog-play"),
    SettingSelectDef("scan_speed", "deviceScannerRate",
                     lambda l: {k: f"{k}KPPS" for k in p.SCAN_SPEEDS}, "mdi:speedometer", enabled=False),
    SettingSelectDef("color_setting", "deviceColorFunc",
                     lambda l: {k: f"{k}.{v}" for k, v in p.COLOR_MODES.items()}, "mdi:palette-advanced"),
    SettingSelectDef("laser_type", "deviceLaserType", lambda l: p.LASER_TYPES, "mdi:laser-pointer", enabled=False),
)


class CubeSettingSelect(CubeEntity, SelectEntity):
    """A persistent device setting from the app's "Laser device settings"."""
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, link: CubeLink, entry, d: SettingSelectDef) -> None:
        super().__init__(link, entry, d.key)
        self._d = d
        self._attr_translation_key = d.key
        self._attr_icon = d.icon
        self._attr_entity_registry_enabled_default = d.enabled

    @property
    def options(self) -> list[str]:
        return list(self._d.options(self.link).values())

    @property
    def current_option(self) -> str | None:
        value = self.link.device_model.get(self._d.field)
        return self._d.options(self.link).get(value) if value is not None else None

    async def async_select_option(self, option: str) -> None:
        by_name = {v: k for k, v in self._d.options(self.link).items()}
        await self.call(self.link.async_set_device_settings(**{self._d.field: by_name[option]}))


class CubePlaylistSelect(CubeEntity, SelectEntity):
    """The active playlist. Also the target of the playlist_* services."""
    _attr_translation_key = "playlist"
    _attr_icon = "mdi:playlist-play"

    def __init__(self, link: CubeLink, entry) -> None:
        super().__init__(link, entry, "playlist")

    @property
    def options(self) -> list[str]:
        return list(self.link.playlists)

    @property
    def current_option(self) -> str | None:
        return self.link.playlist_name

    async def async_select_option(self, option: str) -> None:
        await self.call(self.link.async_playlist_select(option))

    async def async_service_add(self, library=None, pattern=None, duration=None, playlist=None, position=None):
        await self.call(self.link.async_playlist_add(library, pattern, duration, playlist, position))

    async def async_service_rename(self, name, playlist=None):
        await self.call(self.link.async_playlist_rename(name, playlist))

    async def async_service_create(self, name=None):
        await self.call(self.link.async_playlist_create(name))

    async def async_service_remove(self, index=None, playlist=None):
        await self.call(self.link.async_playlist_remove(index, playlist))

    async def async_service_set_duration(self, index, duration, playlist=None):
        await self.call(self.link.async_playlist_set_duration(index, duration, playlist))

    async def async_service_move(self, index, to, playlist=None):
        await self.call(self.link.async_playlist_move(index, to, playlist))


class CubePictureSelect(CubeEntity, SelectEntity):
    """Pictures found in your media folder (and ``www``); choosing one shows it on the laser."""
    _attr_translation_key = "picture"
    _attr_icon = "mdi:image-outline"

    def __init__(self, link: CubeLink, entry) -> None:
        super().__init__(link, entry, "picture")

    @property
    def options(self) -> list[str]:
        return list(self.link.picture_files)

    @property
    def current_option(self) -> str | None:
        return self.link.picture_name if self.link.picture_name in self.link.picture_files else None

    async def async_select_option(self, option: str) -> None:
        await self.call(self.link.async_show_picture_file(option))
