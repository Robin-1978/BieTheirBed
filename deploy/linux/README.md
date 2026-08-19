# Knoa Linux deployment

Linux uses the same three deployment roles as Windows:

| Role | Processes installed | Typical host |
| --- | --- | --- |
| `hub` | Hosted Hub + Relay | public or always-on Hub server |
| `node` | Node Runtime | workstation, GPU server or home computer |
| `all` | Hub + Node | one-machine personal deployment |

The installer creates native `systemd --user` services. Hub and Node remain
separate processes and can be installed, updated, restarted and removed
independently. The shared Python environment contains the same Knoa release;
on a host running both roles, update with `--role all`.

## Install or update

From the source checkout:

```bash
git pull
deploy/linux/install-knoa.sh --role all --source "$PWD" \
  --hub-public-url https://hub.example.com
```

Hub-only server:

```bash
deploy/linux/install-knoa.sh --role hub --source "$PWD" \
  --hub-public-url https://hub.example.com
```

Node-only computer:

```bash
deploy/linux/install-knoa.sh --role node --source "$PWD"
```

The default paths are:

```text
~/.local/share/knoa/runtime/venv       installed Knoa Python runtime
~/.local/share/knoa/hosted-hub        Hosted Hub persistent data
~/.knoa                               Node persistent data
~/.local/share/knoa/workspace         Node working directory
~/.config/knoa                        private configuration
~/.config/systemd/user                service definitions
```

Enable boot-before-login once when this machine is intended to operate as an
always-on server:

```bash
sudo loginctl enable-linger "$USER"
```

Verify the selected services:

```bash
systemctl --user status knoa-hosted-hub.service
systemctl --user status knoa-node.service
curl --fail http://127.0.0.1:9529/health
curl --fail http://127.0.0.1:9531/health
```

## Embedded management consoles

Hub and Node consoles are part of their owning services rather than separate
deployments:

```text
https://hub.example.com/console       Hub Console
http://127.0.0.1:9531/console         local Node Console
```

The Hub Console creates a ten-minute, single-use Workspace Enrollment Code.
Paste it into the target computer's local Node Console. After the Node opens
its outbound Relay connection, the Node Console generates the QR scanned by
the App. Account Tokens and Workspace IDs are not part of the user workflow.

## Publish the Android App to Hosted Hub

Publishing copies one signed APK into the Hub-owned immutable release channel.
It does not restart Hub or Node:

```bash
knoa-publish-app --apk /secure/builds/knoa.apk \
  --hub-public-url https://hub.example.com \
  --notes "Knoa update"
```

The stable installation URL is:

```text
https://hub.example.com/downloads/android/latest.apk
```

For a separate build machine, copy only the independent value
`KNOA_HUB_RELEASE_PUBLISH_TOKEN` from the Hub's private environment file into
`~/.knoa/secrets/hosted-hub-release-publisher.token` on the build machine and
set mode `0600`. Never copy the bootstrap token. Then build and publish with:

```bash
KNOA_HUB_PUBLIC_URL=https://hub.example.com \
KNOA_MOBILE_RELEASE_NOTES="Knoa update" \
scripts/build-and-publish-mobile-apk.sh
```
