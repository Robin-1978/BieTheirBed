# Knoa 配置控制面与管理页面架构设计

> 状态：正向目标设计；当前实现偏差会明确标记
>
> 日期：2026-08-16
>
> 范围：系统配置真相、配置页面、generation、校验、热生效、Node 组件重启、Secret 与审计
>
> 关系：产品对象、配置入口与归属以 `docs/knoa-product-domain-architecture.md` 为准；本文为 RuntimeSpec、Profile、Agent Definition、Model Binding、Skill 和治理策略提供发布与应用机制

> 多节点演进：本文描述当前 Node-local `ManagedConfig` 基线。目标配置按 `WorkspacePublishedSpec + WorkspaceNodeConfigGeneration + NodeOverlayGeneration + MaterializedConfigCandidate` 拆分；用户从 Workspace 管理共享资源和 Node Desired State，Node 保留 Secret、物理事实与最终执行权；同一字段不得双写

> 当前实现：SQLite Config Registry、immutable Revision、history/rollback API 和 UI 已存在。正向产品保留 Registry、optimistic Draft、validate/preflight/publish、desired/applied generation、Runtime generation swap/drain、Skill digest 和最小影响 reload；版本树与 rollback 不再是目标产品能力，可在重构时删除。YAML 仅在首次启动导入，之后不覆盖 Registry。

## 1. 决策

Knoa 不再把 YAML 文件当作长期运行配置的唯一真相，也不让配置页面直接修改配置文件。

目标模型：

```text
Bootstrap file / environment
  只负责启动 Core、定位数据库和建立初始管理员
                         |
                         v
ConfigurationService + Config Registry
  唯一配置写入口与 canonical source of truth
                         |
             +-----------+-----------+
             |                       |
             v                       v
      Management UI             CLI / Import
      typed editor              same typed API
                         |
                         v
                ConfigApplyCoordinator
                         |
          validate -> preflight -> publish
                         |
                         v
             atomic switch + bounded drain
```

核心决定：

1. 配置页面、CLI 和导入工具都只调用同一个 typed Configuration API，不各自解释 YAML。
2. SQLite Config Registry 保存当前 Published Spec、Draft、generation/digest 和应用状态，是初始化后的唯一运行配置真相。
3. YAML 只用于 bootstrap、备份、导入和导出；初始化完成后不再与数据库隐式 merge。
4. 每次发布递增 Desired Generation 并生成 digest；Agent Invocation 与 TaskExecution 固化解析快照/digest。
5. 发布前必须完成全量 schema、引用、权限、Runtime compatibility 和连接健康预检。
6. 大部分配置对新 Invocation 热生效；正在执行的 Invocation 继续使用创建时快照。
7. Runtime/模型变化通过“构建新 generation、健康检查、原子切换、旧 generation 有界 drain”生效，不原地改写 live Runtime。
8. 不保留长期多版本 Runtime 池；每个 Agent Definition 最多存在一个 active generation 和一个短期 draining generation。
9. 不支持热加载的 Node 配置通过页面触发目标 Node 组件或 Node service 受控重启；host bootstrap 字段需要本机操作，不伪称 in-process hot reload。
10. Secret 只写入 Secret Store，Published Spec 只保存 `secret_ref`，API 永不回传明文。

## 2. 为什么不能继续依赖配置文件

早期 `AppConfig + PersistentConfigController` 只能校验 Pydantic 配置、原子写入 override YAML，并让少量标量字段返回 `restart_required`。它适合早期系统，但不适合 RuntimeSpec、Profile、Agent Definition、模型、Skill、MCP 和委派策略形成关系图后的系统：

- 文件无法自然表达 draft、发布和 Desired/Applied 状态；
- 用户看不到跨对象引用和最终 effective policy；
- 多个客户端或 CLI 同时编辑时没有 generation conflict；
- Secret 容易与普通配置一起落盘、导出或展示；
- 校验错误只能暴露为字段错误，无法提供影响范围和修复建议；
- Runtime 热切换、健康检查和 drain 不是一次文件写入可以正确表达的；
- YAML 与页面若都可直接写入，会形成两个配置真相。

