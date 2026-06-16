#!/usr/bin/env bash
# Build the Scroll macOS app. Requires the FULL Xcode (not just Command Line Tools).
#   App Store → Xcode, then:  sudo xcode-select -s /Applications/Xcode.app/Contents/Developer
set -euo pipefail
cd "$(dirname "$0")"

if ! xcodebuild -version >/dev/null 2>&1; then
  echo "✗ xcodebuild not found. Install Xcode, then run:"
  echo "    sudo xcode-select -s /Applications/Xcode.app/Contents/Developer"
  echo "  (Command Line Tools alone cannot build a .app bundle.)"
  exit 1
fi

command -v xcodegen >/dev/null 2>&1 && xcodegen generate

xcodebuild -project Scroll.xcodeproj -scheme Scroll -configuration Release \
  -derivedDataPath build -destination 'platform=macOS' build

APP="$(pwd)/build/Build/Products/Release/Scroll.app"
echo
echo "✓ Built: $APP"
echo "  Run it:  open \"$APP\""
