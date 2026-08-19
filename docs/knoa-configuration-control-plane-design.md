# Knoa 配置控制面与管理页面设计

> 状态：权威设计
> 更新：2026-08-19

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

Node Console 的 Model Center 必须提供完整 CRUD：新增/删除 Provider、在一个 Provider 下新增多个 Model、
修改 endpoint、保存不回显的 API Key、设置默认模型和视觉能力。共享模型同页选择允许调用的 Workspace
Node；发布时由 Node 身份向 Hub 同步 ModelResource、Deployment 与 ResourceGrant，Provider endpoint 和
Secret 永不上传。删除或关闭本地 Deployment 时，Node 必须同步禁用 Hub 目录项并撤销旧 Grant。
若 ModelResource 由 Workspace 管理员预先定义，Node 只能在资源定义完全一致且 Deployment 已明确指向
本 Node 时上报物化状态和授权；不得改写 Workspace 所有的资源定义、generation 或 digest。

Node Console 使用顶部任务导航，不把所有配置堆在一个长页面：`概览` 负责状态、Enrollment 与 App 配对；
`模型` 先展示 Agent 可选择的 Model，Provider、Endpoint 和 API Key 收入高级区；`Agent` 展示模型绑定与
Prompt，Tools/Subagent 策略默认折叠；`共享` 独立展示本 Node 发布和其他 Node 授权的模型；`系统` 承担
服务生命周期、更新与完整 JSON。草稿状态、重新加载和热发布作为配置页共用的顶部操作条。

发布页展示 validation error、warning、影响组件、热生效或需重启、预计断连和当前 applied state。普通
UI 不展示 revision graph 或 rollback tree。

### 4.1 新用户流程

1. 在 Node Console 完成 Hub Enrollment 与 App 配对；
2. 在“模型”配置本地 llama.cpp 或云 Provider，API Key 只写入 Secret Store；
3. 默认 `knoa` Agent 绑定该模型即可开始使用；
4. 需要专业角色时，在 Agent 页面新建自定义 Knoa Agent，填写 Prompt，并选择模型、Skill 和 Tool ceiling；
5. 需要委派时，先把目标 Agent 设为“仅作为 Subagent”，再由父 Agent 选择目标和有界 Child Task 预算；
6. 所有修改统一执行 Draft → validate → preflight → publish → applied。

### 4.2 老用户修改流程

- App Agent 列表负责查看默认角色、Runtime kind、模型、Skill/Tool 摘要和 Subagent 状态；
- App Agent 编辑页负责高频完整配置，不再要求用户编辑 JSON；
- Node Console 负责 endpoint、API Key、本地路径、Codex command、故障诊断和没有 App 时的完整管理；
- “高级完整配置”仍可用于导入/审计，但不是主要产品入口；
- Prompt、模型绑定、Skill/Tool ceiling 和 delegation policy 对新 Invocation 热生效；运行中的 Invocation 保留
  原 policy snapshot。

### 4.3 管理页面边界

Hub Console 不读取 Node 的 Prompt、API Key、Agent、Conversation 或 Task 正文。Node Console 与已配对 App 都
调用同一个 ConfigurationService 发布链；它们只是不同交互界面，不拥有第二套配置语义。

## 5. 不变量

1. 配置只有 ConfigurationService 一个发布入口；
2. Secret 永不回显；
3. 当前已应用 generation 在新配置失败时继续服务；
4. 运行中 Invocation 保留开始时快照；
5. NodeAgent 为单一 Agent 编辑聚合；
6. Workspace 不管理 Conversation/Task/Agent 配置。
