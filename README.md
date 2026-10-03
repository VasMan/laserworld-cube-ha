# Laserworld Cube Laser (Bluetooth) for Home Assistant

Local Bluetooth control of Laserworld **Cube** lasers (the ones controlled by the
*Cube Laser Control* app, BLE name `BLEAPP_xxxx`). No cloud needed at runtime.

<p align="center">
  <img src="https://raw.githubusercontent.com/VasMan/laserworld-cube-ha/main/docs/images/preview.png" alt="Preview of the Laserworld Cube integration in Home Assistant" width="800">
</p>

*A Home Assistant dashboard built from this integration: laser control, the pattern library browser
(tap a thumbnail to play, ◀ ▶ to change page), playlists, text and picture projection.*

> ⚠️ **Laser safety.** This integration can switch laser output on from automations.
> Only automate it where that is safe (no one can be in the beam path, audience-scanning
> rules, etc.). The laser switch is *never* restored to "on" after a restart.

## Disclaimer
**Use this integration entirely at your own risk.**

* This is an **unofficial, community project**. It is **not affiliated with, endorsed by or supported by
  Laserworld, Temei or Home Assistant**. It was written by analysing the behaviour of the official app and
  may stop working at any time, for example after a laser firmware or app update.
* It is provided **"as is", without warranty of any kind**, express or implied, including but not limited
  to fitness for a particular purpose, reliability or safety.
* **The author accepts no responsibility or liability** for any damage, injury, loss or consequence of any
  kind arising from the installation or use of this integration. This includes, without limitation: damage
  to or malfunction of the laser or other equipment, loss of warranty, eye or skin injury, property damage,
  fire, legal or regulatory problems, and data loss.
* **Lasers can cause serious, permanent eye injury.** You alone are responsible for operating the laser
  safely and lawfully, including keeping the beam away from people, aircraft, vehicles and animals, and
  complying with local laser-safety rules. Never rely on this software as a safety system. Automations,
  scripts, voice assistants and network access can turn laser output on – consider carefully who and what
  can control it.
* Some entities change the **laser's saved settings** (for example scanning speed, color output limits,
  Safety and DMX settings). Wrong values can affect hardware or safety behavior. Change them only if you
  understand the effect; they are disabled by default for that reason.
* You are responsible for complying with the terms of your laser, the official app and any applicable laws.

If you do not agree with these terms, do not install or use this integration.

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
**tap the ◀ ▶ arrows drawn on the image to change page**, and **long-press a thumbnail to add it to the playlist** – no leaving the view:

1. Dashboard → ⋮ → *Edit dashboard* → **Add card** → scroll to **Manual**.
2. Paste the contents of `dashboard/library_browser.yaml` and save.
3. If your entity IDs differ (they follow your device name), run
   `python3 tools/make_dashboard.py your_device_slug` (e.g. `laserworld_cube_847e`) and paste again, or
   just edit the entity IDs in the card.

Underneath the card the same functions are available as services (`laserworld_cube.play_overview_tile`,
`overview_next_page`, `overview_previous_page`), so you can also call them from scripts.

## Playlists
Build your own show from patterns of **any libraries** and choose **how long each one stays on** – like
the official app's *Equipment playList*. Playlists are saved and survive restarts; you can have several.

**Build one**
1. Press **New playlist** (it starts as *Playlist 1*, *2*, …), then type your own name in **Playlist name**
   (up to 40 characters). Editing that field **renames** the active playlist at any time, even while it plays;
   if you have no playlist yet, typing a name creates one with that name.
2. Set **Playlist item duration** (seconds the *next* pattern will stay on).
3. Find a pattern – pick a library and number, or tap it in the library browser card – then press
   **Add to playlist**. Repeat with other libraries and other durations.
   *Shortcut:* **long-press a thumbnail** in the library browser card to add it directly.
4. **Playlist overview** (image) shows the list with thumbnails, group, pattern number and on-time;
   **Playlist summary** (sensor) lists the items. Fix mistakes with **Remove last playlist item**,
   **Clear playlist**, or the services below.

**Play it**: turn on **Play playlist**. Each pattern is shown for its own time; **Repeat playlist**
(on by default) loops forever, otherwise it plays once and stops. Edits made while it plays take effect
immediately. Turning the laser off, pressing Stop, starting Loop play or showing text ends it.
Choose another playlist with the **Playlist** select; **Delete playlist** removes the active one.

**Services** (for automations and scripts; target the *Playlist* select, or the overview image for tiles):

