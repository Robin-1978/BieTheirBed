# Knoa Agent Runtime、Profile 与 Subagent 架构设计

> 状态：已实现，作为当前架构基线
>
> 日期：2026-08-16
>
> 范围：Agent Runtime、模型绑定、Agent Profile、Agent Definition、Session/Invocation、Subagent Delegation、Skill 与 Capability 治理
>
> 关系：扩展 `docs/knoa-agent-runtime-design.md`；配置真相、管理页面、发布与热生效机制见 `docs/knoa-configuration-control-plane-design.md`
>
> 实施策略：前向直接切换，不保留 `AgentConfig/config.agents` 兼容层

> 落地范围：typed RuntimeSpec/Profile/Agent Definition、统一 resolver、Invocation policy 快照、Capability Gateway Tool allowlist/预算/fingerprint、统一 System Agent 执行入口、Session rebind、staged Child Task delegation、跨 Session Artifact 复制、Skill digest 冻结、Codex capability bundle 校验，以及 Runtime generation 热切换与 deadline interrupt 均已进入代码与回归测试。

## 1. 决策

Knoa 将 Agent 从当前的“一个 `agent_id` 对应一个 Runtime 对象”拆分为五个正交概念：

```text
RuntimeSpec + Agent Profile = Agent Definition

Agent Definition + ResolvedInvocationPolicy + Session/Turn = Agent Invocation

具有 parent invocation 的 Agent Invocation = Subagent / Child Run
```

完整分层如下：

```text
Model Deployment
  |- platform-managed model
  `- runtime-managed model
             |
             v
Runtime Implementation
  |- native
  `- codex
             |
             v
RuntimeSpec
  |- native-main
  |- native-approval-reviewer
  `- codex-default
             |
             +--------------------+
                                  |
Agent Profile                     |
  |- assistant                    |
  |- approval-reviewer            |
  |- researcher                   |
  `- coder                        |
             |                    |
             +---------+----------+
                       v
                Agent Definition
                  |- knoa
                  |- reviewer_agent
                  `- codex
                       |
                       v
          ResolvedInvocationPolicy
                       |
                       v
                Agent Invocation
                       |
                       `- optional child invocation
```

核心决定：

1. `AgentRuntime` 继续是统一的中立 SPI，统一 Session、Turn、事件、命令、交互和健康语义。
2. Runtime 可以使用 Platform 管理的 LLM，也可以像 Codex 一样自行管理 LLM、认证和模型选择。
3. RuntimeSpec 描述一个 Agent 所使用的执行方式、模型绑定、sandbox 和 Runtime-native 能力；模型归 RuntimeSpec，而不是归 Profile。
4. Profile 描述角色、行为和治理边界，不直接绑定具体模型名称。
5. Agent Definition 是一个稳定 ID，组合一个 RuntimeSpec 和一个 Profile。
6. ResolvedInvocationPolicy 是 Platform 为本次运行解析的唯一授权快照，固化 definition digest、调用类型、Tool、Skill、Artifact 和可执行限制；它不替代 Runtime sandbox，也不阻止当前 policy 在重试时进一步撤权。
7. Subagent 不是新的 Runtime 类型，而是带父 Invocation 的普通 Agent Invocation。
8. Subagent 由 Platform Delegation Service 创建和治理，Agent 不能直接启动进程、复制凭据或自行授予权限。
9. Reviewer 是 Platform-owned System Agent；它复用统一 Runtime SPI，但不作为普通可委派 Agent 暴露。
10. Skill 是 data-only 的可组合知识包，不是 Agent、Runtime 或权限单元。
11. Capability Gateway 是 Platform Tool、MCP 和外部副作用的唯一授权边界；Runtime-native 行动由 RuntimeSpec、Profile ceiling、ResolvedInvocationPolicy 和 sandbox 共同治理。

## 2. 背景与问题

当前实现已经具备多 Runtime 和受限 System Agent 的基础：

- `KnoaAgentRuntime` 与 `CodexAgentRuntime` 均实现统一 `AgentRuntime` SPI；
- `AgentManager` 管理静态可信 Runtime 集、启停、并发容量和 system Agent；
- `reviewer_agent` 使用独立 Prompt、独立模型、独立 Runtime 实例和无 Tool grant；
- Task 已具有 `parent_task_id`，Agent 可以创建 detached Task；
- Skill 已有 data-only package、触发选择、工具需求和 capability 需求；
- Capability Gateway 已能签发 Turn-scoped、短期、可撤销的 capability grant。

但现有概念把以下内容绑定在同一个 `agent_id` 中：

```text
agent_id
  = Runtime implementation
  + Runtime configuration
  + model binding
  + role/profile
  + user-visible Agent identity
```

例如：

- `knoa` 同时表示原生 Runtime、默认主 Agent 和通用助手角色；
- `codex` 同时表示 Codex Runtime adapter、Codex 自管理模型以及潜在的编码角色；
- `reviewer_agent` 已经接近独立 Profile，但仍通过专门的 composition 分支手工构造；
- 当前部署使用 Qwen3.5 4B 作为审批专用独立模型，却容易被误解为 Knoa 主 Agent 的模型或 fallback。

这会导致以下架构问题：

1. 无法清楚表达“同一种 Runtime 实现使用不同模型部署”。
2. 无法清楚表达“同一种 Runtime 实现承载多个专业角色”。
3. Profile、Skill、模型和工具权限之间的边界不明确。
4. 当前 detached Task 不能完整表达父 Agent 委派、等待、取消传播和结果汇总。
5. 若直接增加 `spawn_agent`，容易绕过现有 Session binding、Task durability 和 Capability Gateway。
6. Reviewer、Router、Summarizer 等 system Agent 会继续以 composition 特例增长。

## 3. 目标与非目标

### 3.1 目标

- 统一定义 Runtime、模型、Profile、Agent、Invocation 与 Subagent 的语义。
- 正确表达 Native Runtime 和 Codex Runtime 不同的模型所有权。
- 支持一个 Runtime implementation 的多个独立 RuntimeSpec。
- 支持 Prompt、Skill、工具上限和委派策略组成专业 Profile。
- 把 Profile、principal 和 delegation 约束解析为可持久化、可重放的 ResolvedInvocationPolicy。
- 支持主 Agent 将有界工作委派给另一个 Agent Definition。
- 复用现有 Task、Session、Artifact、Approval、Interaction、Trace 与 Capability 设施。
- 保持 Reviewer 的无工具、fail-closed、Platform-only 调用边界。
- 保证 Profile 和 Skill 永远不能自行授予 Capability。
- 为未来 researcher、coder、planner、reviewer、summarizer 等角色留出稳定扩展点。

