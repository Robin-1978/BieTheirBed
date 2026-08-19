#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
DISK_DEV="${DISK_DEV:-/disk/dev}"
APK_PATH="${1:-$DISK_DEV/knoa-mobile-out/app/outputs/apk/release/app-release.apk}"
HUB_URL="${KNOA_HUB_PUBLIC_URL:-https://knoa.tinydotdot.com}"
TOKEN_FILE="${KNOA_HUB_RELEASE_TOKEN_FILE:-${KNOA_HOME:-$HOME/.knoa}/secrets/hosted-hub-release-publisher.token}"
NOTES="${KNOA_MOBILE_RELEASE_NOTES:-}"
MIN_VERSION_CODE="${KNOA_MOBILE_MIN_VERSION_CODE:-1}"

if [ ! -f "$APK_PATH" ]; then
    printf 'Signed APK not found: %s\n' "$APK_PATH" >&2
    exit 1
fi
if [ -x "$REPO_ROOT/.venv/bin/python" ]; then
    KNOA_RELEASE_PYTHON="$REPO_ROOT/.venv/bin/python"
else
    KNOA_RELEASE_PYTHON="$(command -v python3)"
fi

export PYTHONPATH="$REPO_ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
exec "$KNOA_RELEASE_PYTHON" -m knoa_platform.hub.admin mobile-upload "$APK_PATH" \
    --hub-url "$HUB_URL" \
    --token-file "$TOKEN_FILE" \
    --min-version-code "$MIN_VERSION_CODE" \
    --notes "$NOTES"
