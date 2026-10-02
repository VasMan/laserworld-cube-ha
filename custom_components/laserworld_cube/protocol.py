"""Wire protocol for Laserworld Cube Laser Control devices (BLE name ``BLEAPP_*``).

Home Assistant independent. Everything here is a pure function so it can be
unit-tested against vectors produced by the official app's own JavaScript.

Summary of the protocol
-----------------------
* Transport: GATT service FFE0, characteristic FFE1 (write + notify).
* Every message is ``[cmd, 0x12, 0x34, len_hi, len_lo, payload...]``.
* The first byte stays in clear text, the rest is AES-128-CTR encrypted
  (only the first 240 bytes after the command byte; zero padded to 16 bytes).
* Handshake uses a key/IV derived from the BLE name and a fixed table. After
  the handshake an activated device (activateType == 1) uses
  ``deviceSecret`` as key and ``deviceKey`` as IV.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

SERVICE_UUID = "0000ffe0-0000-1000-8000-00805f9b34fb"
CHAR_UUID = "0000ffe1-0000-1000-8000-00805f9b34fb"
NAME_PREFIX = "BLEAPP"
APP_COMPANY = "CubeLaserTemeiAI"
CLIENT_COMPANY = "ChinaTemeiAI"

# command bytes (request -> response)
CMD_TRANSFER = 0xAD
CMD_TRANSFER_CONT = 0xA5
CMD_SIMPLE = 0xAA
CMD_HANDSHAKE = 0xAB
RSP_TRANSFER = 0x85
RSP_SIMPLE = 0x8A
RSP_HANDSHAKE = 0x8B

# functions / actions
FUNC_MY_DEVICE = 1
FUNC_DISCONNECT_DEVICE = 7
ACT_DISCONNECT = 1
ACT_DEVICE_SET_MODEL = 1
ACT_ENABLE_LASER_OUTPUT = 5
ACT_SEARCH_DEVICE_SET_MODEL = 11
ACT_SET_RUN_PARAMETERS = 12
ACT_GET_DEVICE_BIND_INFO = 25

# run modes (value sent in ENABLE_LASER_OUTPUT)
RUN_MODE_APP = 0
RUN_MODE_DMX = 1
RUN_MODE_ILDA = 4
RUN_MODES = {RUN_MODE_APP: "APP mode", RUN_MODE_DMX: "DMX512 mode", RUN_MODE_ILDA: "ILDA mode"}

WORK_MODE_VOICE = 0
WORK_MODE_AUTO = 1

COLOR_MODES = {
    1: "White", 2: "Red", 3: "Yellow", 4: "Green", 5: "Cyan", 6: "Blue",
    7: "Purple", 8: "Red/Green", 9: "Red/Blue", 10: "Green/Blue",
    11: "RGB", 12: "White/Yellow/Cyan/Purple",
}

BIND_NONE = 255

KEY_TABLE = bytes((
    37, 94, 123, 97, 95, 87, 42, 48, 81, 85, 123, 93, 121, 63, 68, 67,
    88, 58, 78, 78, 89, 79, 53, 51, 122, 97, 49, 79, 98, 36, 36, 83,
    77, 99, 46, 49, 67, 41, 97, 89, 33, 83, 84, 46, 48, 39, 37, 126,
    76, 68, 101, 35, 81, 124, 120, 37, 116, 108, 92, 125, 69, 77, 100, 42,
    94, 58, 70, 98, 105, 48, 93, 50, 91, 38, 40, 60, 111, 111, 51, 83,
    110, 34, 108, 118, 120, 116, 109, 69, 89, 34, 115, 74, 126, 90, 101, 99,
    126, 41, 77, 45, 117, 107, 76, 114, 113, 56, 47, 97, 94, 98, 60, 78,
    93, 125, 124, 91, 70, 59, 92, 42, 71, 67, 34, 85, 85, 119, 102, 60,
    58, 63, 101, 104, 108, 82, 120, 58, 59, 112, 73, 84, 70, 53, 81, 101,
    113, 72, 49, 42, 41, 119, 45, 92, 110, 95, 72, 69, 98, 44, 82, 36,
    38, 86, 104, 123, 122, 57, 73, 62, 41, 65, 44, 35, 80, 47, 33, 64,
    40, 41, 84, 71, 83, 81, 121, 67, 77, 54, 47, 44, 82, 123, 88, 59,
    105, 85, 41, 115, 87, 95, 33, 88, 121, 122, 54, 104, 75, 74, 120, 37,
    103, 102, 126, 79, 45, 124, 49, 75, 38, 39, 41, 112, 99, 74, 64, 37,
    58, 110, 92, 50, 34, 125, 43, 52, 46, 42, 119, 64, 97, 52, 95, 109,
    69, 42, 44, 65, 86, 83, 104, 110, 57, 39, 86, 106, 111, 50, 88, 47,
))

# Field layouts: (name, size in bytes), big endian.
RUN_PARAM_FIELDS: tuple[tuple[str, int], ...] = (
    ("runParaWhiteMax", 1), ("runParaRedMax", 1), ("runParaGreenMax", 1),
    ("runParaBlueMax", 1), ("runParaColorMode", 1), ("runParaColorSpeed", 1),
    ("runPlayMode", 1), ("runWorkMode", 1), ("runAutoSpeed", 1), ("runMusicDb", 1),
    ("runsizeX", 2), ("runsizeY", 2), ("runPositionX", 2), ("runPositionY", 2),
    ("runRotateZ", 2), ("runRotateX", 2), ("runRotateY", 2), ("runAddress", 2),
    ("runChannelMode", 1), ("runColorMode", 1), ("runZoneX", 1), ("runZoneY", 1),
    ("frameRate", 1), ("scannerRate", 1), ("setColor", 1), ("reserve", 5),
)

DEVICE_MODEL_FIELDS: tuple[tuple[str, int], ...] = (
    ("deviceScannerRate", 2), ("deviceSizeX", 2), ("deviceSizeY", 2), ("deviceSizeXY", 2),
    ("devicePositionX", 2), ("devicePositionY", 2), ("deviceInvertX", 1),
    ("deviceInvertY", 1), ("deviceSwapXY", 1), ("deviceColorFunc", 1),
    ("deviceLaserType", 1), ("deviceMasterFunc", 1), ("deviceChannelMode", 1),
    ("deviceAddress", 2), ("deviceRunWorkMode", 1), ("deviceAutospeed", 1),
    ("deviceMusicDb", 1), ("devicesafety", 1), ("deviceRedMax", 1),
    ("deviceGreenMax", 1), ("deviceBlueMax", 1), ("devicecontrollerFunc", 1),
    ("deviceStdChannleTotal", 1), ("deviceProChannleTotal", 1),
    ("deviceSCEChannleTotal", 1), ("deviceWhiteMax", 1), ("deviceRotateZ", 2),
    ("deviceZoneX", 1), ("deviceZoneY", 1), ("deviceframeRate", 1), ("reserve", 96),
)

BIND_FIELDS: tuple[tuple[str, int], ...] = (
    ("bindEn", 1), ("bindCode", 2), ("bindAuth", 1), ("bindUser", 4),
)

DEFAULT_RUN_PARAMS: dict[str, int] = {
    "runParaWhiteMax": 255, "runParaRedMax": 255, "runParaGreenMax": 255,
    "runParaBlueMax": 255, "runParaColorMode": 0, "runParaColorSpeed": 8,
    "runPlayMode": 1, "runWorkMode": WORK_MODE_AUTO, "runAutoSpeed": 50,
    "runMusicDb": 80, "runsizeX": 100, "runsizeY": 100, "runPositionX": 128,
    "runPositionY": 128, "runRotateZ": 0, "runRotateX": 0, "runRotateY": 0,
    "runAddress": 1, "runChannelMode": 0, "runColorMode": 11, "runZoneX": 100,
    "runZoneY": 100, "frameRate": 60, "scannerRate": 30, "setColor": 11,
}


class ProtocolError(Exception):
    """Malformed or unexpected message."""


class DeviceStatusError(ProtocolError):
    """Device answered with a non-zero status."""

    def __init__(self, status: int) -> None:
        super().__init__(f"device returned status {status}")
        self.status = status


# --------------------------------------------------------------------------- crypto

def _pad16(text: str) -> bytes:
    """Equivalent of the app's ``Ot``: right-pad with '0' to 16 chars, cut at 16."""
    return text.ljust(16, "0")[:16].encode("utf-8")


