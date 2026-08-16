# Knoa 部署架构

> 状态：当前 V1 可部署架构与后续生产演进边界
>
> 更新日期：2026-08-16
>
> 范围：Mobile App、Node、HubService、Relay、Agent Runtime、LLM、Skill、Tool、MCP、网络、安全、存储、故障与运维拓扑
>
> 权威关系：模块职责以 `knoa-module-architecture.md` 为准；Workspace 资源归属与跨 Node 调用以 `knoa-workspace-resource-fabric-design.md` 为准；本文是进程、网络和运维部署的权威入口
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

- Agent 是 Node 内的稳定逻辑身份，由 RuntimeSpec 与 Profile 组合；
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
- 未 enrollment Hub 时保持禁用，不影响 Node 本地能力和 direct pairing；
- Hub 或 Relay 断开时进行有界重连，不能阻塞 Core 启动；
- 删除 enrollment 时停止 Relay connector 并移除本地 Hub pin；
- Hub identity 不匹配时 fail closed，不自动接受新 Hub key；
- Relay 收到的明文请求只能在端到端解密后进入现有 Gateway 认证和业务路由。

## 5. 当前部署单元与独立性

| 部署单元 | 当前入口 | 是否独立部署 | 当前说明 |
| --- | --- | --- | --- |
| Knoa Node | `knoa --serve` 或 `python -m knoa_platform.service` | 是 | 完整执行服务器，拥有 Core、Gateway、Agent、Tool、Extension 和本地状态 |
| HubService + Relay | `knoa-hub` | 是 | 当前作为一个进程、一个 HTTP/WSS 监听器部署 |
| RelayBroker | 无独立 CLI | 否 | 代码模块独立，但由 `HubApplication` 同进程创建 |
| Mobile App | Android App | 是 | 客户端，不拥有服务端业务事实 |
| Local LLM Server | llama.cpp/Ollama/OpenAI-compatible server | 是 | 独立进程或外部服务；Secret、模型路径和执行仍归目标 Node |
| MCP Server | stdio 或 streamable HTTP | 可选 | 可由 Node 管理本地进程，也可远程独立部署 |
| Codex App Server | Codex Runtime 自有入口 | 是 | 受信 Runtime adapter 使用，不进入 Knoa 原生模型 Provider |
| Agent/Profile/Skill/Tool | 无独立进程入口 | 否 | Node 内逻辑配置或扩展，不为追求“微服务化”单独部署 |

### 5.1 当前 Hub/Relay 的硬限制

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

### 6.2 Self-hosted Hub + Relay（当前推荐的多 Node V1）

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
- direct endpoint 可作为优化，但不能成为多 Node 的部署前置条件；
- App 先访问 Hub 选择 Node 和获取短期 ticket，再 direct 优先、Relay fallback；
- Node-to-Node 共享 LLM 同样 direct 优先、Relay fallback，并复用同一 invocation ID；
- Hub/Relay 单实例部署，不启用多 worker。

### 6.3 Knoa Hosted Hub（形态 3 仿真实现，非生产交付）

```text
Knoa Hosted Simulation :9540
├── Hosted Account bootstrap / token digest
├── shared Hosted Hub identity
└── /workspaces/{workspace_id}
    ├── isolated Workspace SQLite
    ├── isolated HubApplication
    └── isolated in-process RelayBroker
```

Hosted 形态不是给每个用户启动一套物理 Hub 进程，而是共享基础设施中的逻辑 Workspace 隔离。

当前已交付 `hosted_simulation` composition，用真实 HTTP 路由、账户 token、独立 tenant database 和
RelayBroker 验证形态 3 的关键边界：

- Hosted 根服务拥有 Account、token 摘要和 Personal Workspace 映射；
- 同一 Hosted Hub 的 Workspace 共享 `hub_id` 与 signing identity；
- 每个 Workspace 使用独立 `HubApplication`、SQLite 和 Relay 连接表；
- Account token 只能进入自己的 `/workspaces/{workspace_id}`；
- Node enrollment、directory、资源授权和 Relay 状态不能跨 Workspace；
- App 可创建仿真账户并自动切换到返回的 Workspace URL。

该实现用于验证架构、协议与 App 流程，不等同于生产 Hosted。生产仍未完成：

- Account recovery 与 step-up authentication；
- 生产多租户数据库、Relay 跨实例路由与容量治理；
- abuse control、计费、SLO 和数据保留；
- Hosted Secret/Key 管理与完整运维体系。

