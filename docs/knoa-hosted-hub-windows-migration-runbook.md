# Knoa Hosted Hub 迁移到 Windows 操作手册

> 状态：当前可执行 Runbook
>
> 更新日期：2026-08-19
>
> 范围：将现有 Linux Hosted Hub 迁移到一台原生 Windows 主机，并在同一台 Windows 主机新增 Knoa Node
>
> 目标形态：Hosted Hub WinSW Service + Node Runtime WinSW Service + Relay + Cloudflare Tunnel

## 0. 2026-08-19 实际操作记录与升级基线

本节记录本次 Windows 主机上已经发生的操作，目的是让后续服务化升级、问题定位和 Windows 本机
回退有明确边界，不表示退回 Linux。

### 0.1 已完成状态

| 项目 | 当前值/状态 |
| --- | --- |
| Windows 源码目录 | `C:\knoa` |
| Git 设计改造前基线 | `866452bbe45d065b3e707feee1d3264648502109` |
| Python venv | `C:\ProgramData\Knoa\Runtime\venv` |
| Hosted Hub data | `C:\ProgramData\Knoa\HostedHub` |
| Hosted Hub listener | `127.0.0.1:9529` |
| Canonical Hub URL | `https://knoa.tinydotdot.com` |
| Hub ID | `hub_knoa_hosted` |
| Workspace | `ws_99eLurjyegBjifqIg4gPx6R5` |
| 旧 per-user Node root | `C:\Users\jalsy\AppData\Local\Knoa\Node` |
| Node enrollment | 已成功加入上述 Workspace |
| App Account login | 已成功 |
| App 当前阻塞 | 旧 App 显示“配对 App”，旧二维码要求 Node public Gateway URL |

Node 曾使用以下前台命令临时启动：

```powershell
& "C:\ProgramData\Knoa\Runtime\venv\Scripts\python.exe" -m knoa_platform.service --config "C:\ProgramData\Knoa\Config\node-windows.yaml"
```

这只用于验证进程和 enrollment，不是最终部署方式。升级前应在该控制台按 `Ctrl+C` 停止。

### 0.2 本次升级必须完成的收口

1. 删除名为 `Knoa Hosted Hub`、`Knoa Node` 的历史计划任务；
2. `KnoaHostedHub` 与 `KnoaNode` 都由 WinSW 注册为 Automatic Service；
3. 将旧 per-user Node state 复制到 `C:\ProgramData\Knoa\Node`，保持同一 Node identity/enrollment；
4. 保留旧 per-user 目录作为 Windows 本机回退快照，验收前不删除；
5. App 扫 Node 生成的二维码后，通过 Hosted Hub Relay 完成初始配对；
6. Node 不配置独立域名、不开放 `9531` 公网入口。

升级后检查：

```powershell
Get-ScheduledTask -TaskName "Knoa Hosted Hub","Knoa Node" -ErrorAction SilentlyContinue
Get-Service KnoaHostedHub,KnoaNode
```

第一条应无结果，第二条的两个 Service 应为 `Running`。

## 1. 目的与结论

本文用于一次有计划、可验证、可回滚的主机迁移：

```text
迁移前                                  迁移后

Linux Hosted Hub                       Windows Hosted Hub
  + Hub 数据                              + 原 Hub 数据与身份
  + Hub signing key                       + 原 Account / Workspace
  + Android release channel               + 原 Android release channel
  + cloudflared-knoa                       + cloudflared-knoa
             |                                        |
             +-- knoa.tinydotdot.com -----------------+

Linux Node 保留                         Windows Node 新增
```

迁移不改变以下产品身份：

- canonical Hub URL：`https://knoa.tinydotdot.com`；
- Hub ID：`hub_knoa_hosted`；
- Hub signing identity；
- Account、登录身份、Workspace、Membership；
- 已有 Linux Node identity 和 enrollment；
- Hosted Android App 发布权威。

Windows Node 是一个新的执行节点。禁止把旧 Linux Node 的 Runtime Root、Node 私钥、Conversation、
Task、LLM Secret 或 MCP Secret 当作 Hub 数据复制到 Windows。