def default_key_iv(ble_name: str) -> tuple[bytes, bytes]:
    """Key/IV used for the handshake, derived from the advertised BLE name."""
    parts = ble_name.split("_")
    if len(parts) < 2 or len(parts[1]) < 4:
        raise ProtocolError(f"unexpected BLE name {ble_name!r}; expected BLEAPP_<id>")
    try:
        offset = int(parts[1][1] + parts[1][3], 16)
    except ValueError as err:
        raise ProtocolError(f"cannot derive key from BLE name {ble_name!r}") from err
    n = len(KEY_TABLE)
    key = bytes(KEY_TABLE[(offset + i) % n] for i in range(16))
    iv = bytes(KEY_TABLE[(offset + 16 + i) % n] for i in range(16))
    return key, iv


def session_key_iv(activate_type: int, product_key: str, device_key: str,
                   device_secret: str) -> tuple[bytes, bytes]:
    """Key/IV used after the handshake."""
    if activate_type == 0:
        k = _pad16(product_key)
        return k, k
    if activate_type == 1:
        return _pad16(device_secret), _pad16(device_key)
    raise ProtocolError(f"unsupported activateType {activate_type}")


def _ctr(key: bytes, iv: bytes, data: bytes) -> bytes:
    c = Cipher(algorithms.AES(key), modes.CTR(iv)).encryptor()
    return c.update(data) + c.finalize()


