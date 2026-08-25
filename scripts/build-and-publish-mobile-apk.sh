#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

"$SCRIPT_DIR/test-mobile-app.sh"
"$SCRIPT_DIR/build-mobile-apk.sh"
"$SCRIPT_DIR/test-mobile-app.sh" \
  "${KNOA_MOBILE_BUILD_DIR:-/disk/dev/knoa-mobile-out}/app/outputs/apk/release/app-release.apk"
"$SCRIPT_DIR/publish-hosted-mobile-apk.sh"
