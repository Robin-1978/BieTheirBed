# Knoa Native Windows deployment

Knoa runs directly on Windows with standard CPython. WSL, Docker and a
PyInstaller EXE are not required.

## Canonical process model

```text
Windows
├── KnoaHostedHub (WinSW / LocalSystem / Automatic)
│   ├── Hub + Relay: 127.0.0.1:9529
│   └── state: C:\ProgramData\Knoa\HostedHub
├── KnoaNode (WinSW / LocalSystem / Automatic)
│   ├── Core: 127.0.0.1:9527
│   ├── Capability MCP: 127.0.0.1:9530
│   ├── Secure Gateway: 127.0.0.1:9531
│   └── state: C:\ProgramData\Knoa\Node
└── named cloudflared services when this host owns public Tunnels
```

Hub and Node Runtime always use separate WinSW services. They can be installed
on one computer or on different computers. The installer deletes the obsolete
`Knoa Hosted Hub` and `Knoa Node` scheduled tasks. A future desktop Companion
may run in the signed-in user's Session, but desktop-session access is not a
reason to move the Node Runtime itself out of Windows Service Control Manager.

## Prerequisites

- Windows x64 with NTFS;
- elevated Windows PowerShell 5.1 or newer;
- standard CPython 3.14 x64 with the `py` launcher, not `3.14t`;
- WinSW x64 for the first installation;
- a Knoa wheel, or an existing source checkout and initialized venv;
- Cloudflare only when this host owns the public Tunnel connector.

## First installation

The deployment roles are:

| Role | Services installed | Typical host |
| --- | --- | --- |
| `hub` | `KnoaHostedHub` | public or always-on Hub server |
| `node` | `KnoaNode` | workstation, GPU server or home computer |
| `all` | both services | one-machine personal deployment |

From an elevated PowerShell window:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
cd C:\knoa
.\deploy\windows\Install-Knoa.ps1 -Role all -SourcePath C:\knoa -WinSWExecutable C:\Tools\WinSW-x64.exe -HubPublicUrl https://knoa.tinydotdot.com
```

Hub-only server:

```powershell
.\deploy\windows\Install-Knoa.ps1 -Role hub -SourcePath C:\knoa -WinSWExecutable C:\Tools\WinSW-x64.exe -HubPublicUrl https://knoa.tinydotdot.com
```

Node-only computer:

```powershell
.\deploy\windows\Install-Knoa.ps1 -Role node -SourcePath C:\knoa -WinSWExecutable C:\Tools\WinSW-x64.exe
```

For a restored Hosted Hub, also pass `-HostedBackupPath`. The restore target
must be empty. Never replace a restored `hub-signing.key`.

## Updating the existing C:\knoa deployment

Stop any manually launched foreground Node with `Ctrl+C`, then run:

```powershell
cd C:\knoa
git pull
.\deploy\windows\Install-Knoa.ps1 -Role all -SourcePath C:\knoa -HubPublicUrl https://knoa.tinydotdot.com
```

On split hosts, use `-Role hub` on the Hub server and `-Role node` on Node
computers. Installing or updating one role does not stop or reinstall the other
role's service. On a host running both roles, use `-Role all` so both processes
run the same Knoa release.

On update, the installer:

1. preserves `C:\ProgramData\Knoa\HostedHub`;
2. reuses the installed WinSW binary;
3. deletes legacy Knoa scheduled tasks;
4. copies an existing `%LOCALAPPDATA%\Knoa\Node` identity into
   `C:\ProgramData\Knoa\Node` when the service root has no state;
5. retains the old per-user Node directory as a rollback snapshot;
6. installs and starts only the selected WinSW services;
7. prints a fresh App pairing QR when the selected Node is already enrolled.

## Publish the Android App to Hosted Hub

`scripts/build-mobile-apk.sh` creates a self-describing release directory:

```text
/disk/dev/knoa-mobile-out/release/
  knoa-<version>.apk
  knoa-<version>.release.json
  Publish-Knoa-<version>.cmd
```

Copy all three files into one directory on the Windows Hub, then double-click
`Publish-Knoa-<version>.cmd`. The build-generated command owns the version name
and version code; the operator does not enter or infer either value.

For command-line publication, the installed publisher automatically reads and
validates the adjacent `.release.json` file:

```powershell
C:\ProgramData\Knoa\Scripts\Publish-KnoaApp.ps1 -ApkPath C:\Builds\knoa-0.2.53.apk
```

The command validates and publishes immutable release metadata, verifies the
active release and prints the stable download URL:

```text
https://knoa.tinydotdot.com/downloads/android/latest.apk
```

Publishing an APK does not restart Hub or Node. Logged-in Apps query the Hub
release channel and can download the new version from the App update page.

For publishing from a separate Linux build machine, the Hub owns an independent
publisher credential at:

```text
C:\ProgramData\Knoa\Secrets\hosted-hub-release-publisher.token
```

Copy this token once through a private channel to the build machine as
`~/.knoa/secrets/hosted-hub-release-publisher.token` with mode `0600`. Do not
reuse or copy the Hosted bootstrap token. The build machine can then run:

```bash
KNOA_MOBILE_RELEASE_NOTES="Knoa update" scripts/build-and-publish-mobile-apk.sh
```

The build machine reads and validates the APK manifest, uploads at most 100 MiB
over HTTPS, and verifies the Hub-returned version and SHA-256 digest. The remote
publisher endpoint is disabled when no dedicated publisher token is configured.

## Node enrollment and App QR pairing

Enrollment and App pairing are separate trust steps:

```text
Node enrollment -> Node joins Workspace and opens outbound Relay
App QR pairing  -> App receives a Node-specific device identity through Relay
```

After enrollment, `Enroll-KnoaNode.ps1` restarts `KnoaNode` and prints a QR.
The App scans that QR. The QR names the Workspace Hub, not a public Node
Gateway. The App obtains a short-lived pairing-only Relay ticket; the Node
allows only `/v1/pair/challenge` and `/v1/pair/complete` until pairing finishes.
No second Node domain, port-forward or Cloudflare Tunnel is required.

For an already enrolled Node, print a fresh five-minute QR with:

```powershell
C:\ProgramData\Knoa\Scripts\Show-KnoaPairingQr.cmd
```

## Verification and operations

```powershell
Get-Service KnoaHostedHub,KnoaNode -ErrorAction SilentlyContinue
curl.exe http://127.0.0.1:9529/health
curl.exe http://127.0.0.1:9531/health
Restart-Service KnoaHostedHub,KnoaNode
```

Both services must remain running after logout and reboot. Node desktop capture,
clipboard, input, window and notification capabilities are intentionally absent
from the Session 0 Runtime until the separate desktop Companion exists.

## Cloudflare

The canonical Knoa hostname targets `http://127.0.0.1:9529`. It exposes Hub and
Relay only. Nodes connect outbound to Hub and do not need public hostnames.

Two remotely managed Tunnels require two connector processes and two service
IDs. `Install-Cloudflared.ps1` creates named WinSW services and stores each
token in an ACL-protected token file. Do not use the fixed single-instance
`cloudflared.exe service install` when running multiple Tunnels.

## Uninstall

```powershell
.\deploy\windows\Uninstall-Knoa.ps1 -Role all
```

Use `-Role hub` or `-Role node` to remove one service only. Data is preserved
unless `-PurgeData` is explicitly supplied; role-specific purge deletes only
that role's persistent data.
