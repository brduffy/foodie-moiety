#!/usr/bin/env bash
#
# Publishes a desktop app release to S3/CloudFront.
#
# Usage:
#   ./publish-desktop-release.sh                          # Interactive — prompts for version + notes
#   ./publish-desktop-release.sh 1.0.0 --mac dist/...    # Non-interactive (legacy)
#
# Prerequisites:
#   - AWS CLI configured with credentials that can write to the recipe bucket

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# ── Parse arguments (support both interactive and flag-based) ──
VERSION=""
DMG_PATH=""
MSIX_PATH=""
NOTES=""

# If first arg doesn't start with --, treat it as the version (legacy mode)
if [[ ${1:-} != "" && ${1:-} != --* ]]; then
    VERSION="$1"; shift
fi

while [[ $# -gt 0 ]]; do
    case "$1" in
        --mac)  DMG_PATH="$2"; shift 2 ;;
        --win)  MSIX_PATH="$2"; shift 2 ;;
        --notes) NOTES="$2"; shift 2 ;;
        --version) VERSION="$2"; shift 2 ;;
        *) echo "Unknown option: $1" >&2; exit 1 ;;
    esac
done

# ── Interactive prompts for missing values ──
CURRENT_VERSION=$(grep -o 'APP_VERSION = "[^"]*"' main.py | cut -d'"' -f2)

if [ -z "$VERSION" ]; then
    echo "Current version: ${CURRENT_VERSION}"
    printf "New version: "
    read -r VERSION
    if [ -z "$VERSION" ]; then
        echo "Error: version is required" >&2
        exit 1
    fi
fi

if [ -z "$NOTES" ]; then
    printf "Release notes: "
    read -r NOTES
fi

# Default to mac DMG in dist/
if [ -z "$DMG_PATH" ] && [ -z "$MSIX_PATH" ]; then
    DMG_PATH="dist/FoodieMoiety-${VERSION}.dmg"
    if [ ! -f "$DMG_PATH" ]; then
        echo "Error: DMG not found at ${DMG_PATH}" >&2
        echo "Build first: ./build_release_mac.sh --version ${VERSION}" >&2
        exit 1
    fi
fi

# ── Bump version in source files ──
echo ""
echo "Updating version to ${VERSION} in source files..."

# main.py — APP_VERSION = "x.y.z"
sed -i '' "s/^APP_VERSION = \".*\"/APP_VERSION = \"${VERSION}\"/" main.py

# Both spec files — CFBundleVersion and CFBundleShortVersionString
for spec in foodie_moiety.spec foodie_moiety_prod.spec; do
    sed -i '' "s/'CFBundleVersion': '.*'/'CFBundleVersion': '${VERSION}'/" "$spec"
    sed -i '' "s/'CFBundleShortVersionString': '.*'/'CFBundleShortVersionString': '${VERSION}'/" "$spec"
done

# Verify the bump worked
MAIN_VER=$(grep -o 'APP_VERSION = "[^"]*"' main.py)
echo "  main.py:                  ${MAIN_VER}"
SPEC_VER=$(grep -o "CFBundleVersion': '[^']*'" foodie_moiety.spec)
echo "  foodie_moiety.spec:       ${SPEC_VER}"
PROD_SPEC_VER=$(grep -o "CFBundleVersion': '[^']*'" foodie_moiety_prod.spec)
echo "  foodie_moiety_prod.spec:  ${PROD_SPEC_VER}"

BUCKET="${FM_S3_BUCKET:?Set FM_S3_BUCKET env var}"
CDN_BASE="${FM_CDN_BASE:?Set FM_CDN_BASE env var}"
PREFIX="desktop"

DMG_KEY="${PREFIX}/FoodieMoiety-${VERSION}.dmg"
MSIX_KEY="${PREFIX}/FoodieMoiety-${VERSION}.msix"
MANIFEST_KEY="${PREFIX}/latest_version.json"

echo ""
echo "Publishing Foodie Moiety Desktop v${VERSION}"
echo "============================================="

# Upload installers (only the ones provided)
if [ -n "$DMG_PATH" ]; then
    if [ ! -f "$DMG_PATH" ]; then
        echo "Error: DMG not found at $DMG_PATH" >&2
        exit 1
    fi
    echo "Uploading macOS installer..."
    aws s3 cp "$DMG_PATH" "s3://${BUCKET}/${DMG_KEY}" \
        --cache-control "public, max-age=31536000, immutable" \
        --content-type "application/x-apple-diskimage"
fi

if [ -n "$MSIX_PATH" ]; then
    if [ ! -f "$MSIX_PATH" ]; then
        echo "Error: MSIX not found at $MSIX_PATH" >&2
        exit 1
    fi
    echo "Uploading Windows installer..."
    aws s3 cp "$MSIX_PATH" "s3://${BUCKET}/${MSIX_KEY}" \
        --cache-control "public, max-age=31536000, immutable" \
        --content-type "application/msix"
fi

# Build manifest — include URLs only for platforms being published.
# For platforms not included, fetch existing URL from current manifest.
MAC_URL=""
WIN_URL=""

if [ -n "$DMG_PATH" ]; then
    MAC_URL="${CDN_BASE}/${DMG_KEY}"
fi
if [ -n "$MSIX_PATH" ]; then
    WIN_URL="${CDN_BASE}/${MSIX_KEY}"
fi

# If only publishing one platform, preserve the other platform's URL from the existing manifest
if [ -z "$DMG_PATH" ] || [ -z "$MSIX_PATH" ]; then
    EXISTING=$(aws s3 cp "s3://${BUCKET}/${MANIFEST_KEY}" - 2>/dev/null || echo "{}")
    if [ -z "$MAC_URL" ]; then
        MAC_URL=$(echo "$EXISTING" | python3 -c "import sys,json; print(json.load(sys.stdin).get('mac_url',''))" 2>/dev/null || echo "")
    fi
    if [ -z "$WIN_URL" ]; then
        WIN_URL=$(echo "$EXISTING" | python3 -c "import sys,json; print(json.load(sys.stdin).get('win_url',''))" 2>/dev/null || echo "")
    fi
fi

echo "Uploading version manifest..."
MANIFEST=$(cat <<EOF
{
  "version": "${VERSION}",
  "mac_url": "${MAC_URL}",
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

# Invalidate CloudFront cache for the manifest
CF_DIST=$(aws cloudfront list-distributions --query "DistributionList.Items[?contains(Aliases.Items,'foodiemoiety.com')].Id" --output text 2>/dev/null || echo "")
if [ -n "$CF_DIST" ]; then
    echo "  Invalidating CloudFront cache for manifest..."
    aws cloudfront create-invalidation --distribution-id "$CF_DIST" \
        --paths "/${MANIFEST_KEY}" >/dev/null 2>&1 || echo "  Warning: CloudFront invalidation failed (non-critical)"
fi

# Commit the version bump
echo ""
echo "Committing version bump..."
git add main.py foodie_moiety.spec foodie_moiety_prod.spec
git commit -m "Bump version to ${VERSION}"

echo ""
echo "Done! Desktop v${VERSION} published."
echo "  Manifest: ${CDN_BASE}/${MANIFEST_KEY}"
[ -n "$DMG_PATH" ] && echo "  macOS:    ${CDN_BASE}/${DMG_KEY}"
[ -n "$MSIX_PATH" ] && echo "  Windows:  ${CDN_BASE}/${MSIX_KEY}"
echo ""
echo "Don't forget to: git push"