## 2. 目标部署拓扑

```text
Windows Host
├── Knoa Hosted Hub
│   ├── service: KnoaHostedHub / WinSW / LocalSystem
│   ├── listener: 127.0.0.1:9529
│   └── state: C:\ProgramData\Knoa\HostedHub
│
├── Knoa Node
│   ├── service: KnoaNode / WinSW / LocalSystem
│   ├── Core: 127.0.0.1:9527
│   ├── Capability MCP: 127.0.0.1:9530
│   ├── Secure Gateway: 127.0.0.1:9531
│   └── state: C:\ProgramData\Knoa\Node
│
└── Cloudflared-knoa
    ├── launcher: WinSW / LocalSystem
    ├── remotely managed Tunnel Token
    └── knoa.tinydotdot.com -> http://127.0.0.1:9529
```

Hub 和 Node Runtime 都使用 Windows Service，退出桌面登录后仍保持在线。截图、窗口、剪贴板、键鼠
和通知属于未来独立的登录用户 Desktop Companion，不再让 Node Runtime 依赖计划任务。

## 3. 不可破坏的迁移约束

1. 同一 canonical Hub 域名在任何时刻只能写入一份 Hosted Hub 数据库；
2. 启动 Windows Tunnel connector 前，必须停止 Linux `cloudflared-knoa`；
3. `control.db`、`hub-signing.key`、所有 tenant DB、Android release tree 和 manifest 必须整体迁移；
4. 恢复目标 `C:\ProgramData\Knoa\HostedHub` 必须不存在或为空；
5. 不删除旧 Linux 数据，直到 Windows 通过完整验收并稳定运行；
6. 不复制旧 Node identity 到 Windows；
7. Tunnel Token、Account Token、Provider Key 和 MCP credential 不进入仓库或普通日志；
8. Windows 主机不得自动休眠，否则 Hosted Hub 会整体离线。

## 4. 迁移资产边界

| 资产 | 是否迁移 | 来源/目标 |
| --- | --- | --- |
| Account、密码摘要、Session | 是 | `control.db` |
| Workspace、Membership | 是 | `control.db` |
| Workspace Hub 数据 | 是 | `tenants/<workspace_id>/hub.db` |
| Hub signing identity | 是，必须原样保留 | `hub-signing.key` |
| Android APK 与 release metadata | 是 | `mobile-releases/android` |
| Hub bootstrap token | 可重新生成 | Windows 本地管理 Secret |
| Linux Node identity | 否 | 保留在原 Linux Node |
| Conversation、Task 执行事实 | 否 | 继续属于各自 Node |
| Qwen 模型路径、启动配置 | 否 | 在 Windows Node 重新配置 |
| LLM API Key、MCP credential | 否 | 在 Windows Node 重新配置 |
| Cloudflare Tunnel Token | 安全转移 | Linux Secret -> Windows Secret |

## 5. 角色与停机窗口

迁移需要：

- Linux Hosted Hub 管理权限；
- Windows 本机管理员权限；
- Cloudflare Tunnel Token 读取权限；
- Knoa Hub Account owner/admin 登录凭据；
- 一个可以安全复制备份的通道；
- 一个明确的切换窗口。

建议分为两阶段：

1. 预置阶段：构建 wheel、准备 Windows、下载 WinSW/cloudflared，不中断服务；
2. 切换阶段：冻结旧入口、最终备份、恢复、切换 Tunnel、验收，期间 Hub 暂时不可用。

## 6. 迁移包目录

建议在 Windows 准备以下目录：

```text
D:\KnoaMigration\
├── deploy\windows\
│   ├── Install-Knoa.ps1
│   ├── Run-KnoaHub.ps1
│   ├── Run-KnoaNode.ps1
│   ├── Enroll-KnoaNode.ps1
│   ├── Install-Cloudflared.ps1
│   └── Uninstall-*.ps1
├── dist\
│   └── knoa-<version>-py3-none-any.whl
├── tools\
│   ├── WinSW-x64.exe
│   └── cloudflared.exe
├── hosted-backup\
└── secrets\
    └── knoa-tunnel.token
```

