# Knoa 产品领域架构

> 状态：产品对象、归属、配置入口和生命周期的权威设计
>
> 日期：2026-08-17
>
> 范围：Account、Workspace、Node、Agent、LLM、Skill、MCP、Tool、Secret、Conversation、Task、配置发布与 App 信息架构
>
> 设计取向：正向设计；Workspace-first；local-first、Hub-assisted、self-hostable；高内聚、低耦合；KISS、YAGNI；不保留与目标领域模型冲突的旧页面和配置兼容层

本文回答的是“用户在使用什么产品对象、对象属于谁、从哪里配置、在哪里执行、如何生效”。进程和
代码模块见 `knoa-module-architecture.md`，网络部署见 `knoa-deployment-architecture.md`，资源调用细节
见 `knoa-workspace-resource-fabric-design.md`，配置发布机制见
`knoa-configuration-control-plane-design.md`。如果专题文档中的技术术语与本文的产品归属冲突，以
本文为准。

## 1. 产品主轴

Knoa 的产品主轴固定为：

> 资产在 Workspace 中定义，能力在 Node 上部署，执行发生在 Node，期望状态由 Workspace 管理，
> 实际状态由 Node 上报，HubService 负责身份、协调和安全连接。

产品聚合关系是：

```text
AccountSubject
  ├── AppInstallation[]
  ├── NodeInstallation[]
  └── Membership[] ───────────────────────┐
                                          v
Workspace
  ├── Resources
  │   ├── AgentDefinition
  │   ├── ModelResource
  │   ├── RuntimeSpec
  │   ├── AgentProfile
  │   ├── Skill
  │   ├── MCPDefinition
  │   └── TypedPolicy
  ├── Work
  │   ├── Conversation --bound_to--> WorkspaceNode
  │   │   └── ChatTurn -> AgentInvocation[]
  │   └── TaskDefinition -> Deployment(kind=task) -> WorkspaceNode
  │       └── TaskExecution -> ExecutionAttempt[] -> AgentInvocation
  ├── Nodes
  │   └── WorkspaceNodeEnrollment -> WorkspaceNode
  ├── Deployments   ResourceSpec/TaskSpec -> WorkspaceNode
  └── Grants        Principal -> Resource/Deployment
```

V1 中 Conversation 创建时固定绑定一个 `WorkspaceNode`；已发布或启用的 Task 必须通过
`Deployment(kind=task)` 指向一个 `WorkspaceNode`。`AgentInvocation` 和 `ExecutionAttempt` 再通过
`runs_on -> WorkspaceNode` 固化实际执行位置。默认启动时可以直接恢复上次 Workspace、Node 或对话，
但默认落点不改变以上业务归属。

### 1.1 设计方针评估

该模型按以下原则收敛：

| 原则 | 决策 |
| --- | --- |
| 高内聚 | Workspace 聚合共享资源与用户工作；Node 聚合本机执行事实 |
| 低耦合 | Workspace 管定义和目录，Node 管执行事实；通过显式 binding/deployment 连接，不让两边双写 |
| 正向设计 | 产品归属、执行目标和物理存储分别表达，Node 离线不会破坏 Workspace 资源模型 |
| KISS | Conversation 固定 Node、Task 固定部署 Node；不增加通用调度器或 `NodeExecution` |
| YAGNI | V1 不实现自动跨 Node 迁移、动态 placement、多账户共同拥有设备或跨 Workspace 市场 |
| 优雅性 | Deployment 和 Grant 作为连接 Resource、Principal、WorkspaceNode 的边对象，不复制到两端 |

因此不采用以下两个看似统一、实际增加耦合的设计：

1. 让 Node 直接拥有 Task Definition 或 Workspace Work 目录；
2. 在 Invocation、TaskExecution、Attempt 之外新增一套 `NodeExecution` 状态机。

### 1.2 本轮争议点最终决策

| 问题 | V1 决策 |
| --- | --- |
| Conversation 在哪里 | Workspace 拥有目录和访问控制；创建时固定绑定一个 Node；正文、Turn 和执行在该 Node |
| Task 在哪里 | Workspace 拥有 Task Definition；已发布/启用 Task 必须通过 `Deployment(kind=task)` 指向一个 Node |
| 定时器在哪里 | LaunchPolicy 属于 Task Published Spec；Node materialize 本地启动器并创建 TaskExecution |
| 执行状态是否同步 | Node 向 Workspace 同步最终一致的管理投影；Node 仍是内容和执行写权威 |
| Jira MCP 在另一 Node | 允许；必须通过 MCP Deployment、ResourceGrant、远程策略和可达性 preflight |
| Deployment 是否分两套 | 不分；Resource 与 Task 共用 Deployment envelope，kind 使用 typed spec |
| AgentDefinition/Profile | Definition 是稳定组合根，精确引用一个 RuntimeSpec 和一个 Profile，不允许字段 override |
| Profile/Skill | Skill 是 Workspace 共享资源；Profile 只保存 refs 和 activation policy，不拥有实例 |
| Revision/rollback | UI 只有 Draft/Published/Applied；内部 generation/digest；V1 不建设版本树或 rollback |
| 配置重启谁 | Workspace 不可重启；Node 配置由目标 Node hot apply、reload 或重启本机组件 |

