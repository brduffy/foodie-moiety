#!/usr/bin/env bash
#
# Full release pipeline: build → sign → notarize → publish to CDN.
#
# Usage:
#   ./release.sh              # Interactive — prompts for version + notes
#   ./release.sh --test       # Unsigned build only (no sign/notarize/publish)
#
# Prerequisites:
#   - Python 3.10+ available as 'python3'
#   - Xcode Command Line Tools
#   - Developer ID certificate in Keychain
#   - Notarization credentials stored (xcrun notarytool store-credentials "notarytool-profile")
#   - AWS CLI configured for S3 upload

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# ── Configuration (set these as environment variables) ──
# FM_CODESIGN_IDENTITY  - e.g. "Developer ID Application: Your Name (TEAMID)"
# FM_APPLE_ID           - Apple ID email for notarization
# FM_TEAM_ID            - Apple Developer Team ID
# FM_S3_BUCKET          - S3 bucket name for CDN uploads
# FM_CDN_BASE           - CDN base URL (e.g. https://yourdomain.com)
IDENTITY="${FM_CODESIGN_IDENTITY:?Set FM_CODESIGN_IDENTITY env var}"
APPLE_ID="${FM_APPLE_ID:?Set FM_APPLE_ID env var}"
TEAM_ID="${FM_TEAM_ID:?Set FM_TEAM_ID env var}"
BUCKET="${FM_S3_BUCKET:?Set FM_S3_BUCKET env var}"
CDN_BASE="${FM_CDN_BASE:?Set FM_CDN_BASE env var}"
PREFIX="desktop"

# ── Parse arguments ──
TEST_ONLY=false
while [[ $# -gt 0 ]]; do
    case "$1" in
        --test) TEST_ONLY=true; shift ;;
        *) echo "Unknown option: $1 (use --test for unsigned build only)" >&2; exit 1 ;;
    esac
done

# ── Prompt for version ──
CURRENT_VERSION=$(grep -o 'APP_VERSION = "[^"]*"' main.py | cut -d'"' -f2)
echo "Current version: ${CURRENT_VERSION}"
printf "New version: "
read -r VERSION
if [ -z "$VERSION" ]; then
    echo "Error: version is required" >&2
    exit 1
fi

# ── Prompt for release notes ──
NOTES=""
if ! $TEST_ONLY; then
    printf "Release notes: "
    read -r NOTES
fi

APP_NAME="FoodieMoiety"
APP_PATH="dist/${APP_NAME}.app"
DMG_NAME="${APP_NAME}-${VERSION}.dmg"
DMG_PATH="dist/${DMG_NAME}"
BUILD_VENV=".build_venv"

# ── Bump version ──
echo ""
echo "Bumping version to ${VERSION}..."
sed -i '' "s/APP_VERSION = \"[^\"]*\"/APP_VERSION = \"${VERSION}\"/" main.py
for spec in foodie_moiety.spec foodie_moiety_prod.spec; do
    sed -i '' "s/'CFBundleVersion': '[^']*'/'CFBundleVersion': '${VERSION}'/" "$spec"
    sed -i '' "s/'CFBundleShortVersionString': '[^']*'/'CFBundleShortVersionString': '${VERSION}'/" "$spec"
done

echo ""
echo "=========================================="
echo "  Building Foodie Moiety v${VERSION}"
if $TEST_ONLY; then
    echo "  Mode: TEST (unsigned, no publish)"
else
    echo "  Mode: RELEASE (signed + notarized + publish)"
fi
echo "=========================================="
echo ""

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
cp dist/foodie_moiety_prod.db dist/foodie_moiety.db
echo ""

# ── Step 3: Run PyInstaller ──
echo "Step 3/7: Running PyInstaller (production spec)..."
if ! $TEST_ONLY; then
    export CODESIGN_IDENTITY="$IDENTITY"
fi
pyinstaller foodie_moiety_prod.spec --clean --noconfirm
echo ""

# ── Step 4: Code sign with hardened runtime ──
if ! $TEST_ONLY; then
    echo "Step 4/7: Code signing .app bundle..."
    find "$APP_PATH" -name "*.dylib" -o -name "*.so" -o -name "*.framework" | while read -r lib; do
        codesign --force --options runtime --sign "$IDENTITY" "$lib" 2>/dev/null || true
    done
    codesign --deep --force --options runtime \
        --entitlements entitlements.plist \
        --sign "$IDENTITY" \
        "$APP_PATH"
    echo "  Verifying signature..."
    codesign --verify --deep --strict "$APP_PATH"
    echo "  Signature OK"
else
    echo "Step 4/7: Skipping code signing (test mode)"
fi
echo ""

# ── Step 5: Create DMG ──
echo "Step 5/7: Creating DMG installer..."
rm -f "$DMG_PATH"
BG_IMG="media/dmg_background.png"
if [ ! -f "$BG_IMG" ]; then
    echo "  Generating DMG background image..."
    python3 scripts/generate_dmg_background.py
