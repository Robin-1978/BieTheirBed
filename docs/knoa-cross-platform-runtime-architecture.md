# Knoa 跨平台 Runtime 演进架构

> 状态：目标进程、跨平台和实现语言演进权威文档
>
> 日期：2026-08-19
>
> 范围：Windows/Linux 产品支持矩阵、Hub、Node Host、Agent Runtime、IPC、Console、P2P、部署、数据与更新
>
> 关联权威：领域所有权仍以 [产品领域架构](./knoa-product-domain-architecture.md) 为准；模块职责仍以
> [完整模块架构](./knoa-module-architecture.md) 为准；Agent SPI 与 Capability MCP 合同仍以
> [统一 Agent Runtime 设计](./knoa-agent-runtime-design.md) 为准；本文覆盖实现语言、进程边界、跨平台适配与演进终态

## 1. 决策摘要

Knoa 不进行一次性全量 Rust 重写。采用可持续运行的渐进替换：

1. Rust 承担 Hub、Node Host、P2P/Relay、安装更新和内置管理 UI 承载等基础设施职责；
2. Python Agent Runtime 作为 Node Host 管理的本机 Worker，保留成熟的 Agent、LLM、MCP、Skill 与 Tool 生态；
3. Windows 与 Linux 使用同一个 Universal Host Bundle；`hub`、`node`、`all` 是安装后的角色激活集合；
4. 用户当前选择的“Linux `all` + Windows `node`”只是部署实例，不能写入产品领域或协议；
5. Hub Console 与 Node Console 分别内置于 Hub、Node Host；共用一个无 UI 的最小特权 Host Lifecycle Broker；
6. Node 间和 App 到 Node 的数据面使用 ICE P2P 优先，现有端到端加密 Relay 仅兜底；
7. 迁移期间一个事实始终只有一个写权威，禁止 Rust/Python 双写同一领域数据。

## 2. 产品支持矩阵与实际部署

### 2.1 产品支持矩阵

| 操作系统 | `hub` | `node` | `all` |
| --- | --- | --- | --- |
| Windows x64 | 必须支持 | 必须支持 | 必须支持 |
| Linux x64 | 必须支持 | 必须支持 | 必须支持 |

角色语义在两个系统上完全一致：

```text
hub  = Hub（内含 P2P Signaling、Relay fallback、Hub Console）
node = Node Host（内含 Node Console）+ 受管 Agent Runtime Worker
all  = 同机安装 hub 与 node，但仍是两个独立服务和数据边界
```

Role 描述 Universal Host 上“激活哪些组件”，不改变三种产品形态：单独 `node` 可在 No-Hub 模式本地运行并以后 enrollment；
Self-hosted Hub 由用户部署 `hub` 或 `all`；Hosted Hub 是平台运营的同一 Hub 合同。No-Hub 没有 Account/
Workspace 跨设备目录、ResourceGrant 或 Relay，但 Node-local Agent、Conversation、Task、Model/MCP 仍可工作。

ARM64、macOS、移动端 Node、Kubernetes HA 不属于当前交付承诺，但协议与领域模型不得阻止后续增加。

### 2.2 当前环境的目标生产拓扑

这是完成实施计划 Phase 8 后的目标，不是对当前已经完成迁移的声明：

```text
Linux 主机 / role=all
├── Knoa Hub
│   ├── P2P Signaling / Relay fallback
│   └── built-in Hub Console
├── Knoa Node Host
│   ├── Python Agent Runtime Worker
│   └── built-in Node Console
└── Desktop Companion（存在桌面会话时，Phase 7）

Windows 主机 / role=node
├── Knoa Node Host
│   ├── Python Agent Runtime Worker
│   └── built-in Node Console
├── Qwen / llama.cpp
└── Desktop Companion（Phase 7）
```

迁移完成后，Cloudflare canonical domain 只指向 Linux Hub。Linux Node 与 Windows Node 都主动加入同一个
Workspace，不要求每个 Node 拥有域名。迁移前仍以当前生产 Hub 为唯一写权威，不得同时启用 Windows 与
Linux 两个 Hub writer。

## 3. 目标可部署单元