因此问题不只是“增加一个页面”，而是增加一个紧凑的 Configuration Control Plane。

## 3. 配置分层

### 3.1 BootstrapConfig

BootstrapConfig 保持文件/环境变量管理，只包含 Core 在打开 Config Registry 之前必须知道的信息：

- runtime root 与数据库位置；
- service user / process identity；
- Config Registry 加密或 Secret Store 根密钥引用；
- 初始 owner bootstrap credential；
- supervisor/control socket 等启动信息。

这些字段数量必须保持很小。网络监听、Provider、Model、Agent、Profile、Skill、MCP、审批和普通系统策略都不属于 bootstrap。

### 3.2 ManagedConfig

ManagedConfig 由 Config Registry 管理，按 domain 组织但作为一个完整快照发布：

```text
ManagedConfig
  |- models/providers
  |- runtime_specs
  |- agent_profiles
  |- agents
  |- approval_review
  |- skills/extensions
  |- channels/gateway
  |- task/resource policies
  `- operational settings
```

MVP 不为每类配置创建独立微服务或独立发布事务。当前 Published Spec 是一个完整 typed document，
保证跨 domain 引用在同一原子 generation 中成立。

### 3.3 RuntimeState

运行状态不属于配置：

- Runtime health、PID、并发占用；
- Task 队列深度；
- active/draining generation；
- model latency/error rate；
- MCP connection state；
- applied generation 与失败信息。

页面可以把 Desired Config 与 RuntimeState 放在一起展示，但不能把观测值写回配置文档。

## 4. 持久化模型

目标模型使用现有 SQLite 基础设施，保留三个紧凑对象：

```text
config_drafts
  |- draft_id
  |- base_generation
  |- document_json
  |- draft_version
  |- updated_by
  `- updated_at

config_published
  |- desired_generation
  |- schema_version
  |- document_json
  |- config_digest
  |- change_summary
  |- created_by
  `- created_at

config_control_state              # singleton
  |- desired_generation
  |- applied_generation
  |- apply_status                 # idle | applying | failed
  |- apply_error_code
  `- updated_at
```

详细 apply step、验证结果和操作者写入现有 Audit/Event 设施，不新增第二套通用事件系统。

约束：

1. Published Spec 只能由下一次 publish 替换；V1 不提供历史版本选择或 rollback。
2. Draft 使用 `base_generation + draft_version` 做 optimistic concurrency。
3. `document_json` 使用版本化 typed schema；数据库不存 Pydantic 私有序列化细节。
4. Secret 字段只能出现 `secret_ref`、`configured=true/false` 和可安全展示的 metadata。
5. `desired_generation` 只在完整校验和 preflight 成功后递增。
6. `applied_generation` 只在目标 Node 的必需组件完成切换后更新；失败时旧 active generation 继续服务。

## 5. ConfigurationService 边界

```text
ConfigurationService
  |- read current/draft/diff/audit
  |- create and patch draft
  |- validate draft
  |- preflight draft
  |- publish generation
  |- export/import
  `- write/rotate secret reference

ConfigApplyCoordinator
  |- calculate impact plan
  |- apply live-policy changes
  |- replace Runtime generations
  |- reload affected components
  |- update applied generation
  `- emit audit/status events
```

`ConfigurationService` 是配置领域唯一写入口。`ConfigApplyCoordinator` 只消费已发布且带 digest 的
Spec generation，不接受页面提交的任意 dict。

各 domain 可以提供 code-owned typed applier，但不能通过配置动态下载 applier/plugin。MVP 保持可信小集合：

```text
AgentConfigApplier
ExtensionConfigApplier
ChannelConfigApplier
OperationalConfigApplier
```

