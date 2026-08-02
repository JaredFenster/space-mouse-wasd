# SpaceMouse WASD — macOS controller (native Swift)

The macOS controller, rewritten from the Python/pyobjc/Tk version into a
native Swift app. It speaks the exact same localhost-UDP protocol to the
unchanged Fusion add-in, so it's a drop-in replacement — and it drops every
macOS install headache the Python version had:

- **No pyobjc, no Homebrew, no Tk version dance.** Zero runtime dependencies.
- Direct **CoreGraphics** event tap and cursor capture, **AppKit**
  foreground detection, **SwiftUI** settings window.
- Ships as a proper `.app` bundle, so Accessibility / Input Monitoring
  prompts are clean and attributable.
- Reads and writes the same
  `~/Library/Application Support/SpaceMouseWASD/config.json`
  (`binds_mac`, `fly_button`, `speed`) — your Windows binds in the same file
  are preserved untouched.

## Build

Requirements: macOS 12+, Xcode command-line tools (`xcode-select --install`).

```sh
./build_app.sh          # release build → ./SpaceMouseWASD.app
open SpaceMouseWASD.app
```

First launch asks for **Accessibility** and **Input Monitoring** (System
Settings → Privacy & Security). Global input capture is impossible without
both; the app surfaces an error in-window if the event tap can't be created.

For development you can also just `swift build && swift run SpaceMouseWASD`,
but the `.app` bundle is what makes the TCC permission grants stick.

## Distributing to end users (no Xcode on their side)

Xcode is only needed to **build**; a compiled `.app` runs on any stock
macOS 12+ machine with nothing installed (the Swift runtime ships in the OS,
and `build_app.sh` strips any toolchain rpath so the bundle is provably
self-contained). So end users never touch a toolchain — you ship them the
built app:

- **Automated (recommended).** The
  [`build-macos`](../.github/workflows/build-macos.yml) GitHub Actions
  workflow builds the universal `.app` on a macOS runner and attaches
  `SpaceMouseWASD-macos.zip` to the GitHub Release whenever you push a
  version tag (`git tag v1.2.0 && git push --tags`). Users download that zip.
- **Manual.** Run `./build_app.sh`, then
  `ditto -c -k --keepParent SpaceMouseWASD.app SpaceMouseWASD-macos.zip` and
  share the zip.

### Gatekeeper: two paths

The app is **ad-hoc signed** by `build_app.sh`, which is enough to run
locally but not enough for a friction-free download on someone else's Mac.

- **Free — quarantine removal.** A downloaded ad-hoc app is quarantined; the
  user clears it once with
  `xattr -dr com.apple.quarantine "/Applications/SpaceMouseWASD.app"` and then
  opens it normally. One command, no install, but it *is* the Terminal.
- **Frictionless — Developer ID + notarization** (needs a paid Apple
  Developer account). Build, then:
  ```sh
  codesign --force --deep --options runtime \
      --sign "Developer ID Application: YOUR NAME (TEAMID)" SpaceMouseWASD.app
  ditto -c -k --keepParent SpaceMouseWASD.app SpaceMouseWASD.zip
  xcrun notarytool submit SpaceMouseWASD.zip \
      --apple-id you@example.com --team-id TEAMID --password APP_SPECIFIC_PW --wait
  xcrun stapler staple SpaceMouseWASD.app
  ```
  The stapled app opens with a plain double-click, no warning and no Terminal.
  To automate it, add those steps to the CI workflow guarded by repository
  secrets so they only run when the signing identity is present.

## Layout

```
Package.swift                       SwiftPM manifest (macOS 12+, Swift 5 mode)
Info.plist                          Bundle metadata + permission usage strings
build_app.sh                        Compiles and assembles SpaceMouseWASD.app
Sources/CGShim/                     C shim for the private "cursor in
                                    background" window-server call
Sources/SpaceMouseWASD/
  main.swift                        App entry, window, single-instance lock
  AppController.swift               SwiftUI view-model + key-rebind capture
  ContentView.swift                 The dark settings UI
  Theme.swift                       Palette + orbit-ring logo
  Engine.swift                      Event tap, cursor capture, 90 Hz UDP sender
  UDPClient.swift                   Non-blocking localhost UDP
  Config.swift                      Load/save config.json + constants
  KeyCodes.swift                    macOS virtual keycode tables
```

## Protocol (unchanged from the Python controller)

Controller → add-in, UDP `127.0.0.1:42737`:

- Heartbeat `{"ping":1}` once a second; the add-in replies `{"ack":1}` so the
  UI can show **Connected**.
- Motion at 90 Hz: `{"tx","ty","tz","rx","ry","sp","boost"}` — `tx/ty/tz` are
  pan-x/pan-y/zoom in −1…1, `rx/ry` are mouse orbit rates in px/sec, `sp` the
  speed multiplier.