def encrypt(message: bytes, key: bytes, iv: bytes) -> bytes:
    """Encrypt like the app: byte 0 clear, <=240 bytes encrypted & zero padded."""
    head, body = message[:1], message[1:]
    tail = b""
    if len(body) > 240:
        body, tail = body[:240], body[240:]
    if body:
        body += b"\x00" * (-len(body) % 16)
        body = _ctr(key, iv, body)
    return head + body + tail


def decrypt(message: bytes, key: bytes, iv: bytes) -> bytes:
    head, body = message[:1], message[1:]
    tail = b""
    if len(body) > 240:
        body, tail = body[:240], body[240:]
    if body:
        body = _ctr(key, iv, body)
    return head + body + tail


# ------------------------------------------------------------------- frame builders

def _be(value: int, size: int) -> bytes:
    value = int(round(value))
    if value < 0 or value >= 1 << (8 * size):
        raise ValueError(f"{value} does not fit in {size} byte(s)")
    return value.to_bytes(size, "big")


def _frame(cmd: int, payload: bytes) -> bytes:
    return bytes([cmd, 0x12, 0x34]) + _be(len(payload), 2) + payload


def timestamp_now() -> str:
    return datetime.now().strftime("%Y%m%d%H%M%S")


def build_simple(function: int, action: int) -> bytes:
    return _frame(CMD_SIMPLE, bytes([function, action]))


def build_transfer(function: int, action: int, data: bytes | None = None, *,
                   data_format: int = 1, timestamp: str | None = None) -> bytes:
    """First (and, for small payloads, only) data-transfer packet."""
    ts = ("A" + (timestamp or timestamp_now())).encode("ascii")[:30].ljust(30, b"\x00")
    payload = bytes([function, action, 0, data_format])
    payload += _be(len(data) if data else 0, 4)
    payload += bytes([0, 0]) + ts + _be(0, 2)
    if data:
        payload += data
    return _frame(CMD_TRANSFER, payload)


