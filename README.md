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
2. **Library overview** (image) shows a numbered grid of 20 patterns, with the current pattern
   highlighted in blue, just like the app. Libraries with more than 20 patterns have several pages:
   use **Overview page** (number) or the **Overview previous/next page** buttons to browse them. The
   page follows along when you play or step to another pattern.
3. **Pattern preview** (image) shows the current pattern large.

Thumbnails are cached, so each library is only read once (use **Rebuild thumbnails** if you change the
laser's content). While a build runs the integration keeps the Bluetooth link open, so close the phone app.

### Pick patterns visually (dashboard card)
Home Assistant's pop-up for an image can't contain buttons, so browsing happens on a dashboard card.
`dashboard/library_browser.yaml` gives you a card where you can **tap any thumbnail to play it** and
**tap the ◀ ▶ arrows drawn on the image to change page** – no leaving the view:

1. Dashboard → ⋮ → *Edit dashboard* → **Add card** → scroll to **Manual**.
2. Paste the contents of `dashboard/library_browser.yaml` and save.
3. If your entity IDs differ (they follow your device name), run
   `python3 tools/make_dashboard.py your_device_slug` (e.g. `laserworld_cube_847e`) and paste again, or
   just edit the entity IDs in the card.

Underneath the card the same functions are available as services (`laserworld_cube.play_overview_tile`,
`overview_next_page`, `overview_previous_page`), so you can also call them from scripts.

## Showing text
Type into the **Text** entity (it starts as *Alexandros*; change it any time) and the laser displays
it immediately, like the app's *Text* page. **Play text** re-sends it, **Clear text** removes it.

| Control | What it does |
|---|---|
| Text color | **Rainbow** (one color per letter, the default) / White / Red / Yellow / Green / Cyan / Blue / Purple, or **Color flow** (white text recolored by the laser's flowing-color mode) |
| Flow zones, Flow speed | The color-flow settings (the app's *LaserZones* / *FlowSpeed*); used by *Color flow* |
| Text size | 10–100 % of the laser's frame |
| Text orientation | Horizontal, or Vertical (letters stacked top to bottom) |
| Text direction | Forward, or Reverse (letters in reverse order) |
| Effect, Effect speed | Motion effects – see below |

Changing any of these while text is showing updates it right away. Multi-line text works too (`\n`).

### Software effects
The official app's text effects (Rotate, VBmove, …) are **downloaded from the manufacturer's cloud**
and are not contained in the app, so they can't be copied exactly. Instead this integration provides
its own motion effects, driven from Home Assistant by continuously updating the laser's *position*,
*rotation* and *size*: **Scroll right / Scroll left / Bounce horizontal / Bounce vertical / Rotate /
Pulse**. They work on text and on patterns. **Effect speed** goes from slow (30 s per cycle) to fast
(1.5 s per cycle).

### Hardware effects (smooth, run by the laser itself) – experimental
Using Laserworld's *DMX chart CUBE series* (Standard mode 16CH) the integration can ask the laser to
animate the text with its **built-in effect engine**, so the motion is perfectly smooth:

| Hardware effect | DMX channel used |
|---|---|
| Rotate Z / X / Y | CH9 / CH10 / CH11 (speed range) |
| Horizontal / Vertical movement | CH12 / CH13 (speed range) |
| Zoom | CH14 (zoom speed range) |
| X waves / Y waves | CH16 |
| Color flow | CH5 (flow effects) + CH6 (color speed) – uses *Flow zones* / *Flow speed* |
| Gradual drawing | CH5 + CH15 (text is drawn progressively) |

Choose **Hardware effect** and set **Hardware effect speed** (1–127). It is sent exactly the way the
official app sends its effects (effect values first, then the text).

**One thing needs confirming on your laser: the *layout*.** The list of values the laser expects covers
the last N of the 16 channels, and N is reported by the laser itself (*DMX channel counts* sensor, third
number = scene channels; 14 means it starts at CH3, 16 means CH1). **Hardware effect layout** is
*Automatic* by default and uses that number. If an effect does nothing or the text disappears:
1. Pick **Rotate Z** and watch. 2. Switch **Hardware effect layout** (Configuration section) through
*From CH1 … From CH5* – the right one makes the text spin. 3. Tell me which one worked and the sensor's
numbers so Automatic can be fixed. Choose *None* to go back to normal text.

### Why doesn't text move by itself like in the official app?
I analysed the app: its text keeps moving because, for every text it plays, it first sends a list of
**effect channel values** (a DMX-style list of up to a few dozen numbers) to the laser's built-in effect
engine and *then* the text points. That is why plain points (what this integration sends) stay still.
What each channel means (which one is flow speed, zones, direction…) is defined in the manufacturer's
**cloud**, not in the app, so it can't be read from the app itself.

This integration can already send exactly that command – the experimental **`laserworld_cube.send_effect`**
service on the *Text* entity – so the mapping can be found by experiment (Developer tools → Actions):

```yaml
action: laserworld_cube.send_effect
target:
  entity_id: text.laserworld_cube_847e_text
data:
  channels: [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]   # 16 values, 0-255
  duration: 5
```
Change one value at a time and watch what the text does; an empty `channels: []` removes the effect again.
The channel list stays attached to the text (like in the app), so changing color/size re-sends it.
The app's own default text effect also uses *LaserZones*, *FlowSpeed* and a direction value that are
not on the 16-channel chart; they most likely belong to the laser's **36-channel layout**. If you can
share that chart (the other table in the same PDF) those can be added as well.

Things to know:
* Content never leaves the laser's field. Scroll/bounce need room to move, so reduce **Text size** (or
  Size X/Y) to e.g. 50 % first – you get a message if there is no room.
* Motion is sent over Bluetooth in small steps, so it is not as smooth as an effect running inside the
  laser. Slow to medium speeds look best. While an effect runs the Bluetooth link stays open.
* Selecting **None** (or Stop / Disconnect) puts position, rotation and size back where they were.

Other notes: the text uses a built-in single-line font (A–Z, a–z, digits, common punctuation; accents
are dropped). At 100 % text fills the full width. Text needs the laser's point data format 3 or 4 (the
normal one); an error tells you if yours differs.

## Device settings (the app's "Laser device settings")
These are the laser's **saved** settings, found in the *Configuration* section of the device page. They
change the laser permanently (like the app does), not just the current show:

| Entity | Setting |
|---|---|
| DMX address | 1–512 |
| DMX mode | e.g. *DMX mode 16CH* (channel counts come from the laser) |
| Functional mode | DMX512 / Auto / Music / ILDA mode |
| Scanning speed | 15–40 KPPS *(disabled by default)* |
| Device size X/Y, Device position X/Y | Saved size (10–100 %) and position (0–255) |
| Invert X, Invert Y, Swap X/Y | Axis settings |
| Color setting | The 12 color functions, e.g. *11.RGB* |
| Master | Master/slave |
| Safety | The laser's safety feature *(disabled by default)* |
| Laser type, Red/Green/Blue maximum | TTL/Analog and per-color output limits *(disabled by default)* |

Every change re-reads the laser's current settings first and writes back the whole block with only your
change applied, exactly like the official app. **Scanning speed, Safety, Laser type and the color
maximums are disabled by default** because wrong values can affect the laser hardware or its safety
behavior – enable them in *Settings → Devices → entities* only if you need them. Turning *Safety* off
or changing *Scanning speed* is your responsibility.

## Entities
| Entity | Notes |
|---|---|
| Laser output (switch) | Assumed state – the device does not report it |
| Pattern library (select) | From the laser's catalog, e.g. `Timetunnel (8)`, `Hotspot (128)` |
| Pattern number (number) | Setting it plays that pattern |
| Play pattern / Pause / Stop / Previous pattern / Next pattern (buttons) | Player controls |
| Loop play (switch), Loop mode, Loop interval | Cycling is timed by Home Assistant, like the phone app does |
| Pattern preview, Library overview (images) | Thumbnails read from the laser (see above) |
| Build / Rebuild / Cancel thumbnails (buttons), Thumbnail status (sensor) | Diagnostic |
| Text, Text color / size / orientation / direction, Flow zones / speed, Play text / Clear text | Show text on the laser |
| Software effect, Software effect speed | Stepwise motion effects driven from Home Assistant (scroll, bounce, rotate, pulse) |
| Hardware effect, Hardware effect speed, Hardware effect layout | Smooth laser-side effects for text (rotate, move, zoom, waves, color flow, gradual drawing) |
| DMX channel counts (sensor) | The laser's standard / professional / scene channel counts (diagnostic) |
| Overview page, Overview previous / next page | Browse libraries with more than 20 patterns |
| DMX address/mode, Functional mode, Scanning speed, Device size/position, Invert/Swap, Color setting, Master, Safety, … | Saved device settings (Configuration) |
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
Playlists / *Offline play*, drawing and image playback, the app's own cloud-defined effects, *Program*, DMX channel
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
