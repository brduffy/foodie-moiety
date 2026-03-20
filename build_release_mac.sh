#!/usr/bin/env bash
#
# Build a production macOS release: .app bundle → signed DMG → notarized.
#
# Usage:
#   ./build_release_mac.sh                                          # Unsigned build (for testing)
#   ./build_release_mac.sh --identity "Developer ID Application: Your Name (TEAMID)"
#   ./build_release_mac.sh --identity "..." --notarize --apple-id you@email.com --team-id ABCDE12345
#
# Steps:
#   1. Generate clean production database (schema + default tags, no recipes)
#   2. Create isolated build virtualenv with PySide6-Essentials
#   3. Run PyInstaller with production spec (includes Whisper + wake word models)
#   4. Code sign with hardened runtime (if --identity provided)
#   5. Create DMG installer
#   6. Sign DMG (if --identity provided)
#   7. Notarize + staple (if --notarize provided)
#
# Prerequisites:
#   - Python 3.10+ available as 'python3'
#   - Xcode Command Line Tools (for codesign, hdiutil, notarytool)
#   - Apple Developer ID certificate in Keychain (for signing)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# ── Parse arguments ──
IDENTITY=""
NOTARIZE=false
APPLE_ID=""
TEAM_ID=""
NEW_VERSION=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --identity)  IDENTITY="$2"; shift 2 ;;
        --notarize)  NOTARIZE=true; shift ;;
        --apple-id)  APPLE_ID="$2"; shift 2 ;;
        --team-id)   TEAM_ID="$2"; shift 2 ;;
        --version)   NEW_VERSION="$2"; shift 2 ;;
        *) echo "Unknown option: $1" >&2; exit 1 ;;
    esac
done

if $NOTARIZE && [ -z "$IDENTITY" ]; then
    echo "Error: --notarize requires --identity" >&2
    exit 1
fi

if $NOTARIZE && ([ -z "$APPLE_ID" ] || [ -z "$TEAM_ID" ]); then
    echo "Error: --notarize requires --apple-id and --team-id" >&2
    exit 1
fi

# Bump version if --version provided
if [ -n "$NEW_VERSION" ]; then
    echo "Bumping version to ${NEW_VERSION}..."
    sed -i '' "s/APP_VERSION = \"[^\"]*\"/APP_VERSION = \"${NEW_VERSION}\"/" main.py
    for spec in foodie_moiety.spec foodie_moiety_prod.spec; do
        sed -i '' "s/'CFBundleVersion': '[^']*'/'CFBundleVersion': '${NEW_VERSION}'/" "$spec"
        sed -i '' "s/'CFBundleShortVersionString': '[^']*'/'CFBundleShortVersionString': '${NEW_VERSION}'/" "$spec"
    done
    echo ""
fi

# Read version from main.py
VERSION=$(grep -o 'APP_VERSION = "[^"]*"' main.py | cut -d'"' -f2)
echo "=== Building Foodie Moiety v${VERSION} (macOS Production) ==="
echo ""

APP_NAME="FoodieMoiety"
APP_PATH="dist/${APP_NAME}.app"
DMG_NAME="${APP_NAME}-${VERSION}.dmg"
DMG_PATH="dist/${DMG_NAME}"
BUILD_VENV=".build_venv"

# ── Step 1: Create isolated build virtualenv ──
echo "Step 1/7: Creating build virtualenv..."
if [ -d "$BUILD_VENV" ]; then
    rm -rf "$BUILD_VENV"
fi
python3 -m venv "$BUILD_VENV"
source "$BUILD_VENV/bin/activate"

echo "  Installing build dependencies..."
pip install --quiet --upgrade pip
pip install --quiet -r requirements-build.txt pyinstaller
echo ""

# ── Step 2: Generate clean production database ──
echo "Step 2/7: Generating clean production database..."
python3 create_prod_db.py
# Copy to the filename the app expects (BUNDLE_DIR / "foodie_moiety.db")
cp dist/foodie_moiety_prod.db dist/foodie_moiety.db
echo ""