## 2. 核心领域对象

### 2.1 AccountSubject

AccountSubject 回答“你是谁”，属于一个 identity issuer。它拥有：

- 登录、恢复和 Account Session；
- Workspace Membership；
- App installation；
- 物理 Node installation 的所有权或管理权；
- Account 级安全和产品偏好。

Account 不直接拥有 Agent、LLM、Skill 或 MCP。Personal Workspace 由系统为新 Account 自动创建，
但资源外键仍然指向 Workspace，不能为了个人用户体验而直接指向 Account。

### 2.2 Workspace

Workspace 是唯一逻辑租户、共享资产边界、协作边界和主要管理范围。它拥有：

- Membership、角色和审计；
- Agent、Model、Skill、MCP 和 Tool Policy 等共享资源；
- Workspace 资源 Published Spec generation；
- Node enrollment 和 Workspace 下的 Node 配置；
- Deployment intent、授权与 rollout；
- Conversation 目录、Task Definition 及其稳定产品生命周期；
- Conversation binding、Task Deployment 和跨 Node 的 Work 目录投影。

普通用户默认只有 Personal Workspace；家庭、团队和工作 Workspace 使用同一模型，不另建 Organization
资源层。真实需求出现前不建设组织树、市场和通用跨 Workspace 资产联邦。

### 2.3 NodeInstallation

NodeInstallation 表示一台安装 Knoa 的物理机器或隔离运行实例，回答“这是什么设备”。专题实现文档
中的 `NodeHost` 与它对应。它拥有：

- installation identity 和设备密钥；
- OS、CPU/GPU、内存、存储和网络事实；
- 本机服务监督和更新能力；
- 本机安全存储；
- 操作系统权限和紧急停止能力。

NodeInstallation 由 AccountSubject 管理，但不直接获得某个 Workspace 的业务数据权限。

### 2.4 WorkspaceNodeEnrollment

WorkspaceNodeEnrollment 是 Workspace 与 NodeInstallation 的授权关系，回答“这个 Workspace 如何使用
这台 Node”。它是 Node 日常业务配置的产品归属，拥有：

- Workspace 内显示名称、标签、分组和默认执行偏好；
- Workspace 工作目录映射；
- Node 默认执行偏好、远程调用和资源共享策略；
- WorkspaceNodeConfig 的 Desired Generation 和 rollout 记录；
- 指向该 Node 的 Deployment、Grant 和 Work binding 的只读聚合投影；
- 对 Node 上报的 Applied Generation 与 Observed State 的只读投影。

`Deployment` 与 `ResourceGrant` 本身属于 Workspace，不归 Enrollment 独占。
Node detail 可以聚合显示这些关系对象，但不能把它们复制进 Enrollment 形成第二份写入权威。

因此，用户不需要先连接或进入 Node 才能管理 Node。正常入口始终是：

```text
Account -> Workspace -> Nodes -> Node detail
```

### 2.5 WorkspaceNode

WorkspaceNode 是 NodeInstallation 为一个 Workspace 提供的隔离执行身份和运行范围。它拥有：

- WorkspaceNode identity；
- Node-local overlay 和 Secret Store；
- Runtime generation；
- AgentInvocation、ExecutionAttempt、Approval、ToolStep、Artifact bytes 等执行事实；
- 对本次 Invocation、Tool 和远程资源调用的最终拒绝权。

目标模型允许一个 NodeInstallation 承载多个相互隔离的 WorkspaceNode。第一阶段实现可以保持
`1 NodeInstallation = 1 WorkspaceNode`，但 API、UI 和数据模型不能把物理设备身份与 Workspace
执行身份永久合并。

## 3. Node 不是普通容器

Node 是执行节点、能力宿主和安全边界，而不仅是一个进程容器。Node 可能承载：

