# Knoa Node、Agent 与共享资源架构

> 状态：权威专题设计  
> 更新：2026-08-17  
> 原则：正向设计、高内聚、低耦合、KISS、YAGNI、P2P-first

## 1. 结论

Knoa 的产品层级固定为：

```text
Account
└── Workspace
    ├── Node directory / enrollment
    ├── LLM/MCP shared service directory
    ├── ResourceGrant
    └── management projections
         │
         └── Nodes
             ├── NodeAgent
             ├── Conversation
             ├── Task
             ├── Skill content
             ├── Tool
             ├── Secret
             └── local Model/MCP Endpoint
```

Workspace 是跨 Node 的管理边界，但不是 Conversation、Task 或 Agent 的第二写权威。Node 是执行、
工作数据与本地安全资源的权威边界。

V1 只允许两种跨 Node 调用：

```text
model_inference
mcp_invoke
```

Agent 不共享，Skill 不远程执行，Built-in Tool 和 Secret 不离开 Node。

## 2. NodeAgent

不再建立独立的 `AgentDefinition`、`AgentProfile`、`RuntimeSpec`、`AgentDeployment` 与
`AgentInstance` 产品模型。它们造成对象过多、字段归属模糊和 UI 配置割裂。

一个 Node 上可运行的 Agent 用单一聚合 `NodeAgent` 表达：

```text
NodeAgent
├── agent_id / kind: knoa | codex
├── display_name / enabled / visibility
├── instructions or instructions_ref
├── model_binding
├── Skill refs
├── Tool/MCP capability ceiling
├── runtime limits
├── delegation policy
└── implementation-specific settings
```

`kind=knoa` 使用 Knoa 原生实现和明确的 Model binding；`kind=codex` 使用可信 Codex adapter，Codex
内部模型由 Codex 管理。二者共享 Conversation、Task、Approval、Stop、Artifact 与审计合同，但不强迫
共享内部编排方式。

NodeAgent 是 Node-local 配置。将 Knoa Agent 配置到 Company Node 和 Home Node，表示两个 Node 各有
一个自己的 NodeAgent，而不是部署一个 Workspace Agent 的两个实例。`knoa` 与 `reviewer_agent` 共享内置
Knoa Runtime；`codex` 使用内置 Codex Runtime Adapter。普通自定义 Agent 仍是另一个 `kind=knoa` 的
NodeAgent，不需要安装 package。

如果第三方 Agent 确实带来新的执行循环或 session 语义，其代码以签名 Runtime Extension Bundle 安装到明确
选择的 Node，并作为 Node Host 管理的独立 Worker 运行。该内部交付物不改变“Agent 不共享”的产品边界，也
不进入普通 App 导航。

## 3. Conversation 与 Task

Conversation 和 Task 都属于具体 Node：

```text
NodeConversation
├── NodeAgent binding
├── ChatTurn
└── Invocation

NodeTask
├── NodeAgent binding
├── trigger / launch policy
├── dependency requirements
└── TaskExecution / Attempt
```

创建、编辑、启动、停止和审批都必须连接权威 Node。Workspace 只保存 Node 主动上报的最小只读投影：

```text
WorkProjection
├── entity_kind: conversation | task
├── entity_id / node_id
├── title / state / progress / summary
├── source_generation / source_digest
└── projected_at
```

投影用于跨 Node 查找、状态概览与离线提示，不保存 Conversation 正文，不接受 Work 写入，也不用于
恢复 Node 数据。Node 离线时 App 可显示最后投影，但不得把投影页伪装成可编辑的 Task/Conversation。

## 4. 资源边界

| 对象 | 权威位置 | 可跨 Node 调用 | 说明 |
| --- | --- | --- | --- |
| NodeAgent | Node | 否 | 在拥有它的 Node 执行 |
| Conversation | Node | 否 | Workspace 只有只读投影 |
| Task | Node | 否 | trigger 与 execution 都归 Node |
| ModelEndpoint | Node | 是 | 需显式 `model_inference` Grant |
| MCPEndpoint | Node | 是 | 需显式 `mcp_invoke` Grant，默认关闭 |
| Skill | Node 同步内容 | 否 | 可同步，不是远程服务 |
| Built-in Tool | Node | 否 | 与 OS、文件、桌面权限耦合 |
| Secret | Node | 否 | Hub、Relay、App 不持有明文 |

Workspace 中的 Model/MCP Resource 是共享服务目录记录，不是服务实例。实际 Endpoint、凭据、进程、
容量和健康始终属于提供方 Node。

```text
Workspace Resource
  -> Deployment(target Node)
  -> Node-local Endpoint
  -> EndpointObservation
  -> ResourceGrant(caller Node, capability)
```

云端 LLM API 默认在每个使用它的 Node 单独配置 API Key。只有集中出口、集中凭据或本地 GPU 模型确实
需要共享时，才把提供方 Node 的 ModelEndpoint 发布给其他 Node。