### 3.2 非目标

- 不支持下载任意 Python class 作为 Runtime plugin。
- 不允许 Profile 或 Skill 注册可执行代码。
- 不引入第二套 Tool、Approval、Artifact 或 Task 状态机。
- 不允许 Agent 直接连接绕过 Platform policy 的外部 MCP Server。
- MVP 不支持无限递归、多层 Agent swarm 或自治组织结构。
- MVP 不实现通用 A2A 跨组织协议。
- MVP 不把 Reviewer 变成普通 Agent 可以调用的审批工具。
- 不要求所有 Runtime 使用相同的模型 Provider 配置方式。
- 不要求 Profile 在不同 Runtime 上产生完全一致的行为。
- MVP 不引入长期多版本 Runtime 池或动态 Runtime placement；允许新 generation 原子热发布和一个旧 generation 的有界 drain。
- MVP 不承诺 per-invocation token/iteration 硬预算。

## 4. 术语与身份模型

### 4.1 Model Deployment

Model Deployment 是可供 Runtime 使用的模型端点或模型选择配置。

它可能是：

- llama.cpp 上独立部署的小模型（当前 Reviewer 部署为 Qwen3.5 4B）；
- OpenAI-compatible provider 上的远程模型；
- 本地视觉模型；
- Codex 自己配置和管理的模型。

Model Deployment 不是 Agent，也不拥有 Tool、Session、Memory 或 Approval 权限。

### 4.2 Runtime Implementation

Runtime Implementation 是 `AgentRuntime` SPI 的一种实现方式。

首批可信实现：

```text
native  -> 当前 KnoaAgentRuntime，建议后续重命名为 NativeAgentRuntime
codex   -> CodexAgentRuntime / Codex App Server adapter
```

Runtime Implementation 是代码实现类别，不是用户可选择的 Agent。

### 4.3 RuntimeSpec

RuntimeSpec 是 Agent Definition 中不可变的 Runtime 配置，包含：

- implementation；
- model ownership 与 model binding；
- 进程、认证或 provider 配置；
- workspace/home/sandbox；
- Runtime 级并发与资源上限；
- Runtime-native capability ceiling；
- Profile instructions 的绑定方式与支持等级；
- 健康检查与 drain 策略。

示例：

```text
native-main
native-approval-reviewer
codex-default
```

RuntimeSpec 不是共享的 live deployment service，也不拥有独立状态机。每个 Agent Definition 根据自己的 resolved RuntimeSpec 构造一个 Runtime identity，并获得独立的 Session namespace、配置 digest、并发统计和生命周期。相同 YAML 片段可以复用，但不会因此共享可变 Agent Session 状态。

### 4.4 Agent Profile

Agent Profile 是可信、版本化、不可在运行中自行修改的角色定义。

Profile 包含：

- system/developer instructions；
- default Skills 和 Skill activation policy；
- Platform Tool allowlist；
- Platform capability ceiling；
- Runtime-native capability ceiling；
- Runtime/Profile 固定上限；
- delegation policy；
- visibility 与调用者策略。

Profile 不直接包含 API key、模型 endpoint、Runtime command 或主机凭据。

### 4.5 Agent Definition

Agent Definition 是 Platform 中稳定、可审计的 Agent 身份：

```text
AgentDefinition
  |- agent_id
  |- runtime_spec_id
  |- profile_id
  |- enabled
  |- max_concurrency
  `- config_digest
```

`agent_id` 仍是 Session binding、Task、审计和 UI 使用的稳定身份，但它不再等同于 Runtime implementation。可见性和调用者策略来自已解析 Profile；Agent Definition 不能放宽 Profile 的限制。

### 4.6 ResolvedInvocationPolicy

ResolvedInvocationPolicy 是 Agent Definition 在某个 principal、调用类型和父 Invocation 下解析出的不可变执行授权：

```text
ResolvedInvocationPolicy
  |- agent_definition_digest
  |- invocation_kind          # user | delegate | system
  |- caller_id
  |- platform_capabilities
  |- allowed_platform_tools
  |- allowed_skills
  |- runtime_native_capabilities
  |- artifact_scope
  |- deadline/tool/artifact/child limits
  |- task/concurrency admission ceilings
  `- policy_digest
```

它是 Profile 规则真正进入执行链的合同。Task/Delegation 持久化该快照，`AgentExecutionService`、Capability Gateway、Skill activation 和 restart/retry 共同消费同一份 resolved policy，不在执行时分别重新猜测 Profile。

### 4.7 Agent Invocation

Agent Invocation 是 Agent Definition 的一次具体运行，包括：

- principal；
- Product Session 与 Runtime Session binding；
- Turn 或 Task Execution；
- 本次输入、附件与上下文；
- 本次 ResolvedInvocationPolicy 和实际 capability grant；
- 本次 deadline 与 cancellation；
- 输出事件、Artifact、Usage 与 terminal result。

Invocation 是运行实例，不是长期配置。

### 4.8 Subagent / Child Run

Subagent 是一个拥有父 Invocation 引用的 Agent Invocation：

```text
Subagent = AgentInvocation(parent_invocation_id != null)
```

因此 Subagent：

- 不需要特殊 Runtime SPI；
- 可以使用与父 Agent 相同或不同的 Agent Definition；
- 默认使用独立 Session；
- 默认只接收显式 context packet，而不是完整父会话；
- 只能获得父权限的子集；
- 受到深度、fan-out、可执行限制和取消传播约束。

### 4.9 Skill

Skill 是 data-only 的说明和资源包，用于指导 Agent 组合已有能力。

Skill 可以声明：

- instructions；
- text resources；
- triggers；
- required tools；
- required capabilities；
- 适用 Profile 或能力标签。

Skill 不能：

- 注册 Tool；
- 启动进程；
- 安装依赖；
- 读取未授权 secret；
- 授予 Capability；
- 创建 Agent Definition；
- 绕过 Approval 或 ToolStep。

## 5. 模型所有权

统一 `AgentRuntime` SPI 不意味着统一模型配置方式。RuntimeSpec 必须明确声明模型所有权。

### 5.1 Platform-managed model

Native Runtime 使用 Platform model catalog 和 provider factory：

```text
Platform ModelConfig
  -> ModelProvider
  -> NativeAgentRuntime
```

适用于：

- 主对话模型；
- 审批 Reviewer 的独立模型（当前部署为 Qwen3.5 4B）；
- 本地小模型 Router/Summarizer；
- 远程 OpenAI-compatible 模型。

### 5.2 Runtime-managed model

Codex Runtime 可以使用自己的配置、认证和模型选择：

