#!/usr/bin/env bash
# Check or bump independent Knoa Platform and Mobile product versions.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

if [ -x "$REPO_ROOT/.venv/bin/python" ]; then
    VERSION_PYTHON="$REPO_ROOT/.venv/bin/python"
else
    VERSION_PYTHON="$(command -v python3)"
fi

exec "$VERSION_PYTHON" "$SCRIPT_DIR/version_manager.py" "$@"
