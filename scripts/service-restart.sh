#!/usr/bin/env bash
# Restart the Knoa service (stop if running, then start).
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=service-lib.sh
source "$SCRIPT_DIR/service-lib.sh"

service_stop
echo "---"
service_start