```text
Platform
  -> CodexRuntimeAdapter
       -> isolated Codex configuration
            -> Codex-selected model
```

Platform 可以提供可选 model hint，但不能假定 Runtime 接受、解释或公开底层模型细节。

### 5.3 模型绑定类型

```yaml
model_binding:
  ownership: platform
  model: approval_reviewer_model
```

或：

```yaml
model_binding:
  ownership: runtime
  hint: ""
```

约束：

1. `platform` ownership 必须引用有效 model catalog alias。
2. `runtime` ownership 不允许 Profile 注入 API key 或 endpoint。
3. Runtime descriptor 必须声明是否支持 model hint/override。
4. model hint 不是权限，也不能改变 Capability policy。
5. 审计应记录可获得的模型标识；Runtime 不提供时记录 `runtime-managed/unknown`，不得伪造。

## 6. Profile 设计

### 6.1 Profile 不是 Prompt 别名

Profile 是紧凑的角色与能力上限配置：

```text
Profile
  = Instructions
  + Skills
  + Platform Tool Allowlist
  + Platform Capability Ceiling
  + Runtime-native Capability Ceiling
  + Fixed Runtime/Profile Limits
  + Delegation Policy
```

仅替换 system prompt 不足以形成安全的专业 Agent。Prompt 负责行为引导，不是安全边界；真正的授权由 ResolvedInvocationPolicy、Runtime sandbox 和 Capability Gateway 强制执行。

### 6.2 Profile 数据模型

概念模型：

```python
class AgentProfile:
    profile_id: str
    version: str
    display_name: str
    instructions_ref: str
    default_skills: tuple[str, ...]
    skill_activation: str
    allowed_platform_tools: frozenset[str]
    platform_capability_ceiling: frozenset[str]
    runtime_native_capability_ceiling: frozenset[str]
    runtime_limits: RuntimeLimitsOverride | None
    delegation: DelegationPolicy
    visibility: str
    callable_by: frozenset[str]
```

Profile 必须生成稳定 digest。Session binding、Task 和 Trace 记录使用的必须是已解析配置 digest，而不是只记录可变文件路径。

Memory、Artifact 和 Interaction 在已有 Platform 服务中继续按 principal、Session、Artifact grant 和现有应用合同治理。除非出现第二个真实 Profile consumer 需要不同策略，MVP 不为它们增加通用 Profile policy 类型。Reviewer 的结构化输出继续由专用 `ApprovalReviewer` Port 校验，不引入通用 `OutputContract` 抽象。

### 6.3 Visibility

建议支持：

| Visibility | 含义 |
|---|---|
| `user` | 可被用户选择并绑定普通 Session |
| `delegate` | 不作为默认聊天 Agent，但可由允许的父 Agent 委派 |
| `system` | 仅 Platform service 可调用，不可被用户或普通 Agent 选择 |

Reviewer 必须是 `system`。

### 6.4 Profile 能力要求

Profile 可以声明 Runtime/模型所需能力，但不绑定模型名称：

```yaml
requirements:
  structured_output: true
  tool_calling: false
  vision: false
  min_context_window: 8192
```

Composition 在启动时验证 RuntimeSpec 是否满足要求。不满足时该 Agent Definition 启动失败，而不是静默降级。安全规则不能依赖模型声明的 Prompt 遵循能力；Profile instructions 只在 Runtime 能提供明确、可验证的 system/developer authority 时作为高优先级行为指令安装，否则该 Profile 必须标记为不需要权威 instructions 或启动失败。

## 7. Agent Definition 与配置

### 7.1 建议配置结构

以下 YAML 用于表达 typed configuration contract，也可作为导入/导出格式；它不是配置页面直接编辑的 live source of truth。初始化后的 canonical 配置由 Config Registry 中的 versioned ManagedConfig revision 管理。

```yaml
runtime_specs:
  native_main:
    implementation: native
    model_binding:
      ownership: platform
      model: primary_model
    max_concurrency: 4

  native_approval_reviewer:
    implementation: native
    model_binding:
      ownership: platform
      model: approval_reviewer_model # 当前部署解析为 Qwen3.5 4B
    max_concurrency: 1

  codex_default:
    implementation: codex
    command: ["codex", "app-server"]
    home: agents/codex/home
    cwd: agents/codex/workspace
    sandbox: read-only
    approval_policy: never
    native_capabilities: [workspace_read, command_execution]
    profile_instructions:
      authority: required
    model_binding:
      ownership: runtime
      hint: ""
    max_concurrency: 1

agent_profiles:
  assistant:
    instructions: prompts/assistant.md
    default_skills: [general]
    allowed_platform_tools: ["*"]
    platform_capability_ceiling: ["*"]
    runtime_native_capability_ceiling: []
    visibility: user
    delegation:
      allowed: true
      max_depth: 1
      max_children: 3

  approval_reviewer:
    instructions: prompts/approval-reviewer.md
    default_skills: []
    allowed_platform_tools: []
    platform_capability_ceiling: []
    runtime_native_capability_ceiling: []
    visibility: system
    callable_by: [approval_service]
    runtime_limits:
      max_iterations: 1
      max_output_tokens: 256
    delegation:
      allowed: false

  coder:
    instructions: prompts/coder.md
    default_skills: [coding, repository]
    allowed_platform_tools: [read_file, write_file, shell]
    platform_capability_ceiling: [host_read, host_write, shell]
    runtime_native_capability_ceiling: [workspace_read, command_execution]
    visibility: delegate
    delegation:
      allowed: false

agents:
  knoa:
    runtime: native_main
    profile: assistant
    enabled: true

  reviewer_agent:
    runtime: native_approval_reviewer
    profile: approval_reviewer
    enabled: true

  codex:
    runtime: codex_default
    profile: coder
    enabled: true

default_agent: knoa
```

### 7.2 当前对象映射

| 当前对象 | RuntimeSpec | Model | Profile | 目标身份 |
|---|---|---|---|---|
| `knoa` | `native_main` | Platform 主模型 | `assistant` | 用户主 Agent |
| `reviewer_agent` | `native_approval_reviewer` | 当前部署为独立 Qwen3.5 4B | `approval_reviewer` | Platform System Agent |
| `codex` | `codex_default` | Codex 自管理 | `coder` 或其他 Codex Profile | 用户或委派 Agent |

架构事实是 Reviewer 使用独立的 platform-managed model binding，不属于 `knoa` 主 Agent 或其 fallback。当前产品部署将 `approval_reviewer_model` 指向 Qwen3.5 4B；平台应审计解析后的 provider/model identity，而不是从 RuntimeSpec 或 Agent 名称推断实际模型。