```text
Knoa App
   │ Hub control / Node data
   v
Knoa Hub
├── Account / Workspace
├── Node Directory / Presence
├── Resource Directory / Grant / Ticket
├── ICE Signaling
├── Relay fallback
├── built-in Hub Console UI
└── Android Release Channel

Knoa Node Host
├── Secure Gateway
├── Node–Hub Edge
├── ICE / P2P transport
├── Conversation / Task authority
├── Configuration / Secret / Approval / Artifact
├── Capability Gateway
├── built-in Node Console UI
└── Agent Process Manager
       │ private local IPC
       v
   Python Agent Runtime Worker
   ├── Knoa Agent loop
   ├── Codex adapter
   ├── LLM provider adapters
   ├── Skill composition
   └── MCP protocol adapters

Desktop Companion
└── signed-in user's desktop session

Knoa Host Lifecycle Broker
├── loopback-only authenticated API
├── signed Bundle install / rollback
└── fixed Hub/Node service activation
```

Hub、Node Host 和 Desktop Companion 是独立生命周期单元；Agent Runtime Worker 是由 Node Host 严格管理的
子进程。两个 Console 只是对应宿主服务内的 UI 模块和静态资产，没有独立进程、数据库或版本线；Hub Console
使用 Hub 进程的独立 loopback listener `9532`，Node Console 使用 Node Gateway `9531`。Lifecycle Broker
监听 `9533`，没有 Console 或领域数据，只承担必须提权的固定生命周期动作。
Agent、Skill、Tool、Conversation 和 Task 仍是 Node 内领域对象，不为追求微服务化拆成服务器。

## 4. 领域所有权不因 Rust 迁移改变

| 对象 | 写权威 | 目标承载进程 |
| --- | --- | --- |
| Account / Login / Session | Hub | Rust Hub |
| Workspace / Membership | Hub | Rust Hub |
| Node directory / ResourceGrant | Workspace Hub | Rust Hub |
| WorkProjection | Workspace Hub，只读投影 | Rust Hub |
| NodeAgent | Node | Rust Node Host |
| Conversation / Task | Node | Rust Node Host |
| 产品 Invocation lifecycle / event / terminal | Node | Rust Node Host |
| Runtime turn / context / checkpoint | 具体 Agent Runtime | Agent Worker 私有存储 |
| Model/MCP Endpoint | 承载 Node | Node Host 管理，Worker 适配 |
| Skill / Builtin Tool | Node | Node Host 目录，Worker 消费 |
| Secret | Node | Node Host Secret Store |
| Approval / Artifact | Node | Rust Node Host |

Rust 迁移不能成为把 Conversation、Task、Agent 或 Secret 上传到 Hub 的理由。

## 5. Node Host 与 Agent Runtime

Agent Runtime 的定位必须同时从四个维度理解：

| 维度 | 决策 |
| --- | --- |
| 产品与安全边界 | 属于一个明确 Node，不拥有独立 Hub enrollment、Node identity 或公网入口 |
| 代码边界 | 是可替换的独立模块，通过稳定 Agent Runtime SPI 与 Host 协作 |
| 进程边界 | 作为 Node Host 启动、监控和停止的 Worker 子进程运行 |
| 部署与版本 | 包含在 Node Bundle 中，与兼容的 Node Host 一起安装和更新，不单独部署 |

因此“独立模块”不等于“独立产品服务”。未来可以按 Agent kind 或 principal security domain 启动多个 Worker，
但它们始终属于承载 Node，不能自行注册 Workspace、管理 Conversation/Task 或绕过 Node policy。

### 5.1 职责边界

Node Host 是 Node 的长期权威进程：

- 拥有 Gateway、P2P、Relay、配置、数据、Secret 和审计；
- 创建并冻结 `ResolvedInvocation`；
- 启停和监控 Agent Runtime Worker；
- 运行 Capability MCP Gateway，由既有标准 MCP 调用链执行 Tool/MCP admission 与用户审批；
- 持久化 Conversation、Task、Invocation 事件和 Artifact metadata；
- Worker 崩溃时决定失败、恢复或人工确认，Worker 自己不得静默重放副作用。

Agent Runtime Worker 是受信任但非权威的执行组件。内置 Knoa Worker 同时承载默认 Knoa Agent 与受限
Knoa Reviewer；Codex 通过独立 Adapter 接入。它不是针对恶意 Agent 代码的安全沙箱：

- 执行 Agent reasoning loop；
- 组合 Prompt 与 Skill 内容；
- 使用获准的 Model Binding；
- 使用 Node Host 签发的 session-scoped Capability MCP Grant 调用获准 Tool/MCP；
- 产生增量文本、Usage、Artifact 声明与完成事件；
- 不监听公网、不注册 Workspace、不直接写 Hub。

