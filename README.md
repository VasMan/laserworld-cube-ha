# Laserworld Cube Laser (Bluetooth) for Home Assistant

Local Bluetooth control of Laserworld **Cube** lasers (the ones controlled by the
*Cube Laser Control* app, BLE name `BLEAPP_xxxx`). No cloud needed at runtime.

> ⚠️ **Laser safety.** This integration can switch laser output on from automations.
> Only automate it where that is safe (no one can be in the beam path, audience-scanning
> rules, etc.). The laser switch is *never* restored to "on" after a restart.

## Install (HACS)
1. Put this repo on GitHub as `VasMan/laserworld-cube-ha`.
2. HACS → ⋮ → *Custom repositories* → add the repo URL, category **Integration** → install → restart HA.
3. Make sure Home Assistant has a Bluetooth adapter or an ESPHome Bluetooth proxy in range of the laser.
4. The laser should be auto-discovered (*Settings → Devices & services*), or *Add integration → Laserworld Cube Laser*.

## Prerequisites
* The laser must have been **connected once with the official app** (it needs internet that one time to
  *activate* the device). This integration deliberately does not do activation.
* If you bound the laser to your account in the app (*Bind device*), either unbind it, or enter your
  numeric account user ID under the integration's **Configure** options. The integration honours the
  app's binding and refuses to control a laser bound to someone else.
* Close the phone app before using HA — a laser accepts **one** Bluetooth connection at a time.
  HA disconnects after 30 s idle (configurable) so the phone can reconnect; the
  *Disconnect Bluetooth* button frees it immediately.

## Playing the built-in patterns (APP mode)
The laser only projects something in APP mode once it has been told *what* to play, like the
Timetunnel / Northlight / Animation / Outdoors / Hotspot libraries in the official app.

1. Turn **Laser output** on (it is an assumed state – see below).
2. Pick a **Pattern library** (read from the laser's own catalog, with its pattern count).
3. Set **Pattern number** – this plays that pattern immediately. Or use **Previous / Next pattern**.
4. Optional: **Pattern color** (original colors, solid colors, or flowing), **Loop play** with
   **Loop mode** (Loop / Random / Sequence / Single) and **Loop interval**.
5. **Pause**, **Play** (resume) and **Stop** map to the laser's play states.

Playing a pattern switches the laser to APP mode and respects the Laser output switch: if HA shows
the laser as off, the pattern is sent but the laser stays off.

## Pattern thumbnails
The laser itself stores the shapes of its built-in patterns, so the integration can read them back
and draw thumbnails like the official app's library page.

1. Select a **Pattern library** and press **Build thumbnails** (diagnostic section). This reads every
   pattern of that library from the laser in the background – roughly a minute for a few dozen
   patterns, longer for big libraries like *Hotspot (128)*. Watch **Thumbnail status**.
2. **Library overview** (image) shows a numbered grid of 20 patterns around the current one, with the
   current pattern highlighted in blue, just like the app. It pages along as you step through patterns.
3. **Pattern preview** (image) shows the current pattern large.

Thumbnails are cached, so each library is only read once (use **Rebuild thumbnails** if you change the
laser's content). While a build runs the integration keeps the Bluetooth link open, so close the phone app.

Example dashboard card (your entity IDs may differ):

```yaml
type: vertical-stack
cards:
  - type: picture-entity
    entity: image.laserworld_cube_847e_library_overview
    show_name: false
    show_state: false
  - type: entities
    entities:
      - select.laserworld_cube_847e_pattern_library
      - number.laserworld_cube_847e_pattern_number
      - entity: button.laserworld_cube_847e_previous_pattern
      - entity: button.laserworld_cube_847e_next_pattern
```

## Showing text
Type into the **Text** entity (or call `text.set_value`) and the laser displays it immediately, like
the app's *Text* page. Change **Text color** (single colors or *Rainbow* = one color per letter) and
**Text size**; if text is showing it updates right away. **Clear text** removes it, **Play text**
re-sends it. Multi-line text works too (`\n`, e.g. from a template).

Notes: the text uses a simple built-in single-line font (A–Z, a–z, digits and common punctuation;
accents are dropped). Text fills the laser's full width at 100 % – reduce **Text size** (or the laser's
own *Size* controls) for short words. Scrolling/animated text is not supported yet. Text needs the
laser's point data format 3 or 4 (the normal one); an error message tells you if yours differs.

## Entities
| Entity | Notes |
|---|---|
| Laser output (switch) | Assumed state – the device does not report it |
| Pattern library (select) | From the laser's catalog, e.g. `Timetunnel (8)`, `Hotspot (128)` |
| Pattern number (number) | Setting it plays that pattern |
| Play / Pause / Stop / Previous / Next (buttons) | Player controls |
| Loop play (switch), Loop mode, Loop interval | Cycling is timed by Home Assistant, like the phone app does |
| Pattern preview, Library overview (images) | Thumbnails read from the laser (see above) |
| Build / Rebuild / Cancel thumbnails (buttons), Thumbnail status (sensor) | Diagnostic |
| Text (text), Text color (select), Text size (number), Play text / Clear text (buttons) | Show text on the laser |
| Pattern color (select) | Original colors, White … Purple, Flowing (+ optional *Color flow*, *Color flow speed*) |
| Run mode (select) | APP mode / DMX512 mode / ILDA mode |
| APP work mode (select) | Automatic / Voice |
| Laser color mode (select) | The device's 12 color-function modes |
| Auto speed, Voice sensitivity | 0–100 % |
| Size X / Y, Position X / Y, Rotation | Real-time parameters |
| Read settings / Disconnect Bluetooth (buttons) | diagnostic |

On connect, current size/position/speed settings and the pattern-library catalog are read from the
laser. Laser on/off, run mode and play state cannot be read back, so they are assumed.

Requires the *Pillow* image library (Home Assistant installs it automatically if missing).

**Per-pattern durations** (the "03.3" shown in the app) come from the app's cloud resources and are
not stored in the laser, so Loop play uses one fixed interval instead.

## Not (yet) supported
Playlists / *Offline play*, drawing and image playback, scrolling text, *Program* (custom effects), DMX channel
console, device setup (invert, scan rate persistence), activation, binding management.

## Troubleshooting
* Enable debug logs: `logger: logs: custom_components.laserworld_cube: debug`.
* *"not been activated"* → connect once with the official app.
* *"bound to a different account"* → unbind in the app or set the user ID option.
* *Timeouts / handshake failures* → move the adapter/proxy closer; the handshake reply is ~170 bytes and
  needs a negotiated MTU (BlueZ and ESPHome proxies do this automatically). Check the BLE name HA
  sees is exactly your device name — the handshake key is derived from it.

## Status & testing
Developed by analysing the official Android app. The crypto, packet framing and parameter layouts are
verified **byte-for-byte against vectors generated by the app's own JavaScript** (`tests/test_protocol.py`),
and the session logic is tested against a simulated device. **It has not been tested on real hardware
or inside a real Home Assistant yet** – please open an issue with debug logs if something differs.

Run tests: `python3 tests/…` files are plain pytest-style functions (`pytest tests/`).
See `PROTOCOL.md` for the wire protocol.

Unofficial; not affiliated with Laserworld or Temei.
