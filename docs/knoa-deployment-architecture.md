# Knoa 部署架构

> 状态：当前 V1 可部署架构与后续生产演进边界
>
> 更新日期：2026-08-19
>
> 范围：Mobile App、Node、HubService、Relay、Agent Runtime、LLM、Skill、Tool、MCP、网络、安全、存储、故障与运维拓扑
>
> 权威关系：产品对象和配置归属以 `knoa-product-domain-architecture.md` 为准；模块职责以 `knoa-module-architecture.md` 为准；跨 Node 调用以 `knoa-workspace-resource-fabric-design.md` 为准；本文是进程、网络和运维部署的权威入口
>
> 实现语言、Rust Node Host、Python Agent Worker、跨平台 Bundle/Updater 与迁移阶段以 `knoa-cross-platform-runtime-architecture.md` 和 `knoa-cross-platform-runtime-migration-plan.md` 为准
>
> 设计取向：local-first、Hub-assisted、self-hostable；高内聚、低耦合；KISS、YAGNI；当前能力与目标能力必须明确区分

## 1. 文档目的

本文回答以下部署问题：

1. Knoa 完整产品由哪些可部署单元组成；
2. Hub、Relay、Node、Agent、LLM、Skill、Tool 和 MCP 的层级关系是什么；
3. 哪些部分现在可以独立部署，哪些只是代码模块；
4. 普通用户、自托管用户和无 Hub 用户分别如何部署；
5. 域名、TLS、端口、Secret、持久化和备份放在哪里；
6. 单机 V1 在什么条件下才应演进为多实例或拆分 Relay。

本文不把所有代码模块都包装成微服务。只有具备独立生命周期、资源隔离、故障隔离或扩缩容需求的
边界才成为部署单元。

## 2. 一句话部署模型

```text
App 负责交互和管理；
Node 负责数据、Agent、Tool、LLM 和 MCP 的实际执行；
HubService 负责可选的账户、Workspace、目录、授权票据和连接协调；
Relay 只负责转发端到端加密帧。
```

Node 是执行服务器。HubService 是可选共享控制面。Relay 是无业务语义的数据通道，不是第二个
业务服务器，也不是权限权威。

### 2.1 Mobile App 的启动与故障边界

在使用 Hub 的形态中，App 的根启动流程是控制面优先，而不是 Node 会话优先：

```text
App Installation
  -> restore/login Hub Account
  -> select Workspace
  -> open Workspace Node directory, shared services or activity projections
  -> select the authoritative Node when Conversation/Task content is needed
  -> establish direct or Relay Node session
```

Hub Account、Workspace、management projection 和 Node directory 属于 App 可独立访问
的控制面。Conversation 正文/ChatTurn、TaskExecution、AgentInvocation、ExecutionAttempt 和 live
control 属于绑定或部署 Node；Node 离线、认证失败或 Relay 暂时不可用只能使该 Work 内容/执行不可用，
不能阻断帐号登录、Workspace 切换、查看最后投影、Node directory 或管理其他 Node。

No-Hub 形态没有 Hub 控制面，但遵循同一故障隔离原则：App 先展示本地 pinned Node bindings，
用户选择后才建立 Node 会话。App 不在启动时自动连接历史 active Node。

## 3. 完整产品部署视图

```text
┌────────────────────────────── Client Plane ──────────────────────────────┐
│ Mobile App │ CLI/TUI │ Feishu │ Webhook                                 │
└───────────────────────┬───────────────────────────────┬──────────────────┘
                        │ direct HTTPS/WSS              │ Hub HTTPS/WSS
                        │                               v
                        │          ┌──────── Optional Hub Deployment ──────┐
                        │          │ HubService                            │
│          │ Account / Workspace / Node Directory │
│          │ Hosted Mobile Release Channel        │
                        │          │ Resource directory / Grant / Ticket  │
                        │          │ RelayBroker: opaque encrypted frames │
                        │          └──────────────────┬────────────────────┘
                        │                             │ outbound relay
                        v                             v
┌──────────────────────────────── Node Deployment ──────────────────────────┐
│ Node–Hub Edge Adapter │ Secure Gateway │ Core                            │
│ Conversation │ Task │ Approval │ Artifact │ Configuration Control Plane │
│ Agent Orchestration                                                      │
│   ├── Native Agent Runtime ──> Model Provider ──> local/cloud/remote LLM │
│   └── Codex Adapter ─────────> trusted Codex App Server                  │
│ Capability Gateway ──> Built-in/Platform Tools ──> Host                  │
│                    └─> MCP proxy ────────────────> MCP Server            │
│ Skill packages: data-only instructions and capability requirements       │
└───────────────────────────────────────────────────────────────────────────┘
```

Agent、LLM、Skill、Tool 和 MCP 不是与 Hub、Relay、Node 平级的中心服务：