普通自定义 Agent 通过新增 NodeAgent 配置复用内置 Knoa Worker。第三方自定义执行循环使用签名 Runtime
Extension Bundle，并由 Node Host 作为独立 out-of-process Worker 管理。Extension 不形成新的部署 role；
`role=node` 仍只有一个 Node Host 服务，Worker 数量是其内部受管状态。

### 5.2 本机 IPC

V1 使用 Node Host 创建并由子进程继承的双向匿名管道：

```text
Node Host --command pipe--> Agent Worker
Node Host <--event pipe---- Agent Worker
```

理由：Windows/Linux 都支持；不占固定端口；不需要防火墙；管道句柄只授予被启动的 Worker；比本机 TCP
减少认证和端口生命周期问题。容器化或远程 Runtime 是未来可选 transport，不改变领域消息。

帧格式从第一个跨进程版本开始使用 schema-generated Protobuf，不先建立 MessagePack 临时协议：

```text
4-byte big-endian frame length (flags + payload)
1-byte protocol flags
Protobuf payload
```

单帧默认上限 8 MiB，写端必须遵守背压，不得在内存中无界排队。Worker 的 stdout 专用于协议，日志只写
stderr 或 Host 提供的日志事件。V1 Artifact 只允许既有 opaque Resource URI 或合同上限内的 inline bytes；
大产物未来使用 Node-owned staging file + opaque token + digest + size，不在匿名管道中动态传递 fd/handle。

### 5.3 握手和版本

```text
HostHello
├── protocol_min / protocol_max
├── node_id
├── runtime_instance_id
├── config_generation
└── requested_features

WorkerHello
├── selected_protocol
├── runtime_name / runtime_version
├── supported_agent_kinds
├── supported_model_drivers
└── supported_features
```

没有协议交集时 Worker 不进入 Ready。Node Host 保持服务在线并把 Agent 状态标记为不可用，不影响 Hub
presence、Node Console 和诊断。

### 5.4 Invocation 消息

Node Host 到 Worker：

- `LoadGeneration`：加载不可变 Agent/Skill/Provider generation；
- `ExecuteInvocation`：发送已冻结的执行快照、Runtime session binding 与 Capability MCP Grant；
- `InterruptInvocation`：使用 command ID 和 binding epoch 请求有界取消；
- `ResolveInteraction`：解决 runtime-native user input / permission interaction；
- `ProviderCredentialResult`：仅向受信任 Worker 返回当前 Provider 调用所需的短期 credential lease；
- `DrainGeneration`、`Shutdown`。

Worker 到 Node Host：

- `Ready`、`Heartbeat`；
- `InvocationStarted` 与既有 `RuntimeEvent` discriminated union；
- `InteractionRequested`：只用于 Runtime 原生交互，不替代 Capability MCP Gateway 内的 Tool approval；
- `ProviderCredentialRequested`：只能请求当前 Model Binding 的 Provider scope；
- `InvocationCompleted`、`InvocationFailed`。

IPC 不重新定义 Tool/MCP callback。Tool discovery、resource read、`tools/call` 和调用中的审批继续使用
session-scoped Capability MCP Gateway；Node Host 持久化其产品事件，Worker 保存自己的 context/checkpoint。

每条消息携带 `runtime_instance_id`、`invocation_id`、单调 `sequence`。Host 对重复事件幂等，拒绝跨
Invocation 或跨 Runtime instance 的结果。

### 5.5 Agent 与 LLM 绑定

Agent 模型绑定由 Node Host 配置权威保存：

```text
NodeAgent.model_binding
  -> Model alias
  -> Provider
  -> local / cloud / workspace_remote endpoint
```

Knoa Agent 使用 `ownership=platform`，必须绑定 Node 的 Model alias；Codex Agent 使用
`ownership=runtime`，由 Codex Runtime 自己管理模型。配置发布只影响新 Invocation。

Secret value 永不出现在 `ResolvedInvocation`、日志或配置 diff。Worker 只可通过带 Provider scope、过期时间和
Invocation ID 的 `ProviderCredentialRequested` 获取当前调用所需凭据。该 lease 只存在于受信任 Worker 内存，
IPC trace 必须强制 redaction。MCP 子进程、Skill 和 Tool 不能读取 Provider Key。

## 6. Hub 与 Node 的内置 Console

### 6.1 Hub Console

Hub Console 只管理：

- Account 与登录安全；
- Workspace、Membership；
- Node enrollment、directory、presence 和 revoke；
- Workspace Model/MCP directory、Deployment、Grant；
- WorkProjection、Hub/Relay/P2P 状态；
- Android Release 和 Hub 备份诊断。

