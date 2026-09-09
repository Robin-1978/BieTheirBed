#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# The Gradle release task creates and validates the production JS bundle. Do
# not build that bundle once here and then rebuild it inside Gradle.
KNOA_MOBILE_SKIP_BUNDLE=true "$SCRIPT_DIR/test-mobile-app.sh"
"$SCRIPT_DIR/build-mobile-apk.sh"
# The static gate above already ran typecheck/tests; this pass only verifies
# the signed APK (and runs the optional attached-device smoke test).
"$SCRIPT_DIR/test-mobile-app.sh" \
  --apk-only \
  "${KNOA_MOBILE_BUILD_DIR:-/disk/dev/knoa-mobile-out}/app/outputs/apk/release/app-release.apk"
"$SCRIPT_DIR/publish-hosted-mobile-apk.sh"