- 本地 Qwen3.5 4B、视觉模型或其他 LLM；
- Native/Codex 等 Agent Runtime；
- 文件系统、Workspace 目录和 Artifact；
- 浏览器、桌面自动化和本地 Tool；
- 本地 MCP Server；
- GPU、CPU、队列和运行时状态；
- 本机 Secret 和操作系统授权。

HubService 不能替代 Node 的执行事实、Node-local Secret、文件系统权限或最终授权判断。Relay 只转发
加密连接，不拥有业务对象。

## 4. Workspace 共享资源

共享资源使用稳定逻辑 ID。只有会被部署或执行引用的 Spec 才使用内部不可变发布版本：

```text
WorkspaceResource
  ├── resource_id
  ├── kind
  ├── mutable display metadata
  ├── draft_spec?
  ├── published_generation + digest
  └── visibility / grant
```

UI 只展示“草稿、已发布、已应用”，不要求用户理解 Revision。Agent、Model 配置、RuntimeSpec、
Skill/MCP Package、Policy 和 Task 等可执行 Spec 在发布时生成单调 generation 与 digest；名称、标签、
说明等展示 metadata 直接修改，不产生 generation。V1 不做版本列表、分支、合并、语义版本、版本市场
或一键回滚。Registry 保存当前 Published Spec 和审计 digest；运行中的执行保存自己的解析快照，仍被
运行实例引用的旧 Package 延迟清理。

共享不等于所有 Node 自动安装或自动获得调用权。资源定义、Deployment 和授权是三个不同对象。

### 4.1 RuntimeImplementation、RuntimeSpec、AgentProfile 与 AgentDefinition

四个概念必须分开：

```text
RuntimeImplementation  code-owned: native / codex
RuntimeSpec            如何执行：implementation / model binding / sandbox / limits
AgentProfile           以什么角色执行：instructions / Skill refs / policy ceilings
AgentDefinition        稳定 Agent 身份：RuntimeSpec + AgentProfile
```

`RuntimeImplementation` 不是用户资产，不能从网络下载任意进程内 Runtime class。`RuntimeSpec` 使用可信
实现并描述 model ownership/binding、sandbox、并发和运行参数。`AgentProfile` 描述角色、Prompt、
Workspace Skill 引用/激活策略、Tool ceiling 和 delegation ceiling。`AgentDefinition` 是用户、Conversation、Task 和 Deployment 实际引用
的组合根，V1 必须引用且只引用一个 RuntimeSpec 和一个 AgentProfile。

三个配置位置不得重叠：模型、endpoint、sandbox、command 和并发只在 RuntimeSpec；角色、instructions、
Skill 和治理 ceiling 只在 AgentProfile；AgentDefinition 只保存稳定 `agent_id`、两个引用、enabled、
visibility 和展示 metadata，不能 override Profile/Runtime 字段。Profile 可以被复用，但 V1 UI 以 Agent
编辑器为主并明确显示影响的所有 Agent；不建设 Profile 继承、alias 图、独立市场或多层 override。

### 4.2 LLM / Model

LLM 必须拆成逻辑资源与实际部署：

```text
ModelResource Published Spec
  = Workspace 中的名称、能力、接口要求和选择语义

Deployment(kind=model)
  = 某个 WorkspaceNode 或受控远程服务上的具体可调用端点
```

例如：

```text
Personal Workspace
  └── ModelResource: Qwen 3.5 4B
      └── Deployment: Robin Desktop / llama.cpp / ready
```

同 Workspace 的 Office PC 可以通过 Hub/Relay 调用 Robin Desktop 上的 Deployment，但模型推理仍在
Robin Desktop 发生，且需要明确 ResourceGrant。Hub 不接管 Prompt、响应或 Node Secret。

云模型也遵循同一模型：Workspace 共享 ModelResource；真正的 Provider endpoint、API Key 和进程参数
属于目标 Deployment/Node overlay。若多个 Node 分别直连同一 Provider，每个 Node 分别满足 Secret
Requirement；不能把 API Key 混入 Workspace Published Spec。

### 4.3 Agent

AgentDefinition 是 Workspace 共享、使用 Published Spec generation/digest 的产品资产：

```text
AgentDefinition
  = stable agent_id / display metadata
  + exactly one RuntimeSpec reference
  + exactly one AgentProfile reference
  + enabled / visibility
```

Agent 定义属于 Workspace；Agent Runtime 运行在 Node；Agent Invocation 是某个 Node 上的一次执行。
Reviewer 等 Platform-owned System Agent 可以隐藏于普通资源目录，但仍复用统一 Runtime/Invocation 模型。

