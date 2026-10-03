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

import math
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
FUNC_REAL_TIME_PLAY = 2
FUNC_PATTERN_LIBRARY = 3
FUNC_DISCONNECT_DEVICE = 7
ACT_DISCONNECT = 1
ACT_DEVICE_SET_MODEL = 1
ACT_ENABLE_LASER_OUTPUT = 5
ACT_SEARCH_LIB_FILE = 10
ACT_SEARCH_PATTERN_LIB_DATA = 17
ACT_SEARCH_EFFECT_LIB_DATA = 18
ACT_SEARCH_DEVICE_SET_MODEL = 11
ACT_SET_RUN_PARAMETERS = 12
ACT_GET_DEVICE_BIND_INFO = 25
ACT_FRAME_PLAYING = 5  # under FUNC_PATTERN_LIBRARY: [page, file]
ACT_PLAY_START = 2     # under FUNC_REAL_TIME_PLAY: stream frames (text, drawings)
ACT_PLAY_EFFECT = 3    # under FUNC_REAL_TIME_PLAY: effect channel values for the shown frame
ACT_CLEAR_PLAY_DATA = 6  # under FUNC_REAL_TIME_PLAY

# third byte of ENABLE_LASER_OUTPUT
PLAY_STATE_PLAY = 0
PLAY_STATE_PAUSE = 1
PLAY_STATE_STOP = 2  # the device's default

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

# runParaColorMode: 0 = pattern's own colours, 1-7 solid colour, 8-63 flowing colour
PATTERN_COLORS = {
    0: "Original colors", 1: "White", 2: "Red", 3: "Yellow", 4: "Green",
    5: "Cyan", 6: "Blue", 7: "Purple", 8: "Flowing",
}
# text colours: palette index 1-7, 8 = a different colour per letter,
# 9 = white text recoloured by the laser's colour-flow mode
TEXT_COLORS = {1: "White", 2: "Red", 3: "Yellow", 4: "Green", 5: "Cyan", 6: "Blue",
               7: "Purple", 8: "Rainbow", 9: "Color flow"}
TEXT_ORIENTATIONS = {0: "Horizontal", 1: "Vertical"}
TEXT_DIRECTIONS = {0: "Forward", 1: "Reverse"}
# Motion effects driven from Home Assistant (the app's own effects are cloud-defined)
EFFECTS = {0: "None", 1: "Scroll right", 2: "Scroll left", 3: "Bounce horizontal",
           4: "Bounce vertical", 5: "Rotate", 6: "Pulse"}

OVERVIEW_PAGE = 20  # thumbnails per overview sheet (5 x 4)

# Hardware (laser-side) effects. Channel numbers follow Laserworld's "DMX chart CUBE series",
# Standard mode 16CH: 5 colour, 6 colour speed, 9/10/11 rotate Z/X/Y, 12/13 horizontal/vertical
# movement, 14 zoom, 15 gradual drawing, 16 X/Y waves. Speed channels use the upper half (128-255).
HW_EFFECTS = {0: "None", 1: "Rotate Z", 2: "Rotate X", 3: "Rotate Y", 4: "Horizontal movement",
              5: "Vertical movement", 6: "Zoom", 7: "X waves", 8: "Y waves", 9: "Color flow",
              10: "Gradual drawing"}
# which channel of the 16 the effect array starts at (0 = work it out from the laser)
HW_LAYOUTS = {0: "Automatic", 1: "From CH1 (16 values)", 2: "From CH2 (15 values)",
              3: "From CH3 (14 values)", 4: "From CH4 (13 values)", 5: "From CH5 (12 values)"}

