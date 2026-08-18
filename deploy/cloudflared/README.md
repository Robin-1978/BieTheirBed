# Knoa Cloudflare Tunnel

Knoa keeps the Secure Gateway on loopback. Cloudflare terminates public TLS and
the local `cloudflared` connector forwards traffic without opening an inbound
router or firewall port.

## Cloudflare public hostname

Configure the Tunnel in the Cloudflare dashboard with this public hostname:

| Setting | Value |
| --- | --- |
| Subdomain | `knoa` |
| Domain | `tinydotdot.com` |
| Service type | `HTTP` |
| Origin URL | `127.0.0.1:9529` |

The resulting public Gateway URL is `https://knoa.tinydotdot.com`.

Do not enable "No TLS Verify": the origin is intentionally plain HTTP over the
local connector. Device authentication remains enforced by the Gateway.

## Linux connectors

The current Cloudflare account owns two independent Tunnels. Each Tunnel has
its own Token, connector process and systemd user service:

```text
cloudflared-knoa.service -> ~/.knoa/config/cloudflare.token
cloudflared-per.service  -> ~/.knoa/config/cloudflare-per.token
```

Store both remotely managed Tunnel tokens outside the repository:

```bash
install -d -m 700 ~/.knoa/config
install -m 600 deploy/cloudflared/cloudflare.token.example \
  ~/.knoa/config/cloudflare.token
install -m 600 deploy/cloudflared/cloudflare.token.example \
  ~/.knoa/config/cloudflare-per.token
```

Replace each example with only its matching remotely managed Tunnel Token,
then install both user services:

```bash
install -d -m 700 ~/.config/systemd/user
install -m 600 deploy/cloudflared/cloudflared-knoa.user.service \
  ~/.config/systemd/user/cloudflared-knoa.service
install -m 600 deploy/cloudflared/cloudflared-per.user.service \
  ~/.config/systemd/user/cloudflared-per.service
systemctl --user daemon-reload
systemctl --user enable --now cloudflared-knoa.service
systemctl --user enable --now cloudflared-per.service
```

Verify both sides:

```bash
curl --fail http://127.0.0.1:9529/health
curl --fail https://knoa.tinydotdot.com/health
```

The expected response is:

```json
{"status":"ok","scope":"authentication"}
```

The services are deliberately independent, so a restart or Token rotation for
either application cannot interrupt the other. Both use `--token-file`; Tokens
do not appear in process command lines or unit XML/text.

Linux continues to use systemd. Windows uses two WinSW service instances for
the same two-Tunnel topology; this difference is isolated to deployment.
