"""BLE session layer: connect, handshake, commands, idle disconnect.

Deliberately free of Home Assistant imports. The caller supplies a coroutine
``connect(disconnected_callback)`` returning a connected bleak-like client.
"""
from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import Awaitable, Callable
from typing import Any

from . import protocol as p

_LOGGER = logging.getLogger(__name__)

RESPONSE_TIMEOUT = 5.0
HANDSHAKE_DELAY = 1.0  # the official app waits 1 s after connecting


class CubeError(Exception):
    """Communication failure (retryable)."""


class CubeFatalError(CubeError):
    """Problem that retrying will not fix."""


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
        if self.idle_timeout > 0 and self._client is not None and not self.loop_on:
            loop = asyncio.get_running_loop()
            self._idle_handle = loop.call_later(
                self.idle_timeout, lambda: loop.create_task(self.disconnect()))

    async def disconnect(self) -> None:
        """Politely close the link so the phone app can connect again."""
        self._cancel_loop()
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