# persistent device settings ("Laser device settings" in the app)
FUNCTION_MODES = {0: "DMX512 mode", 1: "Auto mode", 2: "Music mode", 3: "ILDA mode"}
SCAN_SPEEDS = (15, 20, 25, 30, 35, 40)  # KPPS
LASER_TYPES = {0: "TTL", 1: "Analog"}
# allowed values per settings field (min, max); anything else is refused
DEVICE_SETTING_LIMITS: dict[str, tuple[int, int]] = {
    "deviceAddress": (1, 512), "deviceChannelMode": (0, 1), "deviceRunWorkMode": (0, 3),
    "deviceScannerRate": (15, 40), "deviceMasterFunc": (0, 1), "devicesafety": (0, 1),
    "deviceColorFunc": (1, 12), "deviceLaserType": (0, 1),
    "deviceSizeX": (10, 100), "deviceSizeY": (10, 100),
    "devicePositionX": (0, 255), "devicePositionY": (0, 255),
    "deviceInvertX": (0, 1), "deviceInvertY": (0, 1), "deviceSwapXY": (0, 1),
    "deviceRedMax": (0, 100), "deviceGreenMax": (0, 100), "deviceBlueMax": (0, 100),
}
RAINBOW = (2, 3, 4, 5, 6, 7)
# pictures: how they are converted to lines, and how they are coloured
PICTURE_MODES = {0: "Outline", 1: "Silhouette", 2: "Lines"}
PICTURE_COLORS = {0: "Original colors", 1: "White", 2: "Red", 3: "Yellow", 4: "Green", 5: "Cyan",
                  6: "Blue", 7: "Purple", 8: "Rainbow"}
# client-side play modes of the official app
LOOP_MODES = {0: "Loop", 1: "Random", 2: "Sequence", 3: "Single"}

# Library category ids reported by the device (filesNumber) -> app names
LIBRARY_NAMES = {
    1: "My pattern", 2: "Mixture", 3: "Animation", 4: "Northlight", 5: "Timetunnel",
    6: "Outdoors", 7: "Hotspot", 8: "Circles", 9: "Lines", 10: "Polygon", 11: "Waves",
    12: "Geometry", 13: "Highbeams", 14: "Highlight", 15: "Pointbeams", 16: "Dancers",
    17: "Peoples", 18: "Holidays", 19: "Sports", 20: "Christmas", 21: "Chinese",
    22: "Wedding", 23: "Textlogo", 24: "Numberclock", 25: "Ktv", 26: "Club",
    27: "Party", 28: "Lives", 29: "Interaction", 30: "Advertising",
}

LIB_FIELDS: tuple[tuple[str, int], ...] = (
    ("searchLibPageNumber", 1), ("searchLibFileNameKeyId", 4), ("searchLibDelete", 1),
    ("searchLibSize", 4), ("searchLibStepTotal", 1), ("searchLibFileTotal", 1),
    ("filesNumber", 1), ("filesMerge", 1), ("downloadPageMax", 1), ("reserve", 1),
)

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


