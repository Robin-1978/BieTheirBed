#!/usr/bin/env bash
# Start the Knoa service in the background (daemon).
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=service-lib.sh
source "$SCRIPT_DIR/service-lib.sh"
service_start
