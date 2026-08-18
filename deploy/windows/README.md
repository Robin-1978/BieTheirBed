# Knoa Native Windows: Hosted Hub + Node

This deployment runs Python natively on Windows 10/11 or Windows Server. It
does not use WSL or Docker and does not require a PyInstaller executable.

## Process and port layout

```text
Windows
├── Knoa Hosted Hub WinSW service (LocalSystem, automatic)
│   ├── Hub + Relay: 127.0.0.1:9529
│   └── C:\ProgramData\Knoa\HostedHub
├── Knoa Node (select one mode)
│   ├── InteractiveTask: signed-in user Task Scheduler entry
│   └── HeadlessService: LocalSystem WinSW service in Session 0
│
├── Node listeners
│   ├── Core: 127.0.0.1:9527
│   ├── Capability MCP: 127.0.0.1:9530
│   ├── Secure Gateway: 127.0.0.1:9531
│   └── state: %LOCALAPPDATA%\Knoa\Node or C:\ProgramData\Knoa\Node
└── two independent cloudflared WinSW services
    ├── Cloudflared-knoa -> Knoa Tunnel Token
    └── Cloudflared-per  -> PER Tunnel Token
```

Hub runs as `LocalSystem` because it is a headless control-plane process that
must survive logout. `InteractiveTask` is the default Node mode because desktop
observation, notifications and input tools must remain inside the signed-in
user's Session. `HeadlessService` supports Agent, Task, LLM, MCP and Relay but
cannot access the user's desktop.

## Prerequisites

- Windows x64 with NTFS storage;
- an elevated Windows PowerShell 5.1 or newer;
- standard CPython 3.14 x64 installed with the `py` launcher, not `3.14t`;
- a WinSW x64 executable supplied to the installer;
- the Knoa wheel built by `python -m build`;
- internet access to PyPI, or an offline wheelhouse containing all Knoa
  dependencies;
- `cloudflared-windows-amd64.exe` when the public Tunnel is hosted here.

Linux remains a first-class deployment platform. Linux Node and Hub continue
to use their systemd deployment; these WinSW files are Windows-only process
adapters around the same Python Hub and Node entry points.

## Fresh install or Hosted Hub restore

Run from an elevated PowerShell window:

```powershell
Set-ExecutionPolicy -Scope Process Bypass

.\deploy\windows\Install-Knoa.ps1 `
  -WheelPath .\dist\knoa-0.2.26-py3-none-any.whl `
  -WinSWExecutable C:\Tools\WinSW-x64.exe `
  -HubPublicUrl https://knoa.tinydotdot.com
```

To migrate the existing Hosted Hub, first create a consistent backup on the
old host, copy that complete backup directory to Windows, then install with:

```powershell
.\deploy\windows\Install-Knoa.ps1 `
  -WheelPath .\dist\knoa-0.2.26-py3-none-any.whl `
  -WinSWExecutable C:\Tools\WinSW-x64.exe `
  -HostedBackupPath D:\KnoaMigration\hosted-backup `
  -HubPublicUrl https://knoa.tinydotdot.com `
  -HubId hub_knoa_hosted
```

The installer creates one Python 3.14 venv, restores or initializes Hub
storage, applies an NTFS ACL for `SYSTEM`, local Administrators and the
installing user, installs Hub through WinSW and registers the interactive Node
Task. Re-running it stops the old launchers before replacing runtime files.
Use `-RecreateVenv` only when replacing an existing venv with Python 3.14.

For a server-only Node with no desktop tools:

```powershell
.\deploy\windows\Install-Knoa.ps1 `
  -WheelPath .\dist\knoa-0.2.26-py3-none-any.whl `
  -WinSWExecutable C:\Tools\WinSW-x64.exe `
  -NodeMode HeadlessService
```

The Hub backup retains `control.db`, every Workspace database, Android
releases and `hub-signing.key`; never replace the restored signing key.

Use `-WheelhousePath D:\KnoaWheelhouse` for an offline installation. Operational
scripts are copied to `C:\ProgramData\Knoa\Scripts`. The offline wheelhouse
must include a prebuilt Python 3.14 wheel for PyAutoGUI because its upstream
release is source-only.

## Verify the two local processes

```powershell
Get-Service KnoaHostedHub
Get-ScheduledTask -TaskName "Knoa Node"
curl.exe http://127.0.0.1:9529/health
curl.exe http://127.0.0.1:9531/health
```

Node Gateway health returns an authentication-scoped response. This confirms
the listener without bypassing pairing.

## Enroll the new Windows Node

Save a current Hub Account token in an ACL-protected local file, then run:

```powershell
.\deploy\windows\Enroll-KnoaNode.ps1 `
  -WorkspaceId ws_xxxxxxxxxxxx `
  -AccountTokenFile C:\Secure\knoa-account.token `
  -HubPublicUrl https://knoa.tinydotdot.com `
  -DisplayName "Windows Desktop"
```

This creates a new Node identity under `%LOCALAPPDATA%\Knoa\Node`; it does not
replace the Linux Node already enrolled in the same Workspace.

For `HeadlessService`, also pass
`-NodeRoot C:\ProgramData\Knoa\Node` to the enrollment script.

## Cloudflare Tunnel

The current account has two independent remotely managed Tunnels, so Windows
must run two independent connectors. Copy the two current Linux credentials
into separate temporary files, then install both through WinSW:

```powershell
.\deploy\windows\Install-Cloudflared.ps1 `
  -CloudflaredExecutable C:\Tools\cloudflared.exe `
  -WinSWExecutable C:\Tools\WinSW-x64.exe `
  -TunnelNames @("knoa", "per") `
  -TunnelTokenFiles @("C:\Secure\knoa.token", "C:\Secure\per.token")
```

The installer copies the tokens to
`C:\ProgramData\Cloudflared\Secrets`, writes only token-file paths into WinSW
XML and creates `Cloudflared-knoa` and `Cloudflared-per`. Do not also run
`cloudflared.exe service install`; its fixed native service supports only one
Tunnel process.

If the native service was installed earlier, remove it first:

```powershell
C:\Tools\cloudflared.exe service uninstall
```

Configure the Knoa Tunnel hostname to target `http://127.0.0.1:9529`.
WebSocket upgrades are handled automatically. The PER Tunnel keeps its own
hostname, origin and failure lifecycle.

During migration, do not keep two independent Hosted Hub databases active
behind replicas of the same Tunnel. Stop the old Hub, take the final backup,
restore and verify Windows locally, stop the old connector, then start the
Windows connector.

## Operations

```powershell
Start-Service KnoaHostedHub
Stop-Service KnoaHostedHub
Start-ScheduledTask -TaskName "Knoa Node"
Stop-ScheduledTask -TaskName "Knoa Node"
Start-Service Cloudflared-knoa, Cloudflared-per
Stop-Service Cloudflared-knoa, Cloudflared-per
```

Uninstall preserves data by default:

```powershell
.\deploy\windows\Uninstall-Knoa.ps1
.\deploy\windows\Uninstall-Cloudflared.ps1 -TunnelNames @("knoa", "per")
```

`-PurgeData` is intentionally explicit and removes both Hub and Node state.
