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


class FakeDevice:
    def __init__(self, activate=1, bind_en=255, bind_user=0, fw=(2, 1, 0), drop_first=False, model_len=None):
        self.activate, self.bind_en, self.bind_user, self.fw = activate, bind_en, bind_user, fw
        self.device_key, self.device_secret, self.product_key = "DEVKEY-0123456789abcdef", "SECRET-9876543210fedcba", "PROD-KEY-1"
        self.keys = p.default_key_iv(NAME)
        self.received = []          # (function, action, data)
        self.drop_next = drop_first
        self.model_len = model_len
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
                + bytes([1, 2, 0]) + bytes(self.fw) + bytes([1, 8, self.activate])
                + s32(self.device_key) + s32(self.device_secret) + s32(self.product_key)
                + (7).to_bytes(4, "big") + (1).to_bytes(2, "big") + (2).to_bytes(2, "big") + b"\0" + bytes([44])
                + extras)
        return body

    def data_rsp(self, payload):
        return bytes([0x85, 0x12, 0x34, 0, 0xAA, 0x55, len(payload) & 0xFF]) + payload

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
        assert cmd == p.CMD_TRANSFER, hex(cmd)
        func, action = plain[5], plain[6]
        dlen = int.from_bytes(plain[9:13], "big")
        data = plain[47:47 + dlen]
        self.received.append((func, action, data))
        if action == p.ACT_GET_DEVICE_BIND_INFO:
            rsp = self.data_rsp(bytes([self.bind_en]) + b"\x12\x34" + b"\x00" + self.bind_user.to_bytes(4, "big"))
        elif action == p.ACT_SEARCH_DEVICE_SET_MODEL:
            blob = b"".join(self.model[n].to_bytes(s, "big") for n, s in p.DEVICE_MODEL_FIELDS)
            rsp = self.data_rsp(blob[:self.model_len] if self.model_len else blob)
        else:
            if action == p.ACT_ENABLE_LASER_OUTPUT:
                self.laser = (data[0], data[1])
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
