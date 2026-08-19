#!/usr/bin/env bash
set -euo pipefail

ROLE="all"
SOURCE_PATH=""
PYTHON_EXECUTABLE="python3"
HUB_PUBLIC_URL="https://knoa.tinydotdot.com"
RECREATE_VENV=0

usage() {
    printf '%s\n' \
        "Usage: install-knoa.sh [--role hub|node|all] [--source PATH]" \
        "                       [--python PATH] [--hub-public-url URL]" \
        "                       [--recreate-venv]"
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --role) ROLE="${2:?missing role}"; shift 2 ;;
        --source) SOURCE_PATH="${2:?missing source path}"; shift 2 ;;
        --python) PYTHON_EXECUTABLE="${2:?missing Python executable}"; shift 2 ;;
        --hub-public-url) HUB_PUBLIC_URL="${2:?missing Hub public URL}"; shift 2 ;;
        --recreate-venv) RECREATE_VENV=1; shift ;;
        -h|--help) usage; exit 0 ;;
        *) printf 'Unknown argument: %s\n' "$1" >&2; usage >&2; exit 2 ;;
    esac
done

case "$ROLE" in
    hub|node|all) ;;
    *) printf 'Role must be hub, node or all\n' >&2; exit 2 ;;
esac

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ -z "$SOURCE_PATH" ]; then
    SOURCE_PATH="$(cd "$SCRIPT_DIR/../.." && pwd)"
else
    SOURCE_PATH="$(cd "$SOURCE_PATH" && pwd)"
fi
if [ ! -f "$SOURCE_PATH/pyproject.toml" ]; then
    printf 'Knoa source tree not found: %s\n' "$SOURCE_PATH" >&2
    exit 1
fi
if ! command -v systemctl >/dev/null 2>&1; then
    printf 'systemd is required for the Linux user-service installer\n' >&2
    exit 1
fi

DATA_ROOT="$HOME/.local/share/knoa"
RUNTIME_ROOT="$DATA_ROOT/runtime"
VENV_ROOT="$RUNTIME_ROOT/venv"
CONFIG_ROOT="$HOME/.config/knoa"
USER_SERVICE_ROOT="$HOME/.config/systemd/user"
USER_BIN_ROOT="$HOME/.local/bin"
HUB_ROOT="$DATA_ROOT/hosted-hub"
NODE_ROOT="$HOME/.knoa"
WORKSPACE_ROOT="$DATA_ROOT/workspace"
HUB_ENV="$CONFIG_ROOT/hosted-hub.env"
NODE_CONFIG="$CONFIG_ROOT/node-linux.yaml"

install_hub=0
install_node=0
case "$ROLE" in
    hub) install_hub=1 ;;
    node) install_node=1 ;;
    all) install_hub=1; install_node=1 ;;
esac

umask 077
install -d -m 700 "$DATA_ROOT" "$RUNTIME_ROOT" "$CONFIG_ROOT" "$USER_SERVICE_ROOT" "$USER_BIN_ROOT"
if [ "$RECREATE_VENV" -eq 1 ]; then
    rm -rf -- "$VENV_ROOT"
fi
if [ ! -x "$VENV_ROOT/bin/python" ]; then
    "$PYTHON_EXECUTABLE" -m venv "$VENV_ROOT"
fi
"$VENV_ROOT/bin/python" -m pip install --upgrade "$SOURCE_PATH"

if [ "$install_hub" -eq 1 ]; then
    systemctl --user stop knoa-hosted-hub.service 2>/dev/null || true
    install -d -m 700 "$HUB_ROOT"
    bootstrap_token=""
    if [ -f "$HUB_ENV" ]; then
        bootstrap_token="$(sed -n 's/^KNOA_HUB_BOOTSTRAP_TOKEN=//p' "$HUB_ENV" | head -n 1)"
    fi
    if [ "${#bootstrap_token}" -lt 32 ]; then
        bootstrap_token="$("$VENV_ROOT/bin/python" -c 'import secrets; print(secrets.token_urlsafe(48))')"
    fi
    printf 'KNOA_HUB_BOOTSTRAP_TOKEN=%s\nKNOA_HUB_PUBLIC_URL=%s\nKNOA_HUB_ADMIN_ENDPOINT=http://127.0.0.1:9529\n' \
        "$bootstrap_token" "$HUB_PUBLIC_URL" > "$HUB_ENV"
    chmod 600 "$HUB_ENV"
    bootstrap_token=""
    install -m 600 "$SCRIPT_DIR/knoa-hosted-hub.service" \
        "$USER_SERVICE_ROOT/knoa-hosted-hub.service"
    install -m 700 "$SCRIPT_DIR/publish-knoa-app.sh" "$USER_BIN_ROOT/knoa-publish-app"
fi

if [ "$install_node" -eq 1 ]; then
    systemctl --user stop knoa-node.service 2>/dev/null || true
    install -d -m 700 "$NODE_ROOT" "$WORKSPACE_ROOT"
    install -m 600 "$SCRIPT_DIR/node-linux.yaml" "$NODE_CONFIG"
    install -m 600 "$SCRIPT_DIR/knoa-node.service" \
        "$USER_SERVICE_ROOT/knoa-node.service"
fi

systemctl --user daemon-reload
if [ "$install_hub" -eq 1 ]; then
    systemctl --user enable --now knoa-hosted-hub.service
    printf 'Knoa Hosted Hub: http://127.0.0.1:9529\n'
    printf 'Canonical Hub URL: %s\n' "$HUB_PUBLIC_URL"
fi
if [ "$install_node" -eq 1 ]; then
    systemctl --user enable --now knoa-node.service
    printf 'Knoa Node Gateway: http://127.0.0.1:9531\n'
    if [ -f "$NODE_ROOT/data/node-hub.json" ]; then
        "$VENV_ROOT/bin/python" -m knoa_platform --config "$NODE_CONFIG" gateway pair --ttl 600 || true
    else
        printf 'Enroll this Node into a Workspace, then restart knoa-node.service.\n'
    fi
fi

printf 'Installed Knoa role: %s\n' "$ROLE"
printf 'For boot-before-login, enable linger once: sudo loginctl enable-linger %s\n' "$USER"