它们是 ConfigApplyCoordinator 的内部 ports，不是四个独立配置服务。

## 6. 热生效语义

### 6.1 目标

用户体验上统一为“发布并应用”。系统应尽最大可能在线完成，而不是把所有变化粗暴标记为“请手工重启”。

但“热生效”不等于修改正在执行的对象。以下不变量保持不变：

- active Invocation 的 Agent Definition 和 ResolvedInvocationPolicy 不可变；
- active ToolStep 的 grant 不在中途扩大；
- Task/Delegation 创建时的 digest 不被静默替换；
- Runtime 私有 Session 不使用不兼容配置强行 resume。

### 6.2 Apply Class

| Apply Class | 典型配置 | 生效方式 |
|---|---|---|
| `live_policy` | Tool/Skill allowlist、visibility、delegation ceiling、审批风险阈值 | 原子切换 active generation；新 Invocation 使用新策略，旧 Invocation 保持快照 |
| `runtime_replace` | 模型绑定、Provider endpoint、Prompt、RuntimeSpec、Codex home/sandbox | 构建新 generation，preflight 后原子切换；旧 active Invocation 有界 drain |
| `component_reload` | Channel、Gateway listener、MCP connection、通知 adapter | 目标组件 stop/start 或 listener rebind；Core 其余部分继续运行 |
| `node_service_restart` | 不支持 reload 的 Node 组件、监听地址或异常恢复 | 目标 Node drain 后由 supervisor 受控重启服务；页面等待重连并展示 applied generation |
| `host_action` | runtime root、数据库位置、根密钥、process identity、设备 reboot | 需要目标 Node 本机管理员确认，不伪装成普通热生效 |

Workspace 只拥有配置和 Desired State，没有可重启的 Workspace 进程。reload/restart 必须明确目标
WorkspaceNode。普通 Agent、模型、Profile、Skill 和审批配置不得因为实现方便全部归入 restart 类。

### 6.3 Runtime generation swap

```text
Published Generation G1 (active)
              |
          publish G2
              |
              v
build G2 -> validate -> health/preflight
              |
        atomic active pointer swap
              |
       +------+------+
       |             |
new Invocation    old active Invocation
uses G2           finishes on G1
                     |
                  bounded drain -> destroy G1
```

规则：

1. G2 未通过预检时不改变 desired/applied pointer，G1 继续服务。
2. 切换后 G1 不接受新 Invocation，也不恢复新的 Turn。
3. 每个 Agent Definition 最多一个 draining generation，并有明确 drain deadline。
4. 不建立可任意选择历史 generation 的 Runtime pool。
5. 受影响的 queued/paused Child Task 按现有 `agent_definition_changed` 失败，不为它们长期保留 G1。
6. active Invocation 可在 G1 上完成；超出 drain deadline 时按可审计 cancellation/failure 语义终止。
7. Idle Product Session 在下一 Turn 绑定 G2；Runtime/Prompt 不兼容时自动创建新 Runtime Session，Product conversation 本身保留，并向用户显示配置已更新。
8. Reviewer 使用短期 Session，切换后新审批自然使用 G2。

### 6.4 Policy 收窄与紧急撤权（后者为规划）

普通发布不修改 active Invocation 快照。未来若出现明确的活动 Turn 紧急封禁需求，可增加“额外交集”撤权 registry：

```text
effective permission
  = invocation creation snapshot
  ∩ current emergency revocation policy
```

当前不实现通用动态 Policy Engine。现有机制是 Turn cancellation、grant TTL/revoke、Tool definition/origin fingerprint fail-closed，以及发布后新 Invocation 使用收窄策略。

## 7. 管理页面信息架构

复杂配置不应塞进 Node `capabilities.tsx` 诊断页面，也不应呈现为一张超长表单。共享资源从
Workspace Resources 进入，Node overlay 和应用动作从 `Workspace -> Nodes -> Node detail` 进入；未来
桌面/Web 管理端复用同一 API，不为页面另建配置后端。