| Service | What it does |
|---|---|
| `laserworld_cube.playlist_create` | Create a playlist with a `name` (default *Playlist N*) and make it active |
| `laserworld_cube.playlist_rename` | Give a playlist a new `name` (default: the active one) |
| `laserworld_cube.playlist_add` | Add a pattern: `library` (e.g. `Hotspot`), `pattern`, `duration`, optional `playlist`, `position` |
| `laserworld_cube.playlist_remove` | Remove item `index` (default: the last) |
| `laserworld_cube.playlist_set_duration` | Change how long item `index` stays on |
| `laserworld_cube.playlist_move` | Move item `index` to position `to` |
| `laserworld_cube.playlist_add_tile` | Add tile 1–20 of the library overview image (what long-press uses) |

```yaml
action: laserworld_cube.playlist_add
target:
  entity_id: select.laserworld_cube_847e_playlist
data:
  library: Hotspot
  pattern: 45
  duration: 12
```

Dashboard: `dashboard/playlist.yaml` is a ready-made card (add it like the library browser card).

**How this differs from the app's "Offline play":** here Home Assistant times the playlist, so it needs
to stay running and connected over Bluetooth while it plays (and the phone app must be closed). The
app's *Offline play* saves a list **into the laser** so it plays on its own – but in the laser's
protocol such a saved list holds only the patterns, **without per-pattern durations**, so it cannot do
what you asked for. This integration therefore does the timing itself.

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

## Showing pictures
Pick a picture and the laser draws it, like the official app's *Draw → picture* tool.

**Privacy:** the app uploads the picture to the manufacturer's cloud (and needs your login) to convert it.
This integration converts it **locally inside Home Assistant** – nothing leaves your network.

**1. Get a picture into Home Assistant**
* In the sidebar open **Media → My media**, and use **Upload** (any folder; a folder called
  `laserworld_cube` keeps them tidy), **or** copy files to your `media` folder (or `www`).
* Press **Refresh picture list** (diagnostic section) – the **Picture** select lists the pictures it finds.

**2. Show it**: choose it in the **Picture** select. The laser draws it right away, and **Display preview**
(image) shows exactly what is being drawn. Other ways: **Show picture** (button, re-converts the last one) or
the `laserworld_cube.show_image` action (below), which can also take a picture straight from the media browser.

**3. Tune it** – changing any of these updates a picture that is showing:

| Control | What it does |
|---|---|
| Picture mode | **Outline** (edges – photos, line art), **Silhouette** (outlines of solid shapes – logos, icons), **Lines** (single centre lines – drawings, handwriting, text) |
| Picture color | **Original colors** (nearest laser color per line), one color, or **Rainbow** |
| Picture detail | 1–100: how many lines and points are kept (the laser's own point limit is respected) |
| Picture size | 10–100 % of the laser's frame |
| Invert picture | For light-on-dark pictures (white logo on black) |

Tips: logos and icons → *Silhouette*; photos → *Outline* with higher detail; drawings → *Lines*. Transparent
PNGs work best (the shape is taken from the transparency). The picture becomes **lines**, not a filled image –
that is what a laser can draw. If nothing is found you get a message to try another mode or *Invert*.
Pictures up to 15 MB are accepted (PNG, JPEG, GIF – first frame, BMP, WebP). **Clear text** removes a picture too.

**Action** (Developer tools → Actions, target the *Text* entity):

```yaml
action: laserworld_cube.show_image
target:
  entity_id: text.laserworld_cube_847e_text
data:
  media:
    media_content_id: media-source://media_source/local/logo.png
    media_content_type: image/png
  mode: silhouette
  color: original
  detail: 60
  size: 80
```
Instead of `media` you can give `path` (e.g. `/media/logo.png`, `/local/logo.png`) or a web URL.
Files must be in a folder Home Assistant may read (the media folder always is).
`dashboard/picture.yaml` is a ready-made card (add it like the other cards).
Hardware effects (rotate, zoom, …) and the software effects also work on pictures.

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
| Picture, Picture mode / color / detail / size, Invert picture, Show picture, Refresh picture list, Display preview (image) | Show a picture (converted locally) on the laser |
| Playlist, New / Delete playlist, Add to playlist, Remove last item, Clear playlist, Playlist item duration, Play playlist, Repeat playlist, Playlist overview (image), Playlist summary (sensor) | Playlists of patterns from any library, each with its own on-time |
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
Saving a playlist into the laser (*Offline play*), the interactive *Draw* editor (shapes, freehand), the app's own cloud-defined effects, *Program*, DMX channel
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

Unofficial; not affiliated with Laserworld or Temei. See the **Disclaimer** at the top: use at your own risk.
