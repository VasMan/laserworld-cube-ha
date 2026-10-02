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
)


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    async_add_entities(CubeSelect(entry.runtime_data, entry, d) for d in SELECTS)


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