建议路由/页面边界：

```text
/workspaces/{workspaceId}/resources
/workspaces/{workspaceId}/resources/agents
/workspaces/{workspaceId}/resources/agents/{id}
/workspaces/{workspaceId}/resources/models
/workspaces/{workspaceId}/resources/runtimes
/workspaces/{workspaceId}/resources/profiles       # advanced
/workspaces/{workspaceId}/resources/skills-mcp
/workspaces/{workspaceId}/nodes/{nodeId}/configuration
```

手机端使用列表 -> 详情 -> 编辑 -> 发布抽屉的逐层导航；宽屏客户端可以使用左侧导航和双栏详情。页面结构不同，但 draft、validation、diff 和 publish 语义完全相同。

“Workspace 资源与 Node 配置”示意：

```text
┌─ Personal Workspace / 资源与 Node ─────────────────────────────┐
│ Overview  Agents  Models & Runtimes  Skills & MCP  Nodes     │
├────────────────────────────────────────────────────────────────┤
│ Desired G42 · Node Applied G42                    [导入/导出] │
│ Draft: 3 changes · validation passed   [查看差异] [发布并应用] │
├────────────────────────────────────────────────────────────────┤
│ Agent graph / list                                             │
│ knoa       native-main       assistant       healthy           │
│ reviewer   approval-review   approval-review healthy · system  │
│ codex      codex-default     coder           disabled          │
└────────────────────────────────────────────────────────────────┘
```

### 7.1 Overview

- desired/applied generation、digest 和 apply 状态；
- pending draft 和 validation errors；
- Agent/Runtime/Provider/MCP 健康摘要；
- restart/reload/drain 状态；
- 最近配置发布、应用失败、组件 reload/restart 审计。

### 7.2 Agents

列表先展示 Agent Definition，而不是让用户从 Runtime/Profile 的底层表开始：

- Agent ID、显示名、visibility、enabled；
- RuntimeSpec、Profile、模型 identity；
- current generation、health、active/queued count；
- Tool/Skill/delegation 摘要；
- 编辑、复制、禁用、查看 effective policy。

Agent 编辑采用引用选择器：

```text
Identity -> Runtime & Model -> Role Instructions -> Skills & Tools
         -> Delegation -> Limits -> Review
```

页面可以提供向导，但保存结果仍是 RuntimeSpec、Profile 和 Agent Definition 三个正交对象，不能为了 UI 方便重新合并成一个巨大 AgentConfig。

### 7.3 Models & Runtimes

- Provider account 与连接测试；
- Platform-managed model alias；
- Runtime-managed model 的可见 identity/hint；
- Runtime implementation、workspace/home、sandbox 和 native capabilities；
- Profile instruction authority compatibility；
- preflight、健康、最近错误和 drain 状态。

API key 只显示“已配置/最后轮换时间”，不显示原值。修改 Secret 是独立动作，不进入普通文本 diff。

### 7.4 Profiles

Profile 编辑器包含：

- system/developer instructions 与当前 Draft diff；
- Workspace `default_skill_refs`、`allowed_skill_refs` 和 activation policy；
- Platform Tool/capability ceiling；
- Runtime-native capability ceiling；
- caller allowlist ceiling；
- delegation 和固定 Runtime/Profile limits。

Profile 不拥有 Skill package/instance，也不拥有 Agent visibility。Skill 属于 Workspace 共享资源；
AgentDefinition 负责产品目录 visibility。

页面必须明确标注“Prompt 不是安全边界”，并同时展示最终由 Platform/sandbox 强制的限制。

### 7.5 Effective policy preview

发布前可以选择 `user | delegate | system` 调用类型和 target Agent，查看：

- resolved Tool、Skill、Platform capability；
- Runtime-native capability；
- Artifact scope；
- deadline/child/tool limits；
- 被哪一层 policy 移除及原因。

