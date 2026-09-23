#!/usr/bin/env bash
# Maestro E2E gate for Knoa Mobile, layers 2/3 (backend-paired).
# Registers a throwaway hosted account, pairs the local node, sends one
# chat message, then revokes the test device binding.
#
# Footprint: one dormant test account stays on the hosted hub (no delete
# API); the device binding is revoked at the end. Grants are single-use
# and short-lived. Requires exactly one attached emulator/device.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$SCRIPT_DIR/.." && pwd)"
MOBILE="$REPO/apps/knoa-mobile"
APK="${1:-${KNOA_MOBILE_BUILD_DIR:-/disk/dev/knoa-mobile-out}/app/outputs/apk/release/app-release.apk}"
MAESTRO="${MAESTRO_BIN:-/disk/dev/maestro/maestro/bin/maestro}"
export MAESTRO_CLI_NO_ANALYTICS=1
OUT_DIR="${KNOA_MAESTRO_OUT:-/tmp/opencode/maestro}"
DISK_DEV="${DISK_DEV:-/disk/dev}"
VENV="$REPO/.venv/bin/python"

if [[ -f "$DISK_DEV/env.sh" ]]; then
  # shellcheck source=/dev/null
  source "$DISK_DEV/env.sh"
fi

if [[ ! -x "$MAESTRO" ]]; then echo "Maestro missing at $MAESTRO" >&2; exit 1; fi
if [[ ! -f "$APK" ]]; then echo "APK not found: $APK" >&2; exit 1; fi
ADB="$ANDROID_HOME/platform-tools/adb"
mapfile -t DEVICES < <("$ADB" devices | awk 'NR > 1 && $2 == "device" { print $1 }')
if [[ "${#DEVICES[@]}" -ne 1 ]]; then echo "Expected one device, found ${#DEVICES[@]}" >&2; exit 1; fi
SERIAL="${DEVICES[0]}"
export ANDROID_SERIAL="$SERIAL"

# Emulator DNS rots over long runs (unknown host). Reboot once if broken.
if ! "$ADB" -s "$SERIAL" shell "ping -c1 -W4 knoa.tinydotdot.com" 2>/dev/null | grep -q "bytes from"; then
  echo "==> Emulator DNS broken, rebooting"
  "$ADB" -s "$SERIAL" reboot
  sleep 120
  "$ADB" -s "$SERIAL" wait-for-device
  for _ in $(seq 1 30); do
    if [[ "$("$ADB" -s "$SERIAL" shell getprop sys.boot_completed 2>/dev/null | tr -d '\r')" == "1" ]]; then break; fi
    sleep 10
  done
  if ! "$ADB" -s "$SERIAL" shell "ping -c1 -W4 knoa.tinydotdot.com" 2>/dev/null | grep -q "bytes from"; then
    echo "Emulator DNS still broken after reboot; aborting" >&2
    exit 1
  fi
  echo "==> Emulator DNS recovered"
fi

# E2E runs in English locale: Gboard Pinyin transliterates adb-injected
# ASCII (JSON grants) into Chinese. Restore zh-CN afterwards.
"$ADB" -s "$SERIAL" root >/dev/null 2>&1 || true
"$ADB" -s "$SERIAL" shell "setprop persist.sys.locale en-US; setprop ctl.restart zygote"
echo "==> Locale set to en-US, waiting for reboot"
sleep 45
"$ADB" -s "$SERIAL" wait-for-device
for _ in $(seq 1 24); do
  if [[ "$("$ADB" -s "$SERIAL" shell getprop sys.boot_completed 2>/dev/null | tr -d '\r')" == "1" ]]; then break; fi
  sleep 10
done
restore_locale() {
  "$ADB" -s "$SERIAL" shell "setprop persist.sys.locale zh-CN; setprop ctl.restart zygote" >/dev/null 2>&1 || true
}
trap restore_locale EXIT

TS="$(date +%s)"
IDENTITY="e2e-${TS}@example.invalid"
DEVICE_NAME="e2e-emu-${TS}"
PASSWORD="e2e-test-pass-${TS}"
CHAT_TEXT="e2e ping ${TS}"

echo "==> Issuing hosted setup grant"
SETUP_JSON="$(
  set -a; source ~/.config/knoa/hosted-hub.env; set +a
  "$VENV" -m knoa_platform.hub.admin account-grant --ttl 3600 2>/dev/null \
  | grep '^hosted_setup_json=' | sed 's/^hosted_setup_json=//'
)"
if [[ -z "$SETUP_JSON" ]]; then echo "setup grant failed" >&2; exit 1; fi