fi
DMG_STAGE=$(mktemp -d)
cp -R "$APP_PATH" "$DMG_STAGE/"
ln -s /Applications "$DMG_STAGE/Applications"
create-dmg \
    --volname "Foodie Moiety" \
    --background "$BG_IMG" \
    --window-size 660 400 \
    --icon-size 120 \
    --icon "FoodieMoiety.app" 160 175 \
    --icon "Applications" 500 175 \
    --hide-extension "FoodieMoiety.app" \
    --no-internet-enable \
    "$DMG_PATH" \
    "$DMG_STAGE"
rm -rf "$DMG_STAGE"
echo "  DMG: $DMG_PATH"
echo ""

# ── Step 6: Sign DMG ──
if ! $TEST_ONLY; then
    echo "Step 6/7: Signing DMG..."
    codesign --force --sign "$IDENTITY" "$DMG_PATH"
    echo "  DMG signature OK"
else
    echo "Step 6/7: Skipping DMG signing (test mode)"
fi
echo ""

# ── Step 7: Notarize + staple ──
if ! $TEST_ONLY; then
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
    echo "Step 7/7: Skipping notarization (test mode)"
fi
echo ""

# ── Cleanup build venv ──
deactivate
rm -rf "$BUILD_VENV"

# ── Publish to CDN ──
if ! $TEST_ONLY; then
    echo "Build complete. Test the DMG before publishing:"
    echo "  open ${DMG_PATH}"
    echo ""
    printf "Ready to publish to CDN? (y/N): "
    read -r CONFIRM
    if [[ ! "$CONFIRM" =~ ^[Yy]$ ]]; then
        echo ""
        echo "Publish skipped. To publish later:"
        echo "  ./publish-desktop-release.sh ${VERSION} --mac ${DMG_PATH} --notes \"${NOTES}\""
        exit 0
    fi

    echo ""
    echo "=========================================="
    echo "  Publishing to CDN"
    echo "=========================================="
    echo ""

    DMG_KEY="${PREFIX}/FoodieMoiety-${VERSION}.dmg"
    MANIFEST_KEY="${PREFIX}/latest_version.json"

    echo "Uploading macOS installer..."
    aws s3 cp "$DMG_PATH" "s3://${BUCKET}/${DMG_KEY}" \
        --cache-control "public, max-age=31536000, immutable" \
        --content-type "application/x-apple-diskimage"

    # Preserve Windows URL from existing manifest (if any)
    WIN_URL=""
    EXISTING=$(aws s3 cp "s3://${BUCKET}/${MANIFEST_KEY}" - 2>/dev/null || echo "{}")
    WIN_URL=$(echo "$EXISTING" | python3 -c "import sys,json; print(json.load(sys.stdin).get('win_url',''))" 2>/dev/null || echo "")

    echo "Uploading version manifest..."
    MANIFEST=$(cat <<EOF
{
  "version": "${VERSION}",
  "mac_url": "${CDN_BASE}/${DMG_KEY}",
  "win_url": "${WIN_URL}",
  "notes": "${NOTES}"
}
EOF
)
    echo "  Manifest contents:"
    echo "$MANIFEST"
    echo "$MANIFEST" | aws s3 cp - "s3://${BUCKET}/${MANIFEST_KEY}" \
        --cache-control "public, max-age=300" \
        --content-type "application/json"

    # Verify manifest was uploaded correctly
    echo "  Verifying manifest on S3..."
    VERIFY=$(aws s3 cp "s3://${BUCKET}/${MANIFEST_KEY}" - 2>/dev/null || echo "FAILED")
    echo "  S3 manifest: $VERIFY"

    # Invalidate CloudFront cache for the manifest so CDN serves it immediately
    CF_DIST=$(aws cloudfront list-distributions --query "DistributionList.Items[?contains(Aliases.Items,'foodiemoiety.com')].Id" --output text 2>/dev/null || echo "")
    if [ -n "$CF_DIST" ]; then
        echo "  Invalidating CloudFront cache for manifest..."
        aws cloudfront create-invalidation --distribution-id "$CF_DIST" \
            --paths "/${MANIFEST_KEY}" >/dev/null 2>&1 || echo "  Warning: CloudFront invalidation failed (non-critical)"
    fi

    # Commit version bump
    echo ""
    echo "Committing version bump..."
    git add main.py foodie_moiety.spec foodie_moiety_prod.spec
    git commit -m "Bump version to ${VERSION}"

    echo ""
    echo "=========================================="
    echo "  Release complete!"
    echo "=========================================="
    echo "  Version:    ${VERSION}"
    echo "  DMG:        ${DMG_PATH}"
    echo "  Manifest:   ${CDN_BASE}/${MANIFEST_KEY}"
    echo "  Download:   ${CDN_BASE}/${DMG_KEY}"
    echo ""
    echo "Don't forget to: git push"
else
    echo "=========================================="
    echo "  Test build complete: ${DMG_PATH}"
    echo "=========================================="
    echo "  Version:  ${VERSION}"
    echo "  Signed:   No (test mode)"
    echo ""
    echo "Test with: dist/FoodieMoiety.app/Contents/MacOS/FoodieMoiety"
    echo ""
    echo "For a full release: ./release.sh"
fi
