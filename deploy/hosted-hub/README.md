# Knoa Hosted Hub Simulation

This deployment runs architecture shape 3 on `127.0.0.1:9540`. It validates a
shared Hosted identity plus isolated Personal Workspaces, but it is not a
production Hosted service.

## What it deploys

```text
Hosted root
├── accounts.db                 account/token digest/workspace mapping
├── hub-signing.key             shared Hosted Hub identity
└── tenants/<workspace_id>/
    └── hub.db                  isolated Workspace control-plane state
```

Each Workspace also owns an isolated in-process Relay broker. The service must
run as one process with one Uvicorn worker.

## Install as a user service

Install the current checkout into a dedicated deployment virtual environment.
Do not reuse a Conda environment or the Node development environment; the Hub
service must have a stable interpreter and dependency set:

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

Replace the example bootstrap token with a cryptographically random value of
at least 32 characters. The bootstrap token only authorizes simulation account
creation; the returned account access token is separate and is stored only as a
SHA-256 digest by the Hub.

Then start and verify the service:

```bash
systemctl --user daemon-reload
systemctl --user enable --now knoa-hosted-hub.service
curl --fail http://127.0.0.1:9540/health
```

The health response identifies `deployment_mode` as `hosted_simulation` and
reports the number of active tenant Workspaces.

## API boundaries

```text
POST /v1/hosted/accounts
GET  /v1/hosted/account
ANY  /workspaces/{workspace_id}/...
```

Account creation returns a one-time account access token and a canonical
`workspace_path`. Clients must connect to that Workspace path. A token issued
for one Workspace is rejected by every other Workspace.

## Public ingress

Keep `9540` on loopback. A public simulation may place a TLS reverse proxy or
Cloudflare Tunnel in front of this listener, but DNS, certificates and Tunnel
resources are deliberately outside this repository and are not created by the
service. WebSocket upgrade must be forwarded unchanged.

Do not advertise this deployment as production Hosted Hub. It has no account
recovery, step-up authentication, rate limiting, abuse controls, Relay fleet,
multi-worker routing, HA, billing, retention workflow or disaster-recovery SLO.
