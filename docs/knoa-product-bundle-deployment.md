# Knoa 产品 Bundle、安装与生命周期

> 状态：跨平台产品交付权威文档
>
> 日期：2026-08-20
>
> 适用平台：Windows x86_64/aarch64、Linux x86_64/aarch64

## 1. 产品结论

Knoa 每个 OS/架构只发布一个签名、自包含的 Universal Host Bundle，不再发布互不兼容的 Hub Bundle、Node
Bundle 和 All Bundle：

```text
knoa-host-<version>-<os>-<arch>.zip
├── release-manifest.json
├── runtime/                       # 产品内置 Runtime，用户不安装 Python
├── app/                           # Knoa 与锁定依赖
├── bin/
│   ├── knoa-hub
│   ├── knoa-node
│   ├── knoa-host-lifecycle
│   ├── knoa-desktop-companion      # 仅 Windows
│   └── knoa-health
├── install/                       # Windows/Linux service adapter
└── service/WinSW.exe              # 仅 Windows
```

`hub`、`node`、`all` 是同一个 Host Bundle 安装后的激活选择，不再是二进制兼容边界：

```text
installed_roles = {hub, node}
```

因此同一台电脑可以在 Console 中添加、停用或重新启用 Hub/Node，不需要卸载后换包。Hub、Node 仍是独立
服务、独立数据目录和独立故障边界。

普通用户不接触 Git、系统 Python、pip、uv、PowerShell/Shell 脚本或版本号。正式入口是：

- Windows：签名的 Knoa Setup；
- Linux：发行版软件包，第一目标是 `.deb`；
- 首次安装后：Hub Console / Node Console 负责更新、回退、角色启停和诊断。

“一键更新”只指 Universal Host 产品安装：Console 选择已签名 Bundle 后，Lifecycle Broker 自动完成校验、停服、
原子切换、启动、健康检查和失败回退。旧的 source-backed user systemd/venv 部署不具备 Broker，必须先通过
`.deb` 迁移到 Universal Host；不能把 `git pull + pip install + systemctl restart` 称为产品一键更新。当前
Console 尚未实现从 Hosted Release Channel 自动检查和下载，选择本地签名 Bundle 仍是 V1 的一次人工输入。

仓库中的 PowerShell/Shell 安装资产只服务发布构建、CI、开发和灾难恢复，不是最终用户界面。

## 2. 进程、Console 与端口

```text
Universal Knoa Host
├── Knoa Host Lifecycle Broker
│   └── 127.0.0.1:9533，特权、Token 认证、无独立 Console
├── Knoa Hub（可激活）
│   ├── public API / Relay origin: 127.0.0.1:9529
│   └── Hub Console: 127.0.0.1:9532/console
└── Knoa Node（可激活）
    └── Node Console / Gateway: 127.0.0.1:9531/console

Windows 登录用户会话
└── Knoa Desktop Companion（Node 激活时随登录自动启动）
```

Cloudflare Tunnel 只转发 `127.0.0.1:9529`。`9531`、`9532`、`9533` 永不进入 Tunnel。公网
`https://<hub>/console` 必须返回 404。

两个 Console 是 Hub/Node 的附属 UI，不是第三、第四个管理产品。Lifecycle Broker 是两个 Console 共用的
最小特权执行边界，没有页面、帐号、Workspace 或领域数据库。Console 通过本机 Token 代理固定动作，浏览器
不能直接访问 Broker。

## 3. 生命周期安全合同

Broker 只允许：

- 查询当前签名 Release、操作系统、架构、激活角色和服务状态；
- 重启固定的 `KnoaHostedHub` / `KnoaNode` 或对应 systemd service；
- 激活/停用 Hub、Node 角色；
- 回退到 updater 的 `previous` Release；
- 从固定 Incoming 目录安装经 product Trust Store 验证的 Universal Host Bundle。

Broker 不接受任意命令、任意路径、任意 service 名称或未签名程序。上传文件只进入固定 Incoming 目录，安装时
再次校验 Manifest、签名、OS、架构、`role=all`、SPI、大小和 SHA-256。更新采用不可变版本目录和原子
`state.json` 指针；失败恢复 previous，HubRoot、NodeRoot、Secret、Workspace 和 APK 不随版本回退删除。

V1 的“卸载角色”表示停用 service 并保留数据。完整删除产品和清除数据必须由原生安装器提供两个不同操作，
清除数据要求单独确认，不能由网页上的普通按钮隐式执行。

## 4. 数据目录

### Windows

```text
C:\Program Files\Knoa\
├── bin\knoa-update.exe
└── releases\
    ├── state.json
    └── versions\<release_id>\

C:\ProgramData\Knoa\
├── HostedHub\
├── Node\
├── Workspace\
├── Incoming\
├── Config\
│   ├── host-state.json
│   ├── release-trust.json
│   └── node.yaml
├── Secrets\
│   └── lifecycle.token
├── Desktop\
│   └── companion.token
├── Services\
│   ├── KnoaHostLifecycle\
│   ├── KnoaHostedHub\
│   └── KnoaNode\
└── Logs\
```

