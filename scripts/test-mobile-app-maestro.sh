#!/usr/bin/env bash
# Maestro gate for Knoa Mobile, layer 1 (no backend required).
# Installs the APK on the attached emulator/device, runs the smoke flow,
# and leaves screenshots + logcat behind. Skips gracefully when no
# device is attached, mirroring test-mobile-app.sh device behavior.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$SCRIPT_DIR/.." && pwd)"
MOBILE="$REPO/apps/knoa-mobile"
APK="${1:-${KNOA_MOBILE_BUILD_DIR:-/disk/dev/knoa-mobile-out}/app/outputs/apk/release/app-release.apk}"
MAESTRO="${MAESTRO_BIN:-/disk/dev/maestro/maestro/bin/maestro}"
export MAESTRO_CLI_NO_ANALYTICS=1
OUT_DIR="${KNOA_MAESTRO_OUT:-/tmp/opencode/maestro}"
DISK_DEV="${DISK_DEV:-/disk/dev}"

if [[ -f "$DISK_DEV/env.sh" ]]; then
  # shellcheck source=/dev/null
  source "$DISK_DEV/env.sh"
fi

if [[ ! -x "$MAESTRO" ]]; then
  echo "Maestro not installed at $MAESTRO; skipping maestro gate" >&2
  exit 0
fi
if [[ ! -f "$APK" ]]; then
  echo "APK not found: $APK" >&2
  exit 1
fi

ADB="$ANDROID_HOME/platform-tools/adb"
mapfile -t DEVICES < <("$ADB" devices | awk 'NR > 1 && $2 == "device" { print $1 }')
if [[ "${#DEVICES[@]}" -eq 0 ]]; then
  echo "OK: no Android device attached, maestro gate skipped"
  exit 0
fi
if [[ "${#DEVICES[@]}" -ne 1 ]]; then
  echo "Expected one Android test device, found ${#DEVICES[@]}" >&2
  exit 1
fi

SERIAL="${DEVICES[0]}"
export ANDROID_SERIAL="$SERIAL"
mkdir -p "$OUT_DIR"
echo "==> Installing $APK on $SERIAL"
"$ADB" -s "$SERIAL" install -r "$APK" >/dev/null
"$ADB" -s "$SERIAL" logcat -c
echo "==> Maestro smoke flow"
if "$MAESTRO" test --format junit --output "$OUT_DIR/smoke-junit.xml" "$MOBILE/maestro/smoke.yaml"; then
  echo "OK: maestro smoke passed on $SERIAL (screenshots in ~/.maestro/tests)"
else
  status=$?
  "$ADB" -s "$SERIAL" logcat -d -v brief AndroidRuntime:E ReactNativeJS:E '*:S' \
    > "$OUT_DIR/smoke-logcat.txt" || true
  echo "Maestro smoke FAILED; logcat saved to $OUT_DIR/smoke-logcat.txt" >&2
  exit $status
fi
