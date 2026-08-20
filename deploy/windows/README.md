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
├── KnoaHostLifecycle (WinSW / LocalSystem / Automatic for source installs)
│   └── Source update broker: 127.0.0.1:9533
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

The installer persists the deployment role, source path, public Hub URL, data
roots, Python version and ports in:

```text
C:\ProgramData\Knoa\Config\installation.json
```

After installation, use the **System** page in either local Hub Console or Node
Console to check and install updates. The Source Lifecycle Broker rejects dirty
tracked files and divergent history, fast-forwards the checkout, installs from
a detached worktree, restarts every installed role and verifies health. Failed
updates automatically reinstall the pre-update commit; the UI does not expose
manual version rollback. If the bootstrap PowerShell session uses HTTP proxy
environment variables, the installer passes them to the private WinSW updater
service so background Git checks do not depend on an interactive shell.

The following launcher remains only as an administrator/recovery entry when a
Console cannot be used:

```text
C:\ProgramData\Knoa\Scripts\Update-Knoa.cmd
```

The recovery updater requests administrator access, detects the WinSW services actually
installed on that computer, refuses to overwrite tracked local changes, runs
`git pull --ff-only`, reconciles every role already installed on the shared
runtime, restarts the WinSW services and verifies that they are running. A
Node-only computer therefore updates only `KnoaNode`; it never installs Hub
because of stale state.
If installation fails after a service was stopped, it attempts to restore the existing service.
Source updates also reconcile newly introduced Python dependencies; they never
replace only the Knoa package with `--no-deps`. WebRTC P2P remains an optional
acceleration path: if its native runtime cannot load, the Node still starts and
uses authenticated Relay fallback.

The first update from an older installation can be bootstrapped by pulling the
repository once and double-clicking:

```text
C:\knoa\deploy\windows\Update-Knoa.cmd
```

The equivalent manual commands remain:

```powershell
cd C:\knoa
git pull
.\deploy\windows\Install-Knoa.ps1 -Role all -SourcePath C:\knoa -HubPublicUrl https://knoa.tinydotdot.com
```

On split hosts, the updater discovers `hub` on the Hub server and `node` on Node
computers from Windows Service Control Manager. On a host running both services,
it discovers `all` so both processes always move to the same Knoa release. The
persisted installation state supplies paths and ports, but does not override the
actual installed service topology.

On update, the installer:

1. preserves `C:\ProgramData\Knoa\HostedHub`;
2. reuses the installed WinSW binary;
3. deletes legacy Knoa scheduled tasks;
4. copies an existing `%LOCALAPPDATA%\Knoa\Node` identity into
   `C:\ProgramData\Knoa\Node` when the service root has no state;
5. retains the old per-user Node directory as a migration backup;
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

The normal product workflow uses the embedded consoles and does not expose an
Account Token or require users to type a Workspace ID:

1. In the App select the Workspace and generate a one-time Enrollment Code,
   or on the Hub computer open `http://127.0.0.1:9532/console`.
2. On the Windows computer open `http://127.0.0.1:9531/console`, paste the Code
   and select **Join Workspace**.
3. In the same Node Console select **Generate App pairing QR**, then scan it
   from the App's Node page.

`Enroll-KnoaNode.ps1` remains an operator/recovery interface, not the normal
user onboarding path.

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

Both services must remain running after logout and reboot. `KnoaNode` stays in
Session 0, while the installer starts `Knoa Desktop Companion` in the signed-in
user Session and registers it for future logins. Screenshot, clipboard, input,
window and notification requests use its authenticated Session-bound named
pipe; they never execute directly in the WinSW service. Node Console shows
whether the Companion is connected. The one-click source updater stops the
Companion before replacing Python packages and starts the new version after the
service update.

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
