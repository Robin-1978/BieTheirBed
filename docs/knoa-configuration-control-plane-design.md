# Knoa 配置控制面与管理页面设计

> 状态：权威设计
> 更新：2026-08-17

## 1. 边界

Workspace 控制面只管理 Node enrollment、共享 Model/MCP 目录、Deployment、Grant 与投影。Node 配置控制面
管理 NodeAgent、Provider/Model、MCP、Skill、Tool policy、Secret binding 与 Node 网络配置。

```text
Node Console / App / CLI / YAML import
  -> ConfigurationService
  -> Draft
  -> typed validation + secret redaction + preflight
  -> impact plan
  -> publish
  -> component apply / Node restart
```

配置页面是主要入口，YAML 只是导入导出格式。Node Console 承担 endpoint、API Key、MCP command、本地路径和
深度诊断；App 承担日常选择、绑定、状态与快捷操作。任何入口都不能绕开同一套校验、发布、审计和应用机制。

## 2. ManagedConfig

```text
ManagedConfig
├── agents: default_agent + NodeAgent map
├── providers / models / default_model
├── model_deployments
├── skills
├── mcp_servers
├── policy / approval / delegation
└── operational Node settings
```

Secret value 不进入 ManagedConfig；只保存 secret reference 与 version。配置读取、diff、审计和 Node apply
都必须 redacted。

## 3. 生效策略

| 分类 | 示例 | 应用 |
| --- | --- | --- |
| hot | Prompt、Policy、Skill refs、模型参数 | 新 Invocation 使用新 generation |
| component replace | Provider endpoint、MCP config、Secret version | preflight 后切换组件 |
| component restart | MCP command、本地模型引擎 | 只重启目标组件 |
| node restart | Gateway bind/TLS、核心升级 | 明确提示并由用户确认 |

Workspace 自身不“重启”。Workspace 可以在 Node 离线时保存 enrollment、共享 Model/MCP 与 Grant 的控制面
Desired State；Node-local Agent/Provider/Secret 配置不能因此上传 Hub，修改它们需要 Node 在线。Node 恢复后
只 reconcile 属于 Workspace 的控制面事实。

## 4. UI

NodeAgent 在一个编辑页面完成，不拆成 Runtime/Profile/Definition 三个资源。Model、MCP、Skill 和 Secret
仍有独立页面，因为它们可被多个 NodeAgent/Task 引用且生命周期不同。

发布页展示 validation error、warning、影响组件、热生效或需重启、预计断连和当前 applied state。普通
UI 不展示 revision graph 或 rollback tree。

## 5. 不变量

1. 配置只有 ConfigurationService 一个发布入口；
2. Secret 永不回显；
3. 当前已应用 generation 在新配置失败时继续服务；
4. 运行中 Invocation 保留开始时快照；
5. NodeAgent 为单一 Agent 编辑聚合；
6. Workspace 不管理 Conversation/Task/Agent 配置。
