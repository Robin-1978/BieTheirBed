# Knoa 产品领域架构

> 状态：产品领域权威文档
> 更新：2026-08-17

## 1. 聚合与所有权

```text
Account
├── Membership
├── AppInstallation
└── Workspace
    ├── Membership
    ├── NodeEnrollment / NodeDirectory
    ├── SharedServiceDirectory
    │   ├── ModelResource
    │   └── MCPResource
    ├── ResourceGrant
    └── WorkProjection (read-only)

Node
├── NodeAgent
├── Conversation
├── Task
├── ModelEndpoint
├── MCPEndpoint
├── SkillContent
├── BuiltinTool
├── Secret
├── Approval
└── Artifact
```

Account 是身份与成员关系边界。Workspace 是用户管理多 Node、共享 LLM/MCP 服务和权限的协作边界。
Node 是 Agent、Conversation、Task、执行数据和本地 Secret 的权威边界。

## 2. 写权威

| 领域对象 | 写权威 | Workspace 是否保存副本 |
| --- | --- | --- |
| Account / Membership | Hosted Hub 或 Self-hosted Hub | 不适用 |
| Node enrollment / directory | Hub | 是权威 |
| NodeAgent | Node | 否 |
| Conversation / ChatTurn / Invocation | Node | 仅最小只读投影 |
| Task / Trigger / Execution / Attempt | Node | 仅最小只读投影 |
| Model/MCP shared directory / Grant | Workspace | 是权威 |
| Model/MCP Endpoint / health | Node | 仅观察投影 |
| Skill / Tool / Secret | Node | 不保存运行副本；Secret 永不上传 |

不得由 App 把 Task 或 Conversation 同时写入 Node 与 Workspace。Workspace 投影只能由权威 Node 以有序、
签名、幂等方式上报。

## 3. NodeAgent

`NodeAgent` 是单一 Agent 配置聚合，替代三段式 RuntimeSpec/Profile/Definition：

```text
NodeAgent
├── identity: agent_id, kind, display_name
├── behavior: instructions, Skill refs
├── model_binding
├── capability and policy ceilings
├── runtime limits / delegation
└── implementation settings
```

NodeAgent 不是执行实例。每次 Conversation Turn、Task Attempt 或系统调用创建 Invocation，并冻结当次
解析后的 policy/config digest。配置发布只影响之后的新 Invocation。

## 4. Work

### 4.1 Conversation

Conversation 在一个明确 Node 上创建并绑定一个 NodeAgent。Conversation 正文、Turn、Invocation、Approval
和 Artifact 引用只由该 Node 保存。App 可以从 Workspace 投影定位它，再连接权威 Node 查看和操作。

### 4.2 Task

Task 是 Node-local 的持久工作定义，包含目标、NodeAgent、触发策略、依赖、通知和执行历史。创建 Task
时用户已经位于目标 Node，因此不需要额外 `TaskDeployment`。上线、暂停、恢复、执行和删除都是同一
Node 的操作。

Task 依赖可以引用：

- 本 Node 的 Model/MCP/Tool；
- Workspace 显式授权给本 Node 的远程 Model/MCP Endpoint。

## 5. 共享服务

Workspace 只管理两类可跨 Node 调用的服务：

```text
ModelResource -> Deployment -> ModelEndpoint -> model_inference Grant
MCPResource   -> Deployment -> MCPEndpoint   -> mcp_invoke Grant
```

Definition/Deployment 是控制面；Endpoint/Invocation 是数据面。目标 Node 对每次调用做最终授权、generation、
健康、容量、deadline 和幂等校验。

Agent、Conversation、Task、Skill、Built-in Tool 和 Secret 不进入 ResourceGrant target。

## 6. 状态与配置

产品只展示：草稿、校验失败、发布中、已生效、需重启、应用失败。内部 revision/digest 用于并发控制、
审计与幂等，不成为普通用户概念。

配置应用策略：

| 变化 | 行为 |
| --- | --- |
| Prompt、Policy、Skill refs、模型参数 | 新 Invocation 热生效 |
| Provider/MCP endpoint、Secret version | preflight 后替换目标组件 |
| MCP command、本地模型进程 | 组件级重启 |
| Gateway 监听、TLS、核心进程升级 | Node 重启 |

## 7. 删除的旧模型

以下不再是产品领域对象：

- AgentDefinition；
- AgentProfile；
- RuntimeSpec；
- AgentDeployment / AgentInstance；
- Workspace TaskDefinition / TaskDeployment；
- Workspace-owned Conversation；
- 通用 `agent_invoke`；
- 用户可见 Package 目录。

## 8. 不变量

1. 一个事实只有一个写权威；
2. Workspace 不拥有 Work 正文；
3. Node 离线时投影只读；
4. Secret value 永不进入 Hub、Relay、App 投影或配置 diff；
5. 跨 Node 能力必须是 typed capability 与显式 Grant；
6. 共享可见性、管理权限和调用权限是三个独立维度；
7. V1 不做自动 placement、跨账户共享和 Agent sharing。