如果 PER 也迁移到这台 Windows，再准备独立的 `per-tunnel.token`。一个 Tunnel 对应一个 Token、一个
connector 进程和一个 WinSW Service。

## 7. 阶段 A：无停机预置

### 7.1 构建当前 Knoa wheel

在 Linux 仓库根目录执行：

```bash
python3 -m build --wheel
```

确认生成：

```bash
ls -lh dist/knoa-*.whl
```

必须从计划部署的 commit 构建 wheel，并记录 commit：

```bash
git rev-parse HEAD
git status --short
```

### 7.2 安装 Windows 前置软件

安装标准 CPython 3.14 x64，并启用 `py` launcher。不要使用 free-threaded `3.14t`。

```powershell
py -0p
py -3.14 -c "import struct,sys,sysconfig; print(sys.version); print(struct.calcsize('P') * 8); print(bool(sysconfig.get_config_var('Py_GIL_DISABLED')))"
```

预期：

```text
64
False
```

准备：

- WinSW x64 executable；
- `cloudflared-windows-amd64.exe`，重命名为 `cloudflared.exe`；
- 当前 wheel；
- 完整 `deploy\windows` 目录。

### 7.3 Windows 主机预检查

```powershell
Get-Volume
Get-TimeZone
w32tm /query /status
powercfg /GETACTIVESCHEME
Get-Service cloudflared -ErrorAction SilentlyContinue
```

要求：

- 系统盘和 `C:\ProgramData` 使用 NTFS；
- 时间同步正常；
- 磁盘容量足以保存 Hub、APK、日志和至少一份备份；
- 禁止自动睡眠；
- 端口 `9527`、`9529`、`9530`、`9531` 未被其他程序占用；
- 若已安装 cloudflared 原生单实例 Service，应在正式安装命名 Service 前卸载。

端口检查：

```powershell
Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue |
  Where-Object LocalPort -In 9527,9529,9530,9531
```

## 8. 阶段 B：切换前记录基线

在旧 Linux 主机记录：

```bash
systemctl --user is-active knoa-hosted-hub.service
systemctl --user is-active cloudflared-knoa.service
curl --fail http://127.0.0.1:9529/health
curl --fail https://knoa.tinydotdot.com/health
git rev-parse HEAD
```

同时记录：

- 当前 Account 数量；
- 当前 Workspace 数量；
- App 是否能登录；
- 当前在线 Node；
- 最新 Android App versionCode 和 SHA-256。

```bash
~/.local/share/knoa/hosted-hub-venv/bin/knoa-hub-admin mobile-latest \
  --root ~/.local/share/knoa/hosted-hub
```

## 9. 阶段 C：冻结旧 Hub 并生成最终备份

### 9.1 停止旧公网入口

```bash
systemctl --user stop cloudflared-knoa.service
systemctl --user is-active cloudflared-knoa.service
```

必须确认结果不是 `active`。如果 PER 不迁移，不要停止 `cloudflared-per.service`。

### 9.2 停止旧 Hosted Hub

```bash
systemctl --user stop knoa-hosted-hub.service
systemctl --user is-active knoa-hosted-hub.service
```

停止 Hub 后再生成最终备份，可以消除备份完成后继续产生写入的可能。

### 9.3 生成一致性备份

输出目录必须不存在：

```bash
~/.local/share/knoa/hosted-hub-venv/bin/knoa-hub-admin backup \
  --root ~/.local/share/knoa/hosted-hub \
  --output /disk/knoa/backups/knoa-hosted-windows-final
```

确认至少存在：

```bash
find /disk/knoa/backups/knoa-hosted-windows-final -maxdepth 4 -type f -print
sha256sum /disk/knoa/backups/knoa-hosted-windows-final/manifest.json
```

不要修改备份内部文件。恢复命令会根据 manifest 验证所有数据库、signing key 和 Android release。