- Agent 是 Node 内的 `NodeAgent` 配置与 Runtime 实现，不是 Workspace 共享服务；
- LLM 是模型部署或 Provider，可以在本机、云端或另一个 Node；
- Skill 是 Node 安装并由配置发布的数据包，不自行执行；
- Tool 经 Node 的 Capability Gateway 授权后执行；
- MCP Server 可以是 Node 管理的本地进程，也可以是独立远程服务；
- Codex App Server 和本地 LLM Server 可以拥有独立进程，但仍由 Node Adapter 使用。

## 4. “Node–Hub 边缘”是什么

### 4.1 定义

“Node–Hub 边缘”不是第三种服务器，也不是位于公网的边缘计算节点。它是 **Node 进程内面向 Hub 的
边界适配层**，更完整的名称是 `Node Hub Edge Adapter`。

它把 Hub/Relay 协议转换为 Node 已有的本地能力，同时阻止 Hub 直接进入 Core repository、Agent
Runtime、Secret Store 或 Tool 执行边界。

当前代码组成：

```text
Node Hub Edge Adapter
├── NodeHubStore
│   └── 保存并固定单个 Hub enrollment 和 Hub signing public key
├── NodeHubService
│   └── 执行 Node enrollment、签名 transcript、校验 Hub identity
├── NodeRelayManager
│   ├── 主动建立 outbound Relay WebSocket
│   ├── 处理 Client-to-Node 端到端握手
│   ├── 解密后调用同一个 SecureGatewayAdapter ASGI app
│   └── 处理 Node-to-Node resource/model encrypted session
├── RemoteModelEndpoint
│   └── 在目标 Node 执行授权后的模型 Invocation
└── RemoteModelProvider
    └── 在调用 Node 将 workspace_remote 模型接入统一 Agent Runtime
```

### 4.2 为什么需要这个边界

没有该适配层时，容易产生两套错误架构：

1. 为 Relay 重新建设一套 Conversation、Task、Config 和 Model API；
2. 让 Hub 直接调用 Core repository 或持有 Node Secret。

当前设计只保留一套 Node 业务合同：

```text
Direct request ──────────────┐
                             ├─> SecureGatewayAdapter.app ─> CoreClient ─> Core
Relay decrypted request ─────┘
```

因此 direct 与 Relay 不会演化成两套业务控制器。Node–Hub Edge 只拥有连接、enrollment、握手、帧
传输和协议适配职责，不拥有 Conversation、Task、Agent、Tool 或 Config 的业务事实。

### 4.3 生命周期与故障边界

- Node–Hub Edge 随 Secure Gateway 启动和停止；
- 未 enrollment Hub 时保持禁用，不影响 Node 本地能力和 direct pairing；已 enrollment 的 Hosted Node
  默认通过 pairing-scoped Relay ticket 完成 App 初始配对；
- Hub 或 Relay 断开时进行有界重连，不能阻塞 Core 启动；
- 删除 enrollment 时停止 Relay connector 并移除本地 Hub pin；
- Hub identity 不匹配时 fail closed，不自动接受新 Hub key；
- Relay 收到的明文请求只能在端到端解密后进入现有 Gateway 认证和业务路由。

## 5. 当前部署单元与独立性

| 部署单元 | 当前入口 | 是否独立部署 | 当前说明 |
| --- | --- | --- | --- |
| Knoa Node | `knoa --serve` 或 `python -m knoa_platform.service` | 是 | 完整执行服务器，拥有 Core、Gateway、Agent、Tool、Extension、本地状态和内置 Node Console |
| HubService + Relay | `knoa-hub` | 是 | 当前作为一个进程、一个 HTTP/WSS 监听器部署，并内置 Hub Console |
| RelayBroker | 无独立 CLI | 否 | 代码模块独立，但由 `HubApplication` 同进程创建 |
| Mobile App | Android App | 是 | 客户端，不拥有服务端业务事实 |
| Android Release Channel | Hosted Hub 或 Node Gateway 内建模块 | 否 | Hosted Account 的 APK 属于 Hub；No-Hub/Self-hosted 的 APK 属于 Node |
| Local LLM Server | llama.cpp/Ollama/OpenAI-compatible server | 是 | 独立进程或外部服务；Secret、模型路径和执行仍归目标 Node |
| MCP Server | stdio 或 streamable HTTP | 可选 | 可由 Node 管理本地进程，也可远程独立部署 |
| Codex App Server | Codex Runtime 自有入口 | 是 | 受信 Runtime adapter 使用，不进入 Knoa 原生模型 Provider |
| NodeAgent/Skill/Tool | 无独立进程入口 | 否 | Node 内逻辑配置或内容，不为追求“微服务化”单独部署 |

目标态 Agent Runtime Worker 虽然拥有独立代码模块和受管子进程边界，但仍包含在 Node Bundle 中，由 Node Host
控制生命周期和兼容版本；它不是第四种部署角色，也没有独立 Hub enrollment、Console、端口或数据权威。

