#!/bin/bash
# Build FoodieMoiety.app for macOS (test build — no ML models, no code signing)
#
# Usage:
#   bash build_mac.sh
#
# After build:
#   open dist/FoodieMoiety.app
#   (right-click → Open if Gatekeeper blocks it)
#
# To test deep link:
#   open "foodiemoiety://download?url=<encoded-url>&title=Test&type=book"

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# Check PyInstaller is installed
if ! python -c "import PyInstaller" 2>/dev/null; then
    echo "PyInstaller not found. Installing..."
    pip install pyinstaller
fi

echo "=== Building FoodieMoiety.app ==="
echo ""

pyinstaller foodie_moiety.spec --clean --noconfirm

echo ""
echo "=== Build complete ==="
echo "App: dist/FoodieMoiety.app"
echo ""
echo "Next steps:"
echo "  1. open dist/FoodieMoiety.app  (launches app, registers URL scheme)"
echo "  2. Test deep link from browser or: open 'foodiemoiety://download?url=...&title=Test&type=book'"
