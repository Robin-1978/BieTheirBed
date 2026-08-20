#!/usr/bin/env bash
set -euo pipefail

ROLE="all"
SOURCE_PATH=""
CHANNEL_SOURCE_PATH=""
PYTHON_EXECUTABLE="python3"
HUB_PUBLIC_URL="https://knoa.tinydotdot.com"
RECREATE_VENV=0
SKIP_PAIRING_QR=0

usage() {
    printf '%s\n' \
        "Usage: install-knoa.sh [--role hub|node|all] [--source PATH]" \
        "                       [--channel-source PATH]" \
        "                       [--python PATH] [--hub-public-url URL]" \
        "                       [--recreate-venv] [--skip-pairing-qr]"
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --role) ROLE="${2:?missing role}"; shift 2 ;;
        --source) SOURCE_PATH="${2:?missing source path}"; shift 2 ;;
        --channel-source) CHANNEL_SOURCE_PATH="${2:?missing channel source path}"; shift 2 ;;
        --python) PYTHON_EXECUTABLE="${2:?missing Python executable}"; shift 2 ;;
        --hub-public-url) HUB_PUBLIC_URL="${2:?missing Hub public URL}"; shift 2 ;;
        --recreate-venv) RECREATE_VENV=1; shift ;;
        --skip-pairing-qr) SKIP_PAIRING_QR=1; shift ;;
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
if [ -z "$CHANNEL_SOURCE_PATH" ]; then
    CHANNEL_SOURCE_PATH="$SOURCE_PATH"
else
    CHANNEL_SOURCE_PATH="$(cd "$CHANNEL_SOURCE_PATH" && pwd)"
fi
if [ ! -d "$CHANNEL_SOURCE_PATH/.git" ]; then
    printf 'Knoa update channel is not a Git checkout: %s\n' "$CHANNEL_SOURCE_PATH" >&2
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
LIFECYCLE_TOKEN="$CONFIG_ROOT/lifecycle.token"
SOURCE_UPDATE_ENV="$CONFIG_ROOT/source-update.env"
INSTALLATION_STATE="$CONFIG_ROOT/installation.json"
SOURCE_UPDATE_ROOT="$DATA_ROOT/source-updates"
INCOMING_ROOT="$DATA_ROOT/incoming"

install_hub=0
install_node=0
case "$ROLE" in
    hub) install_hub=1 ;;
    node) install_node=1 ;;
    all) install_hub=1; install_node=1 ;;
esac

# Hub and Node share one installed runtime on a host. Updating either role must
# therefore stop and reconcile every role already installed on that host.
if [ -f "$USER_SERVICE_ROOT/knoa-hosted-hub.service" ]; then
    install_hub=1
fi
if [ -f "$USER_SERVICE_ROOT/knoa-node.service" ]; then
    install_node=1
fi
if [ "$install_hub" -eq 1 ] && [ "$install_node" -eq 1 ]; then
    EFFECTIVE_ROLE="all"
elif [ "$install_hub" -eq 1 ]; then
    EFFECTIVE_ROLE="hub"
else
    EFFECTIVE_ROLE="node"
fi

umask 077
install -d -m 700 "$DATA_ROOT" "$RUNTIME_ROOT" "$CONFIG_ROOT" "$USER_SERVICE_ROOT" "$USER_BIN_ROOT" "$SOURCE_UPDATE_ROOT" "$INCOMING_ROOT"
if [ "$install_hub" -eq 1 ]; then
    systemctl --user stop knoa-hosted-hub.service 2>/dev/null || true
fi
if [ "$install_node" -eq 1 ]; then
    systemctl --user stop knoa-node.service 2>/dev/null || true
fi
if [ "$RECREATE_VENV" -eq 1 ]; then
    rm -rf -- "$VENV_ROOT"
fi
if [ ! -x "$VENV_ROOT/bin/python" ]; then
    "$PYTHON_EXECUTABLE" -m venv "$VENV_ROOT"
fi
"$VENV_ROOT/bin/python" -m pip install --upgrade "$SOURCE_PATH"

if [ "$install_hub" -eq 1 ]; then
    install -d -m 700 "$HUB_ROOT"
    bootstrap_token=""
    release_publish_token=""
    if [ -f "$HUB_ENV" ]; then
        bootstrap_token="$(sed -n 's/^KNOA_HUB_BOOTSTRAP_TOKEN=//p' "$HUB_ENV" | head -n 1)"
        release_publish_token="$(sed -n 's/^KNOA_HUB_RELEASE_PUBLISH_TOKEN=//p' "$HUB_ENV" | head -n 1)"
    fi
    if [ "${#bootstrap_token}" -lt 32 ]; then
        bootstrap_token="$("$VENV_ROOT/bin/python" -c 'import secrets; print(secrets.token_urlsafe(48))')"
    fi
    if [ "${#release_publish_token}" -lt 32 ]; then
        release_publish_token="$("$VENV_ROOT/bin/python" -c 'import secrets; print(secrets.token_urlsafe(48))')"
    fi
    printf 'KNOA_HUB_BOOTSTRAP_TOKEN=%s\nKNOA_HUB_RELEASE_PUBLISH_TOKEN=%s\nKNOA_HUB_PUBLIC_URL=%s\nKNOA_HUB_ADMIN_ENDPOINT=http://127.0.0.1:9529\n' \
        "$bootstrap_token" "$release_publish_token" "$HUB_PUBLIC_URL" > "$HUB_ENV"
    chmod 600 "$HUB_ENV"
    bootstrap_token=""
    release_publish_token=""
    install -m 600 "$SCRIPT_DIR/knoa-hosted-hub.service" \
        "$USER_SERVICE_ROOT/knoa-hosted-hub.service"
    install -m 700 "$SCRIPT_DIR/publish-knoa-app.sh" "$USER_BIN_ROOT/knoa-publish-app"