### 5.1 跨平台部署角色合同

Windows 与 Linux 对运维暴露相同的三个角色，而不是把 Hub 与 Node 固化为同一安装单元：

| Role | 安装内容 | 更新/重启边界 |
| --- | --- | --- |
| `hub` | Hosted Hub + Relay + 内置 Hub Console | 只操作 Hub 进程和 Hub 数据 |
| `node` | Node Runtime + 内置 Node Console | 只操作 Node 进程和 Node 数据 |
| `all` | 同机 Hub + Node；Console 分别内置 | 同时更新两个独立服务 |

Windows 使用两个独立 WinSW Service：`KnoaHostedHub` 与 `KnoaNode`；Linux 使用两个独立
systemd user service：`knoa-hosted-hub.service` 与 `knoa-node.service`。`all` 只是安装器编排便利项，
不会把两个进程、数据库或故障边界合并。同一台主机运行两个角色时应使用 `all` 更新，使共享的 Knoa
程序版本保持一致；分离主机分别使用 `hub` 与 `node`。

Android App 不是 Node 包，也不由 Node 安装器承载。Hosted App 发布由 Hub 独占：签名 APK 通过平台
管理命令进入 `<HubRoot>/mobile-releases/android`，Hub 提供认证版本查询、不可变下载地址和稳定人工安装
地址 `/downloads/android/latest.apk`。发布 App 不重启 Hub 或 Node。

跨主机构建采用独立 `ReleasePublisher` 运维凭据，而不是 Account Session、Workspace Membership 或
Hosted bootstrap token。构建机先本地验证 APK 包名、版本和签名，再通过 HTTPS 流式上传；Hub 在读取请求体
前验证专用凭据和声明长度，限制单包不超过 100 MiB，校验 SHA-256、APK 容器、版本单调性后原子发布。
发布权限是 Hosted 平台级运维权限，不属于任一 Account 或 Workspace，V1 不提供 App 内上传入口。

### 5.2 当前 Hub/Relay 的硬限制

当前 Self-hosted Hub 是 V1 单实例实现：

- `HubRepository` 使用单个 SQLite `hub.db`；
- `RelayBroker` 的 Node/Client 连接表在进程内存中；
- 只能运行一个 Hub worker；
- 不能把 WebSocket 连接随机分发给多个互不共享状态的 worker；
- 尚无跨实例 connection routing、共享 presence cache 或 Relay session ownership；
- 当前适合个人或小规模自托管，不宣称 Hosted 多租户、高可用或水平扩容能力。

## 6. 三种部署形态

### 6.1 No-Hub / Direct

适用：单 Node、局域网、Tailscale/WireGuard 或用户已经拥有可信直连网络。

```text
Mobile App
    │ direct HTTPS/WSS or protected private network
    v
Knoa Node
├── Secure Gateway
├── Core / Agent / Tool / MCP
└── local or cloud LLM
```

特性：

- 不部署 Hub 和 Relay；
- App 与每个 Node 独立 pairing；
- 保留完整本地 Conversation、Task、Agent、Skill、Tool 和 MCP 能力；
- 没有自动 Node directory、Relay、跨设备账户恢复和多 Node 共享模型票据；
- 不要求为了直连默认创建公网域名。

### 6.2 Self-hosted Hub + Relay（当前多 Node V1 与 P2P 目标）

适用：一个用户或家庭拥有 N 台 Node，希望只管理一个域名。

```text
Internet
   │
   v
hub.example.com:443
   │ TLS termination / reverse proxy
   v
single knoa-hub process
├── HubService
├── RelayBroker
├── hub.db
└── hub-signing.key
   ^                    ^                    ^
   │ outbound HTTPS/WSS │ outbound HTTPS/WSS │ outbound HTTPS/WSS
Node A               Node B               Node C
```

部署规则：

- 用户只配置一个 Hub 域名；
- Hub 对外只暴露 TLS 保护的 HTTPS/WSS；
- N 个 Node 主动发起出站连接，不需要 N 个公网域名；
- 当前已交付 transport 依次尝试显式 LAN/direct candidate，再使用 Relay fallback；
- 目标 transport policy 是 `p2p_preferred`：按跨平台 Runtime 实施计划 Phase 5 增加 Internet
  ICE P2P/NAT traversal，再使用 Relay fallback；
- 用户不需要为每个 Node 配置域名；显式公网 direct endpoint 只是 P2P candidate 之一，不是部署前置；
- 目标态 App 先访问 Hub 选择 Node、获取短期 ticket 和交换连接 candidate，再进行有界 P2P 建连；
- 目标态 Node-to-Node 共享 LLM/MCP 同样 P2P 优先、Relay fallback，并复用安全连接和同一 invocation ID；
- 目标态 Relay 不承担默认 LLM streaming、MCP 或 Artifact 数据面，只在 P2P 失败时兜底；当前 Relay 仍可能
  承载主要远程数据面，因此必须先完成 streaming 与 backpressure 性能修复；