Hub Console 不显示或写入 NodeAgent、LLM API Key、MCP credential、Conversation 正文或 Task Trace。

### 6.2 Node Console

Node Console 只管理目标 Node：

- NodeAgent、默认 Agent 与 Agent→Model 绑定；
- local/cloud/workspace_remote Model；
- LLM endpoint、API Key 写入与轮换状态；
- MCP、Skill、Tool policy；
- Conversation/Task 诊断；
- Node Hub enrollment、P2P/Relay 状态；
- Runtime generation、日志和组件重启。

Hub Console 由 Hub 在同一 HTTPS origin 下提供，复用 Hub Account Session、Membership 和 CSRF 防护。Node
Console 由 Node Host 在现有管理 surface 下提供，默认只允许 loopback；远程 Node 管理必须经过用户明确部署的
TLS/VPN/Cloudflare Access，不自动公开新的管理端口。

### 6.3 部署归属

| Role | 长期服务 | 内置界面 |
| --- | --- | --- |
| `hub` | 一个 Hub 服务 | Hub Console |
| `node` | 一个 Node Host 服务；Agent Worker 是受管子进程 | Node Console |
| `all` | Hub 服务 + Node Host 服务 | 两个宿主各自内置对应 Console |

Console 不是新的领域后端。Hub Console 只调用同 origin Hub API，Node Console 只调用同 origin Node API，
两者都不直接打开 SQLite。它们可以复用前端视觉组件和错误语言，但不能跨越宿主权限边界，也不能复制
Hub/Node 业务逻辑。

## 7. NAT P2P 与 Relay

当前已交付 transport 是“显式 direct candidate + E2E Relay fallback”，尚未实现 ICE/STUN。以下是 Phase 5
的目标连接模型，不得在实现前对外宣称已支持 NAT P2P。

### 7.1 连接状态机

```text
resolve Node
  -> request short-lived connection ticket
  -> exchange ICE offer/answer through Hub signaling
  -> gather host + server-reflexive candidates using STUN
  -> connectivity checks
  -> authenticated P2P data channel
  -> on bounded failure: existing E2E encrypted Relay
```

Hub 信令只看到候选地址、会话标识和签名元数据，不看到 Node 业务 payload。P2P 和 Relay 使用同一个 Node
identity、ticket、application session handshake 和请求协议，transport 切换不能改变授权结果。

连接建立与业务 admission 明确分离：只有目标 Node 尚未确认 admission 时，调用方才能从 P2P 改走 Relay。
Node 确认 admission 后若链路断开，只允许使用同一 `application_session_id + invocation_id` 执行 reconcile/attach，
不得自动重放。协议携带 connection epoch 与 receive ack cursor，以恢复有序流并判定 outcome unknown。

### 7.2 技术边界

- App/Node 与 Node/Node 使用 ICE；
- STUN 只做地址发现，不转发业务数据；
- 对称 NAT 或受限网络下 P2P 失败是正常状态；
- 当前 Hub Relay 保留为 fallback；
- TURN 不是 V1 必需，因为已有应用层 Relay；只有测量证明值得时才引入；
- 连接建立限时，失败后快速回落，不允许长时间卡住 App；
- Relay 仍必须流式转发并实施背压，不能把完整响应缓存后再返回。

### 7.3 可观测性

只记录非敏感指标：

- `p2p_attempted/succeeded/fallback`；
- candidate 类型，不记录完整长期 IP 历史；
- 建连耗时、RTT、吞吐、fallback 原因码；
- Relay queue、backpressure、stream duration。

## 8. 跨平台适配

领域和协议 crate 不包含散落的操作系统条件。平台差异通过明确接口实现：

| 能力 | Windows | Linux |
| --- | --- | --- |
| Service | Windows SCM；迁移期可由 WinSW 包装 | systemd system/user service |
| 权限 | NTFS ACL | POSIX owner/mode/ACL |
| Runtime IPC | inherited anonymous pipe / Named Pipe | inherited anonymous pipe / Unix socket |
| Secret backend | 受 ACL 文件；后续可选 DPAPI | `0600` 文件；后续可选 Secret Service |
| 原子 release 切换 | directory junction/rename | symlink/rename |
| Desktop | signed-in Windows Session Companion | X11/Wayland user Companion |
| 日志 | rolling files/Event Log adapter | journald/rolling files |

产品层不得根据 OS 改变 Hub、Workspace、NodeAgent、Task 或 ResourceGrant 语义。

## 9. 发布与更新模型