三个 Windows service 都由 WinSW 承载并以 LocalSystem 运行；`KnoaHostLifecycle` 始终启动，Hub/Node 按
`installed_roles` 激活。ProgramData 使用 SYSTEM 和 Administrators ACL。Desktop Companion 不是 service，
安装 Node 时会立即在当前交互 Session 启动，并通过 HKLM 登录启动项在后续登录恢复；它通过按 Windows Session ID 隔离的认证 Named Pipe 接收
固定桌面 Tool 请求。Node service 不直接调用 BitBlt、剪贴板、窗口或键鼠 API。`Desktop` 子目录仅向本机 Users
开放只读 Token，其余 ProgramData 数据仍不可读。

Companion 启动器持续通过 `knoa-update current` 观察活动 Release；Console 更新切换 `state.json` 后自动重启
Companion 子进程，因此 Windows 桌面能力与 Hub/Node 使用同一个版本，不需要用户再次登录。无人登录、锁屏后
桌面不可用或 Companion 未运行时，Node 必须返回 `execution_environment_unavailable`，不得退回 Session 0
BitBlt。

### Linux

```text
/opt/knoa/
├── bin/knoa-update
└── releases/
    ├── state.json
    └── versions/<release_id>/

/var/lib/knoa/
├── hub/
├── node/
├── workspace/
└── incoming/

/etc/knoa/
├── host-state.json
├── release-trust.json
├── hub.env
├── node.yaml
└── secrets/lifecycle.token
```

`knoa-host-lifecycle.service` 以 root 运行，只拥有固定生命周期权限；Hub/Node 以专用 `knoa` 用户运行。

## 5. 用户流程

### 新用户

1. 运行一个原生安装包，选择“Hub”“Node”或“Hub + Node”；
2. 安装包放置同一个 Universal Host Bundle、Trust Store、Updater 和三个 service；
3. Hub 用户打开 `http://127.0.0.1:9532/console`；
4. App 登录 Account、选择 Workspace，并可直接生成十分钟单次 Enrollment Code；
5. Node 用户打开 `http://127.0.0.1:9531/console`，粘贴 Code；
6. Node Console 生成 App 配对二维码。

Enrollment Code 中的 `hub_url` 必须是 Workspace 的公网 Hub URL，不能使用本地 Console 地址。

### 老用户

- Hub 配置、帐号、Workspace 和 Node Directory：Hub Console；
- Node Agent、LLM、Key、MCP、Skill、Tool、Task 和 Conversation：Node Console；
- 产品更新、回退、服务重启：任一已激活角色的本地 Console；
- 日常 Workspace/Node 选择、会话和任务：App。

### Linux 更新现状

- 安装为 Universal Host `.deb` 后：有 Console 一键安装、健康检查、失败回退和上一版回退；
- 当前开发机若仍运行 `~/.local/share/knoa/runtime/venv` 与 user systemd：没有产品级一键更新；
- 正向迁移目标是安装 `.deb` 并保留/导入 HubRoot、NodeRoot 和 identity，迁移完成后删除源码服务；
- 自动检查 Hosted 最新版本属于后续 Release Channel 增量，不阻塞签名 Bundle 的本地一键安装闭环。

## 6. 发布构建合同

发布 CI 在目标 OS 上物化锁定 Runtime/Application，然后始终构建 `role=all` 的 Host Bundle：

```bash
python scripts/build_product_release.py \
  --target-os linux \
  --target-arch x86_64 \
  --runtime /release-input/python-runtime \
  --application /release-input/application \
  --output-directory /release-output \
  --signing-key /secure/product-release-key.pem \
  --key-id knoa-product-2026
```

Windows 构建额外传入经审核的 WinSW。Private signing key 不进入仓库、Bundle、Hub 或 Node。正式 Setup / `.deb`
把 updater、Trust Store 和首个 Host Bundle 封装为一个下载物；当前仓库的 Bundle 与 Broker 合同已经成立，原生
单文件安装器及干净 VM 安装矩阵仍是发布门，未通过前不能宣称大众发行完成。

## 7. Updater 合同

```text
knoa-update install   验证、stage、离线健康检查、激活
knoa-update current   输出当前不可变版本目录
knoa-update run       从 state.json 启动签名 entrypoint
knoa-update rollback  主动切换 previous
knoa-update reject    当前版本失败时恢复 previous
```

service 永远调用稳定的 `knoa-update run`，不硬编码版本目录。用户不直接调用这些命令；Console 通过 Broker
使用它们，CLI 只保留给发布工程、CI 和灾难恢复。
