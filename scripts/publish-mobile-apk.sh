#!/usr/bin/env bash
# Publish an owner-signed Knoa APK to the Gateway private update channel.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$SCRIPT_DIR/.." && pwd)"
DISK_DEV="${DISK_DEV:-/disk/dev}"
ENV_SH="$DISK_DEV/env.sh"
APK="${1:-$DISK_DEV/knoa-mobile-out/app/outputs/apk/release/app-release.apk}"
NOTES="${KNOA_MOBILE_RELEASE_NOTES:-}"
MIN_VERSION_CODE="${KNOA_MOBILE_MIN_VERSION_CODE:-1}"

if [[ -f "$ENV_SH" ]]; then
  # shellcheck source=/dev/null
  source "$ENV_SH"
fi
if [[ ! -f "$APK" ]]; then
  echo "APK not found: $APK" >&2
  exit 1
fi

if [[ -n "${KNOA_PYTHON:-}" ]]; then
  KNOA_RELEASE_PYTHON="$KNOA_PYTHON"
elif [[ -x "$REPO/.venv/bin/python" ]]; then
  KNOA_RELEASE_PYTHON="$REPO/.venv/bin/python"
elif [[ -x "/disk/miniconda3/bin/python" ]]; then
  KNOA_RELEASE_PYTHON="/disk/miniconda3/bin/python"
else
  KNOA_RELEASE_PYTHON="$(command -v python3 2>/dev/null || true)"
fi
if [[ -z "$KNOA_RELEASE_PYTHON" || ! -x "$KNOA_RELEASE_PYTHON" ]]; then
  echo "No usable Python interpreter found." >&2
  exit 1
fi

export PYTHONPATH="$REPO/src${PYTHONPATH:+:$PYTHONPATH}"
exec "$KNOA_RELEASE_PYTHON" -m knoa_platform gateway release publish "$APK" \
  --min-version-code "$MIN_VERSION_CODE" \
  --notes "$NOTES"
