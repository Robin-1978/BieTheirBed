#!/usr/bin/env bash
# Publish an owner-signed Knoa APK to the Gateway private update channel.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$SCRIPT_DIR/.." && pwd)"
DISK_DEV="${DISK_DEV:-/disk/dev}"
ENV_SH="$DISK_DEV/env.sh"
APK="${1:-$DISK_DEV/knoa-mobile-out/app/outputs/apk/release/app-release.apk}"
NOTES="${KNOA_MOBILE_RELEASE_NOTES:-}"

if [[ -f "$ENV_SH" ]]; then
  # shellcheck source=/dev/null
  source "$ENV_SH"
fi
if [[ ! -f "$APK" ]]; then
  echo "APK not found: $APK" >&2
  exit 1
fi

PCA_BIN="${PCA_BIN:-$REPO/.venv/bin/pca}"
if [[ ! -x "$PCA_BIN" ]]; then
  PCA_BIN="$(command -v pca)"
fi
exec "$PCA_BIN" gateway release publish "$APK" --notes "$NOTES"