# The device colour table (index -> RGB) used by library patterns and real-time frames.
PALETTE: tuple[tuple[int, int, int], ...] = (
    (0, 0, 0), (255, 255, 255), (255, 0, 0), (255, 255, 0), (0, 255, 0), (0, 255, 255),
    (0, 0, 255), (255, 0, 255), (255, 128, 128), (255, 140, 128), (255, 151, 128), (255, 163, 128),
    (255, 174, 128), (255, 186, 128), (255, 197, 128), (255, 209, 128), (255, 220, 128), (255, 232, 128),
    (255, 243, 128), (255, 255, 128), (243, 255, 128), (232, 255, 128), (220, 255, 128), (209, 255, 128),
    (197, 255, 128), (186, 255, 128), (174, 255, 128), (163, 255, 128), (151, 255, 128), (140, 255, 128),
    (128, 255, 128), (128, 255, 140), (128, 255, 151), (128, 255, 163), (128, 255, 174), (128, 255, 186),
    (128, 255, 197), (128, 255, 209), (128, 255, 220), (128, 255, 232), (128, 255, 243), (128, 255, 255),
    (128, 243, 255), (128, 232, 255), (128, 220, 255), (128, 209, 255), (128, 197, 255), (128, 186, 255),
    (128, 174, 255), (128, 163, 255), (128, 151, 255), (128, 140, 255), (128, 128, 255), (140, 128, 255),
    (151, 128, 255), (163, 128, 255), (174, 128, 255), (186, 128, 255), (197, 128, 255), (209, 128, 255),
    (220, 128, 255), (232, 128, 255), (243, 128, 255), (255, 128, 255), (255, 128, 243), (255, 128, 232),
    (255, 128, 220), (255, 128, 209), (255, 128, 197), (255, 128, 186), (255, 128, 174), (255, 128, 163),
    (255, 128, 151), (255, 128, 140), (255, 0, 0), (255, 23, 0), (255, 46, 0), (255, 70, 0),
    (255, 93, 0), (255, 116, 0), (255, 139, 0), (255, 162, 0), (255, 185, 0), (255, 209, 0),
    (255, 232, 0), (255, 255, 0), (232, 255, 0), (209, 255, 0), (185, 255, 0), (162, 255, 0),
    (139, 255, 0), (116, 255, 0), (93, 255, 0), (70, 255, 0), (46, 255, 0), (23, 255, 0),
    (0, 255, 0), (0, 255, 23), (0, 255, 46), (0, 255, 70), (0, 255, 93), (0, 255, 116),
    (0, 255, 139), (0, 255, 162), (0, 255, 185), (0, 255, 209), (0, 255, 232), (0, 255, 255),
    (0, 232, 255), (0, 209, 255), (0, 185, 255), (0, 162, 255), (0, 139, 255), (0, 116, 255),
    (0, 93, 255), (0, 70, 255), (0, 46, 255), (0, 23, 255), (0, 0, 255), (23, 0, 255),
    (46, 0, 255), (70, 0, 255), (93, 0, 255), (116, 0, 255), (139, 0, 255), (162, 0, 255),
    (185, 0, 255), (209, 0, 255), (232, 0, 255), (255, 0, 255), (255, 0, 232), (255, 0, 209),
    (255, 0, 185), (255, 0, 162), (255, 0, 139), (255, 0, 116), (255, 0, 93), (255, 0, 70),
    (255, 0, 46), (255, 0, 23), (128, 0, 0), (128, 12, 0), (128, 23, 0), (128, 35, 0),
    (128, 47, 0), (128, 58, 0), (128, 70, 0), (128, 81, 0), (128, 93, 0), (128, 105, 0),
    (128, 116, 0), (128, 128, 0), (116, 128, 0), (105, 128, 0), (93, 128, 0), (81, 128, 0),
    (70, 128, 0), (58, 128, 0), (47, 128, 0), (35, 128, 0), (23, 128, 0), (12, 128, 0),
    (0, 128, 0), (0, 128, 12), (0, 128, 23), (0, 128, 35), (0, 128, 47), (0, 128, 58),
    (0, 128, 70), (0, 128, 81), (0, 128, 93), (0, 128, 105), (0, 128, 116), (0, 128, 128),
    (0, 116, 128), (0, 105, 128), (0, 93, 128), (0, 81, 128), (0, 70, 128), (0, 58, 128),
    (0, 47, 128), (0, 35, 128), (0, 23, 128), (0, 12, 128), (0, 0, 128), (12, 0, 128),
    (23, 0, 128), (35, 0, 128), (47, 0, 128), (58, 0, 128), (70, 0, 128), (81, 0, 128),
    (93, 0, 128), (105, 0, 128), (116, 0, 128), (128, 0, 128), (128, 0, 116), (128, 0, 105),
    (128, 0, 93), (128, 0, 81), (128, 0, 70), (128, 0, 58), (128, 0, 47), (128, 0, 35),
    (128, 0, 23), (128, 0, 12), (255, 192, 192), (255, 64, 64), (192, 0, 0), (64, 0, 0),
    (255, 255, 192), (255, 255, 64), (192, 192, 0), (64, 64, 0), (192, 255, 192), (64, 255, 64),
    (0, 192, 0), (0, 64, 0), (192, 255, 255), (64, 255, 255), (0, 192, 192), (0, 64, 64),
    (192, 192, 255), (64, 64, 255), (0, 0, 192), (0, 0, 64), (255, 192, 255), (255, 64, 255),
    (192, 0, 192), (64, 0, 64), (255, 96, 96), (255, 255, 255), (245, 245, 245), (235, 235, 235),
    (224, 224, 224), (213, 213, 213), (203, 203, 203), (192, 192, 192), (181, 181, 181), (171, 171, 171),
    (160, 160, 160), (149, 149, 149), (139, 139, 139), (128, 128, 128), (117, 117, 117), (107, 107, 107),
    (96, 96, 96), (85, 85, 85), (75, 75, 75), (64, 64, 64),
)
PALETTE_INDEX: dict[tuple[int, int, int], int] = {}
for _i, _rgb in enumerate(PALETTE):
    PALETTE_INDEX.setdefault(_rgb, _i)


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
                   data_format: int = 1, timestamp: str | None = None,
                   total_len: int | None = None, layers: int = 0, frame: int = 0) -> bytes:
    """First (and, for small payloads, only) data-transfer packet.

    ``total_len`` is the length of the *whole* transfer when ``data`` is only the
    first chunk; ``layers``/``frame`` are the two header bytes the app uses for
    real-time frame streaming.
    """
    ts = ("A" + (timestamp or timestamp_now())).encode("ascii")[:30].ljust(30, b"\x00")
    payload = bytes([function, action, 0, data_format])
    payload += _be(total_len if total_len is not None else (len(data) if data else 0), 4)
    payload += bytes([layers, frame]) + ts + _be(0, 2)
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