### 9.4 安全复制到 Windows

优先使用：

- 内网 SCP/SFTP；
- 加密移动介质；
- 端到端加密的文件传输。

禁止把未加密备份或 Tunnel Token 上传到公开网盘、代码仓库、聊天群或工单附件。

Windows 目标：

```text
D:\KnoaMigration\hosted-backup
```

## 10. 阶段 D：在 Windows 恢复并安装

以本机管理员身份运行安装器。Hub 和 Node state 都属于 `C:\ProgramData\Knoa`，不再绑定某个登录
用户或 `%LOCALAPPDATA%`。

```powershell
Set-ExecutionPolicy -Scope Process Bypass
cd D:\KnoaMigration

.\deploy\windows\Install-Knoa.ps1 `
  -WheelPath .\dist\knoa-0.2.27-py3-none-any.whl `
  -WinSWExecutable .\tools\WinSW-x64.exe `
  -HostedBackupPath .\hosted-backup `
  -HubPublicUrl https://knoa.tinydotdot.com `
  -HubId hub_knoa_hosted
```

部署其他版本时替换 wheel 文件名，不要为了匹配示例而降低版本。

安装器会：

1. 创建 `C:\ProgramData\Knoa\Runtime\venv`；
2. 安装 Knoa wheel；
3. 验证 Python 3.14 x64 且非 free-threaded；
4. 恢复 Hosted Hub 完整备份；
5. 应用 NTFS ACL；
6. 安装并启动 `KnoaHostedHub` WinSW Service；
7. 删除遗留 Knoa 计划任务；
8. 安装并启动 `KnoaNode` WinSW Service。

## 11. 阶段 E：Windows 本机验收

在启动 Cloudflare connector 前，先完成本机验收。

### 11.1 Hub Service

```powershell
Get-Service KnoaHostedHub
curl.exe http://127.0.0.1:9529/health
```

必须满足：

- Service 为 `Running`；
- `deployment_mode` 为 `hosted_single_node`；
- `hub_id` 为 `hub_knoa_hosted`；
- Account、Workspace 数量与迁移前一致。

### 11.2 Hub signing identity

```powershell
$manifest = Get-Content D:\KnoaMigration\hosted-backup\manifest.json -Raw | ConvertFrom-Json
$actual = (Get-FileHash C:\ProgramData\Knoa\HostedHub\hub-signing.key -Algorithm SHA256).Hash.ToLowerInvariant()
$manifest.signing_key_sha256
$actual
if ($manifest.signing_key_sha256 -ne $actual) { throw "Hub signing identity mismatch" }
```

必须完全一致。

### 11.3 Node listener

```powershell
Get-Service KnoaNode
curl.exe -i http://127.0.0.1:9531/health
```

Gateway health 可能返回认证范围响应；只要 listener 正常响应且不是连接失败，即可继续注册 Node。

### 11.4 日志

```powershell
Get-ChildItem C:\ProgramData\Knoa\Logs -Recurse |
  Sort-Object LastWriteTime -Descending |
  Select-Object -First 20 FullName,Length,LastWriteTime
```

若 Hub 未启动，优先检查：

```text
C:\ProgramData\Knoa\Logs\Hub
C:\ProgramData\Knoa\Services\KnoaHostedHub
```

## 12. 阶段 F：切换 Cloudflare Tunnel

### 12.1 清理原生单实例 Service

如果存在名为 `cloudflared` 的原生 Service：

```powershell
.\tools\cloudflared.exe service uninstall
```

Knoa 的命名 WinSW Service 与 cloudflared 原生固定 Service 不应并存。

### 12.2 只迁移 Knoa Tunnel

确认 Linux `cloudflared-knoa` 仍处于停止状态，然后执行：

```powershell
.\deploy\windows\Install-Cloudflared.ps1 `
  -CloudflaredExecutable .\tools\cloudflared.exe `
  -WinSWExecutable .\tools\WinSW-x64.exe `
  -TunnelNames @("knoa") `
  -TunnelTokenFiles @(".\secrets\knoa-tunnel.token")
