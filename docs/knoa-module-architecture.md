# Knoa 完整模块架构

> 状态：模块边界权威文档
> 更新：2026-08-17

## 1. 可部署单元

```text
Knoa App
   │ Hub control plane / direct or Relay Node data plane
   v
Hosted or Self-hosted Hub
├── Account / Workspace
├── Node directory / presence
├── LLM/MCP directory / ResourceGrant / ticket
└── RelayBroker

Knoa Node
├── Secure Gateway
├── Core application services
├── NodeAgent host
├── Conversation / Task
├── Configuration control
├── Capability Gateway
├── Model/MCP endpoints
└── local persistence / Secret
```

Hub 与 Relay 当前同进程部署，但模块边界独立。Node、Hub 和 App 可独立构建与部署。Agent、Skill、Tool
不是为追求微服务化而拆出的服务器。

## 2. Node 内模块

| 模块 | 职责 | 不拥有 |
| --- | --- | --- |
| `gateway` | App/CLI 的安全 HTTP surface、认证、streaming、Hub edge | 领域存储 |
| `agents` | NodeAgent typed config、解析、Invocation policy | Account/Workspace |
| `agent_runtime` | Runtime SPI、session binding、generation 与执行编排 | Product navigation |
| `conversations` | NodeConversation、Turn、消息与 live control | Workspace 投影写入 |
| `tasks` | NodeTask、trigger、execution、attempt、通知 | TaskDeployment |
| `configuration` | Draft、校验、impact、publish、apply | 独立业务配置真相 |
| `models` / providers | 本地/云端/远程模型适配 | Agent 身份 |
| `mcp` / extensions | Skill 内容、MCP host/proxy 与检查 | 任意原生代码信任 |
| `tools` | Built-in Tool registry 与实现 | 跨 Node Remote Tool |
| `capability_gateway` | capability、policy、approval、budget、审计 | Runtime-native 行为实现 |
| `node_hub` | enrollment、presence、direct candidate、Relay tunnel | Conversation/Task/Secret |

## 3. Hub 内模块

| 模块 | 职责 |
| --- | --- |
| Hosted account | 注册、登录、session、Workspace membership |
| Hub application | Workspace-scoped API 与部署模式 |
| Hub repository | Node、共享 LLM/MCP、Grant、投影、ticket |
| Hub service | 签名、身份验证、授权与短期 ticket |
| Relay broker | 连接注册、背压和 opaque frame 转发 |

Relay 不调用 Node 领域 service，不解析业务 payload，不持有模型或 MCP Secret。

## 4. Agent 执行边界

```text
Conversation Turn / Task Attempt
  -> resolve NodeAgent
  -> freeze ResolvedInvocationPolicy(node_agent_digest)
  -> AgentRuntime
  -> Capability Gateway for Platform Tool/MCP effects
  -> events / approvals / artifacts
```

`NodeAgentResolver` 只解析与校验配置；`AgentManager` 管理活跃 Runtime generation；Runtime 私有 session
状态归具体 Agent implementation。不得使用 agent 名称分支推断 capability 或 transport。

## 5. 配置依赖方向

```text
UI / CLI / YAML import
        -> ConfigurationService
        -> typed ManagedConfig
        -> validator / impact planner
        -> generation applier
        -> affected Node components
```

所有入口共享一套 schema、校验、Secret redaction、publish 与审计。YAML 是导入/导出格式，不是唯一管理
界面；App 是主要配置客户端。

## 6. 网络路径

```text
Direct request ───────────────┐
                              ├─> SecureGatewayAdapter.app -> Core
Relay decrypted request ──────┘
```

Node–Hub Edge 只做协议适配，因此 direct 和 Relay 不产生两套业务 controller。Node presence 上报签名的
`direct_gateway_url`；App 先做有界 direct 尝试，失败后使用 Hub ticket 建立 E2E Relay session。

## 7. 依赖规则

1. Hub 不依赖 Node Core 实现；
2. Relay 不依赖领域模块；
3. Runtime contracts 不依赖 UI、Hub 或具体 Agent；
4. Agent implementation 通过 SPI 接入，不越过 Capability Gateway 执行 Platform/MCP 副作用；
5. App 不直接写 Workspace 投影；
6. Task 不依赖 Workspace Deployment；
7. Workspace generic deployment kind 只允许 `model | mcp`；
8. 包存储只作为内部导入与内容完整性实现，不进入普通产品导航。

## 8. 目录映射

```text
src/knoa_platform/hub/             Hub control plane + Relay
src/knoa_platform/node_hub.py      Node–Hub edge
src/knoa_platform/gateway/         Node secure API
src/knoa_platform/agents/          NodeAgent and invocation policy
src/knoa_platform/agent_runtime/   Runtime orchestration
src/knoa_platform/tasks/           NodeTask
src/knoa_platform/conversations/   NodeConversation
src/knoa_platform/configuration/   Draft/publish/apply
src/knoa_platform/tools/           Node-local tools
src/knoa_agent/                    Knoa Agent implementation
src/knoa_codex_agent/              Codex adapter
apps/knoa-mobile/                  Account -> Workspace -> Node App
```
