#!/usr/bin/env bash
# Shared source-backed wrappers around Knoa's authoritative service lifecycle.
# Source this file, then call service_start / service_stop / service_is_running.

SERVICE_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVICE_REPO_ROOT="$(cd "$SERVICE_SCRIPT_DIR/.." && pwd)"

if [ -n "${KNOA_PYTHON:-}" ]; then
    SERVICE_PYTHON="$KNOA_PYTHON"
elif [ -x "$SERVICE_REPO_ROOT/.venv/bin/python" ]; then
    SERVICE_PYTHON="$SERVICE_REPO_ROOT/.venv/bin/python"
elif [ -x "/disk/miniconda3/bin/python" ]; then
    SERVICE_PYTHON="/disk/miniconda3/bin/python"
else
    SERVICE_PYTHON="$(command -v python3 2>/dev/null || true)"
fi

service_require_runtime() {
    if [ -z "$SERVICE_PYTHON" ] || [ ! -x "$SERVICE_PYTHON" ]; then
        echo "ERROR: no usable Python interpreter found." >&2
        exit 1
    fi
    if [ ! -f "$SERVICE_REPO_ROOT/src/knoa_platform/__init__.py" ]; then
        echo "ERROR: Knoa source tree not found at $SERVICE_REPO_ROOT." >&2
        exit 1
    fi
}

service_run() {
    CRYPTOGRAPHY_OPENSSL_NO_LEGACY=1 \
        PYTHONPATH="$SERVICE_REPO_ROOT/src${PYTHONPATH:+:$PYTHONPATH}" \
        "$SERVICE_PYTHON" -m knoa_platform "$@"
}

# Returns 0 if Knoa's PID/port-aware lifecycle reports a live service.
service_is_running() {
    service_run --status >/dev/null 2>&1
}

service_start() {
    service_require_runtime
    service_run --start
}

service_stop() {
    service_require_runtime
    service_run --stop
}
