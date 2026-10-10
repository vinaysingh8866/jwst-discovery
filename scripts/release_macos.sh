#!/usr/bin/env bash
# Build, sign, notarize and staple the macOS app and dmg.
#
# Required environment:
#   APPLE_SIGNING_IDENTITY  "Developer ID Application: Name (TEAMID)"
#   APPLE_API_KEY           App Store Connect API key id
#   APPLE_API_ISSUER        App Store Connect issuer id
#   APPLE_API_KEY_PATH      path to the AuthKey_<id>.p8 file
# Optional:
#   SKIP_BUILD=1            reuse src-tauri/target/release/bundle/macos/*.app
#
# Output: src-tauri/target/release/bundle/dmg/JWST Discovery_<version>_aarch64.dmg (stapled)
set -euo pipefail
cd "$(dirname "$0")/.."
# The bundler shells out to xattr, codesign and hdiutil; make sure the system
# tools win over any pip- or brew-installed lookalikes (a Python `xattr` on PATH
# breaks `xattr -cr`).
export PATH="/usr/bin:/bin:/usr/sbin:/sbin:$PATH"

: "${APPLE_SIGNING_IDENTITY:?set APPLE_SIGNING_IDENTITY}"
: "${APPLE_API_KEY:?set APPLE_API_KEY}"
: "${APPLE_API_ISSUER:?set APPLE_API_ISSUER}"
: "${APPLE_API_KEY_PATH:?set APPLE_API_KEY_PATH}"

VERSION=$(python3 -c "import json;print(json.load(open('src-tauri/tauri.conf.json'))['version'])")
ARCH=$(uname -m); [[ "$ARCH" == "arm64" ]] && ARCH=aarch64
APP="src-tauri/target/release/bundle/macos/JWST Discovery.app"
ENT="src-tauri/entitlements.plist"
OUT="src-tauri/target/release/bundle/dmg"
DMG="$OUT/JWST Discovery_${VERSION}_${ARCH}.dmg"
NOTARY=(--key "$APPLE_API_KEY_PATH" --key-id "$APPLE_API_KEY" --issuer "$APPLE_API_ISSUER")

step() { printf '\n==> %s\n' "$*"; }

notarize() {  # notarize <file>
  local out id status
  out=$(xcrun notarytool submit "$1" "${NOTARY[@]}" --wait 2>&1) || true
  echo "$out"
  id=$(echo "$out" | awk '/^  id:/{print $2; exit}')
  status=$(echo "$out" | awk '/status:/{s=$2} END{print s}')
  if [[ "$status" != "Accepted" ]]; then
    echo "notarization failed ($status); log follows" >&2
    [[ -n "$id" ]] && xcrun notarytool log "$id" "${NOTARY[@]}" >&2 || true
    exit 1
  fi
}

if [[ "${SKIP_BUILD:-0}" != 1 ]]; then
  step "Building app bundle (unsigned)"
  # Hide the Apple variables from the bundler: Tauri would otherwise sign and
  # notarize the app itself before the sidecar binaries are signed.
  env -u APPLE_SIGNING_IDENTITY -u APPLE_API_KEY -u APPLE_API_ISSUER -u APPLE_API_KEY_PATH \
    npx tauri build --ci --bundles app
fi
[[ -d "$APP" ]] || { echo "missing $APP" >&2; exit 1; }

step "Signing Mach-O files inside the sidecar resource"
SIDECAR="$APP/Contents/Resources/sidecar/jwst-pipeline"
find "$SIDECAR" -type f -print0 | xargs -0 file --mime-type \
  | grep 'application/x-mach-binary' | cut -d: -f1 > "$OUT/.machos" || true
mkdir -p "$OUT"
# shared libraries and bundles: no entitlements
grep -v -E "/jwst-pipeline$" "$OUT/.machos" | tr '\n' '\0' \
  | xargs -0 codesign --force --timestamp --options runtime --sign "$APPLE_SIGNING_IDENTITY"
# the sidecar executable: hardened runtime with the entitlements CPython needs
codesign --force --timestamp --options runtime --entitlements "$ENT" \
  --sign "$APPLE_SIGNING_IDENTITY" "$SIDECAR/jwst-pipeline"
echo "signed $(wc -l < "$OUT/.machos" | tr -d ' ') files"

step "Signing the app bundle"
codesign --force --timestamp --options runtime --entitlements "$ENT" \
  --sign "$APPLE_SIGNING_IDENTITY" "$APP"
codesign --verify --deep --strict --verbose=1 "$APP"

step "Notarizing the app"
ZIP="$OUT/JWST Discovery_${VERSION}_${ARCH}.app.zip"
rm -f "$ZIP"; ditto -c -k --keepParent "$APP" "$ZIP"
notarize "$ZIP"
xcrun stapler staple "$APP"
rm -f "$ZIP"

step "Building the dmg"
STAGE=$(mktemp -d); cp -R "$APP" "$STAGE/"; ln -s /Applications "$STAGE/Applications"
rm -f "$DMG"
hdiutil create -volname "JWST Discovery" -srcfolder "$STAGE" -ov -format UDZO -quiet "$DMG"
rm -rf "$STAGE"
codesign --force --timestamp --sign "$APPLE_SIGNING_IDENTITY" "$DMG"

step "Notarizing the dmg"
notarize "$DMG"
xcrun stapler staple "$DMG"

step "Verifying"
xcrun stapler validate "$DMG"
spctl -a -vvv -t open --context context:primary-signature "$DMG" 2>&1 | tail -2
spctl -a -vvv -t exec "$APP" 2>&1 | tail -2
shasum -a 256 "$DMG"
echo "done: $DMG"