- Hub/Relay 单实例部署，不启用多 worker。

### 6.3 Knoa Hosted Hub Single-Node（形态 3 单节点 MVP）

```text
Knoa Hosted Hub :9529
├── Account / LoginIdentity / PasswordCredential / Session
├── Workspace / Membership / one-time recovery grant
├── shared Hosted Hub identity
└── /workspaces/{workspace_id}
    ├── isolated Workspace SQLite
    ├── isolated HubApplication
    └── isolated in-process RelayBroker
```

Hosted 形态不是给每个用户启动一套物理 Hub 进程，而是共享基础设施中的逻辑 Workspace 隔离。

当前已交付 `hosted_single_node` composition，用真实 HTTP 路由、密码登录、Session、独立 tenant
database 和 RelayBroker 形成形态 3 的单节点闭环：

- Hosted 根服务拥有 Account、LoginIdentity、scrypt 密码凭证、Session、Workspace 与 Membership；
- Bootstrap Secret 只签发一次性 Account/密码恢复 grant，不进入 App；
- 同一 Hosted Hub 的 Workspace 共享 `hub_id` 与 signing identity；
- `hub_id` 标识 Hosted issuer/签名信任域，`workspace_id` 标识资源、授权和执行租户边界；
- Node presence、Relay ticket 绑定 `hub_id`，Workspace Resource、Deployment、Projection 和 ResourceGrant
  的签名 transcript 必须绑定 `workspace_id`；协议不能假设二者相等；
- Self-hosted 单 Workspace 可让 `hub_id == workspace_id`，Hosted 多 Workspace 则必须保持二者分离；
- 每个 Workspace 使用独立 `HubApplication`、SQLite 和 Relay 连接表；
- Account Session 只能进入具有 active Membership 的 `/workspaces/{workspace_id}`；
- member 可读和使用 Workspace，owner/admin 才能修改资源或签发 Node enrollment grant；
- Node enrollment、directory、资源授权和 Relay 状态不能跨 Workspace；
- App 可创建/登录/恢复帐号、管理 Workspace 和 owner-managed shared membership；
- 提供一致性备份、完整性校验和仅向空目录恢复的运维入口。

该实现可作为个人和受控小规模部署的单节点 Hosted MVP，不等同于 HA 公有云 Hosted。后续生产化仍需：

- MFA/step-up、外部验证渠道与自助恢复投递；
- 生产多租户数据库、Relay 跨实例路由与容量治理；
- abuse control、计费、SLO 和数据保留；
- 托管 KMS/HSM、自动备份保留和演练 SLO。

因此当前代码应明确标记为“Hosted Hub Single-Node”，不能标记为多区域或高可用 Hosted 服务。

## 7. Node 进程拓扑

当前 Node 由一个 `ApplicationDaemon` 统一管理生命周期：

```text
Knoa Node process
├── CoreDaemon
│   ├── CoreHost / CoreServer
│   ├── Conversation / Task / Automation / Artifact
│   │   ├── Conversation content for locally bound sessions
│   │   ├── NodeTask / TaskExecution
│   │   └── Node-local Schedule/Trigger dispatcher
│   ├── Agent Orchestration / Runtime generations
│   ├── Capability Gateway
│   ├── ExtensionManager
│   └── Capability MCP Host
├── SecureGatewayAdapter
│   ├── App authentication and typed API
│   ├── Node Hub enrollment routes
│   ├── NodeRelayManager
│   └── RemoteModelEndpoint
├── optional WebhookAdapter
└── optional ChannelRuntime
    └── FeishuChannel
```

统一进程是生命周期组合，不是模块越权：Gateway 和 Channel 仍通过 CoreClient/Core API 访问 Core，
不能直接写 Core repository。

V1 不把 Conversation、Task、Agent、Tool 和 Configuration 拆成分布式微服务。它们需要共享清晰的
本地事务、调用快照和故障恢复语义，单 Node 内拆分只会增加网络失败与一致性成本。

Task 定义、trigger 与执行都保存在创建它的 Node；定时启动器运行在该 Node。
Workspace/Hub 不运行第二套 Task scheduler。Node 将 Work 管理投影同步给 Workspace Registry，但保留
Conversation 正文、完整 Trace、Tool/MCP 原始载荷、Artifact bytes 和 Secret 的权威。

## 8. Hub 进程拓扑

```text
knoa-hub process
├── Starlette / Uvicorn listener
├── HubApplication
│   ├── HubService
│   ├── HubRepository
│   └── RelayBroker
├── hub.db
└── hub-signing.key
```

HubService 负责：

