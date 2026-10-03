"""BLE session layer: connect, handshake, commands, idle disconnect.

Deliberately free of Home Assistant imports. The caller supplies a coroutine
``connect(disconnected_callback)`` returning a connected bleak-like client.
"""
from __future__ import annotations

import asyncio
import logging
import math
import random
from collections.abc import Awaitable, Callable
from typing import Any

from . import protocol as p
from . import stroke_font

_LOGGER = logging.getLogger(__name__)

RESPONSE_TIMEOUT = 5.0
HANDSHAKE_DELAY = 1.0  # the official app waits 1 s after connecting


class CubeError(Exception):
    """Communication failure (retryable)."""


class CubeFatalError(CubeError):
    """Problem that retrying will not fix."""


class CubeUserError(CubeError):
    """Invalid request (e.g. text too long); the link itself is fine."""


class NotActivated(CubeFatalError):
    """Device was never activated by the official app."""


class DeviceBound(CubeFatalError):
    """Device is bound to a different app account."""


class NotSupported(CubeFatalError):
    """Not a (supported) Cube Laser Control device."""


ConnectFn = Callable[[Callable[[Any], None]], Awaitable[Any]]


class CubeLink:
    """One logical connection to a Cube laser."""

    def __init__(self, ble_name: str, connect: ConnectFn, *, user_id: int = 0,
                 idle_timeout: float = 30.0) -> None:
        self.ble_name = ble_name
        self.user_id = user_id
        self.idle_timeout = idle_timeout
        self._connect = connect
        self._client: Any = None
        self._keys: tuple[bytes, bytes] | None = None
        self._lock = asyncio.Lock()
        self._pending: asyncio.Future[bytes] | None = None
        self._idle_handle: asyncio.TimerHandle | None = None
        self._listeners: list[Callable[[], None]] = []
        self.info = p.HandshakeInfo()
        self.connected = False
        # Assumed state: the device does not report these back.
        self.laser_on = False
        self.run_mode = p.RUN_MODE_APP
        self.run_params: dict[str, int] = dict(p.DEFAULT_RUN_PARAMS)
        self.device_model: dict[str, int] = {}
        self.settings_loaded = False
        # pattern libraries (read from the device catalog)
        self.libraries: list[p.Library] = []
        self.library_loaded = False
        self.selected_library = 0
        self.pattern_index = 1
        self.play_state = p.PLAY_STATE_STOP
        self.flow_precision = 8
        # client-side looping (the official app times this itself)
        self.loop_on = False
        self.loop_mode = 0
        self.loop_interval = 5.0
        self._loop_task: asyncio.Task | None = None
        # thumbnails: "page_file" -> flat [x, y, state, rgb, ...] read from the laser
        self.thumbs: dict[str, list[int]] = {}
        self.thumb_status = ""
        self.on_thumbs_changed: Callable[[], None] | None = None
        self.store: Any = None
        self._thumb_task: asyncio.Task | None = None
        # text
        self.text = "Alexandros"
        self.text_color = 8                # Rainbow
        self.text_size = 100
        self.text_active = False
        self.text_orientation = 0
        self.text_reverse = False
        # raw effect channel values sent with the text (experimental, see send_effect service)
        self.text_effect: dict | None = None
        # hardware (laser-side) effect applied to the text
        self.hw_effect = 0
        self.hw_speed = 60
        self.hw_layout = 0
        self.text_flow_set = False          # we switched the laser's colour flow on for text
        self._text_box: tuple[float, float] | None = None
        # motion effects driven from here (position / rotation / size of the output)
        self.effect = 0
        self.effect_speed = 50
        self._effect_task: asyncio.Task | None = None
        self.overview_page: int | None = None   # None = follow the current pattern
        # playlists (timed from here, like the app's own playlist player)
        self.playlists: dict[str, list[dict]] = {}   # name -> [{"lib", "name", "n", "seconds"}]
        self.playlist_name: str | None = None
        self.playlist_seconds = 5.0                  # duration given to the next added item
        self.playlist_repeat = True
        self.playlist_on = False
        self.playlist_index = 0                      # item being shown while the playlist runs
        self.on_playlists_changed: Callable[[], None] | None = None
        self.playlist_store: Any = None
        self._playlist_task: asyncio.Task | None = None

    # ------------------------------------------------------------ listeners
    def add_listener(self, cb: Callable[[], None]) -> Callable[[], None]:
        self._listeners.append(cb)
        return lambda: self._listeners.remove(cb) if cb in self._listeners else None

    def _notify(self) -> None:
        for cb in list(self._listeners):
            try:
                cb()
            except Exception:  # noqa: BLE001
                _LOGGER.exception("listener failed")

    # ------------------------------------------------------------ low level
    def _on_notify(self, _sender: Any, data: bytearray) -> None:
        _LOGGER.debug("RX raw %s", bytes(data).hex())
        if self._pending is not None and not self._pending.done():
            self._pending.set_result(bytes(data))

    def _on_disconnect(self, _client: Any) -> None:
        _LOGGER.debug("%s disconnected", self.ble_name)
        self.connected = False
        self._keys = None
        self._notify()

    async def _write(self, data: bytes) -> None:
        props: set[str] = set()
        try:
            char = self._client.services.get_characteristic(p.CHAR_UUID)
            props = set(getattr(char, "properties", None) or [])
        except Exception:  # noqa: BLE001
            pass
        if not props or "write" in props:
            await self._client.write_gatt_char(p.CHAR_UUID, data, response=True)
            return
        mtu = getattr(self._client, "mtu_size", 23) or 23
        size = max(20, mtu - 3)
        for i in range(0, len(data), size):
            await self._client.write_gatt_char(p.CHAR_UUID, data[i:i + size], response=False)

    async def _exchange(self, frame: bytes) -> bytes:
        assert self._keys is not None
        loop = asyncio.get_running_loop()
        self._pending = loop.create_future()
        _LOGGER.debug("TX plain %s", frame.hex())
        try:
            await self._write(p.encrypt(frame, *self._keys))
            raw = await asyncio.wait_for(self._pending, RESPONSE_TIMEOUT)
        finally:
            self._pending = None
        plain = p.decrypt(raw, *self._keys)
        _LOGGER.debug("RX plain %s", plain.hex())
        return plain

    async def _transfer(self, function: int, action: int, data: bytes | None = None) -> bytes:
        frame = p.build_transfer(function, action, data, data_format=self.info.data_format)
        if data and self.info.buffer_max and len(data) > self.info.buffer_max - 47:
            raise CubeFatalError("payload larger than one packet; not supported")
        for _ in range(2):
            resp = await self._exchange(frame)
            status = p.parse_status(resp, p.RSP_TRANSFER)
            if status == 1:  # device asks for a resend
                continue
            if status in (0, 2, 3):
                return resp
            raise p.DeviceStatusError(status)
        raise CubeError("device kept asking to resend")

    # ------------------------------------------------------------ connection
    async def _ensure(self) -> None:
        if self._client is not None and self.connected and self._keys is not None:
            return
        await self._drop()
        try:
            self._client = await self._connect(self._on_disconnect)
            await self._client.start_notify(p.CHAR_UUID, self._on_notify)
            self.connected = True
            await asyncio.sleep(HANDSHAKE_DELAY)
            await self._handshake()
            await self._load_settings()
            await self._load_library()
        except BaseException:
            await self._drop()
            raise
        self._notify()

    async def _handshake(self) -> None:
        self._keys = p.default_key_iv(self.ble_name)
        resp = await self._exchange(p.build_handshake(self.user_id))
        info = p.parse_handshake_response(resp)
        if info.status != 0:
            raise NotSupported(f"handshake rejected with status {info.status}")
        if info.app_company and info.app_company != p.APP_COMPANY:
            raise NotSupported(f"unexpected device family {info.app_company!r}")
        if info.activate_type != 1:
            raise NotActivated(
                "This laser has not been activated yet. Connect to it once with the "
                "official Cube Laser Control app (internet required), then retry.")
        self.info = info
        self._keys = p.session_key_iv(info.activate_type, info.product_key,
                                      info.device_key, info.device_secret)
        _LOGGER.debug("handshake ok: fw=%s comm=%s fmt=%s mtu_buf=%s",
                      info.firmware_version, info.communication_version,
                      info.data_format, info.buffer_max)
        if p.version_tuple(info.firmware_version) >= (2, 0, 0):
            await self._check_binding()

    async def _check_binding(self) -> None:
        """Mirror the official app: refuse devices bound to another account."""
        resp = await self._transfer(p.FUNC_MY_DEVICE, p.ACT_GET_DEVICE_BIND_INFO, b"\x00")
        bind = p.parse_fields(p.parse_data_response(resp), p.BIND_FIELDS)
        _LOGGER.debug("bind info: en=%s auth=%s", bind["bindEn"], bind["bindAuth"])
        if bind["bindEn"] != p.BIND_NONE and bind["bindUser"] != self.user_id:
            raise DeviceBound(
                "This laser is bound to a different Cube Laser Control account. "
                "Unbind it in the official app (Settings > Bind device) or enter "
                "your account's user ID in this integration's options.")

    async def _load_settings(self, force: bool = False) -> None:
        """Read device settings. Only once per session unless forced, so values
        set from HA are not overwritten after an idle reconnect."""
        if self.settings_loaded and not force:
            return
        if p.version_tuple(self.info.communication_version) <= (1, 1, 6):
            return
        resp = b""
        try:
            resp = await self._transfer(p.FUNC_MY_DEVICE, p.ACT_SEARCH_DEVICE_SET_MODEL)
            model = p.parse_fields(p.parse_data_response(resp), p.DEVICE_MODEL_FIELDS,
                                   required="deviceAddress")
        except (p.ProtocolError, asyncio.TimeoutError) as err:
            _LOGGER.warning("could not read device settings: %s (raw reply: %s)", err, resp.hex())
            return
        _LOGGER.debug("device settings: %s", {k: v for k, v in model.items() if k != "reserve"})
        self.device_model = model
        self._apply_model(model)

    def _apply_model(self, m: dict[str, int]) -> None:
        """Seed run parameters from the device, with the app's sanity limits."""
        def pick(key: str, lo: int, hi: int, default: int) -> int:
            v = m.get(key, default)
            return v if lo <= v <= hi else default

        r = self.run_params
        r["runAutoSpeed"] = pick("deviceAutospeed", 0, 100, 50)
        r["runMusicDb"] = pick("deviceMusicDb", 0, 100, 80)
        r["runsizeX"] = pick("deviceSizeX", 10, 100, 100)
        r["runsizeY"] = pick("deviceSizeY", 10, 100, 100)
        r["runPositionX"] = pick("devicePositionX", 0, 255, 128)
        r["runPositionY"] = pick("devicePositionY", 0, 255, 128)
        r["runRotateZ"] = pick("deviceRotateZ", 0, 360, 0)
        r["runColorMode"] = pick("deviceColorFunc", 1, 12, 11)
        r["runAddress"] = pick("deviceAddress", 1, 512, 1)
        r["runChannelMode"] = pick("deviceChannelMode", 0, 1, 0)
        r["runZoneX"] = pick("deviceZoneX", 0, 255, 100)
        r["runZoneY"] = pick("deviceZoneY", 0, 255, 100)
        r["frameRate"] = pick("deviceframeRate", 30, 120, 60)
        r["scannerRate"] = pick("deviceScannerRate", 15, 40, 30)
        self.settings_loaded = True

    async def _load_library(self, force: bool = False) -> None:
        """Read the device's pattern-library catalog (one page per request)."""
        if self.library_loaded and not force:
            return
        entries: list[p.LibEntry] = []
        try:
            idx = 1
            while idx <= 64:
                resp = await self._transfer(p.FUNC_MY_DEVICE, p.ACT_SEARCH_LIB_FILE, bytes([idx]))
                total, entry = p.parse_lib_entry(resp)
                entries.append(entry)
                if total < idx + 1:
                    break
                idx += 1
        except (p.ProtocolError, asyncio.TimeoutError) as err:
            _LOGGER.warning("could not read pattern library catalog: %s", err)
            return
        _LOGGER.debug("library catalog: %s", entries)
        self.libraries = p.build_libraries(entries)
        self.library_loaded = True
        if self.selected_library >= len(self.libraries):
            self.selected_library = 0
            self.pattern_index = 1
        _LOGGER.debug("libraries: %s", [l.label for l in self.libraries])

    async def _drop(self) -> None:
        client, self._client = self._client, None
        self._keys = None
        self.connected = False
        if client is not None:
            try:
                await client.disconnect()
            except Exception:  # noqa: BLE001
                _LOGGER.debug("disconnect error ignored", exc_info=True)

    # ------------------------------------------------------------ idle logic
    def _arm_idle(self) -> None:
        if self._idle_handle:
            self._idle_handle.cancel()
            self._idle_handle = None
        if self.idle_timeout > 0 and self._client is not None and not self.loop_on and not self._thumbs_running() \
                and not self._effect_running() \
                and not self._playlist_running():
            loop = asyncio.get_running_loop()
            self._idle_handle = loop.call_later(
                self.idle_timeout, lambda: loop.create_task(self.disconnect()))

    async def disconnect(self) -> None:
        """Politely close the link so the phone app can connect again."""
        self._cancel_loop()
        self._cancel_playlist()
        self._cancel_thumbs()
        await self._stop_effect(restore=True)
        if self._idle_handle:
            self._idle_handle.cancel()
            self._idle_handle = None
        async with self._lock:
            if self._client is not None and self._keys is not None and self.connected:
                try:
                    self._pending = asyncio.get_running_loop().create_future()
                    await self._write(p.encrypt(
                        p.build_simple(p.FUNC_DISCONNECT_DEVICE, p.ACT_DISCONNECT), *self._keys))
                    await asyncio.wait_for(self._pending, 1.5)
                except Exception:  # noqa: BLE001
                    pass
                finally:
                    self._pending = None
                await asyncio.sleep(0.1)
            await self._drop()
        self._notify()

    # ------------------------------------------------------------ operations
    async def _run(self, op: Callable[[], Awaitable[Any]]) -> Any:
        async with self._lock:
            if self._idle_handle:
                self._idle_handle.cancel()
            last: Exception | None = None
            for _attempt in range(2):
                try:
                    await self._ensure()
                    result = await op()
                    self._arm_idle()
                    return result
                except CubeFatalError:
                    await self._drop()
                    raise
                except CubeUserError:
                    raise
                except Exception as err:  # noqa: BLE001
                    last = err
                    _LOGGER.debug("operation failed (%r), reconnecting", err)
                    await self._drop()
            raise CubeError(f"communication failed: {last!r}") from last

    async def async_connect(self, refresh: bool = False) -> None:
        """Connect and read settings; ``refresh`` forces a re-read from the device."""
        async def op() -> None:
            await self._load_settings(force=refresh)
            await self._load_library(force=refresh)
        await self._run(op)
        self._notify()

    async def async_set_laser(self, on: bool) -> None:
        async def op() -> None:
            await self._transfer(p.FUNC_MY_DEVICE, p.ACT_ENABLE_LASER_OUTPUT,
                                 p.build_enable_payload(on, self.run_mode))
        await self._run(op)
        self.laser_on = on
        if not on:
            self._cancel_loop()
            self._cancel_playlist()
        self._notify()

    async def async_set_run_mode(self, mode: int) -> None:
        if mode not in p.RUN_MODES:
            raise ValueError(f"invalid run mode {mode}")

        async def op() -> None:
            await self._transfer(p.FUNC_MY_DEVICE, p.ACT_ENABLE_LASER_OUTPUT,
                                 p.build_enable_payload(self.laser_on, mode))
            await self._transfer(p.FUNC_MY_DEVICE, p.ACT_SET_RUN_PARAMETERS,
                                 p.build_run_params(self.run_params))
        await self._run(op)
        self.run_mode = mode
        self._notify()

    async def async_set_params(self, **values: int) -> None:
        unknown = set(values) - set(p.DEFAULT_RUN_PARAMS)
        if unknown:
            raise ValueError(f"unknown parameters: {sorted(unknown)}")
        new = {**self.run_params, **{k: int(v) for k, v in values.items()}}

        async def op() -> None:
            await self._transfer(p.FUNC_MY_DEVICE, p.ACT_SET_RUN_PARAMETERS,
                                 p.build_run_params(new))
        await self._run(op)
        self.run_params = new
        self._notify()

    # ------------------------------------------------------- pattern playback
    @property
    def current_library(self) -> p.Library | None:
        if 0 <= self.selected_library < len(self.libraries):
            return self.libraries[self.selected_library]
        return None

    async def async_select_library(self, index: int) -> None:
        """Choose which library the pattern number refers to (no Bluetooth)."""
        if not 0 <= index < len(self.libraries):
            raise ValueError("unknown library")
        self.selected_library = index
        self.pattern_index = 1
        self.overview_page = None
        self._notify()

    async def async_play_index(self, n: int | None = None) -> None:
        """Play pattern ``n`` (1-based) of the selected library in APP mode."""
        lib = self.current_library
        if lib is None:
            raise CubeError("No pattern libraries loaded yet - press 'Read settings'")
        n = self.pattern_index if n is None else int(n)
        page, file = lib.item(n)

        async def op() -> None:
            # like the app: switch to "playing" state, then send the frame
            await self._transfer(p.FUNC_MY_DEVICE, p.ACT_ENABLE_LASER_OUTPUT,
                                 p.build_enable_payload(self.laser_on, p.RUN_MODE_APP,
                                                        p.PLAY_STATE_PLAY))
            await self._transfer(p.FUNC_PATTERN_LIBRARY, p.ACT_FRAME_PLAYING,
                                 p.build_frame_play(page, file))
        await self._run(op)
        self.run_mode = p.RUN_MODE_APP
        self.play_state = p.PLAY_STATE_PLAY
        self.pattern_index = n
        self.overview_page = None
        self.text_active = False
        self._notify()

    async def async_step(self, delta: int) -> None:
        lib = self.current_library
        if lib is None:
            raise CubeError("No pattern libraries loaded yet - press 'Read settings'")
        n = (self.pattern_index - 1 + delta) % lib.size + 1
        await self.async_play_index(n)

    async def async_set_play_state(self, state: int) -> None:
        async def op() -> None:
            await self._transfer(p.FUNC_MY_DEVICE, p.ACT_ENABLE_LASER_OUTPUT,
                                 p.build_enable_payload(self.laser_on, self.run_mode, state))
        await self._run(op)
        self.play_state = state
        self._notify()

    async def async_pause(self) -> None:
        await self.async_set_play_state(p.PLAY_STATE_PAUSE)

    async def async_stop(self) -> None:
        self._cancel_loop()
        self._cancel_playlist()
        self.text_active = False
        await self.async_set_play_state(p.PLAY_STATE_STOP)

    async def async_play(self) -> None:
        """Resume if paused, otherwise (re)play the current pattern."""
        if self.play_state == p.PLAY_STATE_PAUSE:
            await self.async_set_play_state(p.PLAY_STATE_PLAY)
        else:
            await self.async_play_index()

    async def async_set_pattern_color(self, index: int) -> None:
        if index not in p.PATTERN_COLORS:
            raise ValueError("invalid colour")
        value = self.flow_precision if index == 8 else index
        await self.async_set_params(runParaColorMode=value)

    async def async_set_flow(self, precision: int | None = None, speed: int | None = None) -> None:
        if precision is not None:
            self.flow_precision = int(precision)
        values: dict[str, int] = {}
        if speed is not None:
            values["runParaColorSpeed"] = int(speed)
        if self.run_params["runParaColorMode"] >= 8 and precision is not None:
            values["runParaColorMode"] = self.flow_precision
        if values:
            await self.async_set_params(**values)
        else:
            self._notify()

    # ----------------------------------------------------------- loop playback
    async def async_set_loop_mode(self, mode: int) -> None:
        if mode not in p.LOOP_MODES:
            raise ValueError("invalid loop mode")
        self.loop_mode = mode
        self._notify()

    async def async_set_loop_interval(self, seconds: float) -> None:
        self.loop_interval = max(1.0, float(seconds))
        self._notify()

    def _cancel_loop(self) -> None:
        task, self._loop_task = self._loop_task, None
        self.loop_on = False
        if task is not None and not task.done():
            task.cancel()

    async def async_set_loop(self, on: bool) -> None:
        if not on:
            self._cancel_loop()
            self._notify()
            return
        if self.current_library is None:
            raise CubeError("No pattern libraries loaded yet - press 'Read settings'")
        if self._loop_task is not None:
            return
        self._cancel_playlist()
        self.loop_on = True
        self._loop_task = asyncio.get_running_loop().create_task(self._loop_runner())
        self._notify()

    def _next_index(self, lib: p.Library) -> int | None:
        n = self.pattern_index
        if self.loop_mode == 0:
            return n % lib.size + 1
        if self.loop_mode == 1:
            return random.randint(1, lib.size)
        if self.loop_mode == 2:
            return n + 1 if n < lib.size else None
        return n  # single: repeat

    async def _loop_runner(self) -> None:
        me = asyncio.current_task()
        try:
            while self.loop_on:
                lib = self.current_library
                if lib is None:
                    break
                await self.async_play_index(self.pattern_index)
                await asyncio.sleep(self.loop_interval)
                nxt = self._next_index(lib)
                if nxt is None:
                    break
                self.pattern_index = nxt
        except asyncio.CancelledError:
            raise
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("loop playback stopped: %s", err)
        finally:
            if self._loop_task is me:
                self._loop_task = None
                self.loop_on = False
                self._arm_idle()
                self._notify()

    # ------------------------------------------------------------- thumbnails
    @staticmethod
    def thumb_key(page: int, file: int) -> str:
        return f"{page}_{file}"

    def get_thumb(self, lib: p.Library, n: int) -> list[int] | None:
        try:
            page, file = lib.item(n)
        except ValueError:
            return None
        return self.thumbs.get(self.thumb_key(page, file))

    def _thumbs_running(self) -> bool:
        return self._thumb_task is not None and not self._thumb_task.done()

    def _cancel_thumbs(self) -> None:
        task, self._thumb_task = self._thumb_task, None
        if task is not None and not task.done():
            task.cancel()

    def _thumbs_changed(self) -> None:
        self._notify()
        if self.on_thumbs_changed:
            self.on_thumbs_changed()

    async def _read_points(self, pat_lib: int, pat_idx: int) -> list[p.DevicePoint]:
        """Read all points of frame 1 of a pattern (several replies of <=80 points)."""
        points: list[p.DevicePoint] = []
        total = 0
        offset = 1  # the app starts at 1, then continues from the count so far
        for _ in range(64):
            resp = await self._transfer(p.FUNC_MY_DEVICE, p.ACT_SEARCH_PATTERN_LIB_DATA,
                                        p.build_pattern_read(pat_lib, pat_idx, 1, offset))
            header, chunk = p.parse_pattern_chunk(resp)
            total = header["pointTotal"]
            if points and chunk and chunk[0] == points[-1]:
                chunk = chunk[1:]          # replies overlap by one point
            if not chunk:
                break
            points.extend(chunk)
            if len(points) >= total:
                break
            offset = len(points)
        return points

    async def _read_item_points(self, lib: p.Library, n: int) -> list[p.DevicePoint]:
        page, file = lib.item(n)
        pat_lib, pat_idx = page, file
        if page in lib.effect_pages:
            resp = await self._transfer(p.FUNC_MY_DEVICE, p.ACT_SEARCH_EFFECT_LIB_DATA,
                                        p.build_effect_read(page, file))
            step = p.parse_effect_step(resp)
            pat_lib, pat_idx = step["pattern_lib"], step["pattern_index"]
            if pat_lib == 0:
                return []
        return await self._read_points(pat_lib, pat_idx)

    async def async_start_thumbnails(self, library_index: int | None = None, *,
                                     rebuild: bool = False) -> None:
        """Read the patterns of a library from the laser and cache thumbnails.

        Runs in the background; progress is in ``thumb_status``.
        """
        if self._thumbs_running():
            raise CubeUserError("Thumbnails are already being built")
        idx = self.selected_library if library_index is None else library_index
        if not 0 <= idx < len(self.libraries):
            raise CubeUserError("No pattern libraries loaded yet - press 'Read settings'")
        lib = self.libraries[idx]
        self._thumb_task = asyncio.get_running_loop().create_task(self._thumb_runner(lib, rebuild))
        self.thumb_status = f"{lib.name}: starting"
        self._notify()

    async def async_cancel_thumbnails(self) -> None:
        self._cancel_thumbs()
        self.thumb_status = "Cancelled"
        self._thumbs_changed()

    async def _thumb_runner(self, lib: p.Library, rebuild: bool) -> None:
        me = asyncio.current_task()
        failures = 0
        try:
            for n in range(1, lib.size + 1):
                page, file = lib.item(n)
                key = self.thumb_key(page, file)
                if key in self.thumbs and not rebuild:
                    continue

                async def op(n: int = n) -> list[p.DevicePoint]:
                    return await self._read_item_points(lib, n)
                try:
                    pts = await self._run(op)
                except CubeFatalError:
                    raise
                except Exception as err:  # noqa: BLE001
                    failures += 1
                    _LOGGER.warning("thumbnail %s #%d failed: %s", lib.name, n, err)
                    if failures >= 3:
                        raise CubeError("too many read errors") from err
                    continue
                flat: list[int] = []
                for x, y, state, rgb in pts:
                    flat += [x, y, state, rgb]
                self.thumbs[key] = flat
                self.thumb_status = f"{lib.name}: {n}/{lib.size}"
                if n % 4 == 0:
                    self._thumbs_changed()
            self.thumb_status = f"{lib.name}: done ({lib.size} patterns)"
        except asyncio.CancelledError:
            raise
        except Exception as err:  # noqa: BLE001
            self.thumb_status = f"{lib.name}: failed ({err})"
        finally:
            if self._thumb_task is me:
                self._thumb_task = None
                self._arm_idle()
            self._thumbs_changed()

    # -------------------------------------------------------------------- text
    def _text_frames(self, text: str, fmt: int) -> list[list[p.Path]]:
        strokes = stroke_font.layout(text[::-1] if self.text_reverse else text,
                                     vertical=self.text_orientation == 1)
        if not strokes:
            raise CubeUserError("Nothing to draw")
        palette = p.PALETTE
        paths: list[p.Path] = []
        for char_i, stroke in strokes:
            if self.text_color == 8:
                rgb = palette[p.RAINBOW[char_i % len(p.RAINBOW)]]
            elif self.text_color == 9:
                rgb = palette[1]                      # white; the laser's colour flow recolours it
            else:
                rgb = palette[self.text_color]
            paths.append([(x, y, rgb) for x, y in stroke])
        w = max(x for path in paths for x, _, _ in path)
        h = max(y for path in paths for _, y, _ in path)
        w, h = max(w, 0.5), max(h, 0.5)
        self._text_box = (w, h)
        scale = max(0.1, min(1.0, self.text_size / 100))
        vw, vh = w / scale, h / scale                 # virtual canvas the text sits centred in
        sx, sy = (vw - w) / 2, (vh - h) / 2
        paths = [[(x + sx, y + sy, rgb) for x, y, rgb in path] for path in paths]
        return [p.normalize_frame(paths, vw, vh)]

    async def _transfer_stream(self, packets: list[bytes]) -> None:
        """Send a multi-packet transfer, one reply per packet."""
        for packet in packets:
            for _ in range(3):
                resp = await self._exchange(packet)
                status = p.parse_status(resp, p.RSP_TRANSFER)
                if status == 1:      # resend this packet
                    continue
                if status in (0, 2, 3):
                    break
                raise p.DeviceStatusError(status)
            else:
                raise CubeError("device kept asking to resend")

    async def async_play_text(self, text: str | None = None) -> None:
        """Show ``text`` on the laser (real-time play, like the app's Text page)."""
        if text is not None:
            self.text = text
        message = self.text.strip("\n")
        if not message.strip():
            raise CubeUserError("Enter some text first")

        async def op() -> dict[str, int] | None:
            fmt = self.info.data_format
            if fmt not in (3, 4):
                raise CubeUserError(
                    f"This laser uses point data format {fmt}; text playback supports 3 and 4")
            frames = self._text_frames(message, fmt)
            count = sum(len(path) for frame in frames for path in frame)
            limit = p.max_points(self.info.scene_max, fmt) if self.info.scene_max else 2000
            if count > limit:
                raise CubeUserError(f"Text too long ({count} points, the laser accepts {limit})")
            data = p.encode_frames(frames, fmt)
            buffer_max = self.info.buffer_max or 244
            packets = p.split_transfer(p.FUNC_REAL_TIME_PLAY, p.ACT_PLAY_START, data, buffer_max,
                                       data_format=fmt, layers=len(frames), frame=0)
            _LOGGER.debug("text %r: %d points, %d bytes, %d packets", message, count, len(data), len(packets))
            await self._transfer(p.FUNC_MY_DEVICE, p.ACT_ENABLE_LASER_OUTPUT,
                                 p.build_enable_payload(self.laser_on, p.RUN_MODE_APP,
                                                        p.PLAY_STATE_PLAY))
            if self.text_effect:      # like the app: effect channel values first, then the points
                eff = self.text_effect
                payload = p.build_play_effect(eff["step"], int(eff["duration"] * 1000),
                                              eff["channels"], self.info.protocols)
                _LOGGER.debug("text effect: %s", payload.hex())
                await self._transfer_stream(p.split_transfer(
                    p.FUNC_REAL_TIME_PLAY, p.ACT_PLAY_EFFECT, payload, buffer_max,
                    data_format=fmt, layers=0, frame=eff["steps"]))
            await self._transfer_stream(packets)
            # "Color flow" text uses the laser's colour-flow mode; leave it again otherwise
            if self.text_color == 9 or self.text_flow_set:
                mode = self.flow_precision if self.text_color == 9 else 0
                params = {**self.run_params, "runParaColorMode": mode}
                await self._transfer(p.FUNC_MY_DEVICE, p.ACT_SET_RUN_PARAMETERS,
                                     p.build_run_params(params))
                return params
            return None
        params = await self._run(op)
        if params is not None:
            self.run_params = params
            self.text_flow_set = params["runParaColorMode"] != 0
        self.run_mode = p.RUN_MODE_APP
        self.play_state = p.PLAY_STATE_PLAY
        self.text_active = True
        self._cancel_loop()
        self._cancel_playlist()
        self._notify()

    async def async_send_effect(self, channels: list[int], step: int = 0, steps: int = 1,
                                duration: float = 5.0) -> None:
        """EXPERIMENTAL: attach raw effect channel values to the text.

        The official app makes text move by sending these channel values (a
        DMX-style list) to the laser's built-in effect engine before the text
        points. Their meaning is defined by the manufacturer's cloud, so this lets
        you experiment. An empty list removes the effect.
        """
        self.hw_effect = 0
        if not channels:
            self.text_effect = None
        else:
            if len(channels) > 64 or any(not 0 <= int(v) <= 255 for v in channels):
                raise CubeUserError("channels must be at most 64 values, each 0-255")
            self.text_effect = {"channels": [int(v) for v in channels], "step": int(step),
                                "steps": max(1, int(steps)), "duration": max(0.05, float(duration))}
        await self.async_play_text()

    async def async_clear_text(self) -> None:
        async def op() -> None:
            await self._transfer(p.FUNC_REAL_TIME_PLAY, p.ACT_CLEAR_PLAY_DATA, None)
        await self._run(op)
        self.text_active = False
        self._notify()

    async def async_set_text_color(self, index: int) -> None:
        if index not in p.TEXT_COLORS:
            raise ValueError("invalid text colour")
        self.text_color = index
        if self.text_active:
            await self.async_play_text()
        else:
            self._notify()

    async def async_set_text_orientation(self, index: int) -> None:
        if index not in p.TEXT_ORIENTATIONS:
            raise ValueError("invalid orientation")
        self.text_orientation = index
        await self._replay_text()

    async def async_set_text_direction(self, index: int) -> None:
        if index not in p.TEXT_DIRECTIONS:
            raise ValueError("invalid direction")
        self.text_reverse = index == 1
        await self._replay_text()

    async def _replay_text(self) -> None:
        if self.text_active:
            await self.async_play_text()
        else:
            self._notify()

    async def async_set_text_size(self, percent: float) -> None:
        self.text_size = int(max(10, min(100, percent)))
        if self.text_active:
            await self.async_play_text()
        else:
            self._notify()

    # ----------------------------------------------------------- overview pages
    def overview_pages(self) -> int:
        lib = self.current_library
        return max(1, -(-lib.size // p.OVERVIEW_PAGE)) if lib else 1

    def current_overview_page(self) -> int:
        if self.overview_page is not None:
            return min(max(1, self.overview_page), self.overview_pages())
        return (self.pattern_index - 1) // p.OVERVIEW_PAGE + 1

    async def async_set_overview_page(self, page: int) -> None:
        self.overview_page = min(max(1, int(page)), self.overview_pages())
        self._notify()

    async def async_overview_step(self, delta: int) -> None:
        pages = self.overview_pages()
        await self.async_set_overview_page((self.current_overview_page() - 1 + delta) % pages + 1)

    # --------------------------------------------------------- device settings
    async def async_set_device_settings(self, **values: int) -> None:
        """Change persistent device settings (the app's "Laser device settings").

        Like the app, this re-reads the whole settings block, changes the given
        fields and writes the block back.
        """
        clean: dict[str, int] = {}
        for key, value in values.items():
            limits = p.DEVICE_SETTING_LIMITS.get(key)
            if limits is None:
                raise CubeUserError(f"{key} cannot be changed")
            value = int(value)
            if not limits[0] <= value <= limits[1]:
                raise CubeUserError(f"{key} must be between {limits[0]} and {limits[1]}")
            if key == "deviceScannerRate" and value not in p.SCAN_SPEEDS:
                raise CubeUserError(f"scan speed must be one of {p.SCAN_SPEEDS}")
            clean[key] = value

        async def op() -> dict[str, int]:
            resp = await self._transfer(p.FUNC_MY_DEVICE, p.ACT_SEARCH_DEVICE_SET_MODEL)
            try:
                model = p.parse_fields(p.parse_data_response(resp), p.DEVICE_MODEL_FIELDS,
                                       required="deviceframeRate")
            except p.ProtocolError as err:
                raise CubeUserError(
                    f"The laser returned an incomplete settings block ({err}); not writing") from err
            model.update(clean)
            await self._transfer(p.FUNC_MY_DEVICE, p.ACT_DEVICE_SET_MODEL, p.build_device_model(model))
            return model
        model = await self._run(op)
        self.device_model = {**self.device_model, **model}
        _LOGGER.info("device settings changed: %s", clean)
        self._notify()

    # ----------------------------------------------------------------- effects
    def _effect_running(self) -> bool:
        return self._effect_task is not None and not self._effect_task.done()

    def _effect_period(self) -> float:
        """Seconds per cycle: 30 s at speed 1 down to 1.5 s at speed 100."""
        speed = min(100, max(1, self.effect_speed))
        return 30.0 * (1.5 / 30.0) ** ((speed - 1) / 99)

    def _content_fractions(self) -> tuple[float, float]:
        """Width/height of what is shown, as a fraction of the laser's field."""
        sx = self.run_params["runsizeX"] / 100
        sy = self.run_params["runsizeY"] / 100
        if self.text_active and self._text_box:
            w, h = self._text_box
            k = self.text_size / 100 / max(w, h)
            return sx * k * w, sy * k * h
        return sx, sy

    def _effect_params(self, effect: int, phase: float) -> dict[str, int]:
        """Run parameters for one animation frame (never moves content out of the field)."""
        params = dict(self.run_params)
        wf, hf = self._content_fractions()
        tri = 1 - abs(2 * phase - 1)
        if effect in (1, 2, 3):
            amp = max(0.0, 1 - wf) * 127.5
            pos = {1: phase, 2: 1 - phase, 3: tri}[effect]
            params["runPositionX"] = round(127.5 - amp + pos * 2 * amp)
        elif effect == 4:
            amp = max(0.0, 1 - hf) * 127.5
            params["runPositionY"] = round(127.5 - amp + tri * 2 * amp)
        elif effect == 5:
            params["runRotateZ"] = int(phase * 360) % 360
            radius = math.hypot(wf, hf)
            if radius > 1:                       # keep the rotating content inside the field
                for key in ("runsizeX", "runsizeY"):
                    params[key] = max(10, int(params[key] / radius))
        elif effect == 6:
            factor = 0.3 + 0.7 * tri
            for key in ("runsizeX", "runsizeY"):
                params[key] = max(10, round(params[key] * factor))
        return params

    async def _send_run_params(self, params: dict[str, int]) -> None:
        async def op() -> None:
            await self._transfer(p.FUNC_MY_DEVICE, p.ACT_SET_RUN_PARAMETERS,
                                 p.build_run_params(params))
        await self._run(op)

    async def _stop_effect(self, restore: bool) -> None:
        task, self._effect_task = self._effect_task, None
        was = self.effect != 0
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        if was:
            self.effect = 0
            if restore:
                try:
                    await self._send_run_params(self.run_params)   # put the output back
                except Exception:  # noqa: BLE001
                    _LOGGER.debug("could not restore parameters after effect", exc_info=True)
            self._notify()

    async def async_set_effect(self, effect: int) -> None:
        if effect not in p.EFFECTS:
            raise ValueError("invalid effect")
        await self._stop_effect(restore=True)
        if effect == 0:
            self._notify()
            return
        wf, hf = self._content_fractions()
        if (effect in (1, 2, 3) and wf > 0.97) or (effect == 4 and hf > 0.97):
            raise CubeUserError(
                "There is no room to move: reduce 'Text size' (or Size X/Y) first, "
                "e.g. to 50 %, so the content can travel across the field.")
        self.effect = effect
        self._effect_task = asyncio.get_running_loop().create_task(self._effect_runner())
        self._notify()

    async def async_set_effect_speed(self, speed: float) -> None:
        self.effect_speed = int(min(100, max(1, speed)))
        self._notify()

    async def _effect_runner(self) -> None:
        me = asyncio.current_task()
        loop = asyncio.get_running_loop()
        t0 = loop.time()
        try:
            while self.effect:
                phase = ((loop.time() - t0) / self._effect_period()) % 1.0
                await self._send_run_params(self._effect_params(self.effect, phase))
                await asyncio.sleep(0.02)
        except asyncio.CancelledError:
            raise
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("effect stopped: %s", err)
        finally:
            if self._effect_task is me:
                self._effect_task = None
                self.effect = 0
                self._arm_idle()
                self._notify()

    # -------------------------------------------------------- hardware effects
    def effect_layout_base(self) -> int:
        try:
            return p.effect_layout_base(self.hw_layout, self.device_model.get("deviceSCEChannleTotal"))
        except p.ProtocolError as err:
            raise CubeUserError(str(err)) from err

    def _apply_hw_effect(self) -> None:
        if self.hw_effect == 0:
            self.text_effect = None
            return
        try:
            channels = p.hw_effect_channels(
                self.hw_effect, self.hw_speed, self.effect_layout_base(),
                flow_zones=self.flow_precision, flow_speed=self.run_params["runParaColorSpeed"])
        except p.ProtocolError as err:
            raise CubeUserError(str(err)) from err
        # a long dwell so the single step keeps running (the time unit is 50 ms, max ~54 min)
        self.text_effect = {"channels": channels, "step": 0, "steps": 1, "duration": 3000.0}

    async def async_set_hw_effect(self, effect: int) -> None:
        """Run a laser-side effect on the text (smooth, timed by the laser itself)."""
        if effect not in p.HW_EFFECTS:
            raise ValueError("invalid hardware effect")
        if not self.settings_loaded:
            await self.async_connect()        # the layout depends on the laser's channel counts
        previous = (self.hw_effect, self.text_effect)
        self.hw_effect = effect
        try:
            self._apply_hw_effect()
            await self.async_play_text()
        except Exception:
            self.hw_effect, self.text_effect = previous
            raise

    async def async_set_hw_speed(self, speed: float) -> None:
        self.hw_speed = int(min(127, max(1, speed)))
        if self.hw_effect:
            await self.async_set_hw_effect(self.hw_effect)
        else:
            self._notify()

    async def async_set_hw_layout(self, layout: int) -> None:
        if layout not in p.HW_LAYOUTS:
            raise ValueError("invalid layout")
        self.hw_layout = layout
        if self.hw_effect:
            await self.async_set_hw_effect(self.hw_effect)
        else:
            self._notify()

    # ---------------------------------------------------------------- playlists
    def current_playlist(self) -> list[dict] | None:
        return self.playlists.get(self.playlist_name) if self.playlist_name else None

    def playlist_total_seconds(self, name: str | None = None) -> float:
        items = self.playlists.get(name or self.playlist_name or "", [])
        return sum(i["seconds"] for i in items)

    def find_library(self, text: str | None) -> p.Library | None:
        """Library by key, label or name (case-insensitive); None = the selected one."""
        if text is None:
            return self.current_library
        t = text.strip().lower()
        for lib in self.libraries:
            if t in (lib.key.lower(), lib.label.lower(), lib.name.lower()):
                return lib
        return None

    def _playlists_changed(self) -> None:
        self._notify()
        if self.on_playlists_changed:
            self.on_playlists_changed()

    def _playlist_for(self, name: str | None) -> tuple[str, list[dict]]:
        name = name or self.playlist_name
        if not name or name not in self.playlists:
            raise CubeUserError("There is no playlist yet - press 'New playlist'")
        return name, self.playlists[name]

    async def async_playlist_create(self, name: str | None = None) -> None:
        if not name:
            n = 1
            while f"Playlist {n}" in self.playlists:
                n += 1
            name = f"Playlist {n}"
        name = name.strip()
        if name in self.playlists:
            raise CubeUserError(f"A playlist called '{name}' already exists")
        self.playlists[name] = []
        self.playlist_name = name
        self._playlists_changed()

    async def async_playlist_delete(self, name: str | None = None) -> None:
        name, _ = self._playlist_for(name)
        if self.playlist_on and name == self.playlist_name:
            self._cancel_playlist()
        del self.playlists[name]
        if self.playlist_name == name:
            self.playlist_name = next(iter(self.playlists), None)
        self._playlists_changed()

    async def async_playlist_select(self, name: str) -> None:
        if name not in self.playlists:
            raise CubeUserError(f"Unknown playlist '{name}'")
        if self.playlist_on:
            self._cancel_playlist()
        self.playlist_name = name
        self._playlists_changed()

    async def async_playlist_add(self, library: str | None = None, pattern: int | None = None,
                                 seconds: float | None = None, playlist: str | None = None,
                                 position: int | None = None) -> None:
        """Add a pattern (default: the one currently selected) with its on-time."""
        if not self.playlists and not playlist:
            await self.async_playlist_create()
        elif playlist and playlist not in self.playlists:
            await self.async_playlist_create(playlist)
        name, items = self._playlist_for(playlist)
        lib = self.find_library(library)
        if lib is None:
            raise CubeUserError(f"Unknown library {library!r}" if library else
                                "No pattern libraries loaded yet - press 'Read settings'")
        n = self.pattern_index if pattern is None else int(pattern)
        if not 1 <= n <= lib.size:
            raise CubeUserError(f"{lib.name} has patterns 1-{lib.size}")
        secs = float(self.playlist_seconds if seconds is None else seconds)
        if not 0.5 <= secs <= 3600:
            raise CubeUserError("duration must be between 0.5 and 3600 seconds")
        item = {"lib": lib.key, "name": lib.name, "n": n, "seconds": secs}
        if position is None or not 1 <= int(position) <= len(items):
            items.append(item)
        else:
            items.insert(int(position) - 1, item)
        self._playlists_changed()

    async def async_playlist_remove(self, index: int | None = None, playlist: str | None = None) -> None:
        name, items = self._playlist_for(playlist)
        if not items:
            raise CubeUserError("The playlist is empty")
        i = len(items) if index is None else int(index)
        if not 1 <= i <= len(items):
            raise CubeUserError(f"The playlist has items 1-{len(items)}")
        del items[i - 1]
        self._playlists_changed()

    async def async_playlist_clear(self, playlist: str | None = None) -> None:
        name, items = self._playlist_for(playlist)
        if self.playlist_on and name == self.playlist_name:
            self._cancel_playlist()
        items.clear()
        self._playlists_changed()

    async def async_playlist_set_duration(self, index: int, seconds: float,
                                          playlist: str | None = None) -> None:
        _, items = self._playlist_for(playlist)
        if not 1 <= int(index) <= len(items):
            raise CubeUserError(f"The playlist has items 1-{len(items)}")
        if not 0.5 <= float(seconds) <= 3600:
            raise CubeUserError("duration must be between 0.5 and 3600 seconds")
        items[int(index) - 1]["seconds"] = float(seconds)
        self._playlists_changed()

    async def async_playlist_move(self, index: int, to: int, playlist: str | None = None) -> None:
        _, items = self._playlist_for(playlist)
        if not (1 <= int(index) <= len(items) and 1 <= int(to) <= len(items)):
            raise CubeUserError(f"The playlist has items 1-{len(items)}")
        items.insert(int(to) - 1, items.pop(int(index) - 1))
        self._playlists_changed()

    async def async_set_playlist_seconds(self, seconds: float) -> None:
        self.playlist_seconds = min(3600.0, max(0.5, float(seconds)))
        self._notify()

    async def async_set_playlist_repeat(self, repeat: bool) -> None:
        self.playlist_repeat = bool(repeat)
        self._notify()

    def _playlist_running(self) -> bool:
        return self._playlist_task is not None and not self._playlist_task.done()

    def _cancel_playlist(self) -> None:
        task, self._playlist_task = self._playlist_task, None
        self.playlist_on = False
        if task is not None and not task.done():
            task.cancel()

    async def async_playlist_play(self, repeat: bool | None = None) -> None:
        """Play the active playlist: each pattern for its own time."""
        if repeat is not None:
            self.playlist_repeat = bool(repeat)
        name, items = self._playlist_for(None)
        if not items:
            raise CubeUserError("The playlist is empty - add patterns first")
        if not self.libraries:
            raise CubeUserError("No pattern libraries loaded yet - press 'Read settings'")
        self._cancel_loop()
        self._cancel_playlist()
        self.playlist_on = True
        self._playlist_task = asyncio.get_running_loop().create_task(self._playlist_runner(name))
        self._notify()

    async def async_playlist_stop(self) -> None:
        self._cancel_playlist()
        self._notify()

    async def _playlist_runner(self, name: str) -> None:
        me = asyncio.current_task()
        try:
            while True:
                played = 0
                i = 0
                while True:
                    items = self.playlists.get(name) or []     # re-read: edits apply immediately
                    if i >= len(items):
                        break
                    item = items[i]
                    lib = self.find_library(item["lib"])
                    if lib is None or not 1 <= item["n"] <= lib.size:
                        _LOGGER.warning("playlist item %d (%s %s) is not available on this laser; skipped",
                                        i + 1, item.get("name"), item.get("n"))
                        i += 1
                        continue
                    self.playlist_index = i
                    await self.async_select_library(self.libraries.index(lib))
                    await self.async_play_index(item["n"])
                    played += 1
                    await asyncio.sleep(item["seconds"])
                    i += 1
                if not self.playlist_repeat or played == 0:
                    break
        except asyncio.CancelledError:
            raise
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("playlist stopped: %s", err)
        finally:
            if self._playlist_task is me:
                self._playlist_task = None
                self.playlist_on = False
                self._arm_idle()
                self._notify()