def build_enable_payload(laser_on: bool, run_mode: int,
                         play_state: int | None = None) -> bytes:
    """Payload of ENABLE_LASER_OUTPUT: [on/off, run mode(, play state)]."""
    out = bytes([1 if laser_on else 0, run_mode])
    return out if play_state is None else out + bytes([play_state])


def build_device_model(model: dict[str, int]) -> bytes:
    """Payload of DEVICE_SET_MODEL: the whole settings block (reserve bytes zero,
    exactly like the official app writes it)."""
    out = b""
    for name, size in DEVICE_MODEL_FIELDS:
        out += b"\x00" * size if name == "reserve" else _be(model[name], size)
    return out


def build_play_effect(index: int, duration_ms: int, channels: list[int] | bytes,
                      protocols: str = "") -> bytes:
    """Payload of REAL_TIME_PLAY/PLAY_EFFECT: ``[0, step index, time(2), channel values...]``.

    The time unit is 50 ms for protocol >= 1.0.1, otherwise whole seconds (like the app).
    """
    channels = bytes(channels)
    if any(v < 0 or v > 255 for v in channels):
        raise ValueError("channel values must be 0-255")
    modern = version_tuple(protocols or "0") >= (1, 0, 1)
    units = round(duration_ms / 50) if modern else duration_ms // 1000
    return bytes([0, index]) + _be(units, 2) + channels


def build_frame_play(page: int, file: int) -> bytes:
    """Payload of PATTERN_LIBRARY/FRAME_PLAYING."""
    return bytes([page, file])


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


def parse_fields(payload: bytes, layout: tuple[tuple[str, int], ...], *,
                 required: str | None = None) -> dict[str, int]:
    """Parse big-endian fields.

    If ``required`` is given, parsing stops quietly at the first field that does
    not fit (like the official app, which never validates length) and only fails
    if ``required`` was not reached. Without it, short payloads raise.
    """
    out: dict[str, int] = {}
    pos = 0
    for name, size in layout:
        chunk = payload[pos:pos + size]
        if len(chunk) < size:
            if required is None:
                raise ProtocolError(f"payload too short for {name}")
            break
        out[name] = int.from_bytes(chunk, "big")
        pos += size
    if required is not None and required not in out:
        raise ProtocolError(
            f"payload too short: {len(payload)} bytes, needed up to {required}")
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
    protocols: str = ""
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
        info.protocols = _ver(msg[155:158]) if len(msg) >= 158 else ""
    return info


def version_tuple(text: str) -> tuple[int, ...]:
    try:
        return tuple(int(p) for p in text.split("."))
    except ValueError:
        return (0,)


# ------------------------------------------------------------- pattern libraries

@dataclass(frozen=True)
class LibEntry:
    """One catalog page reported by SEARCH_LIB_FILE."""
    page: int
    delete: int
    step_total: int
    file_total: int
    files_number: int
    files_merge: int

    @property
    def is_effect(self) -> bool:
        return bool(self.delete & 0x40)

    @property
    def is_download(self) -> bool:
        return bool(self.delete & 0x10)

    @property
    def count(self) -> int:
        return min(self.step_total if self.is_effect else self.file_total, 255)


