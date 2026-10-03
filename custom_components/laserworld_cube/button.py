"""Utility buttons."""
from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.const import EntityCategory

from .entity import CubeEntity


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    link = entry.runtime_data
    async_add_entities([
        CubeRefreshButton(link, entry), CubeDisconnectButton(link, entry),
        CubePlayButton(link, entry, "play", "mdi:play", lambda l: l.async_play()),
        CubePlayButton(link, entry, "pause", "mdi:pause", lambda l: l.async_pause()),
        CubePlayButton(link, entry, "stop", "mdi:stop", lambda l: l.async_stop()),
        CubePlayButton(link, entry, "previous", "mdi:skip-previous", lambda l: l.async_step(-1)),
        CubePlayButton(link, entry, "next", "mdi:skip-next", lambda l: l.async_step(1)),
        CubePlayButton(link, entry, "overview_previous", "mdi:chevron-left-box-outline",
                       lambda l: l.async_overview_step(-1)),
        CubePlayButton(link, entry, "overview_next", "mdi:chevron-right-box-outline",
                       lambda l: l.async_overview_step(1)),
        CubePlayButton(link, entry, "new_playlist", "mdi:playlist-plus", lambda l: l.async_playlist_create()),
        CubePlayButton(link, entry, "add_to_playlist", "mdi:playlist-music", lambda l: l.async_playlist_add()),
        CubePlayButton(link, entry, "remove_playlist_item", "mdi:playlist-remove", lambda l: l.async_playlist_remove()),
        CubePlayButton(link, entry, "clear_playlist", "mdi:playlist-minus", lambda l: l.async_playlist_clear()),
        CubePlayButton(link, entry, "delete_playlist", "mdi:delete-outline", lambda l: l.async_playlist_delete()),
        CubePlayButton(link, entry, "play_text", "mdi:play-circle-outline", lambda l: l.async_play_text()),
        CubePlayButton(link, entry, "clear_text", "mdi:text-box-remove", lambda l: l.async_clear_text()),
        CubePlayButton(link, entry, "build_thumbnails", "mdi:image-plus",
                       lambda l: l.async_start_thumbnails(), diagnostic=True),
        CubePlayButton(link, entry, "rebuild_thumbnails", "mdi:image-sync",
                       lambda l: l.async_start_thumbnails(rebuild=True), diagnostic=True),
        CubePlayButton(link, entry, "cancel_thumbnails", "mdi:image-off",
                       lambda l: l.async_cancel_thumbnails(), diagnostic=True),
    ])


class CubePlayButton(CubeEntity, ButtonEntity):
    """Player controls for the selected pattern library."""

    def __init__(self, link, entry, key: str, icon: str, action, diagnostic: bool = False) -> None:
        super().__init__(link, entry, key)
        if diagnostic:
            self._attr_entity_category = EntityCategory.DIAGNOSTIC
        self._attr_translation_key = key
        self._attr_icon = icon
        self._action = action

    async def async_press(self) -> None:
        await self.call(self._action(self.link))


class CubeRefreshButton(CubeEntity, ButtonEntity):
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_translation_key = "read_settings"
    _attr_icon = "mdi:refresh"

    def __init__(self, link, entry) -> None:
        super().__init__(link, entry, "read_settings")

    async def async_press(self) -> None:
        await self.call(self.link.async_connect(refresh=True))


class CubeDisconnectButton(CubeEntity, ButtonEntity):
    """Free the Bluetooth link right away so the phone app can connect."""
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_translation_key = "disconnect"
    _attr_icon = "mdi:bluetooth-off"

    def __init__(self, link, entry) -> None:
        super().__init__(link, entry, "disconnect")

    async def async_press(self) -> None:
        await self.link.disconnect()
