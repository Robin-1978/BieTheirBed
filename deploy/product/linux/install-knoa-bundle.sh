#!/usr/bin/env bash
set -eEuo pipefail

ROLE=""
BUNDLE=""
TRUST_STORE=""
UPDATER=""
INSTALL_ROOT="/opt/knoa"
DATA_ROOT="/var/lib/knoa"
CONFIG_ROOT="/etc/knoa"
HUB_PUBLIC_URL="https://knoa.tinydotdot.com"

usage() {
    printf '%s\n' \
        "Usage: install-knoa-bundle.sh --role hub|node|all --bundle FILE" \
        "       --trust-store FILE --updater FILE [--install-root DIR]" \
        "       [--data-root DIR] [--config-root DIR] [--hub-public-url URL]"
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --role) ROLE="${2:?missing role}"; shift 2 ;;
        --bundle) BUNDLE="${2:?missing Bundle}"; shift 2 ;;
        --trust-store) TRUST_STORE="${2:?missing Trust Store}"; shift 2 ;;
        --updater) UPDATER="${2:?missing updater}"; shift 2 ;;
        --install-root) INSTALL_ROOT="${2:?missing install root}"; shift 2 ;;
        --data-root) DATA_ROOT="${2:?missing data root}"; shift 2 ;;
        --config-root) CONFIG_ROOT="${2:?missing config root}"; shift 2 ;;
        --hub-public-url) HUB_PUBLIC_URL="${2:?missing Hub URL}"; shift 2 ;;
        -h|--help) usage; exit 0 ;;
        *) printf 'Unknown argument: %s\n' "$1" >&2; usage >&2; exit 2 ;;
    esac
done

case "$ROLE" in hub|node|all) ;; *) usage >&2; exit 2 ;; esac
[ "$(id -u)" -eq 0 ] || { printf 'Run as root\n' >&2; exit 1; }
[ -f "$BUNDLE" ] || { printf 'Bundle not found: %s\n' "$BUNDLE" >&2; exit 1; }
[ -f "$TRUST_STORE" ] || { printf 'Trust Store not found: %s\n' "$TRUST_STORE" >&2; exit 1; }
[ -x "$UPDATER" ] || { printf 'Native updater not executable: %s\n' "$UPDATER" >&2; exit 1; }
command -v systemctl >/dev/null
command -v curl >/dev/null
command -v getent >/dev/null

case "$(uname -m)" in
    x86_64|amd64) TARGET_ARCH="x86_64" ;;
    aarch64|arm64) TARGET_ARCH="aarch64" ;;
    *) printf 'Unsupported architecture: %s\n' "$(uname -m)" >&2; exit 1 ;;
esac

RELEASE_ROOT="$INSTALL_ROOT/releases"
BIN_ROOT="$INSTALL_ROOT/bin"
HUB_ROOT="$DATA_ROOT/hub"
NODE_ROOT="$DATA_ROOT/node"
WORKSPACE_ROOT="$DATA_ROOT/workspace"
SECRET_ROOT="$CONFIG_ROOT/secrets"
NODE_CONFIG="$CONFIG_ROOT/node.yaml"
HUB_ENV="$CONFIG_ROOT/hub.env"
HOST_STATE="$CONFIG_ROOT/host-state.json"
LIFECYCLE_TOKEN="$SECRET_ROOT/lifecycle.token"
LIFECYCLE_TRUST="$CONFIG_ROOT/release-trust.json"
INCOMING_ROOT="$DATA_ROOT/incoming"
STAGING="$INSTALL_ROOT/.incoming.$$"
SERVICES=""
case "$ROLE" in
    hub) SERVICES="knoa-hub.service" ;;
    node) SERVICES="knoa-node.service" ;;
    all) SERVICES="knoa-hub.service knoa-node.service" ;;
esac

install -d -m 755 "$INSTALL_ROOT" "$RELEASE_ROOT" "$BIN_ROOT"
getent group knoa >/dev/null || groupadd --system knoa
id -u knoa >/dev/null 2>&1 || useradd --system --gid knoa --home-dir /nonexistent --shell /usr/sbin/nologin knoa
trap 'rm -rf -- "$STAGING"' EXIT
"$UPDATER" install \
    --archive "$BUNDLE" \
    --staging "$STAGING" \
    --trust-store "$TRUST_STORE" \
    --kind product \
    --role all \
    --target-os linux \
    --target-arch "$TARGET_ARCH" \
    --install-root "$RELEASE_ROOT" \
    --health-entrypoint bin/knoa-health
rm -rf -- "$STAGING"
trap - EXIT

rollback_live_failure() {
    set +e
    systemctl stop knoa-host-lifecycle.service 2>/dev/null || true
    for service in $SERVICES; do systemctl stop "$service" 2>/dev/null || true; done
    recovery_updater="$UPDATER"
    [ -x "$BIN_ROOT/knoa-update" ] && recovery_updater="$BIN_ROOT/knoa-update"
    "$recovery_updater" reject --install-root "$RELEASE_ROOT" --health-entrypoint bin/knoa-health
    if "$recovery_updater" current --install-root "$RELEASE_ROOT" >/dev/null 2>&1; then
        systemctl start knoa-host-lifecycle.service 2>/dev/null || true
        for service in $SERVICES; do systemctl start "$service" 2>/dev/null || true; done
    fi
}
trap rollback_live_failure ERR

