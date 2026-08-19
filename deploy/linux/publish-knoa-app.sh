#!/usr/bin/env bash
set -euo pipefail

APK_PATH=""
HUB_ROOT="$HOME/.local/share/knoa/hosted-hub"
PYTHON_EXECUTABLE="$HOME/.local/share/knoa/runtime/venv/bin/python"
HUB_PUBLIC_URL="https://knoa.tinydotdot.com"
MIN_VERSION_CODE=1
NOTES=""

usage() {
    printf '%s\n' \
        "Usage: publish-knoa-app.sh --apk PATH [--hub-root PATH]" \
        "       [--python PATH] [--hub-public-url URL]" \
        "       [--min-version-code NUMBER] [--notes TEXT]"
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --apk) APK_PATH="${2:?missing APK path}"; shift 2 ;;
        --hub-root) HUB_ROOT="${2:?missing Hub root}"; shift 2 ;;
        --python) PYTHON_EXECUTABLE="${2:?missing Python executable}"; shift 2 ;;
        --hub-public-url) HUB_PUBLIC_URL="${2:?missing Hub public URL}"; shift 2 ;;
        --min-version-code) MIN_VERSION_CODE="${2:?missing version code}"; shift 2 ;;
        --notes) NOTES="${2:?missing release notes}"; shift 2 ;;
        -h|--help) usage; exit 0 ;;
        *) printf 'Unknown argument: %s\n' "$1" >&2; usage >&2; exit 2 ;;
    esac
done

if [ -z "$APK_PATH" ] || [ ! -f "$APK_PATH" ]; then
    printf 'Signed APK not found: %s\n' "$APK_PATH" >&2
    exit 1
fi
if [ ! -x "$PYTHON_EXECUTABLE" ]; then
    printf 'Knoa Python not found: %s\n' "$PYTHON_EXECUTABLE" >&2
    exit 1
fi
if [ ! -d "$HUB_ROOT" ]; then
    printf 'Hosted Hub root not found: %s\n' "$HUB_ROOT" >&2
    exit 1
fi

"$PYTHON_EXECUTABLE" -m knoa_platform.hub.admin mobile-publish "$APK_PATH" \
    --root "$HUB_ROOT" \
    --min-version-code "$MIN_VERSION_CODE" \
    --notes "$NOTES"
"$PYTHON_EXECUTABLE" -m knoa_platform.hub.admin mobile-latest --root "$HUB_ROOT"
printf 'Stable App download: %s/downloads/android/latest.apk\n' "${HUB_PUBLIC_URL%/}"
