# Knoa Hosted Hub Single-Node

This deployment is the complete single-node Hosted MVP for architecture shape
3. It runs on `127.0.0.1:9529` behind one HTTPS/WSS origin and combines the
Hosted account control plane with isolated Workspace Hub/Relay compositions.

## Storage and authority

```text
Hosted root
├── control.db
│   ├── Account / LoginIdentity / PasswordCredential
│   ├── AccountSession
│   ├── Workspace / Membership
│   └── one-time account and password-reset grants
├── hub-signing.key                 shared Hosted issuer identity
├── mobile-releases/android/
│   ├── latest.json                 current Hosted Android release
│   ├── <version_code>.json         immutable release metadata
│   └── knoa-<version_code>.apk     immutable signed APK
└── tenants/<workspace_id>/
    └── hub.db                      isolated Workspace directory/resource state
```

Passwords use salted `scrypt` hashes. Session, account-enrollment and
password-reset secrets are stored only as SHA-256 digests. A Personal Workspace
is created with every Account. Shared Workspaces support owner-managed members;
members may read and use Workspace resources, while owner/admin authorization is
required for resource mutation and Node enrollment.

`control.db`, `hub-signing.key`, `mobile-releases/android` and every
`tenants/*/hub.db` form one recovery unit. Never restore only one tenant
database, APK tree, or signing key.
Each Workspace owns an isolated in-process Relay broker, so this deployment must
run as one process with one Uvicorn worker.

## Install as a user service

The canonical Linux installer supports Hub-only, Node-only and combined roles.
For a Hub-only server, run from the Knoa checkout:

```bash
deploy/linux/install-knoa.sh --role hub --source "$PWD" \
  --hub-public-url https://hub.example.com
```

The commands below document the compatible legacy manual installation with a
dedicated Hub virtual environment.

```bash
/usr/bin/python3 -m venv ~/.local/share/knoa/hosted-hub-venv
~/.local/share/knoa/hosted-hub-venv/bin/pip install .
install -d -m 700 ~/.config/knoa ~/.local/share/knoa/hosted-hub
install -m 600 deploy/hosted-hub/hosted-hub.env.example \
  ~/.config/knoa/hosted-hub.env
install -d -m 700 ~/.config/systemd/user
install -m 600 deploy/hosted-hub/knoa-hosted-hub.user.service \
  ~/.config/systemd/user/knoa-hosted-hub.service
```

Replace the example Bootstrap Secret with a random value of at least 32
characters. It is only accepted on the local administration boundary and must
never be copied into the App.

```bash
systemctl --user daemon-reload
systemctl --user enable --now knoa-hosted-hub.service
curl --fail http://127.0.0.1:9529/health
```

The health response identifies `deployment_mode` as `hosted_single_node` and
reports active Account and Workspace counts.

## Account onboarding and recovery

Create a short-lived account enrollment QR on the Hub host:

```bash
KNOA_HUB_BOOTSTRAP_TOKEN=... \
KNOA_HUB_PUBLIC_URL=https://hub.example.com \
knoa-hub-admin account-grant
```

The App scans the returned `knoa-hosted-account-v1` payload, then chooses its
login identity, display name and password. For recovery, an operator creates a
one-time password reset QR:

```bash
KNOA_HUB_BOOTSTRAP_TOKEN=... \
KNOA_HUB_PUBLIC_URL=https://hub.example.com \
knoa-hub-admin password-reset-grant --login owner@example.com
```

Password reset revokes every previous Account Session and returns a new Session.
Changing a password from an authenticated App keeps the current Session and
revokes the Account's other Sessions.

## Enroll the local Node

The App can enroll any already-paired Node. A Hub operator can also enroll the
local Node without placing a Gateway token in the Hosted service:

```bash
KNOA_HUB_ACCOUNT_TOKEN=... \
knoa-hub-admin node-enroll \
  --hub-url https://hub.example.com \
  --workspace-id ws_... \
  --runtime-root ~/.knoa \
  --display-name "Home Node"
```

Restart the Node after the command writes `data/node-hub.json`. The Node then
opens its outbound encrypted Relay connection to the Workspace URL.

## Publish the Android App

Hosted Account installations use the Hub's platform release channel. Publish a
signed APK after installing the new platform wheel:

```bash
knoa-publish-app --apk /secure/builds/knoa.apk \
  --hub-public-url https://hub.example.com \
  --notes "Hosted Hub update channel"
```

Inspect the active release with `knoa-hub-admin mobile-latest --root ...`.
Authenticated Apps query `/v1/mobile/releases/android/latest`; immutable APKs
are served from their version-and-digest URL. The stable public installation
link is:

```text
https://hub.example.com/downloads/android/latest.apk
```

No-Hub and Self-hosted Hub installations continue to use the selected Node's
local release channel. Hosted Apps do not silently fall back to a Node release
when the Hosted update service fails.

## Backup and restore

Create a WAL-consistent snapshot while the service is running:

```bash
knoa-hub-admin backup \
  --root ~/.local/share/knoa/hosted-hub \
  --output /secure/backups/knoa-hosted-$(date +%Y%m%d-%H%M%S)
```

The command uses SQLite's backup API, verifies every database and Android
release, copies the Hub identity and APK tree, and writes a digest manifest.
Restore only while the Hosted service is stopped, into a new empty root:

```bash
knoa-hub-admin restore \
  --backup /secure/backups/knoa-hosted-20260816-220000 \
  --root ~/.local/share/knoa/hosted-hub-restored
```

## Public ingress and limits

Keep port `9529` on loopback and expose it only through a TLS reverse proxy or
Cloudflare Tunnel. WebSocket upgrades must pass through unchanged. The Node
Gateway moves to a different loopback port, normally `9531`; it no longer needs
its own public URL in Hosted mode.

This is a single-node Hosted MVP, not an HA Hosted fleet. It intentionally does
not provide multi-worker Relay routing, email delivery, MFA/step-up, billing,
cross-region failover, automated retention or an SLO-backed disaster-recovery
service. Those concerns require real scale or product demand before introducing
distributed infrastructure.