开发源码不再是终端用户部署依赖。CI 对每个平台生成签名、不可变 Release Bundle：

```text
knoa-hub-<version>-windows-x86_64.zip
knoa-node-<version>-windows-x86_64.zip
knoa-hub-<version>-linux-x86_64.zip
knoa-node-<version>-linux-x86_64.zip
```

统一 ZIP 不是牺牲 Linux 语义：签名 Manifest 固定 executable 位，安全解包器在 Linux 恢复权限。单一格式减少
重复的 path traversal、symlink、zip/tar bomb、原子 staging 和签名校验实现，符合 KISS。

Node Bundle 在 Python Worker 仍存在期间内置受支持的 Python Runtime 和已安装依赖。用户不安装 Python、pip、
venv 或 Git。Hub Console 静态资产随 Hub Bundle 发布，Node Console 静态资产随 Node Bundle 发布，不建立
单独版本线。Hub 和 Node Bundle 可以分别更新；`all` 主机由 updater 协调两个独立角色使用兼容版本。

更新流程：

```text
download manifest -> verify signature/digest -> unpack releases/<version>
-> stop selected role -> atomically switch current -> start -> health check
-> success retain rollback window / failure switch previous
```

Release Manifest 必须包含 role、OS/arch、协议兼容范围、每个 artifact digest 和签名。Updater 内置信任根，
拒绝未签名、digest 不匹配、角色不匹配和非显式降级。Binary rollback 与 data rollback 是两件事：数据库优先
使用 expand-compatible schema；在旧 binary 无法读取新 schema 或新格式首次写入后进入 rollback cutoff，
只能停机恢复迁移前一致性备份，不得伪装成可自动回退。

App APK 发布仍属于 Hub Release Channel，与 Hub/Node Runtime 更新分离。

## 10. 数据路径与迁移

逻辑路径保持稳定，物理路径由平台 adapter 解析。Self-hosted 与 Hosted Hub 不能伪装成同一种数据库布局：

```text
SelfHostedHubRoot
├── hub.db
└── hub-signing.key

HostedHubRoot
├── control.db
├── hub-signing.key
├── tenants/<workspace_id>/hub.db
└── mobile-releases/android

NodeRoot
├── data
├── secrets
├── artifacts
├── attachments
├── skills
├── mcp
└── logs
```

Rust repository 接管 SQLite 时必须原地读取现有 schema 或执行单向、备份保护的 schema migration。禁止通过
“新 Rust 数据库 + Python 数据库同步”形成永久双写。

## 11. Rust workspace 目标布局

```text
crates/
├── knoa-domain
├── knoa-protocol
├── knoa-agent-ipc
├── knoa-node-host
├── knoa-hub
├── knoa-p2p
├── knoa-relay
├── knoa-update
└── knoa-platform
    ├── windows
    └── linux
```

Python 保留：

```text
python/
└── knoa-agent-runtime
    ├── agent loop
    ├── model adapters
    ├── skill composition
    ├── mcp adapters
    └── codex adapter
```

实际仓库迁移可逐步移动目录，但依赖方向必须从第一阶段开始遵守。
Hub/Node Console 前端可以放在共享 UI workspace 中构建，但产物分别嵌入 `knoa-hub` 与 `knoa-node-host`，
不生成独立服务器二进制。

## 12. 不变量

1. Windows 与 Linux 都支持 `hub`、`node`、`all`；
2. 当前部署选择不得进入领域模型；
3. 一个事实只有一个写权威，迁移期也不双写；
4. Hub 不执行 Agent，不保存 Node Secret；
5. Agent Worker 不监听公网，不自行注册 Hub；
6. Tool/MCP 副作用必须经过 Node Host capability admission；
7. P2P 与 Relay 只改变 transport，不改变身份、授权或 Invocation ID；
8. Hub Console 与 Node Console 分别内置于 Hub/Node，逻辑权限隔离且不形成新写权威；
9. 用户安装和更新不依赖 Git、系统 Python、pip 或 venv；
10. 每个迁移阶段必须保持可部署，并明确 binary rollback、data rollback 或 rollback cutoff，且具有新鲜跨平台测试证据。

## 13. 明确不做

- 不一次性重写全部 Python Agent 代码；
- 不为迁移建立永久兼容层或双数据库同步；
- 不把 Agent、Skill、Tool 包装成 Workspace 共享微服务；
- 不因 P2P 引入未经测量的 TURN 基础设施；
- 不让 Console 成为新的领域写权威；
- 不以当前两台机器的部署方式限制产品支持矩阵。
