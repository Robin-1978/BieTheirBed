#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

"$SCRIPT_DIR/build-mobile-apk.sh"
"$SCRIPT_DIR/publish-hosted-mobile-apk.sh"