- owner Account bootstrap；
- Workspace 和 membership；
- Node enrollment、directory、presence 和 revoke；
- ModelResource、ModelDeployment、ResourceGrant 和 DeploymentObservation；
- 短期 connection/resource invocation ticket；
- opaque Fleet envelope metadata。

RelayBroker 负责：

- Node outbound WebSocket 注册；
- Client/resource session 到目标 Node 的路由；
- 有界 frame 校验、转发和连接替换；
- 不解密 payload，不理解 Gateway、Conversation、Task、Tool 或模型语义。

## 9. 关键请求路径

### 9.1 App direct 访问 Node

```text
App
  -> pinned Node identity / TLS
  -> Secure Gateway authentication
  -> typed Gateway route
  -> CoreClient
  -> Core application service
```

### 9.2 App 经 Relay 访问 Node

```text
App
  -> Hub account session
  -> request short-lived single-use connection ticket
  -> Client-to-Node ephemeral key agreement
  -> encrypted Relay frames
  -> Node Hub Edge decrypts
  -> same SecureGatewayAdapter.app
  -> normal Node authentication and business route
```

Hub account authentication只允许申请连接，不替代 Node business authorization。

App 内部同样保持这两个边界：Hub client 负责 Account、Workspace 与目录；`GatewayProvider` 只负责
用户已选择 Node 的 direct/Relay 会话。`GatewayProvider` 的错误不是 App 根状态，也不得覆盖或清除
Hub 控制面状态。

### 9.3 Node B 调用 Node A 的本地 LLM

```text
Agent on Node B
  -> workspace_remote Model Provider
  -> Hub ResourceGrant + Invocation Ticket
  -> exchange LAN/direct/NAT traversal candidates
  -> establish and reuse E2E P2P session when possible
  -> otherwise use opaque Relay with same invocation_id
  -> Node A admission + persistent idempotency record
  -> local ModelDeployment
  -> encrypted response to Node B
```

Node A 是执行结果权威。Hub 只保存授权、票据元数据和非权威 observation，不保存或解密 Prompt、
模型响应、API Key 或模型文件。

P2P 与 Relay 只改变 transport，不改变 ResourceGrant、Node admission、Invocation identity 或执行
位置。一次 Invocation admission 后发生网络切换时必须 attach/reconcile，禁止因切换路径重复推理。

### 9.4 Node B 调用 Node A 的 MCP

```text
Task/Agent on Node B
  -> granted MCPDeployment on Node A
  -> same P2P-first secure connector
  -> Node A validates capability and local policy
  -> Node A MCP uses Node-local Secret
  -> structured result returns to Node B
```

MCP 默认不跨 Node 共享，只有显式 `mcp_invoke` ResourceGrant 才允许该路径。Agent、Skill、Built-in
Tool 和 Secret 不使用此远程资源路径。

## 10. 网络、端口、域名与 TLS

### 10.1 Node 默认监听

| 端口 | 默认绑定 | 用途 | 公网策略 |
| --- | --- | --- | --- |
| `9527` | `127.0.0.1` | Core service transport | 不公开 |
| `9528` | `127.0.0.1` | optional Webhook | 仅经受控 TLS ingress |
| `9529` | `127.0.0.1` | Secure Gateway | 默认只监听 loopback；显式 remote mode 必须配置 TLS |
| `9530` | `127.0.0.1` | Capability MCP Host | 不公开 |

端口可以配置，但安全性质不能因改端口而改变。Core 和 Capability MCP Host 保持 Node-local。

### 10.2 Hub 默认监听

`knoa-hub` 默认监听 `127.0.0.1:9530`。生产 Self-hosted 部署应在其前面放置 TLS reverse proxy，
对外提供单一 `https://hub.example.com` / `wss://hub.example.com`。

如果 Node 与 Hub 在同一机器运行，Node Capability MCP Host 和 Hub 默认端口都会使用 `9530`，必须
修改其中一个监听端口。生产环境更推荐将 Hub 作为独立服务账户、容器或主机部署。

形态 3 单节点 Hosted 部署使用 `127.0.0.1:9529` 承接唯一公网 Hub 域名。若同机 Node 原来占用
`9529`，应先把 Node Secure Gateway 移到另一个 loopback 端口（当前部署使用 `9531`）并清空其
public URL，再由 Hosted Hub 接管 `9529`。公网入口仍由 TLS ingress 提供，不直接暴露 Uvicorn。

### 10.3 原生 Windows 同机 Hosted Hub + Node

Windows 10/11 或 Windows Server 可以直接使用标准 CPython 3.14 x64、venv 和 Knoa wheel 运行，不要求 WSL，
也不要求先封装为 PyInstaller EXE。当前标准同机拓扑是：

