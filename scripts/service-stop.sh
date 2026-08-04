#!/usr/bin/env bash
# Stop the PC Assistant service.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=service-lib.sh
source "$SCRIPT_DIR/service-lib.sh"
service_stop