CURRENT="$($UPDATER current --install-root "$RELEASE_ROOT")"
systemctl stop knoa-host-lifecycle.service knoa-hub.service knoa-node.service 2>/dev/null || true
install -m 755 "$UPDATER" "$BIN_ROOT/knoa-update"
install -d -m 700 "$CONFIG_ROOT" "$SECRET_ROOT"
install -d -m 770 "$INCOMING_ROOT"

new_secret() {
    head -c 48 /dev/urandom | base64 | tr -d '=\n' | tr '+/' '-_'
}

install -d -m 700 "$HUB_ROOT" "$NODE_ROOT" "$WORKSPACE_ROOT"
[ -s "$SECRET_ROOT/hub-bootstrap.token" ] || new_secret > "$SECRET_ROOT/hub-bootstrap.token"
[ -s "$SECRET_ROOT/hub-release-publisher.token" ] || new_secret > "$SECRET_ROOT/hub-release-publisher.token"
[ -s "$LIFECYCLE_TOKEN" ] || new_secret > "$LIFECYCLE_TOKEN"
chmod 600 "$SECRET_ROOT"/*.token
install -m 600 "$TRUST_STORE" "$LIFECYCLE_TRUST"
{
    printf 'KNOA_HUB_BOOTSTRAP_TOKEN=%s\n' "$(cat "$SECRET_ROOT/hub-bootstrap.token")"
    printf 'KNOA_HUB_RELEASE_PUBLISH_TOKEN=%s\n' "$(cat "$SECRET_ROOT/hub-release-publisher.token")"
    printf 'KNOA_HUB_PUBLIC_URL=%s\n' "$HUB_PUBLIC_URL"
    printf 'KNOA_LIFECYCLE_TOKEN_FILE=%s\n' "$LIFECYCLE_TOKEN"
    printf 'KNOA_LIFECYCLE_INCOMING_ROOT=%s\n' "$INCOMING_ROOT"
} > "$HUB_ENV"
chmod 600 "$HUB_ENV"
if [ ! -f "$NODE_CONFIG" ]; then
    sed \
        -e "s|@NODE_ROOT@|$NODE_ROOT|g" \
        -e "s|@WORKSPACE_ROOT@|$WORKSPACE_ROOT|g" \
        "$CURRENT/install/node.yaml" > "$NODE_CONFIG"
    chmod 600 "$NODE_CONFIG"
fi
sed -e "s|@INSTALL_ROOT@|$INSTALL_ROOT|g" -e "s|@HUB_ROOT@|$HUB_ROOT|g" -e "s|@HUB_ENV@|$HUB_ENV|g" "$CURRENT/install/knoa-hub.service" > /etc/systemd/system/knoa-hub.service
sed -e "s|@INSTALL_ROOT@|$INSTALL_ROOT|g" -e "s|@NODE_ROOT@|$NODE_ROOT|g" -e "s|@NODE_CONFIG@|$NODE_CONFIG|g" -e "s|@WORKSPACE_ROOT@|$WORKSPACE_ROOT|g" -e "s|@LIFECYCLE_TOKEN@|$LIFECYCLE_TOKEN|g" -e "s|@INCOMING_ROOT@|$INCOMING_ROOT|g" "$CURRENT/install/knoa-node.service" > /etc/systemd/system/knoa-node.service
sed -e "s|@INSTALL_ROOT@|$INSTALL_ROOT|g" -e "s|@LIFECYCLE_TOKEN@|$LIFECYCLE_TOKEN|g" -e "s|@LIFECYCLE_TRUST@|$LIFECYCLE_TRUST|g" -e "s|@HOST_STATE@|$HOST_STATE|g" -e "s|@INCOMING_ROOT@|$INCOMING_ROOT|g" "$CURRENT/install/knoa-host-lifecycle.service" > /etc/systemd/system/knoa-host-lifecycle.service
chmod 644 /etc/systemd/system/knoa-hub.service /etc/systemd/system/knoa-node.service /etc/systemd/system/knoa-host-lifecycle.service

case "$ROLE" in
    hub) printf '{"schema_version":1,"installed_roles":["hub"]}\n' > "$HOST_STATE" ;;
    node) printf '{"schema_version":1,"installed_roles":["node"]}\n' > "$HOST_STATE" ;;
    all) printf '{"schema_version":1,"installed_roles":["hub","node"]}\n' > "$HOST_STATE" ;;
esac

chown -R knoa:knoa "$DATA_ROOT"
chown -R root:knoa "$CONFIG_ROOT"
chmod 750 "$CONFIG_ROOT" "$SECRET_ROOT"
chmod 640 "$SECRET_ROOT"/*.token "$LIFECYCLE_TRUST" "$HOST_STATE" "$HUB_ENV" "$NODE_CONFIG"

systemctl daemon-reload
systemctl enable --now knoa-host-lifecycle.service
systemctl disable --now knoa-hub.service knoa-node.service 2>/dev/null || true
for service in $SERVICES; do systemctl enable --now "$service"; done
if [ "$ROLE" = "hub" ] || [ "$ROLE" = "all" ]; then
    curl --fail --retry 30 --retry-delay 1 --retry-connrefused http://127.0.0.1:9529/health >/dev/null
fi
if [ "$ROLE" = "node" ] || [ "$ROLE" = "all" ]; then
    curl --fail --retry 30 --retry-delay 1 --retry-connrefused http://127.0.0.1:9531/health >/dev/null
fi
trap - ERR
chmod 640 "$HOST_STATE"

printf 'Installed and verified Knoa %s Bundle. Persistent data: %s\n' "$ROLE" "$DATA_ROOT"