```text
Windows Host
├── Knoa Hosted Hub WinSW service
│   ├── identity: LocalSystem
│   ├── start: Automatic
│   ├── listener: 127.0.0.1:9529
│   └── state: C:\ProgramData\Knoa\HostedHub
├── Knoa Node
│   ├── service: KnoaNode / WinSW / LocalSystem / Automatic
│   ├── Core: 127.0.0.1:9527
│   ├── Capability MCP: 127.0.0.1:9530
│   ├── Secure Gateway: 127.0.0.1:9531
│   └── state: C:\ProgramData\Knoa\Node
└── independent cloudflared WinSW services
    ├── Knoa Tunnel Token -> canonical Hub hostname -> 127.0.0.1:9529
    └── PER Tunnel Token  -> PER hostname -> configured local origin
```

Hub 和 Node Runtime 都是无桌面常驻服务，因此由 WinSW 以 `LocalSystem` 自动启动。截图、剪贴板、
通知、键鼠和窗口等能力属于未来独立的登录用户 Desktop Companion；不能因此让整个 Node Runtime
退回计划任务。安装器使用 NTFS ACL 将 Knoa 根目录限制为
`SYSTEM`、本机 Administrators 和安装用户；Python 运行时不把 POSIX `0600/0700` mode bits 错当成
Windows ACL。

当前实际配置是两个 remotely managed Cloudflare Tunnel，因此对应两个 Token 和两个 cloudflared
进程。Windows 使用两个不同 Service ID 的 WinSW wrapper，Token 只保存在受 ACL 保护的文件中，XML
只引用 `--token-file`。Knoa Hub 仍只有一个 canonical URL；另一个 Tunnel 不构成第二个 Hub。

共享 Python 包和领域代码继续同时支持 Linux 与 Windows。Linux 保持 systemd 和 POSIX 权限模型；
Windows 的 WinSW、NTFS ACL、Session 0 与未来 Desktop Companion 只属于部署和平台适配边界。

Hosted App 初始配对使用 Node 本地产生的 v3 QR。QR 固定 Node public identity、一次性 Gateway grant、
Workspace Hub URL 和 `transport=relay`。App 必须先登录该 Workspace，Hub 只签发短期
`scope=pairing` Relay ticket；Node 在该会话中只接受 `/v1/pair/challenge` 与
`/v1/pair/complete`。配对完成后的普通会话必须重新申请 `scope=session` ticket。由此不需要给每个
Node 配置公网域名，Relay 也不能把“已登录 Hub”直接提升为“已配对 Node device”。

原生安装、Node enrollment 和 cloudflared 命令见
[`deploy/windows/README.md`](../deploy/windows/README.md)。

### 10.4 TLS 原则

- 公网 Hub 必须使用有效 TLS；
- WebSocket upgrade 必须由 proxy 正确透传；
- TLS 是传输保护，不替代 Node/App identity 和应用层端到端加密；
- Relay 终止 TLS 后看到的仍只能是密文 frame；
- Node direct 公网监听必须启用 Secure Gateway remote TLS fail-closed 模式；
- 不建议普通用户为每个 Node 建独立 DNS、证书和 Tunnel；这只是高级 direct 优化；
- Cloudflare Tunnel 可以承载 Hub 或 direct Gateway 的网络入口，但不能替代 Knoa Account、Ticket、
  Node key、Client key 或 ResourceGrant。

## 11. 持久化与 Secret

### 11.1 Node

默认 Runtime Root 为 `~/.knoa`，主要包含：

```text
~/.knoa/
├── config/       bootstrap、service.env 和非 Registry 启动参数
├── data/         Core/Gateway/Config Registry、Node identity、invocation state
├── secrets/      Provider、MCP 和签名材料
├── packages/     immutable Skill/MCP package bytes（实际位于 data/packages）
├── artifacts/    用户 Artifact bytes
├── agents/       Runtime 私有 Session/Context
└── logs/
```

关键原则：

- LLM API Key、MCP credential、模型路径和 Node private key 留在 Node；
- `node-hub.json` 只保存 Hub enrollment 与固定的 Hub public identity；
- `remote-model-invocations.db` 保存幂等执行和崩溃恢复事实；
- 文件权限必须保持私有；
- 不把整个 Runtime Root 无差别同步到 Hub。

Windows 交互式 CLI 的平台默认 Root 仍可位于 `%LOCALAPPDATA%\Knoa\Node`，但标准 WinSW 部署显式使用
`C:\ProgramData\Knoa\Node`。PID 与 `service.stop` 位于所选 Node Root 的 `run` 子目录，因此多个配置
不会共享一个全局停止文件。Node 配置、identity、Hub enrollment、Secret、Task、Conversation 和执行
状态都属于这个 Windows Node。

### 11.2 Hub

Hub Root 默认是 `~/.knoa/hub`：

```text
~/.knoa/hub/
├── hub.db
└── hub-signing.key
```

`KNOA_HUB_OWNER_TOKEN` 必须通过 Secret 环境注入，长度至少 32 个字符，不写入镜像、仓库、普通配置
导出或日志。