```

### 12.3 同时迁移 Knoa 和 PER Tunnel

仅当 PER 的本地 origin 也已迁移到 Windows 时执行：

```powershell
.\deploy\windows\Install-Cloudflared.ps1 `
  -CloudflaredExecutable .\tools\cloudflared.exe `
  -WinSWExecutable .\tools\WinSW-x64.exe `
  -TunnelNames @("knoa", "per") `
  -TunnelTokenFiles @(".\secrets\knoa-tunnel.token", ".\secrets\per-tunnel.token")
```

Cloudflare Dashboard 中 Knoa hostname 保持：

```text
https://knoa.tinydotdot.com -> http://127.0.0.1:9529
```

不需要新域名、新 DNS 记录或新 Tunnel。

### 12.4 公网验收

```powershell
Get-Service Cloudflared-knoa
curl.exe https://knoa.tinydotdot.com/health
curl.exe -I https://knoa.tinydotdot.com/downloads/android/latest.apk
```

检查 APK 响应中的：

- HTTP 200；
- `content-type: application/vnd.android.package-archive`；
- `content-length`；
- `etag` 或 `x-knoa-sha256`。

如果这是新建 Hub、备份中没有 Android release，或需要发布新版 App，构建机会生成：

```text
/disk/dev/knoa-mobile-out/release/
  knoa-<version>.apk
  knoa-<version>.release.json
  Publish-Knoa-<version>.cmd
```

把三个文件复制到 Windows Hub 的同一目录，双击 `Publish-Knoa-<version>.cmd` 即可。版本号、
版本代码和发布参数均由构建流程写入，部署人员不需要填写。

命令行方式也只需要 APK 路径，发布脚本会自动读取并校验相邻的 `.release.json`：

```powershell
C:\ProgramData\Knoa\Scripts\Publish-KnoaApp.ps1 -ApkPath C:\Builds\knoa-0.2.53.apk
```

发布只更新 `C:\ProgramData\Knoa\HostedHub\mobile-releases\android`，不重启 Hub 或 Node。

跨机自动发布使用独立凭据：

```text
C:\ProgramData\Knoa\Secrets\hosted-hub-release-publisher.token
```

只需通过私密通道复制一次到构建机的
`~/.knoa/secrets/hosted-hub-release-publisher.token` 并设置权限 `0600`。禁止复制或复用
`hosted-hub-bootstrap.token`。之后构建机执行
`scripts/build-and-publish-mobile-apk.sh` 即可完成签名构建、HTTPS 上传和 Hub digest 验证。

## 13. 阶段 G：Account、App 与旧 Node 验收

在 App 中验证：

1. 使用原帐号登录，不创建新帐号；
2. 原 Workspace 全部存在；
3. Membership 和 Workspace 角色正确；
4. 原共享 Model/MCP directory 正确；
5. 原 Linux Node 重新上线；
6. 最新 Android App 仍可查询和下载。

由于 canonical URL、Hub ID 和 signing identity 均未改变，已有 App Session 和 Linux Node enrollment
原则上继续有效。如果 Session 已过期，正常重新登录即可，不应创建第二套 Account。

## 14. 阶段 H：注册 Windows Node

### 14.1 获取一次 Account Session

在 Windows PowerShell 中安全读取密码，不把密码写入命令历史：

```powershell
$hubUrl = "https://knoa.tinydotdot.com"
$login = Read-Host "Knoa login identity"
$securePassword = Read-Host "Knoa password" -AsSecureString
$pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($securePassword)
try {
    $plainPassword = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer)
    $body = @{
        login_identity = $login
        password = $plainPassword
    } | ConvertTo-Json -Compress
    $session = Invoke-RestMethod `
        -Method Post `
        -Uri "$hubUrl/v1/hosted/sessions" `
        -ContentType "application/json" `
        -Body $body
} finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer)
    $plainPassword = $null
    $securePassword = $null
}