@dataclass
class Library:
    """A playable category (e.g. Timetunnel) made of one or more catalog pages."""
    files_number: int
    merge: int
    effect_group: bool
    name: str
    pages: list[tuple[int, int]] = field(default_factory=list)  # (page, count)
    effect_pages: set[int] = field(default_factory=set)  # pages that hold multi-step effects
    label: str = ""

    @property
    def size(self) -> int:
        return sum(c for _, c in self.pages)

    @property
    def key(self) -> str:
        """Stable identity (survives re-reading the catalog), used by saved playlists."""
        return f"{self.files_number}:{self.merge}:{int(self.effect_group)}"

    def item(self, n: int) -> tuple[int, int]:
        """Map pattern number n (1-based) to the device's (page, file)."""
        if not 1 <= n <= self.size:
            raise ValueError(f"pattern {n} out of range 1..{self.size}")
        for page, count in self.pages:
            if n <= count:
                return page, n
            n -= count
        raise ValueError("unreachable")


def parse_lib_entry(message: bytes) -> tuple[int, LibEntry]:
    """Parse one SEARCH_LIB_FILE reply -> (total pages, entry)."""
    payload = parse_data_response(message)
    if not payload:
        raise ProtocolError("empty library reply")
    f = parse_fields(payload[1:], LIB_FIELDS, required="filesMerge")
    return payload[0], LibEntry(
        page=f["searchLibPageNumber"], delete=f["searchLibDelete"],
        step_total=f["searchLibStepTotal"], file_total=f["searchLibFileTotal"],
        files_number=f["filesNumber"], files_merge=f["filesMerge"])


def build_libraries(entries: list[LibEntry]) -> list[Library]:
    """Group catalog pages into libraries the way the official app does."""
    groups: dict[tuple[bool, int, bool, int], Library] = {}
    for e in entries:
        name = LIBRARY_NAMES.get(e.files_number)
        if e.count <= 0 or name is None:
            continue
        effect_group = e.is_effect or e.is_download
        key = (effect_group, e.files_number, e.is_download, e.files_merge)
        lib = groups.get(key)
        if lib is None:
            lib = groups[key] = Library(e.files_number, e.files_merge, effect_group, name)
        lib.pages.append((e.page, e.count))
        if e.is_effect:
            lib.effect_pages.add(e.page)
    libs = sorted(groups.values(), key=lambda l: min(pg for pg, _ in l.pages))
    base_counts: dict[str, int] = {}
    for lib in libs:
        lib.name = lib.name + (str(lib.merge) if lib.merge else "")
        base_counts[lib.name] = base_counts.get(lib.name, 0) + 1
    for lib in libs:
        suffix = ""
        if base_counts[lib.name] > 1:
            suffix = " [effects]" if lib.effect_group else " [patterns]"
        lib.label = f"{lib.name}{suffix} ({lib.size})"
    return libs


# ----------------------------------------------------- multi-packet transfers

def build_continuation(chunk: bytes) -> bytes:
    """Follow-up packet of a transfer that did not fit in one packet."""
    return _frame(CMD_TRANSFER_CONT, chunk)


def split_transfer(function: int, action: int, data: bytes, buffer_max: int, *,
                   data_format: int, layers: int = 0, frame: int = 0,
                   timestamp: str | None = None) -> list[bytes]:
    """Split ``data`` into packets like the app: the first carries ``buffer_max-47``
    bytes of data, every following one ``buffer_max-5``."""
    if buffer_max < 64:
        raise ProtocolError(f"implausible packet size {buffer_max}")
    first, rest = buffer_max - 47, buffer_max - 5
    packets = [build_transfer(function, action, data[:first], data_format=data_format,
                              timestamp=timestamp, total_len=len(data), layers=layers,
                              frame=frame)]
    pos = first
    while pos < len(data):
        packets.append(build_continuation(data[pos:pos + rest]))
        pos += rest
    return packets


# ------------------------------------------------------ real-time point frames

# A frame is a list of paths; a path is a list of (x, y, (r, g, b)).
RGB = tuple[int, int, int]
Path = list[tuple[float, float, RGB]]


def _js_round(x: float) -> int:
    return math.floor(x + 0.5)