Hub signing key 与数据库必须作为同一恢复单元治理。只恢复数据库但生成新 signing key 会破坏 Node
对 Hub identity 的固定；只恢复 signing key 而丢失数据库也无法恢复 membership、Node directory 和
未过期授权事实。

Hosted Single-Node Root 建议为 `~/.local/share/knoa/hosted-hub`：

```text
hosted-hub/
├── control.db
├── hub-signing.key
├── mobile-releases/android/
│   ├── latest.json
│   ├── <version_code>.json
│   └── knoa-<version_code>.apk
└── tenants/<workspace_id>/hub.db
```

`control.db` 保存 Account、登录身份、scrypt 密码摘要、Session digest、Workspace、Membership 和
一次性 grant digest，不保存 Session/grant 明文。Hosted Android APK 是平台级发布资产，不属于任一
Workspace 或 Node。控制库、共享 signing key、Android release tree 与全部 tenant database 是一个
恢复单元；缺失其中任一部分都不能宣称完成 Hosted 恢复。

Hosted Account App 从 Hub 根路径查询 Android release metadata，并从公开、内容寻址的 immutable
URL 下载 APK；`/downloads/android/latest.apk` 只提供稳定人工安装入口。No-Hub 与 Self-hosted App
仍从 Node Gateway 查询本地 release。Hosted 查询失败时禁止静默回退到 Node，避免同一帐号出现两个
互相竞争的版本权威。

## 12. 备份、恢复与升级

### 12.1 当前事实

仓库当前已有 Node 服务脚本、Cloudflare 示例、Hosted Single-Node user systemd unit，以及使用
SQLite backup API 的 Hosted 一致性备份/恢复命令。仍未提供多实例容器编排、自动异地复制和
SLO-backed 灾备，因此当前是“单节点 Hosted MVP 可部署”，不是“HA Hosted 平台已经交付”。

### 12.2 Hosted Hub 从 Linux 迁移到 Windows

迁移改变 Hub 的运行主机，不改变 Hub identity、Account、Workspace 或已有 Node identity。正确顺序是：

1. 保持旧 Linux Node 运行，但停止旧 Hosted Hub 的写入入口；
2. 在旧主机使用一致性备份命令导出 `control.db`、`hub-signing.key`、全部 tenant DB、Android release
   tree 与 manifest；
3. 将完整备份目录复制到 Windows，以 `Install-Knoa.ps1 -HostedBackupPath ...` 恢复到空 Hub Root；
4. 先在 Windows 本机验证 `127.0.0.1:9529`，确认 Hub signing identity 与数据库完整；
5. 停止旧主机 cloudflared connector，再启动 Windows 上的同一个 Tunnel connector；
6. 验证 canonical Hub 域名、Account 登录、Workspace directory 和旧 Linux Node 重连；
7. 使用新的 enrollment grant 将 Windows Node 加入目标 Workspace。

禁止让旧、新两份 Hosted Hub 数据库同时通过同一 canonical 域名提供写服务。Windows Node 是新增的
执行节点，不应复制 Linux Node 的 `node-identity.json`、Conversation/Task 数据库或本地 Secret。

### 12.3 最低备份要求

Self-hosted V1 至少分别备份：

- Node：Runtime Root 中的数据库、identity、config revision、Secret、Package 和 Artifact；
- Self-hosted Hub：`hub.db` 与 `hub-signing.key`；
- Hosted Hub：`control.db`、`hub-signing.key`、全部 tenant DB 与 Android release tree；
- 明文 Secret 备份必须额外加密，不能进入普通日志或未加密对象存储；
- 备份必须在隔离临时目录验证可读取和完整性；
- 恢复演练必须验证旧 Node 仍接受恢复后的 Hub identity。

### 12.4 建议升级顺序

兼容升级：

```text
Hub -> canary Node -> remaining Nodes -> Mobile App minimum-version policy
```

破坏性协议升级必须先提供有界的双版本握手或明确停机迁移方案。不能让 Hub 先删除仍被旧 Node 使用的
Relay frame 或 Ticket 版本。

Node 升级不能自动重放 `outcome_unknown` 的 Tool 或远程模型副作用；恢复仍遵守现有幂等和显式确认
规则。

## 13. 故障语义

