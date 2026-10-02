"""Run mode / work mode / colour mode selects."""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from homeassistant.components.select import SelectEntity
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
    SelectDef("loop_mode", p.LOOP_MODES, lambda l: l.loop_mode,
              lambda l, v: l.async_set_loop_mode(v), None, "mdi:repeat"),
)


def _pattern_color(link: CubeLink) -> int:
    mode = link.run_params["runParaColorMode"]
    return mode if mode <= 7 else 8


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    link = entry.runtime_data
    async_add_entities([*(CubeSelect(link, entry, d) for d in SELECTS), CubeLibrarySelect(link, entry)])


class CubeSelect(CubeEntity, SelectEntity, RestoreEntity):
    def __init__(self, link: CubeLink, entry, d: SelectDef) -> None:
        super().__init__(link, entry, d.key)
        self._d = d
        self._attr_translation_key = d.key
        self._attr_icon = d.icon
        self._attr_options = list(d.options.values())
        self._by_name = {v: k for k, v in d.options.items()}

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
