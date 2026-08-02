#!/usr/bin/env bash
# Build SpaceMouseWASD.app from the SwiftPM package.
#
#   ./build_app.sh          -> release build, assembles ./SpaceMouseWASD.app
#
# The bundle is what you want on macOS: a proper .app lets the system attribute
# the Accessibility / Input Monitoring grants stably and gives clean prompts.
set -euo pipefail

cd "$(dirname "$0")"

APP="SpaceMouseWASD.app"
BIN="SpaceMouseWASD"
CONFIG="release"
# Universal so the one download runs on both Apple Silicon and Intel Macs.
ARCHS=(--arch arm64 --arch x86_64)

echo "==> swift build -c $CONFIG (universal: arm64 + x86_64)"
swift build -c "$CONFIG" "${ARCHS[@]}"

BIN_PATH="$(swift build -c "$CONFIG" "${ARCHS[@]}" --show-bin-path)/$BIN"

echo "==> assembling $APP"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cp "$BIN_PATH" "$APP/Contents/MacOS/$BIN"
cp Info.plist "$APP/Contents/Info.plist"

# Generate AppIcon.icns from the shared PNG, if the toolchain is present.
ICON_SRC="../assets/icon.png"
if [[ -f "$ICON_SRC" ]] && command -v iconutil >/dev/null 2>&1; then
    echo "==> building AppIcon.icns"
    ICONSET="$(mktemp -d)/AppIcon.iconset"
    mkdir -p "$ICONSET"
    for size in 16 32 128 256 512; do
        sips -z "$size" "$size" "$ICON_SRC" \
            --out "$ICONSET/icon_${size}x${size}.png" >/dev/null
        double=$((size * 2))
        sips -z "$double" "$double" "$ICON_SRC" \
            --out "$ICONSET/icon_${size}x${size}@2x.png" >/dev/null
    done
    iconutil -c icns "$ICONSET" -o "$APP/Contents/Resources/AppIcon.icns"
    rm -rf "$(dirname "$ICONSET")"
else
    echo "==> skipping icon (need assets/icon.png + iconutil)"
fi

# Strip any build-toolchain rpath. Every Swift/system dylib is already loaded
# by absolute path (/usr/lib/swift, /System/Library), so this leftover Xcode
# search path is unused — removing it makes the app provably self-contained:
# it runs on a stock Mac with no Xcode / command-line tools installed.
BIN_IN_APP="$APP/Contents/MacOS/$BIN"
otool -l "$BIN_IN_APP" \
    | awk '/LC_RPATH/{f=1} f&&/ path /{print $2; f=0}' \
    | while read -r rp; do
        case "$rp" in
            *Toolchains*|/Applications/Xcode*)
                echo "==> stripping toolchain rpath: $rp"
                install_name_tool -delete_rpath "$rp" "$BIN_IN_APP" \
                    2>/dev/null || true ;;
        esac
    done

# Ad-hoc signature so the app runs locally without Gatekeeper complaints.
# For distribution, replace `-` with a Developer ID identity (see README).
echo "==> ad-hoc codesign"
codesign --force --deep --sign - "$APP" >/dev/null 2>&1 || \
    echo "    (codesign skipped)"

echo "==> done: $APP"
echo "    open $APP   # first launch will ask for Accessibility + Input Monitoring"
