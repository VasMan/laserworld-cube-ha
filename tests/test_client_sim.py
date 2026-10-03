"""End-to-end test of CubeLink against a simulated device.

The simulator implements the device side using the same crypto/framing that
test_protocol.py verified against the official app, so this checks the session
logic (handshake, key switch, binding, commands, retry), not the wire format.
"""
import asyncio
import importlib.util
import pathlib
import sys
import types

ROOT = pathlib.Path(__file__).parent.parent / "custom_components" / "laserworld_cube"
pkg = types.ModuleType("lcube")
pkg.__path__ = [str(ROOT)]
sys.modules["lcube"] = pkg
for name in ("protocol", "client"):
    spec = importlib.util.spec_from_file_location(f"lcube.{name}", ROOT / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[f"lcube.{name}"] = mod
    spec.loader.exec_module(mod)
p = sys.modules["lcube.protocol"]
c = sys.modules["lcube.client"]
c.HANDSHAKE_DELAY = 0.0
NAME = "BLEAPP_847E"


def s32(text):
    return text.encode().ljust(32, b"\x00")


def sim_points(page, file):
    """Deterministic device-format points for a pattern (more than one 80-point reply)."""
    import math
    n = 100 + file
    pts = []
    for i in range(n):
        a = 2 * math.pi * i / n
        rgb = (255, 0, 0) if (i // 7) % 2 == 0 else (0, 0, 0)
        pts.append((int(127 + 100 * math.cos(a)) // 2 * 2, int(127 + 100 * math.sin(a)) // 2 * 2,
                    64 if i == 0 else 0, (rgb[0] << 16) | (rgb[1] << 8) | rgb[2]))
    return pts


def stored_chunk(page, file, offset, pts):
    body = b""
    for x, y, state, rgb in pts[offset - 1: offset - 1 + 80]:
        idx = p.PALETTE_INDEX[((rgb >> 16) & 255, (rgb >> 8) & 255, rgb & 255)]
        body += bytes([(x // 2) | (128 if state & 128 else 0), (y // 2) | (128 if state & 64 else 0), idx])
    hdr = bytes([page, 40, file]) + (1).to_bytes(2, "big") + (1).to_bytes(2, "big") + bytes([3]) \
        + len(pts).to_bytes(2, "big") + offset.to_bytes(2, "big")
    return bytes([0x85, 0x12, 0x34, 0, 0xAA, 0x55, 15 + len(body)]) + hdr + body


class FakeDevice:
    def __init__(self, activate=1, bind_en=255, bind_user=0, fw=(2, 1, 0), drop_first=False, model_len=None, fmt=3):
        self.activate, self.bind_en, self.bind_user, self.fw = activate, bind_en, bind_user, fw
        self.device_key, self.device_secret, self.product_key = "DEVKEY-0123456789abcdef", "SECRET-9876543210fedcba", "PROD-KEY-1"
        self.keys = p.default_key_iv(NAME)
        self.received = []          # (function, action, data)
        self.drop_next = drop_first
        self.model_len = model_len
        self.fmt = fmt
        self.stream = None           # reassembly of multi-packet transfers
        self.realtime = []           # (layers, frame, data) from REAL_TIME_PLAY/PLAY_START
        self.cleared = 0
        self.model_writes = []      # raw DEVICE_SET_MODEL payloads
        self.frames = []            # (page, file) received via PATTERN_LIBRARY/FRAME_PLAYING
        self.enables = []           # raw ENABLE_LASER_OUTPUT payloads
        # (page, delete, step_total, file_total, files_number, merge)
        self.catalog = [(1, 0x00, 0, 8, 5, 0), (2, 0x00, 0, 16, 4, 0), (3, 0x00, 0, 36, 3, 0),
                        (4, 0x00, 0, 20, 6, 0), (5, 0x00, 0, 64, 7, 0), (6, 0x00, 0, 64, 7, 0),
                        (7, 0x40, 10, 5, 2, 0)]
        self.laser = None
        self.model = dict(deviceScannerRate=25, deviceSizeX=80, deviceSizeY=70, deviceSizeXY=0,
                          devicePositionX=100, devicePositionY=140, deviceInvertX=0, deviceInvertY=0,
                          deviceSwapXY=0, deviceColorFunc=5, deviceLaserType=0, deviceMasterFunc=0,
                          deviceChannelMode=1, deviceAddress=17, deviceRunWorkMode=1, deviceAutospeed=33,
                          deviceMusicDb=44, devicesafety=0, deviceRedMax=100, deviceGreenMax=100,
                          deviceBlueMax=100, devicecontrollerFunc=0, deviceStdChannleTotal=8,
                          deviceProChannleTotal=16, deviceSCEChannleTotal=0, deviceWhiteMax=100,
                          deviceRotateZ=90, deviceZoneX=100, deviceZoneY=100, deviceframeRate=60, reserve=0)

    def handshake_response(self):
        extras = (b"\0" * 12 + bytes([1, 0, 5]) + b"\0" + p.APP_COMPANY.encode().ljust(16, b"\0")
                  + bytes([1, 0, 1]) + b"\0" + bytes([1, 0, 0]) + b"\0\0\0\0" + b"\0")
        body = (bytes([0x8B, 0x12, 0x34, 0]) + (244).to_bytes(2, "big") + (36).to_bytes(2, "big")
                + bytes([1, 2, 0]) + bytes(self.fw) + bytes([self.fmt, 8, self.activate])
                + s32(self.device_key) + s32(self.device_secret) + s32(self.product_key)
                + (7).to_bytes(4, "big") + (1).to_bytes(2, "big") + (2).to_bytes(2, "big") + b"\0" + bytes([44])
                + extras)
        return body

    def data_rsp(self, payload):
        return bytes([0x85, 0x12, 0x34, 0, 0xAA, 0x55, len(payload) & 0xFF]) + payload

    def _finish_stream(self):
        st = self.stream
        if len(st["data"]) >= st["total"]:
            if st["func"] == 2 and st["action"] == p.ACT_PLAY_START:
                self.realtime.append((st["layers"], st["frame"], st["data"][:st["total"]]))
            self.stream = None

    def handle(self, plain):
        cmd = plain[0]
        if cmd == p.CMD_HANDSHAKE:
            out = p.encrypt(self.handshake_response(), *self.keys)
            self.keys = p.session_key_iv(self.activate, self.product_key, self.device_key, self.device_secret) \
                if self.activate in (0, 1) else self.keys
            return out
        if cmd == p.CMD_SIMPLE:
            self.received.append(("simple", plain[5], plain[6]))
            return p.encrypt(bytes([0x8A, 0x12, 0x34, 0]), *self.keys)
        if cmd == p.CMD_TRANSFER_CONT:
            n = int.from_bytes(plain[3:5], "big")
            self.stream["data"] += plain[5:5 + n]
            self._finish_stream()
            return p.encrypt(bytes([0x85, 0x12, 0x34, 0]), *self.keys)
        assert cmd == p.CMD_TRANSFER, hex(cmd)
        func, action = plain[5], plain[6]
        dlen = int.from_bytes(plain[9:13], "big")
        layers, frame_b = plain[13], plain[14]
        n = int.from_bytes(plain[3:5], "big")
        data = plain[47:5 + n]
        self.received.append((func, action, data))
        if dlen > len(data):                      # more packets follow
            self.stream = {"func": func, "action": action, "total": dlen, "data": bytes(data),
                           "layers": layers, "frame": frame_b}
            return p.encrypt(bytes([0x85, 0x12, 0x34, 0]), *self.keys)
        data = data[:dlen]
        if func == 2 and action == p.ACT_PLAY_START:
            self.realtime.append((layers, frame_b, bytes(data)))
        if func == 2 and action == p.ACT_CLEAR_PLAY_DATA:
            self.cleared += 1
        if func == 1 and action == p.ACT_DEVICE_SET_MODEL:
            self.model_writes.append(bytes(data))
            self.model.update(p.parse_fields(data, p.DEVICE_MODEL_FIELDS))
        if func == 1 and action == p.ACT_SEARCH_PATTERN_LIB_DATA:
            off = int.from_bytes(data[10:12], "big")
            return p.encrypt(stored_chunk(data[0], data[2], off, sim_points(data[0], data[2])), *self.keys)
        if func == 1 and action == p.ACT_SEARCH_EFFECT_LIB_DATA:
            page, step = data[0], data[2]
            rsp = bytes([0x85, 0x12, 0x34, 0, 0xAA, 0x55, 18, page, 10, step, 6, 66, 1, 3, step, 0, 0, 0])
            return p.encrypt(rsp, *self.keys)
        if func == 1 and action == p.ACT_SEARCH_LIB_FILE:
            page, delete, step, files, num, merge = self.catalog[data[0] - 1]
            entry = bytes([page]) + b"\x00\x00\x00\x2a" + bytes([delete]) + (1234).to_bytes(4, "big") \
                + bytes([step, files, num, merge, 8, 0])
            rsp = self.data_rsp(bytes([len(self.catalog)]) + entry)
        elif func == 3 and action == p.ACT_FRAME_PLAYING:
            self.frames.append((data[0], data[1]))
            rsp = bytes([0x85, 0x12, 0x34, 0])
        elif func == 1 and action == p.ACT_GET_DEVICE_BIND_INFO:
            rsp = self.data_rsp(bytes([self.bind_en]) + b"\x12\x34" + b"\x00" + self.bind_user.to_bytes(4, "big"))
        elif action == p.ACT_SEARCH_DEVICE_SET_MODEL:
            blob = b"".join(self.model[n].to_bytes(s, "big") for n, s in p.DEVICE_MODEL_FIELDS)
            rsp = self.data_rsp(blob[:self.model_len] if self.model_len else blob)
        else:
            if func == 1 and action == p.ACT_ENABLE_LASER_OUTPUT:
                self.laser = (data[0], data[1])
                self.enables.append(bytes(data))
            rsp = bytes([0x85, 0x12, 0x34, 0])
        return p.encrypt(rsp, *self.keys)


class FakeClient:
    def __init__(self, dev, on_disc):
        self.dev, self.on_disc, self.is_connected, self.cb = dev, on_disc, True, None
        dev.keys = p.default_key_iv(NAME)   # every new connection starts with the handshake key
        self.mtu_size = 247
        self.writes = 0
        self.services = types.SimpleNamespace(get_characteristic=lambda u: types.SimpleNamespace(properties=["write", "notify"]))

    async def start_notify(self, uuid, cb):
        self.cb = cb

    async def write_gatt_char(self, uuid, data, response=True):
        self.writes += 1
        if self.dev.drop_next:          # simulate a lost response once
            self.dev.drop_next = False
            self.dev.keys = p.default_key_iv(NAME)
            return
        plain = p.decrypt(bytes(data), *self.dev.keys)
        asyncio.get_running_loop().call_soon(self.cb, None, bytearray(self.dev.handle(plain)))

    async def disconnect(self):
        self.is_connected = False


def make_link(dev, **kw):
    state = {"connects": 0}

    async def connect(cb):
        state["connects"] += 1
        return FakeClient(dev, cb)
    link = c.CubeLink(NAME, connect, **kw)
    link.state = state
    return link


def test_happy_path_and_commands():
    async def go():
        dev = FakeDevice()
        link = make_link(dev, idle_timeout=0)
        await link.async_set_laser(True)
        assert dev.laser == (1, 0) and link.laser_on
        # settings were read from the device on connect
        assert link.run_params["runAutoSpeed"] == 33 and link.run_params["runsizeX"] == 80
        assert link.run_params["runColorMode"] == 5 and link.run_params["runRotateZ"] == 90
        await link.async_set_params(runsizeX=55, runPositionY=10)
        func, action, data = dev.received[-1]
        assert action == p.ACT_SET_RUN_PARAMETERS and len(data) == 38
        sent = p.parse_fields(data, p.RUN_PARAM_FIELDS)
        assert sent["runsizeX"] == 55 and sent["runPositionY"] == 10 and sent["runAutoSpeed"] == 33
        await link.async_set_run_mode(p.RUN_MODE_ILDA)
        assert dev.laser == (1, 4)
        await link.disconnect()
        assert ("simple", 7, 1) in dev.received and not link.connected
        assert link.state["connects"] == 1
    asyncio.run(go())


def test_not_activated_is_fatal():
    async def go():
        link = make_link(FakeDevice(activate=0), idle_timeout=0)
        try:
            await link.async_set_laser(True)
        except c.NotActivated:
            return
        raise AssertionError("expected NotActivated")
    asyncio.run(go())


def test_bound_to_other_account_refused_then_allowed_with_user_id():
    async def go():
        dev = FakeDevice(bind_en=1, bind_user=4242)
        try:
            await make_link(dev, idle_timeout=0).async_set_laser(True)
            raise AssertionError("expected DeviceBound")
        except c.DeviceBound:
            assert dev.laser is None            # nothing was sent
        link = make_link(FakeDevice(bind_en=1, bind_user=4242), idle_timeout=0, user_id=4242)
        await link.async_set_laser(True)
        assert link.laser_on
    asyncio.run(go())


def test_retry_after_lost_response():
    async def go():
        c.RESPONSE_TIMEOUT = 0.3
        dev = FakeDevice(drop_first=True)
        link = make_link(dev, idle_timeout=0)
        await link.async_set_laser(True)          # 1st handshake lost -> reconnect -> ok
        assert link.state["connects"] == 2 and link.laser_on
    asyncio.run(go())


def test_idle_disconnect():
    async def go():
        link = make_link(FakeDevice(), idle_timeout=0.2)
        await link.async_set_laser(True)
        assert link.connected
        await asyncio.sleep(0.6)
        assert not link.connected
        await link.async_set_laser(False)         # reconnects transparently
        assert link.state["connects"] == 2
        await link.disconnect()
    asyncio.run(go())


def test_short_settings_reply_like_real_device():
    """Real V217 firmware sends fewer 'reserve' bytes than the app's struct."""
    async def go():
        dev = FakeDevice(model_len=38 + 10)          # only 10 of 96 reserve bytes
        link = make_link(dev, idle_timeout=0)
        await link.async_set_laser(True)
        assert link.settings_loaded and link.run_params["runsizeX"] == 80
        await link.disconnect()
        # far too short (garbage) is still rejected, defaults kept
        dev2 = FakeDevice(model_len=8)
        link2 = make_link(dev2, idle_timeout=0)
        await link2.async_set_laser(True)
        assert not link2.settings_loaded and link2.run_params["runsizeX"] == 100
        await link2.disconnect()
    asyncio.run(go())


def test_reconnect_does_not_overwrite_ha_values():
    async def go():
        dev = FakeDevice()
        link = make_link(dev, idle_timeout=0.2)
        await link.async_set_laser(True)
        await link.async_set_params(runsizeX=33)
        await asyncio.sleep(0.6)                      # idle disconnect
        assert not link.connected
        await link.async_set_params(runPositionX=7)   # reconnects
        assert link.run_params["runsizeX"] == 33      # not reset to device's 80
        sent = p.parse_fields(dev.received[-1][2], p.RUN_PARAM_FIELDS)
        assert sent["runsizeX"] == 33 and sent["runPositionX"] == 7
        await link.async_connect(refresh=True)        # explicit refresh re-reads
        assert link.run_params["runsizeX"] == 80
        await link.disconnect()
    asyncio.run(go())


def test_libraries_built_from_device_catalog():
    async def go():
        link = make_link(FakeDevice(), idle_timeout=0)
        await link.async_connect()
        labels = [l.label for l in link.libraries]
        assert labels == ["Timetunnel (8)", "Northlight (16)", "Animation (36)", "Outdoors (20)",
                          "Hotspot (128)", "Mixture (10)"], labels   # two pages merged into Hotspot
        hot = link.libraries[4]
        assert hot.item(1) == (5, 1) and hot.item(64) == (5, 64) and hot.item(65) == (6, 1) and hot.item(128) == (6, 64)
        await link.disconnect()
    asyncio.run(go())


def test_play_pattern_sends_state_then_frame():
    async def go():
        dev = FakeDevice()
        link = make_link(dev, idle_timeout=0)
        await link.async_set_laser(True)
        await link.async_select_library(4)                  # Hotspot
        await link.async_play_index(70)
        assert dev.frames[-1] == (6, 6)
        assert dev.enables[-1] == bytes([1, 0, 0])          # laser on, APP mode, state PLAY
        await link.async_pause();  assert dev.enables[-1] == bytes([1, 0, 1])
        await link.async_play();   assert dev.enables[-1] == bytes([1, 0, 0])   # resume only
        n = len(dev.frames)
        await link.async_stop();   assert dev.enables[-1] == bytes([1, 0, 2]) and len(dev.frames) == n
        await link.async_play_index(128)
        await link.async_step(1)                            # wraps to 1
        assert dev.frames[-1] == (5, 1) and link.pattern_index == 1
        await link.async_step(-1)                           # back to 128
        assert dev.frames[-1] == (6, 64)
        await link.disconnect()
    asyncio.run(go())


def test_play_forces_app_mode_and_respects_laser_off():
    async def go():
        dev = FakeDevice()
        link = make_link(dev, idle_timeout=0)
        await link.async_set_run_mode(p.RUN_MODE_ILDA)
        await link.async_play_index(1)
        assert dev.enables[-1] == bytes([0, 0, 0]) and link.run_mode == p.RUN_MODE_APP   # laser stays OFF
        await link.disconnect()
    asyncio.run(go())


def test_pattern_color_and_flow():
    async def go():
        dev = FakeDevice()
        link = make_link(dev, idle_timeout=0)
        await link.async_set_pattern_color(3)
        sent = p.parse_fields(dev.received[-1][2], p.RUN_PARAM_FIELDS)
        assert sent["runParaColorMode"] == 3
        await link.async_set_flow(precision=20)
        await link.async_set_pattern_color(8)
        assert p.parse_fields(dev.received[-1][2], p.RUN_PARAM_FIELDS)["runParaColorMode"] == 20
        await link.async_set_flow(speed=40)
        sent = p.parse_fields(dev.received[-1][2], p.RUN_PARAM_FIELDS)
        assert sent["runParaColorSpeed"] == 40 and sent["runParaColorMode"] == 20
        await link.disconnect()
    asyncio.run(go())


def test_loop_modes():
    async def go():
        dev = FakeDevice()
        link = make_link(dev, idle_timeout=0.1)               # idle timer must not interfere with the loop
        await link.async_set_laser(True)
        await link.async_select_library(0)                    # Timetunnel (8)
        await link.async_set_loop_interval(1)
        link.loop_interval = 0.05                             # speed up the test
        await link.async_set_loop_mode(0)
        await link.async_set_loop(True)
        await asyncio.sleep(0.6)
        assert link.loop_on and link.connected
        seen = [f for f in dev.frames]
        assert len(seen) >= 4 and seen[:4] == [(1, 1), (1, 2), (1, 3), (1, 4)], seen
        await link.async_set_loop(False)
        n = len(dev.frames); await asyncio.sleep(0.2); assert len(dev.frames) == n
        # sequence mode stops at the end
        await link.async_select_library(0); link.pattern_index = 6
        await link.async_set_loop_mode(2); await link.async_set_loop(True)
        await asyncio.sleep(0.5)
        assert not link.loop_on and dev.frames[-1] == (1, 8)
        # single mode repeats the same pattern
        link.pattern_index = 3; await link.async_set_loop_mode(3); await link.async_set_loop(True)
        await asyncio.sleep(0.4); await link.async_set_loop(False)
        assert set(dev.frames[-3:]) == {(1, 3)}
        # turning the laser off ends a loop
        await link.async_set_loop_mode(1); await link.async_set_loop(True)
        await link.async_set_laser(False)
        assert not link.loop_on
        await link.disconnect()
    asyncio.run(go())


async def _wait_thumbs(link, timeout=10):
    for _ in range(int(timeout / 0.02)):
        if not link._thumbs_running():
            return
        await asyncio.sleep(0.02)
    raise AssertionError("thumbnail build did not finish")


def test_thumbnails_read_all_points_across_replies():
    async def go():
        dev = FakeDevice()
        link = make_link(dev, idle_timeout=0.05)          # idle timer must not interrupt a build
        await link.async_connect()
        await link.async_select_library(0)                 # Timetunnel (8), plain pattern pages
        await link.async_start_thumbnails()
        await _wait_thumbs(link)
        assert sorted(link.thumbs) == [f"1_{i}" for i in range(1, 9)]
        for file in range(1, 9):
            flat = link.thumbs[f"1_{file}"]
            want = sim_points(1, file)
            assert len(flat) == 4 * len(want), (file, len(flat) // 4, len(want))   # overlap removed
            assert [tuple(flat[i:i + 4]) for i in range(0, len(flat), 4)] == want
        assert "done" in link.thumb_status
        # existing thumbnails are kept unless rebuilding
        n = len(dev.received)
        await link.async_start_thumbnails(); await _wait_thumbs(link)
        assert len(dev.received) == n
        await link.disconnect()
    asyncio.run(go())


def test_thumbnails_for_effect_pages_resolve_pattern():
    async def go():
        dev = FakeDevice()
        link = make_link(dev, idle_timeout=0)
        await link.async_connect()
        idx = [l.name for l in link.libraries].index("Mixture")
        await link.async_start_thumbnails(idx)
        await _wait_thumbs(link)
        assert set(link.thumbs) == {f"7_{i}" for i in range(1, 11)}
        # the sim maps effect step k -> pattern (3, k)
        flat = link.thumbs["7_4"]
        assert [tuple(flat[i:i + 4]) for i in range(0, len(flat), 4)] == sim_points(3, 4)
        await link.disconnect()
    asyncio.run(go())


def test_thumbnail_cancel_and_cache_hook():
    async def go():
        dev = FakeDevice()
        link = make_link(dev, idle_timeout=0)
        saved = []
        def changed():
            saved.append(len(link.thumbs))
            if len(link.thumbs) >= 8:
                link._cancel_thumbs()                      # cancel mid-build
        link.on_thumbs_changed = changed
        await link.async_connect()
        await link.async_select_library(4)                 # Hotspot (128)
        await link.async_start_thumbnails()
        await asyncio.sleep(0.5)
        assert not link._thumbs_running() and 8 <= len(link.thumbs) < 128 and saved
        try:
            await link.async_start_thumbnails(99)
            raise AssertionError("expected error")
        except c.CubeError:
            pass
        await link.disconnect()
    asyncio.run(go())


def _decode_points(data):
    count = int.from_bytes(data[:2], "big")
    pts = []
    for i in range(count):
        o = 2 + i * 6
        pts.append((int.from_bytes(data[o:o + 2], "big"), int.from_bytes(data[o + 2:o + 4], "big"), data[o + 4], data[o + 5]))
    assert len(data) == 2 + count * 6
    return pts


def test_text_is_streamed_in_packets_and_decodes():
    async def go():
        dev = FakeDevice()
        link = make_link(dev, idle_timeout=0)
        await link.async_set_laser(True)
        await link.async_play_text("Hello World")
        assert len(dev.realtime) == 1
        layers, frame, data = dev.realtime[0]
        assert layers == 1 and frame == 0
        pts = _decode_points(data)
        assert len(pts) > 50 and len(data) > 244 - 47          # more than one packet, reassembled intact
        assert pts[0][2] & 64 and pts[-1][2] & 128           # first point starts a path, last ends the frame
        xs = [x for x, _, _, _ in pts]; ys = [y for _, y, _, _ in pts]
        assert min(xs) == 0 and max(xs) == 65535             # wide text fills the full width
        assert 0 < min(ys) and max(ys) < 65535 and abs((min(ys) + max(ys)) / 2 - 32767) < 400   # centred vertically
        assert dev.enables[-1] == bytes([1, 0, 0]) and link.text_active
        await link.disconnect()
    asyncio.run(go())


def test_text_color_size_and_rainbow():
    async def go():
        dev = FakeDevice()
        link = make_link(dev, idle_timeout=0)
        link.text_color = 1                                      # (the default is Rainbow)
        await link.async_play_text("Hi")
        white = _decode_points(dev.realtime[-1][2])
        assert {c_ for *_, c_ in white} == {1}
        await link.async_set_text_color(2)                   # replays because text is active
        red = _decode_points(dev.realtime[-1][2])
        assert {c_ for *_, c_ in red} == {2} and len(dev.realtime) == 2
        await link.async_set_text_color(8)
        assert {c_ for *_, c_ in _decode_points(dev.realtime[-1][2])} == {2, 3}   # one colour per letter
        await link.async_set_text_size(50)
        half = _decode_points(dev.realtime[-1][2])
        xs = [x for x, *_ in half]
        assert 16000 < min(xs) < 17000 and 48500 < max(xs) < 49500
        await link.disconnect()
    asyncio.run(go())


def test_text_errors_do_not_drop_the_link():
    async def go():
        dev = FakeDevice()
        link = make_link(dev, idle_timeout=0)
        await link.async_connect()
        for bad in ("", "   ", "x" * 400):
            try:
                await link.async_play_text(bad)
                raise AssertionError("expected CubeUserError")
            except c.CubeUserError:
                assert link.connected and link.state["connects"] == 1
        dev2 = FakeDevice(fmt=1)                              # unsupported point format
        link2 = make_link(dev2, idle_timeout=0)
        try:
            await link2.async_play_text("Hi"); raise AssertionError("expected error")
        except c.CubeUserError as err:
            assert "format 1" in str(err)
        await link.async_clear_text()
        assert dev.cleared == 1 and not link.text_active
        # playing a library pattern or stopping ends 'text active'
        await link.async_play_text("Hi"); await link.async_select_library(0); await link.async_play_index(1)
        assert not link.text_active
        await link.disconnect(); await link2.disconnect()
    asyncio.run(go())


def test_device_settings_read_modify_write():
    async def go():
        dev = FakeDevice()
        link = make_link(dev, idle_timeout=0)
        await link.async_connect()
        before = dict(dev.model)
        await link.async_set_device_settings(deviceAddress=77, devicesafety=0)
        assert len(dev.model_writes) == 1 and len(dev.model_writes[0]) == 134
        assert dev.model["deviceAddress"] == 77 and dev.model["devicesafety"] == 0
        for k, v in before.items():                       # everything else is written back unchanged
            if k not in ("deviceAddress", "devicesafety"):
                assert dev.model[k] == v, k
        assert dev.model_writes[0][-96:] == bytes(96)       # reserve block is zero, like the app
        assert link.device_model["deviceAddress"] == 77
        # it re-reads before writing, so changes made elsewhere are not overwritten
        dev.model["deviceSizeX"] = 55
        await link.async_set_device_settings(deviceScannerRate=25)
        assert dev.model["deviceSizeX"] == 55 and dev.model["deviceScannerRate"] == 25
        await link.disconnect()
    asyncio.run(go())


def test_device_settings_validation_and_incomplete_reply():
    async def go():
        dev = FakeDevice()
        link = make_link(dev, idle_timeout=0)
        await link.async_connect()
        for bad in ({"deviceAddress": 0}, {"deviceAddress": 513}, {"deviceScannerRate": 22},
                    {"deviceScannerRate": 45}, {"deviceFoo": 1}, {"deviceRedMax": 101}):
            try:
                await link.async_set_device_settings(**bad)
                raise AssertionError(bad)
            except c.CubeUserError:
                assert link.connected and not dev.model_writes
        short = FakeDevice(model_len=20)                    # laser answers with an incomplete block
        link2 = make_link(short, idle_timeout=0)
        try:
            await link2.async_set_device_settings(deviceAddress=5)
            raise AssertionError("expected refusal")
        except c.CubeUserError as err:
            assert "incomplete" in str(err) and not short.model_writes
        await link.disconnect(); await link2.disconnect()
    asyncio.run(go())


def test_text_orientation_direction_and_color_flow():
    async def go():
        dev = FakeDevice()
        link = make_link(dev, idle_timeout=0)
        assert link.text == "Alexandros"                      # preset text
        await link.async_play_text()
        horiz = _decode_points(dev.realtime[-1][2])
        await link.async_set_text_orientation(1)              # vertical: tall, narrow
        vert = _decode_points(dev.realtime[-1][2])
        assert (max(y for _, y, _, _ in vert) - min(y for _, y, _, _ in vert)) > 60000
        assert (max(x for x, *_ in vert) - min(x for x, *_ in vert)) < 30000
        await link.async_set_text_orientation(0)
        await link.async_set_text_direction(1)                # reverse the letters
        rev = _decode_points(dev.realtime[-1][2])
        assert rev != horiz and len(rev) == len(horiz)
        await link.async_set_text_direction(0)
        # colour flow: text is sent white and the laser's colour flow is switched on
        await link.async_set_flow(precision=30)
        await link.async_set_text_color(9)
        assert {c_ for *_, c_ in _decode_points(dev.realtime[-1][2])} == {1}
        sent = p.parse_fields(dev.received[-1][2], p.RUN_PARAM_FIELDS)
        assert sent["runParaColorMode"] == 30 and link.text_flow_set
        await link.async_set_text_color(2)                    # back to a plain colour: flow off again
        assert p.parse_fields(dev.received[-1][2], p.RUN_PARAM_FIELDS)["runParaColorMode"] == 0
        assert not link.text_flow_set
        await link.disconnect()
    asyncio.run(go())


def _run_params_since(dev, start):
    return [p.parse_fields(d, p.RUN_PARAM_FIELDS) for f_, a, d in dev.received[start:]
            if f_ == 1 and a == p.ACT_SET_RUN_PARAMETERS]


def test_effects_move_within_the_field_and_restore():
    async def go():
        dev = FakeDevice()
        link = make_link(dev, idle_timeout=0.05)               # idle timer must not interrupt an effect
        await link.async_set_params(runsizeX=100, runsizeY=100)
        await link.async_set_text_size(50)
        await link.async_play_text("Hi")
        await link.async_set_params(runPositionX=128, runPositionY=128)
        # no room to move at 100 % size
        await link.async_set_text_size(100)
        try:
            await link.async_set_effect(1); raise AssertionError("expected CubeUserError")
        except c.CubeUserError:
            assert link.effect == 0
        await link.async_set_text_size(50)
        link.effect_speed = 100
        start = len(dev.received)
        await link.async_set_effect(3)                          # bounce horizontal
        await asyncio.sleep(0.8)
        assert link.effect == 3 and link.connected
        frames = _run_params_since(dev, start)
        xs = [f_["runPositionX"] for f_ in frames]
        assert len(set(xs)) > 5 and min(xs) >= 60 and max(xs) <= 196      # travels, but stays inside the field
        assert link.run_params["runPositionX"] == 128                    # base values untouched while running
        n = len(dev.received)
        await link.async_set_effect(0)
        last = _run_params_since(dev, n)[-1]
        assert last["runPositionX"] == 128 and link.effect == 0          # output restored
        await asyncio.sleep(0.2)
        assert len(dev.received) == n + 1 or len(_run_params_since(dev, n)) == 1   # nothing keeps moving
        # rotate keeps big content inside by shrinking it; pulse varies the size; vertical moves Y
        await link.async_set_effect(5); await asyncio.sleep(0.5)
        rot = _run_params_since(dev, n)[-10:]
        assert len({f_["runRotateZ"] for f_ in rot}) > 3
        await link.async_set_effect(6); await asyncio.sleep(0.5)
        sizes = {f_["runsizeX"] for f_ in _run_params_since(dev, n)[-10:]}
        assert min(sizes) < 100 and max(sizes) >= 30 and len(sizes) > 3
        await link.async_set_effect(4); await asyncio.sleep(0.4)
        assert len({f_["runPositionY"] for f_ in _run_params_since(dev, n)[-8:]}) > 2
        # disconnecting stops the effect and restores the output
        n2 = len(dev.received)
        await link.disconnect()
        assert link.effect == 0 and _run_params_since(dev, n2)[-1]["runPositionY"] == link.run_params["runPositionY"]
    asyncio.run(go())


def test_overview_paging():
    async def go():
        dev = FakeDevice()
        link = make_link(dev, idle_timeout=0)
        await link.async_connect()
        await link.async_select_library(4)                      # Hotspot (128) -> 7 pages of 20
        assert link.overview_pages() == 7 and link.current_overview_page() == 1
        await link.async_set_overview_page(3)
        assert link.current_overview_page() == 3
        await link.async_overview_step(1); assert link.current_overview_page() == 4
        await link.async_set_overview_page(7); await link.async_overview_step(1)
        assert link.current_overview_page() == 1                 # wraps
        await link.async_overview_step(-1); assert link.current_overview_page() == 7
        await link.async_set_overview_page(99); assert link.current_overview_page() == 7   # clamped
        await link.async_play_index(45)                          # playing something follows it again
        assert link.overview_page is None and link.current_overview_page() == 3
        await link.async_select_library(0)
        assert link.overview_pages() == 1
        await link.disconnect()
    asyncio.run(go())


def test_defaults_rainbow_text_and_raw_effect_service():
    async def go():
        dev = FakeDevice()
        link = make_link(dev, idle_timeout=0)
        assert link.text == "Alexandros" and link.text_color == 8           # Rainbow by default
        await link.async_play_text()
        assert len({c_ for *_, c_ in _decode_points(dev.realtime[-1][2])}) > 1
        assert link.info.protocols == "1.0.1"
        # an effect is sent BEFORE the text points, in the app's format
        start = len(dev.received)
        channels = [0, 0, 128, 0, 36, 40, 200, 0, 0, 0, 0, 0, 0, 0, 0, 0]
        await link.async_send_effect(channels, step=0, steps=1, duration=5)
        order = [(f_, a) for f_, a, _ in dev.received[start:] if f_ == 2]
        assert order[0] == (2, p.ACT_PLAY_EFFECT) and order[1] == (2, p.ACT_PLAY_START)
        eff = next(d for f_, a, d in dev.received[start:] if (f_, a) == (2, p.ACT_PLAY_EFFECT))
        assert eff == p.build_play_effect(0, 5000, channels, "1.0.1")
        # it stays attached when the text is re-sent (e.g. colour change) ...
        n = len(dev.received)
        await link.async_set_text_color(2)
        assert any((f_, a) == (2, p.ACT_PLAY_EFFECT) for f_, a, _ in dev.received[n:])
        # ... and an empty list removes it again
        n = len(dev.received)
        await link.async_send_effect([])
        assert not any((f_, a) == (2, p.ACT_PLAY_EFFECT) for f_, a, _ in dev.received[n:]) and link.text_effect is None
        for bad in ([256], [-1], [0] * 65):
            try:
                await link.async_send_effect(bad); raise AssertionError(bad)
            except c.CubeUserError:
                assert link.connected
        await link.disconnect()
    asyncio.run(go())


def test_hardware_effect_is_sent_before_the_text_and_replayed():
    async def go():
        dev = FakeDevice()
        dev.model["deviceSCEChannleTotal"] = 14                 # scene arrays cover CH3..CH16
        link = make_link(dev, idle_timeout=0)
        await link.async_set_hw_speed(100)
        start = len(dev.received)
        await link.async_set_hw_effect(1)                       # Rotate Z
        seq = [(f_, a) for f_, a, _ in dev.received[start:] if f_ == 2]
        assert seq[0] == (2, p.ACT_PLAY_EFFECT) and seq[1] == (2, p.ACT_PLAY_START)
        data = next(d for f_, a, d in dev.received[start:] if (f_, a) == (2, p.ACT_PLAY_EFFECT))
        assert data == p.build_play_effect(0, 3_000_000, p.hw_effect_channels(1, 100, 3), "1.0.1")
        assert len(data) == 4 + 14 and data[4 + 6] == 228       # CH9 -> index 6 -> 128 + 100
        assert link.hw_effect == 1 and link.text_active
        # changing the speed re-sends effect + text
        n = len(dev.received)
        await link.async_set_hw_speed(20)
        eff = next(d for f_, a, d in dev.received[n:] if (f_, a) == (2, p.ACT_PLAY_EFFECT))
        assert eff[4 + 6] == 148
        # explicit layout from CH1: 16 values and full intensity
        n = len(dev.received)
        await link.async_set_hw_layout(1)
        eff = next(d for f_, a, d in dev.received[n:] if (f_, a) == (2, p.ACT_PLAY_EFFECT))
        assert len(eff) == 4 + 16 and eff[4] == 255 and eff[4 + 8] == 148
        # None removes it again
        n = len(dev.received)
        await link.async_set_hw_effect(0)
        assert not any((f_, a) == (2, p.ACT_PLAY_EFFECT) for f_, a, _ in dev.received[n:]) and link.text_effect is None
        # the raw service replaces a hardware effect
        await link.async_set_hw_effect(4)
        await link.async_send_effect([1, 2, 3])
        assert link.hw_effect == 0 and link.text_effect["channels"] == [1, 2, 3]
        await link.disconnect()
    asyncio.run(go())


def test_hardware_effect_unsupported_layout_leaves_state_unchanged():
    async def go():
        dev = FakeDevice()
        dev.model["deviceSCEChannleTotal"] = 36                 # a layout the 16CH chart cannot describe
        link = make_link(dev, idle_timeout=0)
        try:
            await link.async_set_hw_effect(1); raise AssertionError("expected CubeUserError")
        except c.CubeUserError as err:
            assert "36" in str(err) and link.hw_effect == 0 and link.text_effect is None and link.connected
        await link.async_set_hw_layout(3)                       # but an explicit layout works
        await link.async_set_hw_effect(1)
        assert link.hw_effect == 1
        await link.disconnect()
    asyncio.run(go())


def test_playlist_editing_and_validation():
    async def go():
        dev = FakeDevice()
        link = make_link(dev, idle_timeout=0)
        saved = []
        link.on_playlists_changed = lambda: saved.append(sum(len(v) for v in link.playlists.values()))
        await link.async_connect()
        await link.async_select_library(0); await link.async_play_index(3)       # Timetunnel #3
        await link.async_playlist_add()                       # auto-creates "Playlist 1"; current pattern, default 5 s
        assert link.playlist_name == "Playlist 1"
        assert link.current_playlist() == [{"lib": link.libraries[0].key, "name": "Timetunnel", "n": 3, "seconds": 5.0}]
        await link.async_set_playlist_seconds(12)
        await link.async_playlist_add(library="hotspot", pattern=70)                 # another group, by name
        await link.async_playlist_add(library="Northlight (16)", pattern=5, seconds=2.5, position=1)
        names = [(i["name"], i["n"], i["seconds"]) for i in link.current_playlist()]
        assert names == [("Northlight", 5, 2.5), ("Timetunnel", 3, 5.0), ("Hotspot", 70, 12.0)]
        assert link.playlist_total_seconds() == 19.5
        await link.async_playlist_move(3, 1)
        assert [i["name"] for i in link.current_playlist()] == ["Hotspot", "Northlight", "Timetunnel"]
        await link.async_playlist_set_duration(2, 8)
        assert link.current_playlist()[1]["seconds"] == 8
        await link.async_playlist_remove(1)
        assert [i["name"] for i in link.current_playlist()] == ["Northlight", "Timetunnel"]
        await link.async_playlist_remove()                                           # last
        assert [i["name"] for i in link.current_playlist()] == ["Northlight"]
        # several playlists
        await link.async_playlist_create("Party")
        assert link.playlist_name == "Party" and link.current_playlist() == []
        await link.async_playlist_add(library="Animation", pattern=36, playlist="Party")
        await link.async_playlist_select("Playlist 1"); assert len(link.current_playlist()) == 1
        await link.async_playlist_delete("Playlist 1")
        assert link.playlist_name == "Party" and "Playlist 1" not in link.playlists
        await link.async_playlist_clear(); assert link.current_playlist() == []
        assert saved                                                                  # persistence hook fired
        # validation
        for call in (lambda: link.async_playlist_add(library="Nope"), lambda: link.async_playlist_add(library="Hotspot", pattern=129),
                     lambda: link.async_playlist_add(library="Hotspot", pattern=1, seconds=0.1),
                     lambda: link.async_playlist_create("Party"), lambda: link.async_playlist_remove(),
                     lambda: link.async_playlist_set_duration(5, 3), lambda: link.async_playlist_select("zzz")):
            try:
                await call(); raise AssertionError("expected CubeUserError")
            except c.CubeUserError:
                pass
        await link.disconnect()
    asyncio.run(go())


def test_playlist_plays_each_pattern_for_its_time_and_repeats():
    async def go():
        dev = FakeDevice()
        link = make_link(dev, idle_timeout=0.05)               # idle timer must not interrupt a playlist
        await link.async_set_laser(True)
        for lib, n in (("Timetunnel", 3), ("Hotspot", 70), ("Northlight", 5)):
            await link.async_playlist_add(library=lib, pattern=n)
        for item, secs in zip(link.current_playlist(), (0.1, 0.2, 0.1)):
            item["seconds"] = secs                               # short times for the test
        n0 = len(dev.frames)
        await link.async_playlist_play(repeat=True)
        assert link.playlist_on
        await asyncio.sleep(0.25)
        assert dev.frames[n0:n0 + 2] == [(1, 3), (6, 6)]       # different groups, right page/file
        await asyncio.sleep(0.6)
        assert link.connected and link.playlist_on
        seq = dev.frames[n0:]
        assert (2, 5) in seq and seq.count((1, 3)) >= 2        # went on to the third item and repeated
        await link.async_playlist_stop()
        assert not link.playlist_on
        m = len(dev.frames); await asyncio.sleep(0.4); assert len(dev.frames) == m
        # play once (no repeat) ends by itself
        await link.async_playlist_play(repeat=False)
        await asyncio.sleep(0.8)
        assert not link.playlist_on
        await link.disconnect()
    asyncio.run(go())


def test_playlist_edits_apply_live_missing_items_are_skipped_and_cancellation():
    async def go():
        dev = FakeDevice()
        link = make_link(dev, idle_timeout=0)
        await link.async_set_laser(True)
        await link.async_playlist_add(library="Timetunnel", pattern=1)
        await link.async_playlist_add(library="Timetunnel", pattern=2)
        link.current_playlist()[0]["seconds"] = 0.2
        link.current_playlist()[1]["seconds"] = 0.2
        link.current_playlist().insert(1, {"lib": "99:0:0", "name": "Gone", "n": 1, "seconds": 0.1})   # not on this laser
        n0 = len(dev.frames)
        await link.async_playlist_play(repeat=False)
        await asyncio.sleep(0.15)
        link.current_playlist()[:] = [link.current_playlist()[0], {"lib": link.libraries[0].key, "name": "Timetunnel", "n": 7, "seconds": 0.1}]
        await asyncio.sleep(0.5)
        assert dev.frames[n0:] == [(1, 1), (1, 7)]              # the unknown item was skipped, the live edit was used
        # laser off, loop and stop all end a playlist
        link.current_playlist()[0]["seconds"] = 5
        await link.async_playlist_play(repeat=True); assert link.playlist_on
        await link.async_set_laser(False); assert not link.playlist_on
        await link.async_set_laser(True); await link.async_playlist_play(); assert link.playlist_on
        await link.async_set_loop(True); assert not link.playlist_on and link.loop_on
        await link.async_set_loop(False)
        await link.async_playlist_play(); await link.async_stop(); assert not link.playlist_on
        await link.async_playlist_play()
        await link.disconnect(); assert not link.playlist_on
        # empty playlist / no playlist cannot be played
        await link.async_playlist_clear()
        try:
            await link.async_playlist_play(); raise AssertionError("expected CubeUserError")
        except c.CubeUserError:
            pass
    asyncio.run(go())