$session.workspaces | Format-Table workspace_id,display_name,kind,role
```

选择目标 Workspace，临时保存 Account Token：

```powershell
$tokenFile = "C:\ProgramData\Knoa\Secrets\account-enroll.token"
[IO.File]::WriteAllText($tokenFile, $session.access_token, [Text.Encoding]::ASCII)
```

### 14.2 注册 Node

```powershell
C:\ProgramData\Knoa\Scripts\Enroll-KnoaNode.ps1 `
  -WorkspaceId ws_xxxxxxxxxxxx `
  -AccountTokenFile C:\ProgramData\Knoa\Secrets\account-enroll.token `
  -HubPublicUrl https://knoa.tinydotdot.com `
  -DisplayName "Windows Desktop"
```

成功后脚本会重启 `KnoaNode` Service，使 Node 建立 outbound Relay 连接，并在终端打印 App 可扫描的
Relay 配对二维码。

### 14.3 删除并撤销临时 Account Session

```powershell
Invoke-RestMethod `
  -Method Delete `
  -Uri "$hubUrl/v1/hosted/session" `
  -Headers @{ Authorization = "Bearer $($session.access_token)" }

Remove-Item C:\ProgramData\Knoa\Secrets\account-enroll.token -Force
$session = $null
```

### 14.4 Node 验收

```powershell
Get-Service KnoaNode
curl.exe -i http://127.0.0.1:9531/health
```

在 App 中确认：

- `Windows Desktop` 出现在目标 Workspace；
- Node 显示在线；
- 可以进入该 Node 的设置和诊断；
- 原 Linux Node 仍作为独立 Node 存在。

## 15. Windows Node 资源配置

Hub 迁移不会把某个旧 Node 的本地资源变成 Windows Node 资源。需要按需在 Windows Node 重新配置：

- 本地 Qwen 模型服务、endpoint 和模型路径；
- 云端 LLM Provider Key；
- Jira/GitLab MCP 服务和 credential；
- Node-local Skill、Tool 和 Agent 配置；
- Workspace 对 Model/MCP 的共享授权。

资源配置顺序建议：

1. 先验证 Node-local 实例健康；
2. 再发布为 Workspace 可发现资源；
3. 最后对指定调用 Node 创建 ResourceGrant；
4. 不需要的家庭/公司资源不要默认跨 Node 共享。

## 16. 完成验收清单

只有以下项目全部通过，迁移才算完成：

- [ ] Windows `KnoaHostedHub` 自动启动且健康；
- [ ] Hub ID 与迁移前一致；
- [ ] signing key SHA-256 与 backup manifest 一致；
- [ ] Account 数量一致；
- [ ] Workspace 数量一致；
- [ ] 原帐号可以登录；
- [ ] 原 Workspace 和成员关系完整；
- [ ] 最新 Android APK 的版本、大小和 SHA-256 一致；
- [ ] `https://knoa.tinydotdot.com/health` 正常；
- [ ] Cloudflare 只有 Windows connector 为 Knoa Hub 提供流量；
- [ ] 原 Linux Node 已重新连接；
- [ ] Windows Node 已注册并在线；
- [ ] Windows 重启后 Hub 自动恢复；
- [ ] Windows 重启且无人登录时 Node Service 自动恢复；
- [ ] Windows 主机睡眠已禁用；
- [ ] 旧 Linux Hub 数据和最终备份仍被保留。

## 17. Windows 本机服务化回退

### 17.1 触发条件

本节只回退“计划任务/前台进程 → WinSW Node Service”这次 Windows 本机升级，不切换回 Linux Hub。
出现以下任一情况时，先停止新 Node Service 并保留数据：

- `KnoaNode` 无法启动；
- 新 `C:\ProgramData\Knoa\Node` 未保留原 Node identity；
- Hub 中出现意外的新 Node，而不是原 Windows Node；
- Relay enrollment 丢失。

### 17.2 停止新 Node Service

```powershell
Stop-Service KnoaNode
```

不要停止 `KnoaHostedHub`、Cloudflare 或删除 Hub 数据。

### 17.3 使用保留的 Windows Node 快照诊断