### 7.3 配置真相与管理页面

RuntimeSpec、Profile、Agent Definition、Model Binding 和 Skill 引用必须通过统一 `ConfigurationService` 管理。配置页面、CLI 和 YAML import 都只是客户端，不能各自成为配置真相或绕过同一套校验/发布链。

```text
typed draft
  -> full validation/reference check
  -> Runtime/policy preflight
  -> immutable Config Revision
  -> build affected Agent generation
  -> atomic switch
  -> bounded drain old generation
```

配置页面以 Agent Definition 为主要入口，同时提供 Models & Runtimes、Profiles、Skills & Tools、effective policy preview、revision diff、发布影响和 rollback。Secret 只通过 write-only Secret Store slot 管理，不进入 Profile、Revision diff 或普通导出。

完整的数据模型、API、页面信息架构和 apply class 见 `docs/knoa-configuration-control-plane-design.md`。

## 8. Runtime 组合与生命周期

### 8.1 统一 SPI 保持不变

`AgentRuntime` 继续负责：

- descriptor 与 capability negotiation；
- create/resume/delete Runtime Session；
- start Turn；
- event stream；
- interrupt/steer/interaction resolution；
- health/drain。

Profile、Agent Definition 和 Delegation 不应污染中立 Runtime 协议中的 Tool execution、Platform repository 或 Task 对象。

### 8.2 一个 Agent Definition 一个 Runtime identity

Composition Root 根据 `RuntimeSpec + Profile + Agent Definition` 构造具体 `AgentRuntime` 实例。

即使两个 Agent Definition 使用相同 implementation 或 RuntimeSpec 片段，也必须具有：

- 独立 `agent_id`；
- 独立配置 digest；
- 独立 Runtime Session namespace；
- 独立并发和 drain 统计；
- 独立 Profile instructions；
- 独立 Tool inventory projection。

可以共享无状态 provider transport 或底层连接池，但不能共享可变 Agent Session 状态。

### 8.3 Runtime implementation 仍是可信小集合

首批继续只允许：

```text
native
codex
```

配置不能填写 Python import path、动态下载包或任意 executable runtime type。未来新增 Runtime implementation 必须通过代码发布和契约测试进入可信集合。

### 8.4 建议的管理组件

```text
AgentDefinitionResolver    # 一次性解析 RuntimeSpec + Profile、校验兼容性并计算 digest
AgentManager               # 管理已构造 Runtime 实例、容量、健康与 drain
AgentExecutionService      # 执行普通 Conversation/Task Turn 和 ResolvedInvocationPolicy
DelegationService          # 解析父子策略、创建 Child Task，并管理父子关系
```

RuntimeSpec 和 Profile 在配置中可以分别保存和复用，但 MVP 不为它们建立三个独立运行时 Catalog service。`AgentDefinitionResolver` 是单一的组合、校验和调用资格解析边界，不管理 live Runtime 状态；`AgentManager` 只管理已构造的 Agent Definition Runtime，不负责重新解释 Profile。

Agent 解析必须携带可信调用类型和调用者：

```text
resolve(agent_id, invocation_kind, caller_id)
  where invocation_kind = user | delegate | system
```

解析成功后返回不可伪造的 resolved handle。Session binding 和执行阶段租用该 handle，不再用裸 `agent_id` 重新走 user resolver。Runtime 所需 MCP transport、Profile instruction authority 和 native capability support 必须来自 descriptor/RuntimeSpec，不能继续通过 `agent_id != "knoa"` 等名称分支推断。

## 9. Capability 与 Tool 权限

### 9.1 ResolvedInvocationPolicy

Agent Definition 只表达静态角色上限。每次 user、delegate 或 system Invocation 开始前，Platform 必须解析并固化：

```text
ResolvedInvocationPolicy
  = principal policy
  ∩ channel/session policy
  ∩ Agent Profile ceilings
  ∩ parent Invocation policy（仅 Child）
  ∩ delegation request（仅 Child）
  ∩ Platform invocation-kind policy
```

该快照必须至少包含：

- Agent Definition digest；
- invocation kind 与可信 caller；
- Platform capabilities；
- allowed Platform Tool names；
- allowed Skill IDs；
- Runtime-native capabilities；
- Artifact scope；
- wall-clock、Gateway tool-call、Artifact byte、child count 等 Platform 可执行限制；
- Task/Agent concurrency admission ceiling 或其稳定 policy reference。

Child Task/Execution 持久化该快照。该快照是创建时的授权上限；`AgentExecutionService` 在 retry/restart 时复用它，并与当前 principal policy 再次相交。当前 policy 可以进一步撤权，但任何重算都不能扩大创建时的 Child 权限。

### 9.2 Platform Capability Plane

普通 Invocation：

```text
effective_capabilities
  = principal policy
  ∩ channel/session policy
  ∩ Agent Profile capability ceiling
  ∩ current request grant
```

Child Invocation：

```text
child_effective_capabilities
  = parent effective capabilities
  ∩ child Profile capability ceiling
  ∩ delegation request
  ∩ Platform delegation policy
```

Tool inventory：

```text
visible_tools
  = registered tools
  ∩ ResolvedInvocationPolicy.allowed_platform_tools
  ∩ tools permitted by effective capabilities
```

Capability grant 的 digest 必须包含 allowed Tool names，而不仅是 capability 集。Gateway 在 `tools/list` 和 `ToolStep` commit 两处都强制相同 allowlist，避免模型手工调用未展示但 capability 相同的 Tool。

### 9.3 Runtime-native Capability Plane

Codex 等 Runtime 可能拥有不经过 Platform Tool/MCP 的原生行动：

```text
Runtime-native capabilities
  |- workspace_read
  |- workspace_write
  |- command_execution
  `- native_file_edit
```

它们由以下交集治理：

```text
effective_runtime_native_capabilities
  = RuntimeSpec declared capabilities
  ∩ Profile runtime-native ceiling
  ∩ ResolvedInvocationPolicy
  ∩ Runtime sandbox/workspace policy