每次 Agent generation 必须解析并冻结 RuntimeSpec/Profile/Model/Skill/Policy digest。Profile 或
RuntimeSpec 发布变化时，impact plan 列出所有引用它的 AgentDefinition，并只为这些 Agent 构建新
generation；历史 Invocation 继续使用自己的快照。

### 4.4 Skill、MCP 与 Tool

- Skill 是 Workspace 共享的 data-only 知识与流程包，发布时计算 generation/digest；
- AgentProfile 只保存 `default_skill_refs`、`allowed_skill_refs` 和 typed activation policy，不拥有或复制 Skill；
- Agent generation 解析 Workspace grant 并冻结 Skill digest，目标 Node 按需下载/校验；Invocation activation 是临时执行事实，不是长期 Skill instance；
- MCP Definition 是 Workspace 共享的服务描述、来源和权限要求；
- MCP Deployment 位于 Node 或受控远程服务；
- Tool Policy 属于 Workspace，具体 Tool capability 由 Node 发现并最终授权；
- Workspace 资源不能绕过 Capability Gateway 直接授予本机或外部副作用权限。

### 4.5 Typed Policy

Workspace Policy 是有限、类型化的资源集合：

- ToolPolicy；
- ApprovalPolicy；
- DelegationPolicy；
- ModelSelectionPolicy；
- ResourceSharingPolicy。

不实现可解释任意表达式的通用 Policy Engine。出现真实新策略类型时增加 typed schema 和 applier。

### 4.6 Secret

Workspace 可以拥有 SecretRequirement、SecretBinding metadata 和授权关系，但 V1 Secret value 默认只
存储在目标 Node 的 Secret Store。App 和 Hub 只能看到：

```text
required | configured | missing | rotated_at
```

不建设可向所有普通 Node 下发明文 Secret 的 Workspace Vault。未来若确有需求，必须作为独立安全
子系统设计，而不是给 Hub 数据库增加一个明文字段。

## 5. Workspace Work

`Work` 是 Workspace 中用户长期识别和管理的定义与目录，首批只有 Conversation 与 Task。Workspace
负责稳定 ID、访问控制、定义和目录；Node 负责被绑定或部署到本机的内容与执行事实。产品归属不能被
误解为“这些工作可以不选择 Node 就运行”。

### 5.1 Conversation

```text
ConversationSession
  └── ChatTurn[]
      └── AgentInvocation[]
          └── runs_on -> WorkspaceNode
```

- ConversationSession 是 Workspace 内稳定的多轮上下文；
- ChatTurn 是一次用户输入和最终回答；
- 首次执行、重试和受控 child invocation 可以形成多个 AgentInvocation；
- V1 创建 Conversation 时必须设置 `workspace_node_id`，后续 Turn 固定在该 Node；
- Node 离线时 Workspace 目录仍能显示 Conversation，但正文和继续对话标记为暂不可用；
- V1 不在同一 Conversation 内切换 Node，不迁移 Runtime Session、Artifact 或未完成 Turn；
- 若用户要在另一 Node 继续，显式新建 Conversation；上下文导出或分叉在出现真实需求后再设计。

### 5.2 Task

```text
TaskDefinition
  └── Deployment(kind=task) -> WorkspaceNode
      └── TaskExecution[]
          └── ExecutionAttempt[]
              └── AgentInvocation
                  └── runs_on -> WorkspaceNode
```

- TaskDefinition 是 Workspace 内稳定的目标和启动策略；
- TaskDefinition 引用一个 AgentDefinition，可增加 task-specific resource requirements 和只收窄权限的 policy；
- Task 类型 Deployment 把一个 Task Published Spec 和启动策略发布到 WorkspaceNode；
- TaskExecution 是某次用户可见执行；
- ExecutionAttempt 是 lease、恢复和故障诊断的技术尝试；
- 草稿 Task 可以暂未部署；已发布、启用或立即执行的 Task 必须有且只有一个目标 Node；
- 定时或事件启动策略随 Deployment 下发，由目标 Node 的本地启动器实际触发；
- 每个 TaskExecution 及其全部 Attempt 固定在 Deployment 的 Node，V1 不自动跨 Node recovery；
- 改变目标 Node 发布新的 Deployment generation，只影响未来 Execution，不改写历史。

### 5.3 执行绑定不是新状态机

Conversation binding、Deployment 和 Invocation/Attempt placement 各自回答不同问题：

```text
Conversation.workspace_node_id        会话固定在哪个 Node
Deployment(kind=task).target_node_id  Task Published Spec 发布到哪个 Node
ExecutionPlacement.workspace_node_id  这次调用事实上在哪个 Node 运行
```

`ExecutionPlacement` 是 Invocation/Attempt 上的值对象或引用：