## 5. 双 Node 目标形态

```text
Company Node
├── Knoa NodeAgent
├── optional Codex NodeAgent
├── Qwen 3.5 4B ModelEndpoint
├── Jira MCPEndpoint
├── GitLab MCPEndpoint
└── company Conversations / Tasks

Home Node
├── Knoa NodeAgent
├── optional local/cloud ModelEndpoint
├── home-local Tools
└── home Conversations / Tasks
```

当前共享矩阵：

| 服务 | Company Node | Home Node |
| --- | --- | --- |
| Company Qwen 3.5 4B | 本地调用 | 可选远程调用 |
| Jira MCP | 本地调用 | 默认无 Grant |
| GitLab MCP | 本地调用 | 默认无 Grant |
| NodeAgent | 各自本地执行 | 不跨 Node 调用 |

Home Node 上的 Task 可以依赖 Company Qwen Endpoint，但 Task 本身仍在 Home Node 执行。Company Node
离线时，该依赖按 Task 策略等待或失败；App 与 Workspace 管理不能因此卡死。

消费侧配置遵循本地所有权：Company Node 只负责“共享给 Home Node”，Home Node 在自己的模型页将该
Workspace 模型加入本地目录，并由自己的 Knoa Agent 选择使用。Workspace Grant 不自动改写 Home Node
Agent，也不让 Codex Runtime 改用 Platform Model Provider。

## 6. P2P-first

当前已交付数据路径按以下顺序解析：

```text
1. Node 声明并签名的 direct Gateway candidate
2. direct HTTPS 有界连接尝试
3. Hub 签发短期 ticket
4. Relay 端到端加密回退
```

V1 已支持“显式 direct candidate + Relay fallback”。由于真实远程使用已证明 Relay 延迟和带宽成为问题，
[跨平台 Runtime 实施计划](./knoa-cross-platform-runtime-migration-plan.md) Phase 5 将增加 STUN/ICE/NAT traversal。
该增强仍保持同一 Node identity、ticket、Invocation ID 和 Relay fallback，不建立第二套远程执行协议。

Hub 负责 Account、Workspace、Node directory、presence、ResourceGrant、ticket 与连接协调。Relay 只转发
端到端加密 frame，不解密 Prompt、模型响应或 MCP payload，不拥有 Invocation 状态。Node 主动连接 Hub，
普通用户只需要 Hub 域名，不需要为每个 Node 配置公网域名。

一次请求从 direct 回退 Relay 时必须保持同一业务 request/invocation id，不能重复执行。后续请求可重新
尝试 direct；Relay 只使用短暂冷却，不能成为长期默认路径。

## 7. App 信息架构

```text
Account
└── Workspace
    ├── Nodes
    │   └── Node
    │       ├── Agents
    │       ├── Conversations
    │       ├── Tasks
    │       ├── Models / MCP
    │       ├── Skills / Tools / Secrets
    │       └── Settings
    ├── Shared Services: LLM / MCP
    ├── Members
    └── Activity projections (read-only)
```

默认可直接进入上次 Workspace、Node 或 Conversation，但导航层级不能被省略，任何 Node 页面都能返回
Workspace。Node 离线不阻断登录、Workspace 切换、Node directory 或最后投影查看。

普通 UI 不显示 Package、digest、RuntimeSpec 或 Profile。用户看到的是“添加 Agent、配置模型、添加 MCP、
同步 Skill、选择 Node、开启共享”。内容寻址存储、签名、安装 artifact 与 digest 只作为内部实现和高级
诊断信息存在。

## 8. 配置生效

- Workspace 目录和 Grant 是控制面配置，不需要重启；
- NodeAgent、模型、MCP、Skill 和 Policy 通过 Node 配置草稿校验并发布；
- 能安全替换的配置对新 Invocation 热生效，运行中的 Invocation 保留开始时快照；
- MCP 进程、模型引擎或 native binary 无法安全替换时，只重启目标组件；
- Node 网络监听、TLS 或 Runtime 升级需要 Node 重启时，UI 明确显示；
- 不建立用户可见的复杂 revision/rollback 产品；失败时保留当前已应用配置并允许重新发布。

## 9. 架构不变量

1. Workspace 是管理边界，Node 是 Work、Agent 与执行权威；
2. Conversation、Task、NodeAgent 不在 Workspace 双写；
3. V1 只共享 ModelEndpoint 与 MCPEndpoint；
4. MCP 默认不共享；
5. Skill 同步内容，Tool 与 Secret 保持 Node-local；
6. Direct/P2P 优先，Relay 回退；
7. Hub 离线不影响 Node 本地执行；
8. Node 离线不阻断 Account/Workspace 管理；
9. Package 不进入普通产品导航；
10. 不为 Agent sharing、通用 Remote Tool、Marketplace 或自动 placement 提前建设抽象；Runtime Extension
    先只支持管理员向指定 Node 导入签名 Bundle。