| 故障 | 必须保持可用 | 允许暂时不可用 | 禁止行为 |
| --- | --- | --- | --- |
| Hub 离线 | Node 本地执行、已有 direct pairing | 新 enrollment、ticket、directory、Relay | Node 删除本地数据或停止 Core |
| Relay 断开 | Node 本地执行、direct 路径 | Relay fallback | 降级为 Hub 可读明文代理 |
| 某 Node 离线 | 其他 Node、本地 Agent、Workspace 最后投影 | 该 Node 的 Conversation、Task、模型/Tool/Artifact | Hub 伪造成功结果或隐式换 Node |
| 远程 MCP Node 离线 | Task Definition 与其他资源 | 依赖该 MCP Deployment 的执行 | 静默选择另一 MCP 或复制 Secret |
| 本地 LLM 不可用 | Node Core、其他 Provider | 绑定该 Deployment 的调用 | Hub 接管 Secret 或模型执行 |
| Hub DB 损坏 | Node 本地事实 | Hub control plane | 静默新建同 ID Hub 并替换 key |
| Node 崩溃于远程调用中 | 持久 invocation reconciliation | 未确认结果 | 使用新 invocation ID 自动重放 |
| App 离线 | 已 placement 的 Node TaskExecution 继续运行 | 实时交互 | 将 App 当作 Task 状态权威 |

## 14. 观测与健康检查

### 14.1 Node

最低观测项：

- Core、Gateway、Channel、MCP、Agent Runtime 和 Provider health；
- active/applied Config Generation；
- Hub enrollment、Relay connection、last error 和 reconnect generation；
- ModelDeployment health、capacity、queue depth 和 observation epoch；
- Remote Invocation 状态、latency、transport 和 outcome_unknown；
- Task、ToolStep、Approval、Artifact 和审计事件。

### 14.2 Hub

最低观测项：

- `/health`、Hub ID 和进程启动时间；
- SQLite 可写和 WAL 状态；
- active Node/Client Relay connection 数；
- enrollment、ticket issuance、ticket rejection 和 replay rejection；
- frame bytes、reset、backpressure 和连接替换；
- Node presence age 和 DeploymentObservation age；
- 日志不得包含 owner token、ticket secret、Node private key 或 Relay 明文。

当前 `/health` 只证明进程和 HubService 基本可响应，不等同于完整 readiness。生产部署前应增加数据库、
签名材料和 Relay admission 的 readiness 检查。

## 15. Relay 何时独立部署

当前不拆分 Relay，原因是个人 Self-hosted V1 的 Hub 与 Relay 负载很小，同进程能降低部署和一致性
复杂度。

只有出现以下真实需求时才拆分：

1. 单进程 WebSocket 连接或带宽成为瓶颈；
2. Hub control API 与 Relay data plane 需要不同扩缩容策略；
3. Hosted 多区域接入需要就近 Relay；
4. Relay 滚动升级不能中断 Hub control plane；
5. 已有共享 session ownership、connection routing 和 backpressure 设计。

拆分后的依赖方向必须是：

```text
HubService issues/verifies bounded ticket metadata
          │
          v
Relay fleet routes opaque frames
          │
          v
Node Hub Edge terminates end-to-end session
```

Relay 仍不能获得 Workspace 明文、Node Secret、模型 Prompt、模型响应或 Core repository 访问权。

不接受仅为了“看起来像云原生”而提前引入 Redis、Kafka、Service Mesh 或 Kubernetes Operator。

## 16. 当前生产化缺口

### 16.1 Self-hosted V1 发布前必须补齐

- `knoa-hub` 的生产 Dockerfile 或 systemd unit；
- 单实例 Docker Compose/安装脚本；
- TLS reverse proxy 示例和 WebSocket 配置；
- Hub/Node 数据卷与权限规范；
- SQLite 一致性备份和恢复演练脚本；
- health/readiness、日志轮转和资源限制；
- Node/Hub/App 协议兼容矩阵；
- 明确禁止 Hub 多 worker 的启动校验或部署说明；
- 安全的 owner token 初始化与轮换流程。

### 16.2 Hosted 前额外补齐

- 把已验证的 per-Workspace 隔离迁移到生产多租户存储与完整隔离测试矩阵；
- Relay 多实例和跨实例路由；
- rate limit、abuse control、容量压测和 SLO；
- 外部验证渠道、自助 Account recovery、MFA/step-up 和 key rotation；
- metadata 隐私、数据保留与删除；
- 灾难恢复、区域故障与审计合规。

## 17. V1 推荐部署决策

当前正向且不过度设计的产品决策是：

```text
普通单机用户：No-Hub direct
个人多 Node 用户：单实例 Self-hosted HubService + Relay
个人/受控 Hosted：Hosted Hub Single-Node，单进程 `127.0.0.1:9529`
公网 HA Hosted：暂不宣称生产可用
Node：一个 ApplicationDaemon 进程
Hub/Relay：一个 knoa-hub 进程、一个 worker
LLM/MCP/Codex：按实际运行需求使用独立外部进程
Agent/Skill/Tool：保持 Node 内逻辑边界，不拆微服务
```

这满足当前 Personal Workspace + N Node 的真实需求，同时为未来 Hosted Hub 与 Relay 水平扩展保留
清晰边界，而不提前承担分布式系统复杂度。

形态 3 单节点 Hosted 的安装、帐号、Workspace、备份、systemd 和 API 入口见
`deploy/hosted-hub/README.md`。