def build_handshake(user_id: int = 0, app_version: tuple[int, int, int] = (1, 0, 0),
                    nonce: bytes | None = None) -> bytes:
    import os
    nonce = nonce if nonce is not None else os.urandom(4)
    if len(nonce) != 4:
        raise ValueError("nonce must be 4 bytes")
    payload = (
        _be(user_id, 4) + _be(0, 4) + _be(3, 4) + bytes(app_version) + b"\x00"
        + CLIENT_COMPANY.encode().ljust(16, b"\x00") + bytes([1, 0, 0]) + b"\x00"
        + bytes([0, 0, 0]) + nonce
    )
    return _frame(CMD_HANDSHAKE, payload)


def build_enable_payload(laser_on: bool, run_mode: int) -> bytes:
    """Payload of ENABLE_LASER_OUTPUT: [on/off, run mode]."""
    return bytes([1 if laser_on else 0, run_mode])


def build_run_params(params: dict[str, int]) -> bytes:
    out = b""
    for name, size in RUN_PARAM_FIELDS:
        if name == "reserve":
            out += b"\x00" * size
            continue
        out += _be(max(0, params[name]), size)
    return out


# ------------------------------------------------------------------ response parsing

def parse_status(message: bytes, expected_cmd: int) -> int:
    """Check the 4 byte response header and return the status byte."""
    if len(message) < 4:
        raise ProtocolError(f"response too short: {message.hex()}")
    if message[0] != expected_cmd:
        raise ProtocolError(f"unexpected response command 0x{message[0]:02x}")
    return message[3]


def parse_data_response(message: bytes) -> bytes:
    """Return payload of a data response (marker AA 55, length, payload)."""
    if len(message) < 7 or message[4] != 0xAA or message[5] != 0x55:
        raise ProtocolError(f"missing AA55 marker: {message.hex()}")
    return message[7:]


def parse_fields(payload: bytes, layout: tuple[tuple[str, int], ...]) -> dict[str, int]:
    out: dict[str, int] = {}
    pos = 0
    for name, size in layout:
        chunk = payload[pos:pos + size]
        if len(chunk) < size:
            raise ProtocolError(f"payload too short for {name}")
        out[name] = int.from_bytes(chunk, "big")
        pos += size
    return out


@dataclass
class HandshakeInfo:
    status: int = 0
    buffer_max: int = 0
    conn_interval: int = 0
    communication_version: str = ""
    firmware_version: str = ""
    data_format: int = 1
    scene_max: int = 0
    activate_type: int = 0
    device_key: str = ""
    device_secret: str = ""
    product_key: str = ""
    maker_key: int = 0
    app_company: str = ""
    extras: dict = field(default_factory=dict)


def _text(raw: bytes) -> str:
    return raw.decode("latin-1").replace("\x00", "")


def _ver(raw: bytes) -> str:
    return ".".join(str(b) for b in raw)


def parse_handshake_response(msg: bytes) -> HandshakeInfo:
    status = parse_status(msg, RSP_HANDSHAKE)
    info = HandshakeInfo(status=status)
    if status != 0:
        return info
    if len(msg) < 123:
        raise ProtocolError(f"handshake response too short ({len(msg)} bytes)")
    info.buffer_max = int.from_bytes(msg[4:6], "big")
    info.conn_interval = int.from_bytes(msg[6:8], "big")
    info.communication_version = _ver(msg[8:11])
    info.firmware_version = _ver(msg[11:14])
    info.data_format = msg[14]
    info.scene_max = msg[15]
    info.activate_type = msg[16]
    info.device_key = _text(msg[17:49])
    info.device_secret = _text(msg[49:81])
    info.product_key = _text(msg[81:113])
    info.maker_key = int.from_bytes(msg[113:117], "big")
    if msg[122] > 0 and len(msg) >= 155:
        info.app_company = _text(msg[139:155])
    return info


def version_tuple(text: str) -> tuple[int, ...]:
    try:
        return tuple(int(p) for p in text.split("."))
    except ValueError:
        return (0,)