```powershell
Get-ChildItem "$env:LOCALAPPDATA\Knoa\Node" -Force
Get-ChildItem "C:\ProgramData\Knoa\Node" -Force
```

安装器只复制旧 per-user state，不删除源目录。确认目标 identity/enrollment 有误时，可以停止
`KnoaNode`，修正 `C:\ProgramData\Knoa\Node` 后重新启动。禁止同时运行前台 Node 和 Service。

## 18. 常用运维命令

### 18.1 Hub

```powershell
Get-Service KnoaHostedHub
Start-Service KnoaHostedHub
Stop-Service KnoaHostedHub
Restart-Service KnoaHostedHub
```

### 18.2 Node Runtime

```powershell
Get-Service KnoaNode
Start-Service KnoaNode
Stop-Service KnoaNode
Restart-Service KnoaNode
```

### 18.3 Tunnel

```powershell
Get-Service Cloudflared-knoa
Start-Service Cloudflared-knoa
Stop-Service Cloudflared-knoa
Restart-Service Cloudflared-knoa
```

### 18.4 数据位置

```text
Hub data       C:\ProgramData\Knoa\HostedHub
Runtime venv   C:\ProgramData\Knoa\Runtime\venv
Scripts        C:\ProgramData\Knoa\Scripts
Hub logs       C:\ProgramData\Knoa\Logs\Hub
Node data      C:\ProgramData\Knoa\Node
Legacy snapshot %LOCALAPPDATA%\Knoa\Node
Tunnel secrets C:\ProgramData\Cloudflared\Secrets
Tunnel logs    C:\ProgramData\Cloudflared\Logs
```

## 19. 常见故障

| 现象 | 优先检查 |
| --- | --- |
| Hub Service 启动失败 | WinSW 日志、Python venv、HubRoot ACL、端口 9529 |
| Restore 提示目标非空 | 清理未完成安装，换用空 `HubRoot`，不要覆盖恢复 |
| Hub identity mismatch | 立即停止切换，检查是否使用了错误 backup/signing key |
| 公网 502/1033 | Windows cloudflared Service、Tunnel Token、origin 9529 |
| App 能登录但 Node 离线 | `KnoaNode` Service、Node enrollment、outbound Relay、系统时间 |
| Node 重启后不运行 | WinSW Node 日志、Python venv、NodeRoot ACL、端口占用 |
| App 显示未配对 | 在 Node 生成 v3 Relay QR，并用已登录当前 Workspace 的 App 扫码 |
| 截图/键鼠不可用 | 当前 Node Runtime 在 Session 0；等待/安装独立 Desktop Companion |
| Qwen 未出现在资源页 | Windows Node 尚未配置并发布本地 ModelResource |
| 旧 Linux Node 反复认证失败 | signing key/Hub ID 是否改变、系统时间是否同步 |
| 两边数据不一致 | 是否曾让两个 connector 同时服务同一 Tunnel |

## 20. 删除旧主机的时机

至少满足以下条件后，才考虑卸载旧 Linux Hosted Hub：

- Windows 连续稳定运行一个观察周期；
- Windows 重启恢复测试通过；
- App、旧 Node、新 Node 全部通过验收；
- 已在独立介质保存最终 Linux backup；
- 已为 Windows Hosted Hub 建立新的定期备份流程；
- 已完成一次 Windows backup 的恢复演练。

即使卸载旧服务，也建议保留最终备份，不直接删除历史恢复点。

## 21. 相关文档与脚本

- `deploy/windows/README.md`：原生 Windows 部署入口；
- `deploy/windows/Install-Knoa.ps1`：Hub + Node 安装与恢复；
- `deploy/windows/Enroll-KnoaNode.ps1`：Windows Node enrollment；
- `deploy/windows/Install-Cloudflared.ps1`：一个或多个独立 Tunnel Service；
- `deploy/hosted-hub/README.md`：Hosted Hub 数据与备份语义；
- `docs/knoa-deployment-architecture.md`：完整部署边界和迁移原则。