def _corner_angle(v: tuple, a: tuple, n: tuple) -> float:
    """Angle at ``v`` between the directions to ``a`` and ``n`` (degrees)."""
    ax, ay = a[0] - v[0], a[1] - v[1]
    bx, by = n[0] - v[0], n[1] - v[1]
    denom = math.sqrt(ax * ax + ay * ay) * math.sqrt(bx * bx + by * by)
    if denom == 0:
        return math.nan
    c = max(-1.0, min(1.0, (ax * bx + ay * by) / denom))
    return math.acos(c) * (180 / math.pi)


def normalize_frame(paths: list[Path], width: float, height: float) -> list[Path]:
    """Scale local coordinates to the 0..65535 square, aspect-preserving and
    centred (what the app does for every layer before sending)."""
    t = 65535 / max(width, height)
    ox = (65535 - t * width) / 2
    oy = (65535 - t * height) / 2
    out: list[Path] = []
    for path in paths:
        pts = [(math.floor(x * t + ox), math.floor(y * t + oy), rgb) for x, y, rgb in path]
        pts = [pt for pt in pts if 0 <= pt[0] <= 65535 and 0 <= pt[1] <= 65535]
        if pts:
            out.append(pts)
    return out


def encode_frames(frames: list[list[Path]], data_format: int) -> bytes:
    """Encode frames of normalized points (formats 3 and 4 only).

    Layout: for each frame a 2-byte point count, then all points. A point is
    ``x(2) y(2) flags(1) colour`` where colour is a palette index (format 3) or
    r,g,b (format 4); ``flags`` = corner angle (6 bits) | 64 path start | 128 end.
    """
    if data_format not in (3, 4):
        raise ProtocolError(f"point data format {data_format} is not supported")
    counts = bytearray()
    body = bytearray()
    prev = None
    last_frame = len(frames) - 1
    for s, frame in enumerate(frames):
        counts += _be(sum(len(path) for path in frame), 2)
        for c, path in enumerate(frame):
            closed = bool(path) and path[0][:2] == path[-1][:2]
            start, end = 64, 0
            for m, v in enumerate(path):
                if len(path) > 1 and m == len(path) - 1 and c == len(frame) - 1 and s == last_frame:
                    start, end = 0, 128
                if prev is None:
                    prev = (path[-2] if len(path) >= 2 else None) if closed else path[-1]
                if m + 1 == len(path):
                    nxt = (path[1] if len(path) >= 2 else None) if closed else path[0]
                else:
                    nxt = path[m + 1]
                angle = _corner_angle(v, prev, nxt) if prev and nxt else 180
                bits = 0
                if not math.isnan(angle):
                    bits = math.floor(_js_round(angle) / 180 * 63)
                flags = bits | start | end
                x, y, rgb = _js_round(v[0]), _js_round(v[1]), v[2]
                body += _be(x, 2) + _be(y, 2) + bytes([flags])
                if data_format == 3:
                    body += bytes([max(PALETTE_INDEX.get(rgb, 0), 0)])
                else:
                    body += bytes(rgb)
                prev = v
                start = end = 0
    return bytes(counts + body)


def max_points(scene_max: int, data_format: int) -> int:
    """How many points the device accepts in one real-time play (from the app)."""
    return (512 * scene_max - 128) // (6 if data_format == 3 else 1)


# ------------------------------------------------- reading patterns back (thumbnails)

PATTERN_HEADER_FIELDS: tuple[tuple[str, int], ...] = (
    ("patternLibIndex", 1), ("patternTotal", 1), ("patternIndex", 1), ("frameTotal", 2),
    ("frameIndex", 2), ("pointFormat", 1), ("pointTotal", 2), ("pointOffset", 2),
)


def build_pattern_read(page: int, file: int, frame: int = 1, offset: int = 1) -> bytes:
    """Payload of SEARCH_PATTERN_LIB_DATA."""
    return bytes([page, 0, file]) + _be(0, 2) + _be(frame, 2) + bytes([0]) + _be(0, 2) + _be(offset, 2)


def build_effect_read(page: int, step: int) -> bytes:
    """Payload of SEARCH_EFFECT_LIB_DATA."""
    return bytes([page, 0, step])