```

Child Invocation 不直接把父 Runtime 的 native capability 集套到另一种
Runtime 上。Platform 使用显式的跨平面映射保持“不越权”语义：父 policy 的
`host_read` 可授权 `workspace_read`，`host_write` 可授权
`workspace_write/native_file_edit`，`shell` 可授权 `command_execution`；之后再与
Child RuntimeSpec、Profile 和持久化 snapshot 取交集。没有对应 Platform
authority 的 native capability 必须被移除。

每次 Turn 都把 resolved native capability 集交给 Runtime adapter。Codex
adapter 只能在 `read-only` 与 `workspace-write` 间精确执行或降权；无法精确
执行的组合 fail closed。`danger-full-access` 不进入受管 RuntimeSpec contract。

Runtime 必须在 descriptor/startup check 中证明这些能力可以被配置和限制。无法证明禁用或收窄的 Runtime 不能用于要求更严格边界的 Profile。Platform Tool、外部 MCP、网络业务系统、Task、Memory 和其他 Platform 对象仍只能通过 Capability Gateway。

Profile instructions 不承担 Runtime-native capability 的安全 enforcement；即使模型忽略 Prompt，sandbox 和 Runtime policy 仍必须阻止未授权行动。

### 9.4 权限不变量

1. Profile 只能收窄权限，不能扩大 principal 或 parent 权限。
2. Skill 的 `required_capabilities` 只是激活前置条件，不是授权声明。
3. 父 Agent 不能将自己没有的 Capability 委派给子 Agent。
4. Runtime-managed model 不改变 Platform Capability enforcement。
5. Codex 自带 MCP、Apps、Web Search 和内置 Agent inventory 必须继续隔离并核验；允许的 native action 必须显式出现在 RuntimeSpec 和 resolved policy 中。
6. Reviewer 的 Platform grant 和 Runtime-native capability 集永远为空，`allow_tools=false`。
7. Platform Tool 执行仍经过 schema、policy、approval、stale check 和 ToolStep checkpoint。
8. Task/retry/restart 不得丢弃已持久化的 Invocation policy 并按 principal 全量权限重新签发 grant。

## 10. Reviewer System Agent

### 10.1 定位

Reviewer 是：

```text
Agent Definition: reviewer_agent
RuntimeSpec: native_approval_reviewer
Model binding: platform-managed reviewer model（当前部署为 Qwen3.5 4B）
Profile: approval_reviewer
Visibility: system
Caller: ApprovalService only
```

它不是：

- Knoa 主 Agent 的模型；
- Knoa 主 Agent 的 fallback；
- 用户可选择 Agent；
- 普通 delegate Agent；
- Capability 或 Approval 权威。

### 10.2 调用边界

```text
ToolStep
  -> deterministic Tool Policy
  -> ApprovalService
  -> ApprovalReviewerPort.review(request)
  -> reviewer_agent Invocation
  -> approve | deny | escalate recommendation
  -> Platform mode/risk ceiling
  -> human or automatic Approval resolution
  -> stale revalidation
  -> ToolStep commit
```

Reviewer 继续通过专用 `ApprovalReviewer` Port 调用，不通过 Agent-facing `spawn_subagent` Tool 调用；Port 内部统一进入 `AgentExecutionService.execute_system_turn()`，因此仍受 resolver、`callable_by`、policy observer、deadline、generation lease 和 tool-less grant 约束。

### 10.3 安全不变量

- 只接收当前 human instruction、规范化 proposed action 和 Platform verified facts；
- 不接收普通 Session memory、episodic memory 或通用 Skill；
- 无 Tool、无 Capability、无 Task creation；
- 单轮、有界输出、严格 schema；
- 结构化结果由专用 `ApprovalReviewer` Port 校验，不依赖通用 Profile output contract；
- timeout、模型错误、非法输出均 `escalate`；
- Platform 是唯一 Approval 状态写入者；
- high risk 永远不因 Reviewer 单独决定而自动执行。

## 11. Subagent Delegation

### 11.1 为什么复用 Task

Child Run 需要：

- 独立并发；
- detached Session；
- 持久状态；
- 取消、审批和交互；
- Artifact 和 Trace；
- 重启后的治理；
- 跨 Client 可见性。

这些都已由 Task 系统提供。因此 Child Run 应以受治理的 Task Execution 为持久载体，不新增第二套 Subagent executor 或状态机。

### 11.2 Delegation Service

```text
Parent Invocation
  -> spawn_subagent request
  -> DelegationService
       |- authenticate parent resolved handle/policy
       |- resolve target Agent Definition for invocation_kind=delegate
       |- verify visibility/callable_by
       |- enforce max depth/fan-out
       |- derive ResolvedInvocationPolicy subset
       |- create target-agent detached Session
       |- copy authorized Artifacts into child Session
       |- create unclaimable staged child Task
       |- persist immutable DelegationLink + policy snapshot
       `- activate child Task
  -> child AgentExecutionService
```

DelegationService 是唯一能把 Agent-facing delegation request 转换为 Child Task 的 Platform 服务。

### 11.3 Agent-facing 工具

MVP 提供两个 Agent-facing Tool：

```text
spawn_subagent
subagent(action=get|await|cancel)
```

工具只传递 Platform ID 和结构化数据，不返回 Runtime 私有 Session ID、进程句柄或凭据。

`await` 只对 Conversation parent 开放；Task parent 即使能调用该 Tool，也只能 `get`、`cancel` 自己创建的 detached Child，不能借此形成同步等待。

### 11.4 Spawn 请求

概念模型：

```python
class SpawnSubagentRequest:
    target_agent_id: str
    goal: str
    context: DelegationContext
    requested_capabilities: frozenset[str]
    requested_platform_tools: frozenset[str]
    requested_skills: frozenset[str]
    deadline_seconds: float
    mode: Literal["join", "detached"]
    idempotency_key: str
```

`DelegationContext` 只允许显式内容：

- task brief；
- selected message excerpts；
- Artifact references；
- workspace references；
- structured facts；
- 简短结果要求。

不得默认复制父 Agent 完整 transcript、private checkpoint、secret 或全部 Memory。

### 11.5 Child result

Child Run 返回类型化结果：

```python
class SubagentResult:
    delegation_id: str
    child_task_id: str
    status: str
    summary: str
    artifacts: tuple[ArtifactRef, ...]
    warnings: tuple[str, ...]
    usage: UsageSummary
```

父 Agent 默认只接收 summary、Artifact references、warnings 和 usage，不把子 Agent 全量 event trace 注入父模型上下文。若某个业务调用者需要结构化结果，应像 ApprovalReviewer 一样由专用 application Port 定义和校验，不先引入通用 Profile output contract。

### 11.6 Join 与 detached

`join`：

- 只允许 Conversation Invocation 使用；
- target Agent Definition 必须不同于 parent Agent Definition，避免父 Turn 占用唯一 Runtime capacity 时等待同一 Agent 的 Child；
- 父 Conversation Turn 等待子任务终态；
- cancellation 从父向子传播：父取消会取消未完成子任务；子失败只作为结果返回，不自动使父失败；
- 等待时间受父 Turn deadline 限制。

