"""Reading pictures for the laser: Home Assistant media, local files and URLs."""
from __future__ import annotations

import os
from pathlib import Path

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from .client import CubeLink, CubeUserError

IMAGE_EXT = (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp")
MAX_BYTES = 15 * 1024 * 1024
SUBFOLDER = "laserworld_cube"          # <media dir>/laserworld_cube/ is scanned as well as the media root
MAX_FILES = 300


def scan_dirs(dirs: list[str]) -> dict[str, str]:
    """Blocking: pictures directly inside each directory and its ``laserworld_cube`` sub-folder.

    Returns ``{label: path}``; the label is the path relative to the directory.
    """
    found: dict[str, str] = {}
    for base in dirs:
        for sub in ("", SUBFOLDER):
            folder = os.path.join(base, sub) if sub else base
            try:
                names = sorted(os.listdir(folder))
            except OSError:
                continue
            for name in names[:MAX_FILES]:
                full = os.path.join(folder, name)
                if name.lower().endswith(IMAGE_EXT) and os.path.isfile(full):
                    label = f"{sub}/{name}" if sub else name
                    while label in found and found[label] != full:      # same name in two media dirs
                        label += " *"
                    found[label] = full
    return found


def picture_dirs(hass: HomeAssistant) -> list[str]:
    dirs = list(getattr(hass.config, "media_dirs", {}).values()) or ["/media"]
    dirs.append(hass.config.path("www"))
    return dirs


async def async_scan(hass: HomeAssistant) -> dict[str, str]:
    return await hass.async_add_executor_job(scan_dirs, picture_dirs(hass))


def _read_file(path: str) -> bytes:
    if os.path.getsize(path) > MAX_BYTES:
        raise HomeAssistantError("The picture is larger than 15 MB")
    return Path(path).read_bytes()


async def async_read_file(hass: HomeAssistant, path: str) -> bytes:
    """Read a local file, only if Home Assistant allows access to it."""
    path = os.path.abspath(os.path.expanduser(path))
    if not hass.config.is_allowed_path(path):
        raise HomeAssistantError(
            f"{path} is not in an allowed folder (add it to allowlist_external_dirs, "
            "or put the picture in your media folder)")
    if not os.path.isfile(path):
        raise HomeAssistantError(f"{path} does not exist")
    return await hass.async_add_executor_job(_read_file, path)


async def _fetch(hass: HomeAssistant, url: str) -> bytes:
    from homeassistant.helpers.aiohttp_client import async_get_clientsession
    if url.startswith("/"):                                   # a Home Assistant relative URL
        from homeassistant.helpers.network import get_url
        url = get_url(hass, allow_external=False) + url
    session = async_get_clientsession(hass)
    async with session.get(url, timeout=20) as resp:
        if resp.status != 200:
            raise HomeAssistantError(f"Could not download the picture (HTTP {resp.status})")
        if (resp.content_length or 0) > MAX_BYTES:
            raise HomeAssistantError("The picture is larger than 15 MB")
        data = await resp.content.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise HomeAssistantError("The picture is larger than 15 MB")
    return data


async def async_read_image(hass: HomeAssistant, source: str) -> tuple[bytes, str]:
    """Read a picture from a media-source id, a path (``/media/..``, ``/local/..``) or a URL.

    Returns ``(bytes, display name)``.
    """
    source = source.strip()
    name = os.path.basename(source.split("?")[0]) or "picture"
    if source.startswith("media-source://"):
        from homeassistant.components import media_source
        item = await media_source.async_resolve_media(hass, source, None)
        path = getattr(item, "path", None)
        if path:
            return await async_read_file(hass, str(path)), name
        return await _fetch(hass, item.url), name
    if source.startswith(("http://", "https://")):
        return await _fetch(hass, source), name
    if source.startswith("/local/"):
        source = hass.config.path("www", source[len("/local/"):])
    elif not os.path.isabs(source):
        source = hass.config.path(source)
    return await async_read_file(hass, source), name


def make_picture_loader(hass: HomeAssistant, link: CubeLink):
    """The loader the client uses to read a picture chosen by its label."""
    async def load(label: str) -> bytes:
        path = link.picture_files.get(label)
        if path is None:
            raise CubeUserError(f"Picture '{label}' was not found - press 'Refresh picture list'")
        try:
            return await async_read_file(hass, path)
        except HomeAssistantError as err:
            raise CubeUserError(str(err)) from err
    return load


def make_picture_refresher(hass: HomeAssistant, link: CubeLink):
    async def refresh() -> None:
        link.picture_files = await async_scan(hass)
        link._notify()
    return refresh
