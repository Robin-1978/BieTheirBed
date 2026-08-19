# Knoa Workspace LLM/MCP 共享服务架构

> 状态：跨 Node 资源共享权威设计
> 更新：2026-08-20

## 1. 范围

Workspace Resource Fabric 只解决 LLM 与 MCP 服务发现、部署观察、授权和跨 Node 调用。它不承载 Agent、
Conversation、Task、Skill、Tool 或 Secret sharing。

```text
Workspace
├── ModelResource
├── MCPResource
├── Deployment(target Node)
├── EndpointObservation
└── ResourceGrant
    ├── model_inference
    └── mcp_invoke
```

## 2. 控制面与数据面

Hub 控制面保存逻辑 Resource、目标 Deployment、Grant 和 Node 上报的 Observation。目标 Node 保存实际
Endpoint、Secret、容量、调用数据和审计正文。

```text
Caller Node
-> resolve ResourceGrant
-> obtain short-lived signed ticket
-> direct/P2P target Endpoint
-> Relay fallback
-> target Node final authorization
-> invocation
```

Hub 不转发默认业务 payload；Relay 只转发 E2E frame。

## 3. Grant

Grant 最少绑定 workspace、caller Node、target deployment、capability、expiry、deadline 与约束。目标 Node
还必须校验 applied resource digest/generation、Endpoint health、capacity、nonce 和 idempotency key。

可见不等于可调用；管理权限也不等于调用权限。

## 4. Model

Company Node 的 Qwen 3.5 4B 可发布为 ModelEndpoint，并显式授予 Home Node `model_inference`。权重、GPU、
进程和日志不离开 Company Node。

共享与使用是两个不同职责：提供方 Node 决定授权范围，调用方 Node 决定是否把获授权模型加入本地模型
目录，以及绑定给哪个 Knoa Agent。提供方不得远程修改调用方的 Agent 配置。

```text
Provider Node: Share Model -> choose allowed Nodes
Workspace: ModelResource + Deployment + ResourceGrant
Caller Node: Add Workspace Model -> bind local Knoa Agent
```

ModelResource 可以由提供方 Node 创建，也可以由 Workspace 管理员预先定义。后一种情况下，Node 只拥有
目标 Deployment 的运行与观测职责；Hub 仅在上报的模型协议、identity 和 capability 与 Workspace 定义一致、
且 Deployment 已指向该 Node 时接受上报，不把 Node 提升为资源定义所有者。

App 在调用方 Node 的“模型”页只显示有效 `model_inference` Grant 对应的模型。用户点击“添加”后，Node
创建只含 `remote_deployment_id` 的 `workspace_remote` Provider；Deployment ID、路由地址和 API Key 均不
要求用户输入。Agent 绑定仍是调用方 Node-local 配置。

云端 Provider 默认每个 Node 各持自己的 Key。共享云 ModelEndpoint 只用于集中凭据/出口等明确场景。

## 5. MCP

MCP 默认 Node-local。发布共享 MCPEndpoint 时，Secret 仍留在 Provider Node，Caller 只得到结构化结果。
当前 Jira/GitLab MCP 不授权 Home Node。

## 6. Transport

Node presence 上报签名连接 candidate。Hub 在每次短期 Resource Ticket 中返回当前目标 Node 的 candidate；
Caller 不把该地址长期写死在模型配置中。Caller 使用同一 Invocation ID 在 transport 间转换，不得重复执行。

当前已交付显式 Direct + WebRTC ICE/STUN P2P + Relay fallback。Caller 先用短期 Invocation Ticket 在认证
Resource Relay 中交换 offer/answer，成功后复用 DataChannel；对称 NAT、防火墙或连接故障时回落同一 E2E
Relay，并设置重试冷却。这只替换 transport，不改变 ResourceGrant、目标 Node 最终授权、Invocation ID、
持久化幂等记录或执行权威。

## 7. 不变量

1. generic Workspace Resource/Deployment kind 只允许 `model | mcp`；
2. ResourceGrant capability 只允许 `model_inference | mcp_invoke`；
3. Endpoint 与 Secret 归目标 Node；
4. Agent/Task/Conversation 不进入 fabric；
5. 同一个 invocation_id 在 transport 切换时不得重复执行；
6. Relay 故障不影响 Node 本地调用；
7. 不提前实现自动 placement、跨账户共享或通用 service mesh。