echo "==> Issuing node pairing grant"
if [[ "${KNOA_E2E_DIRECT:-0}" == "1" ]]; then
  # Direct transport: emulator loopback reaches the host gateway through
  # `adb reverse`, so the public URL stays loopback-legal http. The config
  # file is restored immediately after the grant is issued.
  "$ADB" -s "$SERIAL" reverse tcp:9531 tcp:9531
  YAML_BAK="$OUT_DIR/local.yaml.bak"
  cp ~/.knoa/config/local.yaml "$YAML_BAK"
  sed -i 's|^gateway_public_url: ""|gateway_public_url: "http://127.0.0.1:9531"|' ~/.knoa/config/local.yaml
  PAIRING_JSON="$(
    "$VENV" -m knoa_platform gateway pair --ttl 900 2>/dev/null \
    | grep '^pairing_json=' | sed 's/^pairing_json=//'
  )"
  cp "$YAML_BAK" ~/.knoa/config/local.yaml
else
  PAIRING_JSON="$(
    "$VENV" -m knoa_platform gateway pair --ttl 900 2>/dev/null \
    | grep '^pairing_json=' | sed 's/^pairing_json=//'
  )"
fi
if [[ -z "$PAIRING_JSON" ]]; then echo "pairing grant failed" >&2; exit 1; fi

mkdir -p "$OUT_DIR"
FLOW="$OUT_DIR/e2e-flow.yaml"
"$VENV" - "$MOBILE/maestro/e2e-template.yaml" "$FLOW" <<EOF
import sys
tpl = open(sys.argv[1], encoding="utf-8").read()
def sq(value):
    # YAML single-quoted scalar: only '' needs escaping.
    return "'" + value.replace("'", "''") + "'"
subs = {
    "__SETUP_JSON__": sq("""$SETUP_JSON"""),
    "__IDENTITY__": sq("""$IDENTITY"""),
    "__DEVICE_NAME__": sq("""$DEVICE_NAME"""),
    "__PASSWORD__": sq("""$PASSWORD"""),
    "__PAIRING_JSON__": sq("""$PAIRING_JSON"""),
    "__CHAT_TEXT__": sq("""$CHAT_TEXT"""),
    "__WORKSPACE_NAME__": sq("""$DEVICE_NAME 的 Personal Workspace"""),
}
for key, value in subs.items():
    if key not in tpl:
        sys.exit(f"template placeholder missing: {key}")
    tpl = tpl.replace(key, value)
open(sys.argv[2], "w", encoding="utf-8").write(tpl)
print("flow rendered")
EOF

echo "==> Installing $APK on $SERIAL"
"$ADB" -s "$SERIAL" install -r "$APK" >/dev/null
"$ADB" -s "$SERIAL" logcat -c
echo "==> Maestro E2E flow (identity=$IDENTITY)"
status=0
if "$MAESTRO" test "$FLOW"; then
  echo "OK: maestro E2E passed on $SERIAL"
  # Server-side receipt: the turn must exist in the node database, not
  # just echo locally. This is the gate that matters.
  TURNS="$(CHAT_TEXT="$CHAT_TEXT" python3 -c "
import os, sqlite3, time
db = sqlite3.connect('/home/robin/.knoa/data/assistant.db')
n = db.execute(
  'SELECT COUNT(*) FROM conversation_turns WHERE user_input = ? AND created_at > ?',
  (os.environ['CHAT_TEXT'], time.time() - 900),
).fetchone()[0]
print(n)
")"
  if [[ "$TURNS" -ge 1 ]]; then
    echo "OK: server received the chat turn ($TURNS match)"
  else
    echo "E2E FAILED: chat turn never reached the node" >&2
    status=1
  fi
else
else
  status=$?
  "$ADB" -s "$SERIAL" logcat -d -v brief AndroidRuntime:E ReactNativeJS:E '*:S' \
    > "$OUT_DIR/e2e-logcat.txt" || true
  echo "Maestro E2E FAILED; logcat saved to $OUT_DIR/e2e-logcat.txt" >&2
fi

echo "==> Revoking test device binding ($DEVICE_NAME)"
DEVICE_ID="$("$VENV" -m knoa_platform gateway devices 2>/dev/null | grep -F "$DEVICE_NAME" | cut -f1 || true)"
if [[ -n "$DEVICE_ID" ]]; then
  "$VENV" -m knoa_platform gateway revoke "$DEVICE_ID"
else
  echo "test device not found (pairing may have failed); nothing to revoke"
fi
echo "NOTE: throwaway hosted account $IDENTITY remains (no delete API)"
exit $status
