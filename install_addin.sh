#!/bin/bash
# Install (or update) the Fusion add-in for the current user (macOS).
set -e
cd "$(dirname "$0")"
DEST="$HOME/Library/Application Support/Autodesk/Autodesk Fusion 360/API/AddIns/SpaceMouseWASD"
mkdir -p "$DEST"
cp -R addin/SpaceMouseWASD/. "$DEST"
echo "Add-in installed to:"
echo "  $DEST"
echo
echo "Restart Fusion (or Shift+S > Add-Ins > SpaceMouseWASD > Run)."
