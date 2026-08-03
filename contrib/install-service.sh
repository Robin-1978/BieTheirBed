#!/usr/bin/env bash
# Install or uninstall the PC Assistant systemd user service.
#
# Usage:
#   ./install-service.sh          # install + enable + start
#   ./install-service.sh remove   # stop + disable + remove

set -euo pipefail

UNIT_NAME="pc-assistant.service"
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SOURCE="$SCRIPT_DIR/$UNIT_NAME"

if [[ "${1:-}" == "remove" ]]; then
    echo "Stopping and removing $UNIT_NAME ..."
    systemctl --user stop "$UNIT_NAME" 2>/dev/null || true
    systemctl --user disable "$UNIT_NAME" 2>/dev/null || true
    rm -f "$UNIT_DIR/$UNIT_NAME"
    systemctl --user daemon-reload
    echo "Done. Service removed."
    exit 0
fi

if [[ ! -f "$SOURCE" ]]; then
    echo "Error: $SOURCE not found" >&2
    exit 1
fi

mkdir -p "$UNIT_DIR"
cp "$SOURCE" "$UNIT_DIR/$UNIT_NAME"
systemctl --user daemon-reload
systemctl --user enable "$UNIT_NAME"
systemctl --user start "$UNIT_NAME"

echo "Installed and started $UNIT_NAME"
echo "Check status: systemctl --user status $UNIT_NAME"
echo "View logs:    journalctl --user -u $UNIT_NAME -f"