```text
ExecutionPlacement
  ├── workspace_node_id
  ├── agent_definition_digest
  ├── model_deployment_id
  ├── placed_at
  └── placement_reason
```

它不拥有 queued/running/completed 状态。状态继续由 ChatTurn、AgentInvocation、TaskExecution 和
ExecutionAttempt 各自的既有生命周期拥有，从而避免通用 `NodeExecution` 与它们竞争权威。

### 5.4 产品归属不等于物理存储

Conversation/Task 属于 Workspace，不代表 Hosted Hub 必须保存其全部明文。V1 由 Workspace Registry
保存 Conversation 目录、Task Definition/Deployment 和非敏感状态投影；绑定 Node 保存 Conversation
正文，目标 Node 保存 TaskExecution、Invocation、Attempt 和 Artifact 事实。Hosted Hub 只保存实现其
职责所需的目录与协调数据。未来更换存储部署不能改变产品 ID、Workspace 归属或生命周期语义。

## 6. Resource、Deployment 与 Grant

三个概念不得合并：

| 对象 | 回答的问题 | 权威边界 |
| --- | --- | --- |
| Resource Published Spec | “这是什么能力” | Workspace Registry |
| Deployment | “希望哪个 Spec 在哪个 Node 生效” | Workspace Desired State |
| Node Overlay | “这台 Node 如何具体运行” | WorkspaceNode 配置边界 |
| Deployment Observation | “现在实际运行得怎样” | 目标 WorkspaceNode |
| Grant | “谁可以选择或调用它” | Workspace 授权边界，Node 最终拒绝 |

同一字段只能有一个写入权威。Workspace 不能写 Node 的硬件事实、Secret value、本地 PID；Node 不能
反向修改 Workspace Agent Prompt、Model 逻辑名称或成员授权。

Deployment 和 ResourceGrant 是 Workspace 级边对象，不归某一个 Enrollment 独占。UI 可以从
Resource/Task detail 或 Node detail 查看同一个对象，但底层不能复制两份记录。

Deployment 使用一个共享 envelope 和按 kind 区分的 typed spec：

```text
Deployment
  ├── source_ref(kind, resource_id, generation, digest)
  ├── target_workspace_node_id
  ├── typed_spec                 # model | mcp | agent | task
  ├── desired_state
  └── applied/observed projection
```

共享的是发布、应用、健康和停止生命周期；各 kind 不共享万能字段。Source Published Spec 仍是业务
字段唯一写权威：Task LaunchPolicy 只写在 TaskDefinition，Deployment 下发其解析快照而不复制可编辑
字段；MCP typed spec 只补充目标 transport/process，Model typed spec 只补充目标 provider/runtime 参数。

### 6.1 跨 Node 资源依赖

Task 或 Conversation 的执行 Node 不要求承载所有依赖。Workspace 资源可以部署在另一 Node，并通过
显式 ResourceGrant 暴露为远程服务。例如 Jira MCP 在 Node A、Task 在 Node B 时：

```text
Task Published Spec
  -> AgentDefinition dependency closure / required_resource_refs
  -> requires MCPDefinition digest
  -> resolves MCPDeployment(Node A)
  -> ResourceGrant(caller Node B -> deployment on Node A)
  -> Deployment(kind=task, Node B)
```

发布校验必须确认依赖 digest、Grant、远程服务策略和网络可达性；执行快照固化实际 Deployment。
Secret 保留在提供服务的 Node A。Node A 不可用时，Task 按显式策略等待或失败，不能静默换成其他
Deployment。V1 不建设通用服务网格、自动依赖迁移或动态负载均衡；Workspace 可以限制只允许同 Node
依赖。

## 7. 配置与状态控制环

配置页面不是直接编辑运行对象，而是驱动一个版本化控制环：

```text
Workspace Draft
  -> validate
  -> impact plan
  -> publish Desired Generation + digest
  -> Desired State
  -> target Node reconcile
  -> Applied Generation
  -> Observed State
```

必须同时显示：

- Desired Generation：Workspace 希望 Node 使用的配置代次；
- Applied Generation：Node 已经接受并应用的配置代次；
- Observed State：Node 当前健康、容量、错误和实际 generation；
- Sync Status：已生效、应用中、等待 Node 上线、失败或需要人工操作。

“保存成功”只表示 Draft 已保存；“发布成功”不等于所有 Node 已生效；“已生效”必须来自 Node
observation。

### 7.1 热生效语义

用户统一执行“发布并应用”，系统根据影响分为：

