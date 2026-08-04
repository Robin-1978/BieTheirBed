#!/usr/bin/env bash
# Shared helpers for the PC Assistant service scripts.
# Source this file, then call service_start / service_stop / service_is_running.
#
# Runtime files live under $XDG_RUNTIME_DIR/pc-assistant (or ~/.local/run/pc-assistant):
#   service.pid   service.sock   service.log

SERVICE_RUNTIME_DIR="${XDG_RUNTIME_DIR:-$HOME/.local/run}/pc-assistant"
SERVICE_PID_FILE="$SERVICE_RUNTIME_DIR/service.pid"
SERVICE_SOCK_FILE="$SERVICE_RUNTIME_DIR/service.sock"
SERVICE_LOG_FILE="$SERVICE_RUNTIME_DIR/service.log"

SERVICE_BIN="$(command -v pc-assistant 2>/dev/null || true)"

service_require_bin() {
    if [ -z "$SERVICE_BIN" ]; then
        echo "ERROR: 'pc-assistant' not found on PATH. Install with: pip install -e ." >&2
        exit 1
    fi
}

# Returns 0 if the service process is alive (pid file + live pid).
service_is_running() {
    [ -f "$SERVICE_PID_FILE" ] || return 1
    local pid
    pid="$(cat "$SERVICE_PID_FILE" 2>/dev/null || echo "")"
    [ -n "$pid" ] || return 1
    kill -0 "$pid" 2>/dev/null
}

# Wait up to N seconds for the service pid file to appear with a live pid.
service_wait_ready() {
    local timeout="${1:-30}"
    local i pid
    for ((i = 0; i < timeout; i++)); do
        if [ -f "$SERVICE_PID_FILE" ]; then
            pid="$(cat "$SERVICE_PID_FILE" 2>/dev/null || echo "")"
            if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
                return 0
            fi
        fi
        sleep 1
    done
    return 1
}

# Wait up to N seconds for the service to exit.
service_wait_stopped() {
    local timeout="${1:-15}"
    local i pid
    pid="$(cat "$SERVICE_PID_FILE" 2>/dev/null || echo "")"
    if [ -z "$pid" ]; then
        return 0
    fi
    for ((i = 0; i < timeout; i++)); do
        if ! kill -0 "$pid" 2>/dev/null; then
            return 0
        fi
        sleep 1
    done
    return 1
}

service_start() {
    service_require_bin
    if service_is_running; then
        echo "Service already running (pid $(cat "$SERVICE_PID_FILE"))."
        return 0
    fi
    echo "Starting PC Assistant service (daemon)…"
    "$SERVICE_BIN" --serve --daemon
    if ! service_wait_ready 30; then
        echo "ERROR: Service did not become ready in time." >&2
        echo "Check log: $SERVICE_LOG_FILE" >&2
        return 1
    fi
    echo "Service started (pid $(cat "$SERVICE_PID_FILE"))."
    echo "Log: $SERVICE_LOG_FILE"
}

service_stop() {
    service_require_bin
    if ! service_is_running; then
        echo "Service is not running."
        rm -f "$SERVICE_PID_FILE"
        return 0
    fi
    local pid
    pid="$(cat "$SERVICE_PID_FILE")"
    echo "Stopping service (pid $pid)…"
    "$SERVICE_BIN" --stop >/dev/null 2>&1 || kill "$pid" 2>/dev/null || true
    if ! service_wait_stopped 15; then
        echo "Service did not stop gracefully; sending SIGKILL…" >&2
        kill -9 "$pid" 2>/dev/null || true
        sleep 1
    fi
    echo "Service stopped."
}
