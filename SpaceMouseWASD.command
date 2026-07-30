#!/bin/bash
# Launch the SpaceMouse WASD controller UI (macOS).
cd "$(dirname "$0")"
exec python3 controller/spacemouse_wasd.py
