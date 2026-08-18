# Knoa Native Windows: Hosted Hub + Node

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

Hub and Node Runtime always use WinSW. The installer deletes the obsolete
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

From an elevated PowerShell window:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
cd C:\knoa
.\deploy\windows\Install-Knoa.ps1 -SourcePath C:\knoa -WinSWExecutable C:\Tools\WinSW-x64.exe -HubPublicUrl https://knoa.tinydotdot.com
```

For a restored Hosted Hub, also pass `-HostedBackupPath`. The restore target
must be empty. Never replace a restored `hub-signing.key`.

## Updating the existing C:\knoa deployment

Stop any manually launched foreground Node with `Ctrl+C`, then run:

```powershell
cd C:\knoa
git pull
.\deploy\windows\Install-Knoa.ps1 -SourcePath C:\knoa -HubPublicUrl https://knoa.tinydotdot.com
```

On update, the installer:

1. preserves `C:\ProgramData\Knoa\HostedHub`;
2. reuses the installed WinSW binary;
3. deletes legacy Knoa scheduled tasks;
4. copies an existing `%LOCALAPPDATA%\Knoa\Node` identity into
   `C:\ProgramData\Knoa\Node` when the service root has no state;
5. retains the old per-user Node directory as a rollback snapshot;
6. installs and starts both `KnoaHostedHub` and `KnoaNode` services;
7. prints a fresh App pairing QR when the copied Node is already enrolled.

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
Get-Service KnoaHostedHub,KnoaNode
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
.\deploy\windows\Uninstall-Knoa.ps1
```

Uninstall removes the two WinSW services and any legacy scheduled tasks. Data
is preserved unless `-PurgeData` is explicitly supplied.