该预览调用 Core 的真实 resolver dry-run，前端不能自己复制 policy 计算逻辑。

### 7.6 Skills & Tools

- installed/enabled/invalid 状态；
- Skill instructions、来源、digest 和 requirements；
- Tool origin、effect、risk、confirmation；
- 哪些 Profile 引用该 Skill/Tool；
- data-only Skill import；
- executable extension 继续走 MCP/extension 安装流程。

### 7.7 Publish drawer

发布动作必须先展示：

- 结构化 diff；
- 安全影响：新增/移除的 Tool、Capability、caller、visibility；
- 运行影响：live policy、需要 replacement 的 Runtime、需要 reload/restart 的组件；
- 受影响的 active Invocation 和 queued/paused Task 数；
- validation/preflight 结果；
- change summary 与二次确认。

高风险变化，如扩大 Runtime-native write/shell、开放 system Agent、降低审批强度或更换 credential，需要 owner step-up authentication。

## 8. Configuration API

管理 UI 不复用 Conversation Session 上现有的通用 `config_set(field_name, scalar)`。新增 owner/admin-scoped typed API：

```text
GET  /config/current
POST /config/drafts
GET  /config/drafts/{id}
PUT  /config/drafts/{id}           # complete typed document + draft_version
POST /config/drafts/{id}/validate
POST /config/drafts/{id}/preflight
POST /config/drafts/{id}/publish
GET  /config/diff?draft_id=
POST /config/secrets/{slot}/rotate
GET  /config/apply-status
```

真实协议可以继续使用 Core WebSocket typed messages；这里的 HTTP 形式只表达 application contract，不要求另建 REST service。

要求：

1. 读取 API 返回 masked、principal-filtered view。
2. Draft replacement 必须携带 `draft_version`；服务端保存并检查 `base_generation`。
3. publish 接受 draft ID，不接受整份未验证 document。
4. validate 返回稳定 error code、JSON pointer、对象 ID 和修复提示。
5. preflight 不产生持久 Runtime side effect；临时进程、连接和文件必须清理。
6. apply status 通过现有 event stream 推送，页面断线后可以按 generation 恢复查询。
7. 旧 `config_set` 在迁移完成后降级为 CLI convenience adapter，内部仍创建 draft 并走同一 publish pipeline。

## 9. 安全与审计

- 配置读取与修改使用独立 admin capability，不继承普通 Agent Tool capability；
- Agent、Skill 或 MCP Tool 不能调用 ConfigurationService 修改自身权限；
- 默认只允许 owner 的本地客户端；远程已配对 owner device 需要显式启用和 step-up authentication；
- Secret write-only，日志、diff、Published Spec、event 和错误不得包含 Secret 明文；
- 每次 validate、publish、component restart、secret rotate 和紧急撤权记录 actor、device、generation、digest、结果与 correlation ID；
- 导出默认不包含 Secret，包含 Secret 的灾难恢复包必须单独加密并明确确认；
- 扩权和降级安全策略的 diff 必须使用高风险视觉提示，不能只显示普通字段变化；
- apply 失败保留旧 active generation，未知状态 fail closed。

## 10. KISS / YAGNI 边界

MVP 要做：

- 一个 ConfigurationService；
- 一个完整 ManagedConfig snapshot；
- draft、validate、preflight、publish；
- live、runtime replace、component reload、Node service restart 与 host action；
- Agent/Model/Profile/Skill 的核心页面；
- bounded Runtime generation swap；
- Secret reference 和审计。

MVP 不做：

- 每个配置 domain 一个微服务或独立数据库；
- 任意历史 Runtime generation 路由；
- 多人审批流、GitOps server 或复杂 RBAC；
- 前端自行计算 effective policy；
- 任意插件动态注册 config schema/applier；
- 实时协同编辑；
- 将所有低级 Pydantic 字段原样暴露给用户。

