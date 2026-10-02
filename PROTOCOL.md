# Cube Laser Control BLE protocol (as implemented)

Transport: GATT service `FFE0`, characteristic `FFE1` (write + notify). One notification = one response.
Advertised name: `BLEAPP_<id>`.

Frame: `cmd, 0x12, 0x34, len(2, BE), payload`. Byte 0 is clear; bytes after it are AES-128-CTR
(≤240 bytes encrypted, zero-padded to 16; any remainder is clear).

| Request cmd | Response cmd | Purpose |
|---|---|---|
| 0xAB | 0x8B | handshake |
| 0xAD | 0x85 | data transfer (function, action, data) |
| 0xAA | 0x8A | simple function (e.g. disconnect = 7,1) |

Response header: `cmd, addr(2), status`; data responses continue `AA 55 len payload`.

Keys
* Handshake: offset = int(name.split('_')[1][1] + [3], 16); key = table[offset..+16], IV = table[offset+16..+32].
* After handshake, activateType 1: key = pad16(deviceSecret), IV = pad16(deviceKey) (values from the reply).

Transfer payload: `func, action, 0, dataFormat, dataLen(4), 0, 0, "A"+yyyymmddHHMMSS (30 B), 0 0, data`.

Used actions (function 1): 5 ENABLE_LASER_OUTPUT `[on, runMode]`; 11 SEARCH_DEVICE_SET_MODEL;
12 SET_RUN_PARAMETERS (38 B struct); 25 GET_DEVICE_BIND_INFO. Run modes: 0 APP, 1 DMX512, 4 ILDA.