| 类型 | 示例 | 生效方式 |
| --- | --- | --- |
| live policy | allowlist、审批阈值、默认选择 | 新 Invocation 原子使用新 generation |
| runtime replace | Agent Prompt、模型绑定、RuntimeSpec | 新 generation 健康后切换，旧 generation 有界 drain |
| component reload | MCP、Channel、Gateway adapter | 目标组件受控 reload |
| node service restart | 不支持 reload 的 Node 组件或异常恢复 | 目标 Node 显式 drain 后重启服务 |
| host action | OS 权限、安装更新、设备重启、根目录 | App 明确提示并等待 Node 上的用户操作 |

正在执行的 Invocation 使用创建时快照，普通配置发布不能静默改写其模型、Prompt 或权限。

Workspace 只保存配置和 Desired State，没有可重启的 Workspace 进程。所有 reload/restart 都以具体
WorkspaceNode 为目标：Node 校验影响并执行，随后上报 Applied Generation 和 Observed State。Node
离线时动作保持 pending；批量操作只是向多个 Node 分别下发，不定义“重启 Workspace”。

## 8. 配置归属矩阵

| 配置 | 产品入口 | 写入权威 |
| --- | --- | --- |
| 主题、语言、通知、App 更新 | Account / App Settings | App installation |
| 登录、安全、Hub issuer | Account | Identity service |
| 成员、角色、审计 | Workspace / Members | Workspace |
| Agent、Model、Skill、MCP Definition | Workspace / Resources | Workspace Registry |
| Node 名称、标签、工作目录、默认执行偏好 | Workspace / Nodes / Node detail | WorkspaceNodeEnrollment |
| 资源与 Task Deployment、共享、远程调用和审批策略 | Workspace / Resources/Work/Nodes | Workspace Desired State |
| API Key、模型路径、进程参数 | Workspace / Nodes / Node detail / local setup | Node overlay + Node Secret Store |
| CPU/GPU、PID、延迟、队列、健康 | Workspace / Nodes / Node detail / Status | Node Observed State |
| 安装身份、设备密钥、OS 权限 | Node local | NodeInstallation |
| Conversation 目录与 Task Definition/Deployment | Workspace / Work | Workspace Registry |
| Conversation 正文、TaskExecution、Invocation、Attempt、Artifact bytes | Work execution detail | 绑定或目标 WorkspaceNode |

所有正常 Node 管理都从 Workspace 进入。进入 Node 的 Chat/Task shell 是使用执行上下文，不是管理
Node 的唯一入口。

## 9. App 产品信息架构

```text
Account
  ├── Workspaces
  ├── Account Security
  └── App Settings

Workspace
  ├── Overview
  ├── Work
  │   ├── Conversations
  │   └── Tasks
  ├── Agents
  ├── Models
  ├── Skills & MCP
  ├── Nodes
  ├── Members
  └── Settings

Workspace / Nodes / Node detail
  ├── Overview
  ├── Deployments
  ├── Capabilities
  ├── Workspace Directory
  ├── Permissions
  ├── Configuration Status
  └── Logs & Diagnostics

Selected Node
  ├── create Conversation bound to this Node
  ├── deploy Task to this Node
  ├── current Invocations
  └── execution status
```

App 不按后端技术模块向普通用户展示 `Capabilities + Model Center + Extension Center + Node Center +
System Config` 五个并列入口。Draft ID、Secret Ref、Driver、Deployment ID 和 generation/digest 只在需要
时进入高级详情。

Conversation/Task 页面属于 Workspace Work，但执行目标不是可选项：Conversation 创建时绑定一个
Node，Task 发布或启用前部署到一个 Node。Node 页面可以提供“在此 Node 新建对话”“部署任务”和
“仅看此 Node”的投影入口，但不能成为 Work Definition 的产品所有者。

## 10. 新用户完成配置

新用户目标不是“理解架构”，而是在最短路径内获得第一次成功对话：

```text
注册或登录 Hosted Hub
  -> 自动创建 Personal Workspace
  -> 添加第一台 Node
  -> 自动发现硬件、本地模型和 Tool
  -> 选择本地模型或添加云模型
  -> 创建/启用默认 Agent
  -> validate + publish + reconcile
  -> 第一次对话
```

向导规则：

1. 普通用户不填写 Hub token、Workspace ID、Secret Ref 或 Deployment ID；
2. Node 使用 Account 登录或一次性 enrollment QR 加入当前 Workspace；
3. 检测到 Qwen3.5 4B 时，向导创建 Workspace ModelResource 和该 Node 的 ModelDeployment；
4. 添加云模型只要求 Provider、API Key 和模型选择，高级 endpoint/driver 可展开；
5. 默认 Agent 自动引用选中的 Model、基础 Skill 和保守 Tool Policy；
6. 每一步可恢复，Node 离线不会把用户踢回登录页；
7. 向导结束条件是一次真实健康检查和可发送第一条消息，不是“字段已经保存”。

