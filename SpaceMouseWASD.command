#!/bin/bash
# Launch the SpaceMouse WASD controller UI (macOS).
#
# Picks the first interpreter that can actually run this app: it needs
# tkinter with Tk 8.6+ (Apple's /usr/bin/python3 ships 8.5.9, which draws
# blank windows) and pyobjc for the Quartz event tap.
cd "$(dirname "$0")"

CANDIDATES=(
    "$HOME/Library/Application Support/SpaceMouseWASD/venv/bin/python"
    /opt/homebrew/bin/python3
    /usr/local/bin/python3
    /Library/Frameworks/Python.framework/Versions/Current/bin/python3
    "$(command -v python3 2>/dev/null)"
)

usable() {
    "$1" - <<'PY' >/dev/null 2>&1
import sys, tkinter
import Quartz                       # noqa: F401  (pyobjc)
sys.exit(0 if tkinter.TkVersion >= 8.6 else 1)
PY
}

for PY in "${CANDIDATES[@]}"; do
    [ -n "$PY" ] && [ -x "$PY" ] || continue
    if usable "$PY"; then
        exec "$PY" controller/spacemouse_wasd.py
    fi
done

echo "No suitable Python found. This app needs tkinter (Tk 8.6+) and pyobjc:"
echo
echo "    brew install python@3.13 python-tk@3.13"
echo "    /opt/homebrew/bin/python3.13 -m pip install \\"
echo "        pyobjc-framework-Quartz pyobjc-framework-Cocoa"
echo
echo "Apple's /usr/bin/python3 ships Tk 8.5.9 and cannot be used - it renders"
echo "the window blank."
exit 1
