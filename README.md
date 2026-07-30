<p align="center">
  <img src="assets/logo.svg" width="128" alt="SpaceMouse WASD logo"/>
</p>

<h1 align="center">SpaceMouse WASD</h1>

<p align="center">
  Spacemouse-style fly-through navigation for Autodesk Fusion —<br/>
  using nothing but your regular mouse and keyboard.
</p>

<p align="center">
  <img alt="Platform" src="https://img.shields.io/badge/platform-Windows%20%7C%20macOS-0078d4"/>
  <img alt="Python" src="https://img.shields.io/badge/python-3.9%2B-3776ab"/>
  <img alt="Dependencies" src="https://img.shields.io/badge/dependencies-none%20on%20Windows-38e1c8"/>
  <img alt="License" src="https://img.shields.io/badge/license-MIT-green"/>
</p>

---

Hold a side mouse button and Fusion turns into a video game: the mouse orbits
the model (cursor hidden, spacemouse-style), keys glide you around with
smooth, velocity-damped motion. Release the button and everything is
instantly back to normal.

## Controls (defaults — all rebindable in the app)

| Input | Action |
|---|---|
| **Hold forward side button** | Enter fly mode |
| **Mouse** | Orbit around the model |
| **W / S** | Pan up / down |
| **A / D** | Pan left / right |
| **Shift / Ctrl** | Zoom in / out (smooth dolly toward the model) |
| **Scroll wheel** | Adjust speed on the fly |
| **Esc** | Bail out of fly mode |

## How it works

Two parts talk over localhost UDP (port 42737):

```
┌─────────────────────────┐   motion packets    ┌─────────────────────────┐
│  Controller app (this   │ ──── UDP 42737 ───► │  Fusion add-in          │
│  repo's UI): low-level  │                     │  smooths velocities and │
│  input hooks capture    │ ◄─── heartbeat ──── │  drives the viewport    │
│  mouse + bound keys     │                     │  camera every frame     │
└─────────────────────────┘                     └─────────────────────────┘
```

The external controller is required because Fusion binds keys like `S` and
`D` to commands — a low-level hook intercepts your movement keys *only while
flying*, so they never trigger anything. The add-in applies exponential
velocity smoothing (the spacemouse "glide") and re-anchors the orbit/zoom
pivot onto the model itself, so zooming flies toward the geometry and
orbiting after a deep zoom rotates around what you're looking at.

On Windows both parts are pure Python standard library — no packages to
install. On macOS the controller needs `pyobjc` for the Quartz event tap.

## Install — Windows

Requirements: Windows 10/11, [Python 3.9+](https://python.org) (check
"Add to PATH" when installing), Autodesk Fusion.

1. Download this repo (green **Code** button → Download ZIP) and unzip it
   somewhere permanent.
2. Run **`install_addin.bat`** — copies the add-in into Fusion's add-ins
   folder. Restart Fusion (the add-in runs on startup).
3. Run **`SpaceMouseWASD.bat`** — opens the controller app. When the status
   card shows **Connected**, hold the forward side mouse button in Fusion
   and fly.

Tick *Launch at Windows startup* in the app and you never have to think
about it again.

## Install — macOS

Requirements: macOS 12+, Autodesk Fusion, and a Python 3.9+ with **Tk 8.6 or
newer** plus `pyobjc`.

> Apple's built-in `/usr/bin/python3` will **not** work: it links Tk 8.5.9
> from 2010, which renders the window completely blank on modern macOS.
> Install a Python that bundles a current Tk.

1. Download and unzip the repo, then in Terminal from the repo folder:
   ```sh
   brew install python@3.13 python-tk@3.13
   /opt/homebrew/bin/python3.13 -m pip install \
       pyobjc-framework-Quartz pyobjc-framework-Cocoa
   chmod +x install_addin.sh SpaceMouseWASD.command
   ./install_addin.sh
   ```
2. Restart Fusion, then double-click **`SpaceMouseWASD.command`** — it finds
   the first interpreter on the machine that has both a usable Tk and pyobjc,
   so you do not have to remember which python to run it with.
3. macOS will prompt for **Accessibility** and **Input Monitoring**
   permission for Terminal/Python (System Settings → Privacy & Security).
   Grant both and relaunch — global input capture is impossible without
   them, and the app will tell you if the tap couldn't be created.

On macOS the default zoom keys are Shift / Control, and "side buttons"
are mouse buttons 4 and 5 (most third-party mice; drivers that remap
them to gestures may need those remaps disabled).

> **Tip:** Fusion defaults to an orthographic camera, where zoom is just
> magnification. For the full fly-toward-it depth feel, click the dropdown
> next to the ViewCube and switch to **Perspective**.

## Configuring

Everything lives in the app: click any key button and press a new key to
rebind, pick which side button activates fly mode, and drag the speed
slider. Settings persist per-platform to
`%APPDATA%\SpaceMouseWASD\config.json` on Windows and
`~/Library/Application Support/SpaceMouseWASD/config.json` on macOS.

Camera *feel* (sensitivity, glide floatiness, axis inversion) lives at the
top of `addin/SpaceMouseWASD/SpaceMouseWASD.py`:

| Constant | What it does |
|---|---|
| `ORBIT_SENS` | Orbit degrees per pixel of mouse movement |
| `PAN_SPEED` / `DOLLY_SPEED` | Pan / zoom rates |
| `TAU_MOVE` / `TAU_ORBIT` | Smoothing time constants — bigger = floatier |
| `INVERT_ORBIT_X/Y`, `INVERT_ZOOM` | Flip directions |

After editing, re-run `install_addin.bat` and restart the add-in
(Shift+S → Add-Ins → SpaceMouseWASD → Stop → Run).

## Troubleshooting

- **Add-in shows "Waiting..."** — Fusion isn't running the add-in. In
  Fusion: Shift+S → Add-Ins tab → SpaceMouseWASD → Run (tick *Run on
  Startup*). The add-in logs to Fusion's Text Commands palette.
- **Side button doesn't trigger fly mode** — some mouse drivers remap side
  buttons; set them to default "Back"/"Forward" in your mouse software, or
  switch the fly button in the app.
- **Wrong orbit/zoom direction** — flip the `INVERT_*` flags in the add-in.
- **Blank window on macOS** — you're on a Python whose Tk is 8.5.9 (Apple's
  `/usr/bin/python3`). Launch via `SpaceMouseWASD.command`, which finds a
  usable interpreter, or install one via Homebrew (see Install — macOS).
- **Fusion window not detected** — the controller matches the foreground
  window/app name against `Fusion`; if Autodesk renames it, update
  `WINDOW_MATCH` in `controller/backend_win.py` (Windows) or `APP_MATCH`
  in `controller/backend_mac.py` (macOS).

## Repo layout

```
addin/SpaceMouseWASD/           Fusion add-in (camera driver, cross-platform)
controller/spacemouse_wasd.py   Controller app (UI + config)
controller/engine_base.py       Shared engine core (state + UDP streaming)
controller/backend_win.py       Windows input backend (ctypes LL hooks)
controller/backend_mac.py       macOS input backend (Quartz event tap)
assets/                         Logo and icon
scripts/gen_icon.py             Regenerates assets/icon.png
SpaceMouseWASD.bat / .command   Launch the controller app (Win / mac)
install_addin.bat / .sh         Install/update the Fusion add-in (Win / mac)
```

## Credits

macOS testing and fixes: Devansh Gaur — dragged-event capture, background
cursor hiding, and the cross-platform custom widgets.

## License

[MIT](LICENSE)