# ── Step 3: Run PyInstaller ──
echo "Step 3/7: Running PyInstaller (production spec)..."
if [ -n "$IDENTITY" ]; then
    export CODESIGN_IDENTITY="$IDENTITY"
fi
pyinstaller foodie_moiety_prod.spec --clean --noconfirm
echo ""

# ── Step 4: Code sign with hardened runtime ──
if [ -n "$IDENTITY" ]; then
    echo "Step 4/7: Code signing .app bundle..."
    # Sign all bundled dylibs/frameworks first (inside-out signing)
    find "$APP_PATH" -name "*.dylib" -o -name "*.so" -o -name "*.framework" | while read -r lib; do
        codesign --force --options runtime --sign "$IDENTITY" "$lib" 2>/dev/null || true
    done
    # Sign the main executable and app bundle
    codesign --deep --force --options runtime \
        --entitlements entitlements.plist \
        --sign "$IDENTITY" \
        "$APP_PATH"
    echo "  Verifying signature..."
    codesign --verify --deep --strict "$APP_PATH"
    echo "  Signature OK"
else
    echo "Step 4/7: Skipping code signing (no --identity provided)"
fi
echo ""

# ── Step 5: Create DMG ──
echo "Step 5/7: Creating DMG installer..."
# Remove old DMG if it exists
rm -f "$DMG_PATH"
# Stage the .app + Applications symlink so the DMG shows a drag-to-install layout
DMG_STAGE=$(mktemp -d)
cp -R "$APP_PATH" "$DMG_STAGE/"
ln -s /Applications "$DMG_STAGE/Applications"
hdiutil create -volname "Foodie Moiety" \
    -srcfolder "$DMG_STAGE" \
    -ov -format UDZO \
    "$DMG_PATH"
rm -rf "$DMG_STAGE"
echo "  DMG: $DMG_PATH"
echo ""

# ── Step 6: Sign DMG ──
if [ -n "$IDENTITY" ]; then
    echo "Step 6/7: Signing DMG..."
    codesign --force --sign "$IDENTITY" "$DMG_PATH"
    echo "  DMG signature OK"
else
    echo "Step 6/7: Skipping DMG signing (no --identity provided)"
fi
echo ""

# ── Step 7: Notarize + staple ──
if $NOTARIZE; then
    echo "Step 7/7: Submitting for notarization..."
    xcrun notarytool submit "$DMG_PATH" \
        --apple-id "$APPLE_ID" \
        --team-id "$TEAM_ID" \
        --keychain-profile "notarytool-profile" \
        --wait
    echo "  Stapling notarization ticket..."
    xcrun stapler staple "$DMG_PATH"
    echo "  Notarization complete"
else
    echo "Step 7/7: Skipping notarization"
fi
echo ""

# ── Cleanup ──
deactivate
rm -rf "$BUILD_VENV"

# ── Done ──
echo "============================================="
echo "Build complete: $DMG_PATH"
echo "  Version:  ${VERSION}"
if [ -n "$IDENTITY" ]; then
    echo "  Signed:   Yes ($IDENTITY)"
else
    echo "  Signed:   No (unsigned — Gatekeeper will block; use right-click → Open for testing)"
fi
if $NOTARIZE; then
    echo "  Notarized: Yes"
else
    echo "  Notarized: No"
fi
echo ""
echo "To publish this release:"
echo "  ./publish-desktop-release.sh ${VERSION} --mac ${DMG_PATH}"
echo ""
if [ -z "$IDENTITY" ]; then
    echo "To sign and notarize (when Apple Developer account is ready):"
    echo "  ./build_release_mac.sh --identity \"Developer ID Application: Your Name (TEAMID)\" --notarize --apple-id you@email.com --team-id TEAMID"
    echo ""
    echo "Setup steps:"
    echo "  1. Enroll at https://developer.apple.com ($99/year)"
    echo "  2. Create a Developer ID Application certificate in Xcode → Settings → Accounts"
    echo "  3. Store notarization credentials: xcrun notarytool store-credentials \"notarytool-profile\" --apple-id you@email.com --team-id TEAMID"
fi
