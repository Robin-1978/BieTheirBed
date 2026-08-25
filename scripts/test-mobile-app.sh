#!/usr/bin/env bash
# Release gate for Knoa Mobile. Static checks always run; when an Android
# device is attached, the signed APK is also installed and crash-smoke-tested.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$SCRIPT_DIR/.." && pwd)"
MOBILE="$REPO/apps/knoa-mobile"
APK="${1:-}"
DISK_ENV="${DISK_DEV:-/disk/dev}/env.sh"

if [[ -f "$DISK_ENV" ]]; then
  # shellcheck source=/dev/null
  source "$DISK_ENV"
fi

echo "==> TypeScript"
(cd "$MOBILE" && npm run typecheck)

echo "==> Unit and contract tests"
(cd "$MOBILE" && npm test)

echo "==> Android production bundle"
(cd "$MOBILE" && npm run bundle:android)

if [[ -z "$APK" ]]; then
  echo "OK: static mobile release gate passed"
  exit 0
fi
if [[ ! -f "$APK" ]]; then
  echo "APK not found: $APK" >&2
  exit 1
fi

: "${ANDROID_HOME:?ANDROID_HOME is required to inspect an APK}"
BUILD_TOOLS="$(find "$ANDROID_HOME/build-tools" -mindepth 1 -maxdepth 1 -type d | sort -V | tail -1)"
"$BUILD_TOOLS/aapt" dump badging "$APK" | sed -n '1p'
"$BUILD_TOOLS/apksigner" verify --verbose "$APK" >/dev/null

ADB="$ANDROID_HOME/platform-tools/adb"
mapfile -t DEVICES < <("$ADB" devices | awk 'NR > 1 && $2 == "device" { print $1 }')
if [[ "${#DEVICES[@]}" -eq 0 ]]; then
  echo "OK: APK verified; no Android device attached, device smoke test skipped"
  exit 0
fi
if [[ "${#DEVICES[@]}" -ne 1 ]]; then
  echo "Expected one Android test device, found ${#DEVICES[@]}" >&2
  exit 1
fi

SERIAL="${DEVICES[0]}"
ADB_DEVICE=("$ADB" -s "$SERIAL")
PACKAGE="dev.knoa.mobile"
echo "==> Android device smoke ($SERIAL)"
"${ADB_DEVICE[@]}" install -r "$APK" >/dev/null
"${ADB_DEVICE[@]}" logcat -c
"${ADB_DEVICE[@]}" shell am force-stop "$PACKAGE"
"${ADB_DEVICE[@]}" shell am start -W \
  -a android.intent.action.VIEW \
  -d 'knoa://node?workspaceId=smoke&workspaceName=Smoke&nodeId=smoke' \
  "$PACKAGE" >/dev/null

for _ in 1 2 3 4 5; do
  if [[ -n "$("${ADB_DEVICE[@]}" shell pidof "$PACKAGE" | tr -d '\r')" ]]; then
    break
  fi
  sleep 1
done
if [[ -z "$("${ADB_DEVICE[@]}" shell pidof "$PACKAGE" | tr -d '\r')" ]]; then
  "${ADB_DEVICE[@]}" logcat -d -v brief AndroidRuntime:E ReactNativeJS:E '*:S' >&2
  echo "Knoa Mobile exited while opening the Node route" >&2
  exit 1
fi
if "${ADB_DEVICE[@]}" logcat -d -v brief AndroidRuntime:E ReactNativeJS:E '*:S' \
  | grep -Eq 'FATAL EXCEPTION|ReactNativeJS.*(Error|Invariant Violation)'; then
  "${ADB_DEVICE[@]}" logcat -d -v brief AndroidRuntime:E ReactNativeJS:E '*:S' >&2
  echo "Knoa Mobile reported a fatal Node-route error" >&2
  exit 1
fi

echo "OK: Node route stayed alive on Android device $SERIAL"