## 11. 老用户修改配置

老用户按对象归属进入，不使用万能配置页：

```text
修改 Agent       -> Workspace / Agents / Agent detail
修改共享 LLM     -> Workspace / Models / Model detail
部署本地模型     -> Workspace / Nodes / Node / Deployments
导入 Skill/MCP   -> Workspace / Skills & MCP
修改 Node 目录   -> Workspace / Nodes / Node / Workspace Directory
检查生效状态     -> Workspace / Nodes / Node / Configuration Status
修改 App 外观    -> Account / App Settings
```

编辑流程固定为：

```text
edit draft -> validate -> impact -> publish -> watch rollout -> inspect failure/retry
```

从 Node 页面编辑 Workspace Agent/Model 时，App 只能 deep-link 到对应 Workspace 资源，不复制一份
Node-local 定义形成双写。

## 12. Work 执行与存储权威

Conversation 目录、Task Definition/Deployment、稳定 ID 与产品生命周期属于 Workspace。Conversation
正文和 ChatTurn 由绑定 Node 保存；TaskExecution、AgentInvocation、ExecutionAttempt、ToolStep、
Approval 处理和 Artifact bytes 由目标 Node 保存执行事实。Workspace/Hub 提供非敏感目录和状态投影，
但不能把 Hub projection 当成内容或执行真相。

Node 必须向 Workspace Registry 同步带单调序列号的 Work Management Projection：Work ID、绑定或部署 Node、
最新状态、进度、时间戳、待审批摘要、结果摘要、Artifact 引用和所用 generation/digest。完整消息正文、Trace、
Tool/MCP 原始载荷、Artifact bytes 与 Secret 默认不进入投影。投影最终一致；Stop、审批、暂停、恢复和
重试命令必须到达权威 Node 后才算成功。Node 离线时保留最后投影并明确标记 stale/offline。

Workspace Registry 与 Hosted Hub 不是同义词。opaque Hosted Hub 默认只接收 ID、目标 Node、状态、
时间戳和 digest；标题、结果摘要、审批内容等可能敏感字段只有在 `hosted_trusted` 或端到端加密投影
模式下才可同步。产品上的 Workspace 可见性不能被实现成 Hub 默认读取全部 Work 明文。

Hosted 协议必须显式区分 `hub_id` 与 `workspace_id`：前者是 issuer、签名密钥和 Relay 信任域，后者是
资源、Deployment、Grant、Work Projection 和执行授权域。Self-hosted 单 Workspace 下二者可以相等，
但任何票据验证和签名 transcript 都不能依赖该巧合；Hosted 多 Workspace 下二者必然不同。

一次执行至少固化：

- Workspace ID、Work ID 与 ExecutionPlacement；
- Agent Definition resolved digest；
- Model selection/Deployment；
- ResolvedInvocationPolicy；
- Skill digest；
- Task/Conversation input snapshot。

切换当前 Agent 或 Model 只影响未来 Invocation，不改写历史结果。

## 13. 三种部署形态保持同一产品模型

### 13.1 No-Hub

本地 owner identity、Workspace Registry 和 WorkspaceNode 组合在同一 Node。UI 仍展示 Personal
Workspace，不出现“无 Workspace”的第二套产品。

### 13.2 Self-hosted Hub

用户运行自己的 HubService，管理 Account、Workspace、Node directory、Relay 和 rollout。Node 仍是
执行与 Secret 权威。

### 13.3 Hosted Hub

Knoa 托管 Account、Workspace 控制面、Node directory、Relay 和平台更新渠道。Hosted Hub 不因为
托管而自动成为模型、文件、Prompt 和 Secret 的执行权威。

三种形态只替换基础设施部署和可用能力，不改变 Account、Workspace、Node、Resource、Deployment
和 Conversation/Task 的产品语义。

## 14. 权限边界

- Membership 决定 Account 是否可以进入 Workspace；
- Workspace role 决定是否可以编辑资源、管理成员或 enroll Node；
- ResourceGrant 决定 WorkspaceNode 是否可以选择/调用资源；
- ResolvedInvocationPolicy 决定一次 Agent Invocation 的能力上限；
- Capability Gateway 决定一次 Tool/MCP 副作用是否被执行；
- 目标 Node 对本地 Secret、文件、OS 权限和远程 Invocation 保留最终拒绝权。