fi

if [ "$install_node" -eq 1 ]; then
    install -d -m 700 "$NODE_ROOT" "$WORKSPACE_ROOT"
    install -m 600 "$SCRIPT_DIR/node-linux.yaml" "$NODE_CONFIG"
    install -m 600 "$SCRIPT_DIR/knoa-node.service" \
        "$USER_SERVICE_ROOT/knoa-node.service"
fi

if [ ! -s "$LIFECYCLE_TOKEN" ]; then
    "$VENV_ROOT/bin/python" -c 'import secrets,sys; open(sys.argv[1], "w", encoding="utf-8").write(secrets.token_urlsafe(48))' "$LIFECYCLE_TOKEN"
fi
chmod 600 "$LIFECYCLE_TOKEN"
"$VENV_ROOT/bin/python" -c 'import json,os,sys; keys=("HTTP_PROXY","HTTPS_PROXY","NO_PROXY","http_proxy","https_proxy","no_proxy"); open(sys.argv[1],"w",encoding="utf-8").write("".join(f"{key}={json.dumps(os.environ[key])}\n" for key in keys if os.environ.get(key)))' "$SOURCE_UPDATE_ENV"
chmod 600 "$SOURCE_UPDATE_ENV"
escaped_source="$(printf '%s' "$CHANNEL_SOURCE_PATH" | sed 's/[&|]/\\&/g')"
sed "s|@SOURCE_ROOT@|$escaped_source|g" "$SCRIPT_DIR/knoa-host-lifecycle.service" > "$USER_SERVICE_ROOT/knoa-host-lifecycle.service"
chmod 600 "$USER_SERVICE_ROOT/knoa-host-lifecycle.service"
installed_commit="$(git -C "$SOURCE_PATH" rev-parse --verify HEAD)"
if ! printf '%s' "$installed_commit" | grep -Eq '^[0-9a-fA-F]{40}$'; then
    printf 'Could not determine the installed Knoa source revision\n' >&2
    exit 1
fi
"$VENV_ROOT/bin/python" -c 'import json,sys,time; path,role,source,public_url,python,commit=sys.argv[1:]; json.dump({"schema_version":1,"install_mode":"source","role":role,"source_path":source,"hub_public_url":public_url,"python_executable":python,"installed_commit":commit.lower(),"updated_at":time.time()},open(path,"w",encoding="utf-8"),ensure_ascii=False,indent=2); open(path,"a",encoding="utf-8").write("\n")' "$INSTALLATION_STATE" "$EFFECTIVE_ROLE" "$CHANNEL_SOURCE_PATH" "$HUB_PUBLIC_URL" "$PYTHON_EXECUTABLE" "$installed_commit"
chmod 600 "$INSTALLATION_STATE"

systemctl --user daemon-reload
systemctl --user enable knoa-host-lifecycle.service
if [ "${KNOA_SOURCE_UPDATE_ACTIVE:-0}" != "1" ]; then
    systemctl --user restart knoa-host-lifecycle.service
fi
if [ "$install_hub" -eq 1 ]; then
    systemctl --user enable --now knoa-hosted-hub.service
    printf 'Knoa Hosted Hub: http://127.0.0.1:9529\n'
    printf 'Canonical Hub URL: %s\n' "$HUB_PUBLIC_URL"
fi
if [ "$install_node" -eq 1 ]; then
    systemctl --user enable --now knoa-node.service
    printf 'Knoa Node Gateway: http://127.0.0.1:9531\n'
    if [ -f "$NODE_ROOT/data/node-hub.json" ] && [ "$SKIP_PAIRING_QR" -ne 1 ]; then
        "$VENV_ROOT/bin/python" -m knoa_platform --config "$NODE_CONFIG" gateway pair --ttl 600 || true
    elif [ ! -f "$NODE_ROOT/data/node-hub.json" ]; then
        printf 'Enroll this Node into a Workspace, then restart knoa-node.service.\n'
    fi
fi

printf 'Installed Knoa role: %s\n' "$EFFECTIVE_ROLE"
printf 'For boot-before-login, enable linger once: sudo loginctl enable-linger %s\n' "$USER"