Task Invocation 在 MVP 中不得使用 `join`。否则多个父 Task 可能占满 `TaskExecutor` slot 并同时等待排队中的 Child，形成调度死锁。Task parent 只能创建 `detached` Child；确有 Task-to-Task join 需求后，再设计 parent suspension 或 child capacity reservation。

`detached`：

- 父 Turn 创建后继续；
- Child Task 独立持久运行；
- 完成结果通过 Task event/notification 呈现；
- 父 Turn 取消默认不自动取消 Child；需要显式 `cancel`，且该策略记录在创建时 policy 中；
- 父 Turn 不承诺在 Core 重启后自动恢复并继续汇总。

### 11.7 MVP 限制

- 最大 delegation depth：1；
- 每个父 Invocation 最大 child 数：3；
- 每个父 Invocation 最大并行 child 数：3；
- Child Agent 默认不能再次 delegate；
- 不允许 target 为 `system` Agent；
- 不允许 target 为当前 principal 不可使用的 Agent；
- 不允许 capability escalation；
- Child 必须持久化 ResolvedInvocationPolicy；
- wall-clock、Gateway tool-call、Artifact bytes、child count 和 Task capacity 必须有 Platform 可执行硬上限；
- token、iteration 和 completion token 在 MVP 中不是 per-invocation 硬预算，只能使用 Runtime/Profile 固定上限并记录 usage；
- spawn 必须幂等；
- parent 和 child 必须属于同一 principal，跨 principal delegation 不在 MVP 范围。

## 12. Delegation 持久化

现有 `TaskRecord.parent_task_id` 只覆盖 Task-to-Task 关系，不能表达 Conversation Turn 创建 Child Task。建议增加不可变 DelegationLink：

```text
agent_delegations
  |- delegation_id
  |- principal_id
  |- parent_kind              # conversation_turn | task_execution
  |- parent_id
  |- parent_agent_id
  |- parent_agent_digest
  |- child_agent_id
  |- child_agent_digest
  |- child_session_handle
  |- child_task_id
  |- mode                     # join | detached
  |- depth
  |- invocation_policy_json
  |- invocation_policy_digest
  `- created_at
```

DelegationLink 不拥有运行状态、blocking reason、Approval、Attempt、terminal result 或 result summary。它只保存父子关系和创建时治理快照：

```text
DelegationLink
  -> child_task_id
       -> TaskState / phase / Approval / Attempt / Trace / final_summary