## 11. 实施状态

当前实现已具备 Registry、Draft、validate、preflight、publish、diff、history/rollback、active + draining
generation 和发布屏障。正向重构保留前述热生效能力，删除普通产品 UI/API 中的 history/rollback，
并把 restart 明确建模为目标 Node 动作。Tool fingerprint 继续保护旧 grant。

Phase 5 已完成旧 Agent 配置入口的前向删除。BootstrapConfig 的进一步物理拆分、Secret rotation UI、step-up authentication 和更丰富的安全 impact 可视化保持为独立安全增强，避免把并未完成的安全语义伪装成普通配置热更新。

### Phase 1：建立唯一配置真相

- 定义 ManagedConfig versioned contract；
- 增加 Config Registry 与 ConfigurationService；
- 首次启动将现有 resolved AppConfig 导入 generation 1；
- 保留 YAML export/backup，不再在每次启动隐式覆盖数据库；
- 现有 PersistentConfigController 改为 ConfigurationService adapter。

### Phase 2：只读管理页面与校验

- Overview、Agent graph、Models/Runtimes、Profiles、Skills/Tools；
- current/draft diff；
- resolver-based effective policy preview；
- connection/runtime preflight。

### Phase 3：Draft 与发布

- typed draft editor；
- optimistic concurrency；
- publish impact plan；
- live policy apply、component reload 与审计；
- 不新增 rollback；应用失败保持旧 active generation。

### Phase 4：Runtime replacement

- generation build/health/swap/drain；
- queued/paused digest mismatch 处理；
- Product Session 自动 rebind；
- apply status/event UI。

### Phase 5：减少 bootstrap 与旧入口

- Provider、Agent、MCP、Channel 等迁入 ManagedConfig；
- 旧 scalar `config_set` 只作为新 pipeline 的 adapter；
- BootstrapConfig 收缩到真正无法在线定位的字段。

## 12. 验收不变量

1. UI、CLI、import 不存在绕过 ConfigurationService 的第二写路径。
2. 初始化后数据库 Published Spec 是唯一运行配置真相；YAML 不会在重启时静默覆盖它。
3. publish 前全量 schema、引用、安全和 Runtime compatibility 校验完成。
4. apply 失败时旧 active generation 和 Runtime 继续可用。
5. active Invocation 的 definition/policy digest 不因发布被中途替换。
6. 新 Invocation 在原子切换后只使用新 generation。
7. 每个 Agent Definition 最多一个 active 和一个 bounded draining generation。
8. queued/paused Child 不因热发布静默扩大权限或切换 definition。
9. Secret 明文不进入 Published Spec、diff、日志、event 或普通导出。
10. effective policy preview 与真实执行使用同一个 resolver。
11. 扩权、审批降级和 system visibility 变化可识别、需 step-up 并完整审计。
12. 除极少数 bootstrap 字段外，配置可以在线发布；需要 reload/restart 时由页面统一编排并反馈结果。

## 13. 最终表述

Knoa 的配置页面不是配置文件的图形皮肤，而是 Configuration Control Plane 的一个客户端。

```text
配置真相      = current Published Spec + desired/applied generation in Config Registry
编辑体验      = typed draft + validation + diff + impact preview
生效机制      = atomic publish + live policy / runtime replace / component reload
运行安全      = immutable Invocation snapshot + grant revoke/TTL + Tool fingerprint
规划增强      = 在真实需求出现后增加 emergency revocation intersection
失败恢复      = new generation 未健康时保持旧 active generation；不建设 rollback 产品
Secret        = write-only Secret Store reference
YAML          = bootstrap / import / export / disaster recovery format
```

这使配置管理保持高内聚：ConfigurationService 负责“期望系统是什么”，各 Runtime/组件只负责“如何应用自己的那部分”；同时通过完整 snapshot 和统一发布事务避免跨对象配置碎片化。
