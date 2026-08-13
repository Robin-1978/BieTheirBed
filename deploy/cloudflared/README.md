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

## Local connector

Store the remotely managed Tunnel token outside the repository:

```bash
install -d -m 700 ~/.knoa/config
install -m 600 deploy/cloudflared/cloudflare.token.example \
  ~/.knoa/config/cloudflare.token
```

Replace the example content in `cloudflare.token` with only the remotely
managed Tunnel token, then install the user service:

```bash
install -d -m 700 ~/.config/systemd/user
install -m 600 deploy/cloudflared/cloudflared-knoa.user.service \
  ~/.config/systemd/user/cloudflared-knoa.service
systemctl --user daemon-reload
systemctl --user enable --now cloudflared-knoa.service
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

This service is deliberately independent of `cloudflared-per.service`, so a
restart or token rotation for either application cannot interrupt the other.