```

Task 是 Child Run 生命周期的唯一事实权威。API 和 UI 通过 `child_task_id` 关联查询 Child Task 并派生当前状态；不得在 Delegation 表复制第二套 authoritative state。若未来为了查询性能增加 projection，它必须明确是可重建的非权威缓存，而不是新的状态机。

## 13. Session 与配置版本

### 13.1 Binding

Product Session 继续绑定稳定 `agent_id`，同时持久化：

- runtime spec digest；
- profile digest；
- resolved Agent Definition digest；
- Runtime Session reference；
- binding epoch。

### 13.2 配置更新

Agent Definition 在自己的 generation 内完全不可变，但 Config Revision 可以在线发布新的 generation。热生效采用 build/validate/swap/drain，不修改 live Runtime 对象，也不把新 Prompt、模型或权限注入正在执行的 Invocation。

发布与恢复规则：

1. 每个 Config Revision 一次性解析全部 RuntimeSpec、Profile、Skill 内容和 Agent Definition digest。
2. 受影响的新 generation 先完整构建并校验 instruction authority、native capability/sandbox、引用和健康状态；任一关键 preflight 失败都不切换 applied revision。
3. 通过预检后原子切换 active generation；新 Invocation 只使用新 definition，旧 active Invocation 继续使用创建时 generation 和 ResolvedInvocationPolicy。
4. 每个 Agent Definition 最多保留一个 bounded draining generation；它不接受新 Invocation，deadline 后中断 active Turn，并在 lease 归零后销毁。MVP 不提供历史 generation 路由或长期池化。
5. Child spawn 时立即固定 definition digest；Child Task 先以 `delegation_staged` 创建，Executor 不能 claim，只有 policy snapshot 与 DelegationLink 完成后才激活。该 staged protocol 避免为多个 repository 引入通用 UnitOfWork，同时关闭“未授权 Child 抢跑”的竞态。
6. digest 不同的 queued/paused Child Task 明确失败为 `agent_definition_changed`，由父调用者或用户重新委派；不得为了排队任务长期保留旧 generation。
7. Idle Product Session 在下一 Turn rebind 当前 generation；若 RuntimeSpec、Prompt authority 或 Runtime 私有 Session 不兼容，则创建新 Runtime Session，保留 Product conversation 并记录 binding epoch 变化，不强行 resume 旧私有状态。
8. Core/组件重启后，binding digest 相同才允许 resume；不同则执行同一 rebind/fail-closed 规则，不静默恢复。
9. Reviewer 使用短期 Session，切换后新审批自然使用当前 generation。
10. Profile instructions 和引用 Skill 内容变化必须产生新 digest。
11. Agent Definition digest 只覆盖 Platform 可控制的 resolved non-secret 配置、Profile、Skill 内容和 Runtime implementation version。Codex 等 runtime-managed model 的真实 model/config identity 另行记录；不可获得时标记 unknown，不伪称 digest 能覆盖外部可变状态。
12. 生产 Codex RuntimeSpec 必须使用隔离、版本固定且可计算配置摘要的 home/config；不允许 delegate/system Agent 继承可变的 ambient `~/.codex`。
13. 动态紧急撤权 registry 属于后续安全增强；当前通过 cancellation、grant TTL/revoke 和 Tool fingerprint fail-closed 收窄。任何热发布或重算都不能扩大 active Invocation 创建时权限。

## 14. Skill 导入与 Profile 组合

### 14.1 Canonical Skill format

内部继续使用安全的 canonical Skill package：

```text
skill.yaml
instructions.md
resources/*.{md,txt,json,yaml,yml}
```

外部格式如 `SKILL.md` 可以通过 importer 转换为 canonical package，但运行时只消费校验后的内部格式。

### 14.2 两类扩展必须分离

Data-only Skill：

- instructions；
- examples；
- templates；
- text references；
- tool/capability requirements。

Executable Extension：

- scripts；
- Python/JS code；
- dependency installation；
- background service；
- MCP Server；
- custom Tool。

Executable content 不能作为 Skill 导入 Core。它必须走受治理的 MCP/extension 安装、进程隔离、版本固定和本地 policy。

### 14.3 Skill 激活

有效 Skill 集：

```text
active_skills
  = ResolvedInvocationPolicy.allowed_skills
  ∩ installed and enabled skills
  ∩ trigger/explicit selection
  ∩ available tools
  ∩ effective capabilities
```

Reviewer Profile 默认不加载普通 Skill。

## 15. 事件、观测与审计

每个 Invocation 和 Delegation 必须可关联：

```text
principal_id
session_handle
agent_id
agent_definition_digest
runtime_spec_id/digest
profile_id/digest
runtime_session_ref
turn_id or task_execution_id
parent_invocation/delegation_id
model identity when available
capability grant digest
usage and terminal status
```

Child Run 的 UI/事件树建议：

```text
Parent Task/Turn
  |- Delegated: researcher
  |    `- completed, 12s, 3 tool calls, 1 artifact
  `- Delegated: codex
       `- waiting approval
```

不向用户展示 Runtime 私有 prompt、secret、完整 chain-of-thought 或内部 credential。

## 16. 安全与失败语义

### 16.1 Fail closed

- 未知 Runtime implementation：拒绝启动；
- Profile requirement 不满足：Agent Definition unavailable；
- Skill requirement 不满足：不激活 Skill；
- Delegation target 不可调用：拒绝 spawn；
- capability derivation 失败：拒绝 spawn；
- Child result schema 非法：标记 failed，不让父 Agent当作可信结构化事实；
- Reviewer 异常：`escalate` 并保留人工 Approval；
- Runtime-managed model identity 不可获得：记录 unknown，不猜测。

### 16.2 Prompt injection

- Child goal、Artifact、Tool result 和 imported Skill content 均可能包含不可信指令；
- Platform policy、Capability 和 sandbox 不依赖 Prompt 执行；
- Profile instructions 只在 Runtime 能提供可验证的 system/developer authority 时作为高优先级行为指令安装；
- Runtime 不支持权威 instructions 时，不得把普通 user text 包装成“等价 system prompt”；依赖该能力的 Agent Definition 必须启动失败；
- verified facts 与 untrusted content 使用类型和标签分离；
- 子 Agent 不能把文本声明当作 Capability grant；
- 父 Agent 汇总 Child result 时必须保留其来源和可信级别。

### 16.3 资源治理

MVP 的 Platform 可执行硬限制包括：

- wall-clock deadline；
- Gateway max tool calls；
- max child count；
- max Artifact bytes；
- RuntimeSpec/Agent concurrency；
- principal/global Task capacity。

其中 per-invocation 限制和稳定的 admission ceiling/reference 必须进入 ResolvedInvocationPolicy；当前并发占用量由 AgentManager/Task admission 在执行时判断，不把瞬时容量复制进快照，也不做 capacity reservation。模型 token、iteration 和 completion token 在 MVP 中属于 Runtime/Profile 固定上限与 usage 观测，不描述成可预留的 per-invocation 硬预算。后续只有在 `AgentRuntime` 出现真实的 typed turn-limit consumer 时才扩展统一 SPI。

## 17. 组件关系

目标结构：

```text
ConversationService / TaskExecutor
                |
                v
       AgentExecutionService
                |
       ResolvedInvocationPolicy
                |
     AgentDefinitionResolver
                |
           AgentManager
         /              \
 NativeAgentRuntime   CodexAgentRuntime
         |              |
         v              v
 Runtime-native    Turn-scoped MCP Grant
 sandbox/policy             |
                            v
                   Capability Gateway

Parent Agent Tool Call
                |
                v
        DelegationService
                |
        Child Task/Execution
                |
                `----> AgentExecutionService

ApprovalService
                |
       ApprovalReviewer Port
                |
                `----> reviewer_agent
```

关键依赖规则：

1. Runtime 不依赖 Platform repository 或 Task model。
2. DelegationService 依赖 Agent Definition resolver、Session、Task 和 policy derivation application ports。
3. Agent-facing Tool 只调用 DelegationService，不直接调用 AgentManager。
4. ApprovalService 通过专用 Reviewer Port，不通过 Delegation Tool。
5. SkillCatalog 不调用 Runtime、Task 或 Capability grant service；它只根据 resolved allowed Skill IDs、可见 Tool 和 capability 选择 data-only context。

## 18. 当前设计的替代方案

### 18.1 Profile 直接配置模型

```text
Profile -> model + prompt + tools
```

拒绝作为主模型：

- 混淆角色与部署；
- Codex 的模型由 Runtime 自管理，无法统一表达；
- 更换审批模型会修改角色定义和 digest；
- API key、endpoint 等部署细节容易进入 Profile。

Profile 可以声明模型能力要求，但模型绑定属于 RuntimeSpec。

### 18.2 `agent_id` 继续直接代表 Runtime

拒绝：

- 无法复用一种 Runtime implementation 创建多个专业 Agent；
- Qwen Reviewer 继续依赖 composition 特例；
- Codex 既是技术实现又是角色，命名含义不稳定；
- 无法独立版本化 Prompt、Skill 和权限上限。

### 18.3 新建独立 Subagent Runtime

拒绝：

- Subagent 是父子调用关系，不是执行协议；
- 会重复 Session、Task、Approval、Artifact、Trace 和取消状态机；
- 不利于 Knoa/Codex 作为 child 的统一选择。

### 18.4 让普通 Agent 调用 Reviewer Agent

拒绝：

- 审批调用合同属于 Platform policy；
- 普通 Agent 不应选择审核输入、证据或风险上限；
- 容易形成自批自执行的循环。

### 18.5 无限递归 Agent swarm

拒绝 MVP：

- 成本和资源不可预测；
- 取消、审批和失败传播复杂；
- Prompt injection 与 capability laundering 风险显著；
- 当前产品需求可由单层、有界 delegation 满足。

### 18.6 强制 Codex 所有行动都转换为 Platform Tool

拒绝作为默认方案：

- Codex 的 command execution、workspace read/write 和 native file edit 是其 Runtime 核心执行能力；
- 全部禁用会使 Codex 失去作为 coding Agent 的主要价值；
- 把 Runtime 内部每个动作重新包装为 Platform Tool 会耦合 Codex 协议细节与 Platform Tool domain。

目标采用双执行边界：Runtime-native action 由 RuntimeSpec、Profile ceiling、ResolvedInvocationPolicy 和 sandbox 治理；Platform Tool、MCP 和外部副作用由 Capability Gateway 治理。要求绝对无 native side effect 的 Profile 可以把 native capability ceiling 设为空，并在启动时验证 Runtime 确实能做到 fail closed。

## 19. 实施基线与后续演进

本设计的 Phase 1 至 Phase 3 已完成正向落地；Phase 4 的 data-only Skill 管理、Profile Skill allowlist 与配置控制面已完成基础闭环。Phase 5 保持为真实使用数据驱动的后续演进，不作为当前架构的隐式复杂度。

### Phase 1：术语与配置模型

- 引入 RuntimeSpec、Agent Profile、Agent Definition 和 ResolvedInvocationPolicy 配置/合同类型；
- 将这些类型纳入 versioned ManagedConfig snapshot，由 ConfigurationService 统一校验和发布；
- 将当前三个 Agent 映射到新结构；
- 保持现有 `AgentRuntime` SPI；
- 为 Agent binding 和 Trace 增加 runtime spec/profile/definition digest；
- 将 `KnoaAgentRuntime` 的命名迁移为 `NativeAgentRuntime`，或至少在文档和 descriptor 中使用 `native` implementation 名称。

### Phase 2：统一 Composition 与执行策略

- 用单一 AgentDefinitionResolver 替代 `reviewer_agent`、`codex` 的手工分支语义；
- 仍只支持受信任的 `native` 与 `codex` implementation；
- 将 Reviewer Prompt、固定 Runtime limits 和 Tool policy 移入 Profile；
- 将 resolved policy 传入 Capability grant、Tool list/commit 和 Skill activation；
- 用 descriptor/RuntimeSpec 选择 MCP transport，不再按 `agent_id` 名称推断；
- 明确并验证 Codex native capability、sandbox 和 Profile instruction authority；
- 支持受影响 Agent generation 的 build/health/atomic swap/bounded drain；
- 保持 Reviewer 专用 Port 和 Platform-only visibility。

### Phase 3：单层 Delegation

- 增加不可变 DelegationLink 和 Invocation policy snapshot；
- 增加 target-agent detached Session 创建；
- 增加 spawn 与紧凑 get/await/cancel application API；
- 复用 TaskExecutor 执行 Child Run；
- 实现 depth、fan-out、可执行限制和 capability/tool/Skill subset enforcement；
- MVP 仅允许 Conversation parent 使用 join；Task parent 只能 detached；
- UI/Channel 展示父子执行关系。

### Phase 4：Skill import 与专业 Profile

- 增加外部 data-only Skill importer；
- 建立 researcher/coder 等 delegate Profile；
- Profile 默认 Skill 与 query activation 合并；
- 增加 Profile/Runtime requirement validation。

### Phase 5：评测后扩展

只有在真实使用证明需要时，才考虑：

- depth > 1；
- Task-to-Task join、parent suspension 或 child capacity reservation；
- Router Agent；
- Planner/Executor 分离；
- 远程 Agent service/A2A；
- 动态 Runtime placement；
- 成本感知模型路由。

## 20. 验收不变量

后续实现必须证明：

1. `AgentRuntime` SPI 对 Native 与 Codex 保持统一。
2. Reviewer 与 Knoa 主 Agent 使用独立 platform-managed model binding；当前部署解析出的 Reviewer model identity 为 Qwen3.5 4B。
3. Profile 不包含模型 endpoint、API key 或 Runtime command。
4. Agent Definition digest 覆盖 Platform 可控制的 resolved RuntimeSpec、Profile、Skill 内容和 implementation version；runtime-managed 外部 identity 单独记录且不可获得时标记 unknown。
5. Reviewer 不能被用户选择、不能被普通 Agent delegate、不能获得 Tool。
6. 每个 Invocation 使用持久或可审计的 ResolvedInvocationPolicy；Child 的 capability、Tool 和 Skill 集永远是 parent policy 的子集。
7. Capability grant 在 list 与 commit 两处强制 allowed Tool names，retry/restart 不扩大创建时权限。
8. Child Run 使用独立 Session，并通过现有 Task/Approval/Artifact/Trace 治理；DelegationLink 不拥有第二套生命周期状态。
9. Conversation join 不能指向 parent 自身的 Agent Definition，父取消能可靠传播到 Child；Task parent 在 MVP 中不能 join；detached child 的继续策略明确可审计。
10. Agent Definition 在自己的 generation 内不可变；配置通过新 generation 原子热发布，旧 generation 只做有界 drain，digest mismatch 不静默恢复或扩大权限。
11. Skill 激活不会授予 Tool 或 Capability，并受 ResolvedInvocationPolicy.allowed_skills 约束。
12. Codex Runtime-native action 只能在显式 RuntimeSpec、Profile ceiling、resolved policy 和 sandbox 的交集内发生；Platform/MCP/external action 不能绕过 Capability Gateway。
13. Profile Prompt 不作为安全 enforcement；Runtime 无法提供所需 instruction authority 时 Agent Definition 启动失败。
14. per-invocation 硬预算只声明 Platform 或 Runtime 当前确实能执行的字段。
15. 未知、超时、非法输出和结果不明均 fail closed。

## 21. 最终架构表述

Knoa 的 Agent 模型最终统一为：

```text
RuntimeSpec
  定义怎么执行、模型由 Platform 还是 Runtime 管理，以及 Runtime-native capability 与 sandbox。

Agent Profile
  定义角色、Prompt、Skill、Platform Tool 上限、Runtime-native capability ceiling 和委派策略。

Agent Definition
  以稳定 agent_id 组合一个 RuntimeSpec 和一个 Profile。

ResolvedInvocationPolicy
  把 principal、调用类型、Profile 和父 Invocation 约束固化为本次执行的 Tool、Skill、Capability、Artifact 和硬限制快照。

Agent Invocation
  是 Agent Definition 在某个 principal、Session、Turn 或 Task 中的一次运行。

Subagent
  是由 Platform Delegation Service 创建、具有父 Invocation、权限与已声明执行限制均受约束的 Agent Invocation。

Skill
  是可导入、可选择的 data-only 知识包，只指导如何使用已有能力。

Capability Gateway
  是 Platform Tool、MCP 和外部副作用的唯一行动授权边界；Runtime-native action 由 RuntimeSpec、Profile ceiling、ResolvedInvocationPolicy 和 sandbox 共同治理。
```

当前三个 Agent 的准确解释为：

```text
knoa
  = native-main RuntimeSpec
  + assistant Profile

reviewer_agent
  = native-approval-reviewer RuntimeSpec
  + approval-reviewer Profile
  + 当前部署的 Qwen3.5 4B dedicated model binding

codex
  = codex-default RuntimeSpec
  + coder Profile
  + Codex runtime-managed model
```

这套分层保留统一 Agent Runtime 的技术边界，同时用 ResolvedInvocationPolicy 把静态角色真正落到执行授权；Task 仍是 Child Run 唯一生命周期权威，Runtime-native 与 Platform capability 各自高内聚、边界明确。