任何一层只能收窄权限，不能通过 Profile、Skill、Deployment 或 Relay 创造权限。

## 15. 最小领域数据模型

```text
AccountSubject
AppInstallation
Workspace
WorkspaceMembership

NodeInstallation
WorkspaceNodeEnrollment
WorkspaceNodeConfigGeneration
NodeObservedState

AgentDefinition
AgentProfile
RuntimeSpec
ModelResource
SkillPackage
McpDefinition
ToolPolicy

Deployment
DeploymentObservation
ResourceGrant
SecretRequirement

ConversationSession
ChatTurn
AgentInvocation
TaskDefinition
TaskExecution
ExecutionAttempt
Approval
Artifact
```

`Conversation.workspace_node_id` 是 V1 必填绑定；`ExecutionPlacement` 首先作为
AgentInvocation/ExecutionAttempt 的 typed value object，不要求单独建表。

V1 不需要通用 Asset 超类、任意层级组织树、跨 issuer 身份联邦、跨 Workspace 市场、通用调度 DSL
或多区域控制面。出现至少两个真实且无法由现有模型表达的用例后，再提炼新的抽象。

## 16. 不变量

1. Account 不直接拥有 Workspace 资源；资源属于 Workspace。
2. HubService 是服务部署，不是第二个业务租户。
3. NodeInstallation 与 WorkspaceNodeEnrollment 是不同对象。
4. Node 的正常业务配置从 Workspace 管理。
5. 物理硬件事实、设备密钥和 OS 权限不能伪装成 Workspace 配置。
6. Resource、Deployment、Grant 和 Observation 不得合并或双写。
7. Agent 定义属于 Workspace，Agent Runtime 和 Invocation 运行在 Node。
8. Model 定义属于 Workspace，具体模型服务属于 Deployment。
9. Secret value 默认属于 Node Secret Store，Hub/App 不读取明文。
10. Desired、Applied 和 Observed 必须分别表达。
11. Node 离线是局部状态，不使 Account/Workspace 不可用。
12. Relay 只转发，不拥有身份、业务资产、明文和执行状态。
13. 默认 Node 可配置，但 Conversation binding 和 `Deployment(kind=task)` 必须显式持久化。
14. 历史 Invocation 使用不可变配置快照，不受后来修改影响。
15. Conversation 目录和 Task Definition 属于 Workspace；Conversation 内容和所有执行必须落在 Node。
16. Deployment 与 ResourceGrant 是 Workspace 级边对象，不复制进 Resource 和 Node 两套配置。
17. 不创建与 AgentInvocation、TaskExecution、ExecutionAttempt 重叠的通用 NodeExecution 状态机。
18. V1 一个 Conversation 固定一个 Node，一个已启用 Task 固定一个部署 Node，不做隐式迁移。

## 17. 对当前产品的直接修正

当前 App 和 API 后续应按以下顺序重构：

1. 把 Workspace 首页从摘要页升级为真实资源与 Node 管理入口；
2. 建立新用户 Setup Wizard，闭环到第一次真实对话；
3. 将 Agent、Model、Skill/MCP 从 Node `Capabilities` 页迁移到 Workspace 资源页；
4. 将 Node Center 拆成 Workspace Node directory 与 Node detail；
5. 将 Node detail 接入 Desired/Applied/Observed 状态，而不是只显示连接状态；
6. 将 Node-local `ManagedConfig` 拆成 Workspace Definition、WorkspaceNodeConfig 和 materialized runtime config；
7. 让所有发布通过 typed Draft/Validate/Impact/Publish/Rollout API；
8. 将高频 Conversation/Task 切换器保留在 Work shell，并显示绑定或部署 Node；Node detail 只管理设备、部署投影与执行状态；
9. 建立 Workspace Work 目录，但让 Conversation 创建时绑定 Node、Task 启用前部署到 Node；
10. Workspace 提供跨 Node Work 目录，Node 页面提供新建/部署入口、过滤和执行状态；
11. 不新增通用 NodeExecution，现有执行服务分别持有自己的状态机；
12. 合并 Resource/Task 的重复 Deployment 生命周期，保留一个 envelope + typed spec；
13. 以 AgentDefinition 为编辑入口，RuntimeSpec/Profile 字段不重叠，Profile 只引用 Workspace Skill；
14. 用 Draft/Published/Applied generation 替代用户可见 Revision/history/rollback；
15. 建立 Node -> Workspace Work Management Projection，同步目录状态但不复制内容/执行写权威。

完成这些修正后，App 页面、Hub API、Node reconcile 和 Runtime 热生效才会围绕同一套产品架构工作。
