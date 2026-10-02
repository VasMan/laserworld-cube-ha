"""Smoke test: import all HA platform modules against stub HA modules and drive
the entity classes with a real CubeLink + simulated device. This does NOT replace
testing inside a real Home Assistant."""
import asyncio, importlib, importlib.util, pathlib, sys, types

ROOT = pathlib.Path(__file__).parent.parent / "custom_components"


class _Base:
    def __init_subclass__(cls, **kw): pass
    def __class_getitem__(cls, item): return cls
    def __init__(self, *a, **k): pass


class _Stub(types.ModuleType):
    def __getattr__(self, name):
        if name.startswith("__"): raise AttributeError(name)
        if name in ("callback",): return lambda f: f
        if name.isupper() or name in ("DEGREE", "PERCENTAGE"): return "x"
        cls = type(name, (_Base,), {})
        setattr(self, name, cls)
        return cls


for m in ["homeassistant", "homeassistant.components", "homeassistant.components.bluetooth",
          "homeassistant.components.switch", "homeassistant.components.select",
          "homeassistant.components.number", "homeassistant.components.button",
          "homeassistant.config_entries", "homeassistant.const", "homeassistant.core",
          "homeassistant.exceptions", "homeassistant.helpers", "homeassistant.helpers.device_registry",
          "homeassistant.helpers.entity", "homeassistant.helpers.restore_state",
          "bleak_retry_connector", "voluptuous"]:
    sys.modules[m] = _Stub(m)
sys.modules["homeassistant"].components = sys.modules["homeassistant.components"]
sys.modules["homeassistant.components"].bluetooth = sys.modules["homeassistant.components.bluetooth"]
sys.modules["homeassistant.helpers"].device_registry = sys.modules["homeassistant.helpers.device_registry"]
sys.modules["homeassistant.helpers.device_registry"].CONNECTION_BLUETOOTH = "bluetooth"


sys.modules["homeassistant.const"].Platform = types.SimpleNamespace(SWITCH="switch", SELECT="select", NUMBER="number", BUTTON="button")
sys.modules["homeassistant.const"].EntityCategory = types.SimpleNamespace(DIAGNOSTIC="diagnostic")
sys.modules["homeassistant.components.number"].NumberMode = types.SimpleNamespace(SLIDER="slider")
sys.modules["homeassistant.const"].CONF_ADDRESS = "address"
class HAError(Exception): pass
sys.modules["homeassistant.exceptions"].HomeAssistantError = HAError

pkg = types.ModuleType("custom_components"); pkg.__path__ = [str(ROOT)]; sys.modules["custom_components"] = pkg
spec = importlib.util.spec_from_file_location("custom_components.laserworld_cube", ROOT / "laserworld_cube" / "__init__.py",
                                              submodule_search_locations=[str(ROOT / "laserworld_cube")])
top = importlib.util.module_from_spec(spec); sys.modules[spec.name] = top; spec.loader.exec_module(top)
mods = {n: importlib.import_module(f"custom_components.laserworld_cube.{n}")
        for n in ("config_flow", "switch", "select", "number", "button")}

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import test_client_sim as sim   # reuses the simulated device


pkgclient = importlib.import_module("custom_components.laserworld_cube.client")
pkgclient.HANDSHAKE_DELAY = 0.0


def make_link(dev, **kw):
    async def connect(cb):
        return sim.FakeClient(dev, cb)
    return pkgclient.CubeLink(sim.NAME, connect, **kw)


def _entry():
    e = types.SimpleNamespace(data={"address": "AA:BB", "ble_name": sim.NAME})
    return e


def test_entities_drive_link():
    async def go():
        dev = sim.FakeDevice()
        link = make_link(dev, idle_timeout=0)
        entry = _entry()
        sw = mods["switch"].CubeLaserSwitch(link, entry)
        await sw.async_turn_on()
        assert sw.is_on and dev.laser == (1, 0)
        num = {d.key: mods["number"].CubeNumber(link, entry, d) for d in mods["number"].NUMBERS}
        assert num["size_x"].native_value == 80          # read from device on connect
        await num["size_x"].async_set_native_value(42)
        assert num["size_x"].native_value == 42
        sel = {d.key: mods["select"].CubeSelect(link, entry, d) for d in mods["select"].SELECTS}
        assert sel["color_mode"].current_option == "Cyan"  # model colourFunc=5
        await sel["color_mode"].async_select_option("Blue")
        assert link.run_params["runColorMode"] == 6
        await sel["run_mode"].async_select_option("ILDA mode")
        assert dev.laser == (1, 4) and sel["run_mode"].current_option == "ILDA mode"
        await sel["work_mode"].async_select_option("Voice")
        assert link.run_params["runWorkMode"] == 0
        # errors surface as HomeAssistantError
        bad = make_link(sim.FakeDevice(activate=0), idle_timeout=0)
        try:
            await mods["switch"].CubeLaserSwitch(bad, entry).async_turn_on()
            raise AssertionError("expected HAError")
        except HAError:
            pass
        await link.disconnect()
    asyncio.run(go())