因此当前代码只能以“Hosted Hub Simulation”名义部署。

## 7. Node 进程拓扑

当前 Node 由一个 `ApplicationDaemon` 统一管理生命周期：

```text
Knoa Node process
├── CoreDaemon
│   ├── CoreHost / CoreServer
│   ├── Conversation / Task / Automation / Artifact
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

### 9.3 Node B 调用 Node A 的本地 LLM

```text
Agent on Node B
  -> workspace_remote Model Provider
  -> Hub ResourceGrant + Invocation Ticket
  -> direct Node A when available
  -> otherwise opaque Relay with same invocation_id
  -> Node A admission + persistent idempotency record
  -> local ModelDeployment
  -> encrypted response to Node B
```

Node A 是执行结果权威。Hub 只保存授权、票据元数据和非权威 observation，不保存或解密 Prompt、
模型响应、API Key 或模型文件。

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

形态 3 仿真部署固定建议使用 `127.0.0.1:9540`，避免与 Node Capability MCP Host 和 Self-hosted
Hub 的默认端口冲突。公网入口仍应由独立 TLS ingress 提供，不直接暴露 Uvicorn listener。

### 10.3 TLS 原则

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

Hosted Simulation Root 建议为 `~/.local/share/knoa/hosted-hub`：

```text
hosted-hub/
├── accounts.db
├── hub-signing.key
└── tenants/<workspace_id>/hub.db
```

`accounts.db` 只保存 access token 的 SHA-256 摘要，不保存 token 明文。账户库、共享 signing key 与
全部 tenant database 是一个恢复单元；缺失其中任一部分都不能宣称完成 Hosted 恢复。

## 12. 备份、恢复与升级

### 12.1 当前事实

仓库当前已有 Node 服务脚本、Cloudflare 示例和 Hosted Simulation user systemd unit；仍未提供生产级
Hub Dockerfile、Docker Compose、自动备份与恢复制品。因此当前是“仿真部署可重复”，不是“生产
Hosted 部署包已经交付”。

### 12.2 最低备份要求

Self-hosted V1 至少分别备份：

- Node：Runtime Root 中的数据库、identity、config revision、Secret、Package 和 Artifact；
- Hub：`hub.db` 与 `hub-signing.key`；
- 明文 Secret 备份必须额外加密，不能进入普通日志或未加密对象存储；
- 备份必须在隔离临时目录验证可读取和完整性；
- 恢复演练必须验证旧 Node 仍接受恢复后的 Hub identity。

### 12.3 建议升级顺序

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
| 某 Node 离线 | 其他 Node、本地 Agent | 该 Node 的模型/Tool/Artifact | Hub 伪造成功结果 |
| 本地 LLM 不可用 | Node Core、其他 Provider | 绑定该 Deployment 的调用 | Hub 接管 Secret 或模型执行 |
| Hub DB 损坏 | Node 本地事实 | Hub control plane | 静默新建同 ID Hub 并替换 key |
| Node 崩溃于远程调用中 | 持久 invocation reconciliation | 未确认结果 | 使用新 invocation ID 自动重放 |
| App 离线 | Node Task 继续运行 | 实时交互 | 将 App 当作 Task 状态权威 |

## 14. 观测与健康检查

### 14.1 Node

最低观测项：

- Core、Gateway、Channel、MCP、Agent Runtime 和 Provider health；
- active/applied Config Revision；
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
- Account recovery、step-up 和 key rotation；
- metadata 隐私、数据保留与删除；
- 灾难恢复、区域故障与审计合规。

## 17. V1 推荐部署决策

当前正向且不过度设计的产品决策是：

```text
普通单机用户：No-Hub direct
个人多 Node 用户：单实例 Self-hosted HubService + Relay
架构/产品联调：Hosted Hub Simulation，单进程 `127.0.0.1:9540`
公网 Hosted 服务：暂不宣称生产可用
Node：一个 ApplicationDaemon 进程
Hub/Relay：一个 knoa-hub 进程、一个 worker
LLM/MCP/Codex：按实际运行需求使用独立外部进程
Agent/Skill/Tool：保持 Node 内逻辑边界，不拆微服务
```

这满足当前 Personal Workspace + N Node 的真实需求，同时为未来 Hosted Hub 与 Relay 水平扩展保留
清晰边界，而不提前承担分布式系统复杂度。

形态 3 仿真的安装、环境变量、systemd unit 和 API 入口见 `deploy/hosted-hub/README.md`。
