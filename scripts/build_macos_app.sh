#!/bin/zsh
set -euo pipefail

cd "${0:A:h}/.."
MAC_CLEANER_PYTHON_BIN="${MAC_CLEANER_PYTHON_BIN:-python3}"
"$MAC_CLEANER_PYTHON_BIN" -m PyInstaller \
  --noconfirm \
  --clean \
  --windowed \
  --name "Mac Cleaner" \
  --osx-bundle-identifier "com.theolodocoder.maccleaner" \
  mac_cleaner_gui.py

APP_PATH="dist/Mac Cleaner.app"
ZIP_PATH="dist/Mac-Cleaner-macOS.zip"

if [[ -n "${DEVELOPER_ID_APPLICATION:-}" ]]; then
  codesign --force --options runtime --timestamp --sign "$DEVELOPER_ID_APPLICATION" "$APP_PATH"
fi

ditto -c -k --keepParent "$APP_PATH" "$ZIP_PATH"

if [[ -n "${APPLE_ID:-}" && -n "${APPLE_TEAM_ID:-}" && -n "${APPLE_APP_PASSWORD:-}" ]]; then
  xcrun notarytool submit "$ZIP_PATH" \
    --apple-id "$APPLE_ID" \
    --team-id "$APPLE_TEAM_ID" \
    --password "$APPLE_APP_PASSWORD" \
    --wait
  xcrun stapler staple "$APP_PATH"
  ditto -c -k --keepParent "$APP_PATH" "$ZIP_PATH"
fi

codesign --verify --verbose "$APP_PATH" 2>/dev/null || true
echo "Built $ZIP_PATH"