# a decoded device point: (x 0..254, y 0..254, state flags, 0xRRGGBB)
DevicePoint = tuple[int, int, int, int]


def parse_pattern_chunk(message: bytes) -> tuple[dict[str, int], list[DevicePoint]]:
    """Parse one SEARCH_PATTERN_LIB_DATA reply."""
    if len(message) < 19 or message[4] != 0xAA or message[5] != 0x55:
        raise ProtocolError("bad pattern data reply")
    header = parse_fields(message[7:19], PATTERN_HEADER_FIELDS)
    fmt = header["pointFormat"]
    if fmt not in (3, 5):
        raise ProtocolError(f"unsupported stored point format {fmt}")
    body = message[19:19 + max(0, message[6] - 15)]
    points: list[DevicePoint] = []
    for i in range(0, len(body) - fmt + 1, fmt):
        b0, b1 = body[i], body[i + 1]
        state = (128 if b0 & 128 else 0) | (64 if b1 & 128 else 0)
        if fmt == 3:
            idx = body[i + 2]
            r, g, b = PALETTE[idx] if idx < len(PALETTE) else (255, 255, 255)
        else:
            r, g, b = body[i + 2], body[i + 3], body[i + 4]
        points.append((2 * (b0 & 127), 2 * (b1 & 127), state, (r << 16) | (g << 8) | b))
    return header, points


def parse_effect_step(message: bytes) -> dict[str, int]:
    """Parse the first record of a SEARCH_EFFECT_LIB_DATA reply."""
    if len(message) < 15 or message[4] != 0xAA or message[5] != 0x55:
        raise ProtocolError("bad effect data reply")
    return {"page": message[7], "step_total": message[8], "step_start": message[9],
            "channels": message[10], "play_ms": 50 * message[11], "sub_steps": message[12],
            "pattern_lib": message[13], "pattern_index": message[14]}


# ------------------------------------------------------------ hardware effects

def effect_layout_base(layout: int, scene_channels: int | None) -> int:
    """First DMX channel (1-5) that the effect value array starts at.

    ``layout`` 0 = decide from the laser's own scene-channel count: an array of N
    values covers the last N of the 16 channels, so it starts at channel 17 - N.
    """
    if layout:
        return layout
    if not scene_channels:
        return 3          # unknown: assume it starts at the pattern group, like library effect steps
    base = 17 - scene_channels
    if not 1 <= base <= 5:
        raise ProtocolError(
            f"the laser reports {scene_channels} effect channels; only layouts of 12-16 "
            "channels (16CH chart) are known")
    return base


def hw_effect_channels(effect: int, speed: int, base: int, *, flow_zones: int = 40,
                       flow_speed: int = 36, reverse: bool = False) -> list[int]:
    """Effect value array (channels ``base``..16) for a hardware effect.

    Unused channels are 0 (the app pads with zeros too). Colour is set to
    "original" and intensity (if part of the array) to full so nothing blacks out.
    """
    if effect not in HW_EFFECTS or effect == 0:
        raise ValueError("invalid hardware effect")
    speed = min(127, max(1, int(speed)))
    ch: dict[int, int] = {1: 255, 5: 2}
    if effect in (1, 2, 3, 4, 5, 6):
        ch[{1: 9, 2: 10, 3: 11, 4: 12, 5: 13, 6: 14}[effect]] = 128 + speed
    elif effect == 7:
        ch[16] = speed                       # X wave speed 1-127
    elif effect == 8:
        ch[16] = 128 + speed                 # Y wave speed 128-255
    elif effect == 9:
        v = max(44, min(236, 4 * int(flow_zones)))
        ch[5] = v - (v - 44) % 4             # one of the "multiple flow effects", 4 values per step
        ch[6] = (128 if reverse else 0) + max(4, min(127, 2 * int(flow_speed)))
    elif effect == 10:
        ch[5] = 248                          # gradual drawing effect
        ch[15] = min(255, 2 * speed)
    needed = [c for c in ch if c != 1 and (c != 5 or effect in (9, 10))]
    if min(needed) < base:
        raise ProtocolError(
            f"{HW_EFFECTS[effect]} needs channel {min(needed)}, but the effect layout starts at channel {base}")
    return [ch.get(c, 0) for c in range(base, 17)]
