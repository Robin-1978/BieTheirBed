# Knoa 扩展生态、模型接入与多节点 Hub 架构设计

> 状态：权威目标架构；Phase 1、Phase 2 与 Phase 3 首轮已交付，生产强化边界明确标记
>
> 更新日期：2026-08-16
>
> 范围：Skill、MCP、第三方 Tool、LLM Provider、扩展中心、模型中心、Account、Workspace、HubService、Relay、Node、多电脑配置与自托管
>
> 关系：产品对象和配置归属以 `knoa-product-domain-architecture.md` 为准；本文建立在 `knoa-module-architecture.md`、`knoa-configuration-control-plane-design.md`、`knoa-secure-gateway-design.md` 和 `knoa-capability-extension-design.md` 之上
>
> 后续演进：Workspace 共享资产、ModelDeployment/DeploymentObservation、跨 Node 本地 LLM 调用、Virtual Node 与跨 Workspace 共享以 `knoa-workspace-resource-fabric-design.md` 为准；本文继续作为扩展导入、现有 Hub/Relay 实现、Node-local 配置和安全连接的权威设计。本文历史段落中的“逻辑 Hub”在目标术语中等同 Workspace；运行服务称 HubService
>
> 设计取向：local-first、Hub-assisted、self-hostable；高内聚、低耦合；KISS、YAGNI；不引入任意进程内插件和分布式 Core

## 1. 执行摘要

Knoa 需要同时解决两个产品问题：

1. 网络上大量 MCP、Skill、模型服务如何被普通用户安全、方便地发现、导入、配置、更新和停用；
2. 一个用户如何在 N 台电脑上运行 Knoa，而不要求每台电脑配置公网 IP、独立域名、TLS 证书或 Cloudflare Tunnel。

统一答案是：

```text
扩展生态：Catalog -> Staging/Inspect -> Permission Review -> Config Publish
          -> Extension Runtime -> Capability Gateway

多节点：Account -> Workspace -> optional HubService
                                  |- Node Directory / sealed rollout
                                  `- optional Relay -> N 个 WorkspaceNode
```

Workspace 是唯一逻辑租户，拥有共享资源和 Conversation/Task 等 Work。Knoa WorkspaceNode 是
AgentInvocation、ExecutionAttempt、Secret、Artifact bytes 和本地权限的执行事实权威。HubService 是可选身份、目录、密文投递与 Relay 控制面，不是第二个
资源租户。普通用户可以使用 Knoa 托管 HubService；高级用户可以自托管；单机和完全离线用户
可以由 owner Node 承担本地 Workspace Registry，不使用 HubService。

第三方可执行能力统一通过 MCP 进入平台。Skill 保持 data-only。模型服务通过少量可信
Provider driver 接入。任何 Marketplace、作者声明、模型 Prompt 或 Hub 身份都不能绕过
Node 上的 ConfigurationService、Runtime sandbox 和 Capability Gateway。

## 2. 目标与非目标

### 2.1 目标

- 支持标准 MCP Server 的 `stdio` 和 `streamable_http` 接入；
- 支持本地目录、上传包、HTTPS/Git 来源和 Catalog 条目的受控导入；
- 将不同生态的 data-only Skill 归一化为 Knoa canonical SkillPackage；
- 为扩展提供来源、版本、digest、权限、Secret requirements、更新和卸载记录；
- 为 OpenAI、Anthropic、OpenAI-compatible 和本地模型提供可用的配置页面；
- 所有普通模型、Agent、Skill、MCP 和运行参数通过 Desired Generation 热发布；
- App 可以管理一个用户的多个 Knoa Node；
- 普通用户不配置域名即可远程访问 Node；
- 支持 Knoa 托管 Hub、自托管 Hub 和无 Hub 三种部署形态；
- Relay 不成为业务数据、权限或执行权威；
- 多节点配置允许客户端本地模板化和批量生成逐 Node 候选，但每个 Node 独立校验、发布和回滚。

### 2.2 非目标

- 不直接从互联网下载 Python/JavaScript 后 `import` 到 Core；
- 不构建通用插件内核或任意 Runtime class 下载机制；
- 不声称所有 Skill 生态已经形成统一标准；
- 不因 Marketplace 排名或作者签名自动授予 Tool 权限；
- 不让 Hub 直接读写 Node SQLite、Secret、本地文件或 live Runtime；
- 不让 N 个 Node 共享数据库或组成分布式 Core；
- 不默认同步完整 Conversation、Artifact 和 Tool 输出到云端；
- 不在第一阶段建设组织、部门、复杂 RBAC 和企业 IAM；
- 不要求每个 Node 拥有独立公网域名。

## 3. 2026-08-16 实施状态与缺口

| 能力 | 已交付 | 明确后续 |
| --- | --- | --- |
| MCP connection | stdio/HTTP inspect；inventory digest；漂移 fail closed；只读 Tool 默认策略 | 连接测试 UX、更新 diff、卸载/GC 工作流 |
| 本地 MCP package | content-addressed immutable PackageStore；Config Draft 激活 | App 文件上传、Archive/Git/HTTPS source adapter |
| Skill | data-only 校验、不可变导入、digest、Extension Center Draft | 外部生态 adapter、Archive/Git/Catalog、更新 UI |
| 第三方 Tool | MCP Tool 经 Capability Gateway；移除 Core 内直接部署入口 | 包装与发布指引、Catalog metadata |
| LLM Provider | Model Center；四类 driver；write-only Node Secret；generation 热发布 | Provider 连接测试、模型能力探测、Secret 管理详情页 |
| 配置页面 | Draft/validate/preflight/publish；Extension/Model/Node Center；现有 rollback 为待删除旧能力 | 更细的 diff、批量模板、Node reload/restart 与运行状态诊断 |
| 远程身份 | 独立 Node signing/configuration keys；QR key pinning；Hub enrollment | key rotation/recovery 与正式 Hosted Account |
| 多电脑 | AppInstallationIdentity + N 个 NodeDeviceBinding；Node selector；Hub directory；direct 优先/Relay fallback | endpoint discovery、连接诊断与 Hosted Account UX |
| Hub/Fleet | self-hosted single-owner Hub、presence、ticket、opaque Fleet envelope、Node apply | App Fleet rollout UI、Node 主动拉取/回报、Hosted Hub |
| Relay | Node outbound connector；App consumer；ticket + Ed25519/X25519/HKDF/ChaCha20-Poly1305；Gateway business tunnel；有限事件轮询 | 显式 window backpressure、容量/滥用治理、Hosted Relay |
| Mobile release | Hosted Hub 平台级 APK repository、帐号鉴权 metadata、公开 immutable/stable download；Node 本地通道 | Catalog/CDN/灰度发布（有真实规模后） |

本轮已闭环 Self-hosted Hub 的首个远程数据面：Node 保存一个 Hub enrollment 并主动建立 outbound
WebSocket；App 在 direct 网络失败后申请 90 秒 single-use ticket，经 opaque Relay 与固定 Node
identity 建立端到端加密 session，再把现有 Gateway HTTP typed contract 封装为加密 request/response
stream。Hub/Relay 只看到 Node、session、stream、sequence 和 ciphertext size，不能读取业务 method、
headers、body、Prompt、Artifact 或 Secret。

direct 路径继续使用配对时固定的 Node identity、TLS 与现有 Gateway challenge/session；Relay 路径
增加应用层 Node session。将 direct 也迁移到同一应用层加密 session、显式 `window_update` 流控、
大文件断点续传和 Hosted 容量治理属于生产强化，不为首轮再复制业务 API 或建设通用 QUIC 栈。

## 4. 核心术语

### 4.1 Account Subject

人的登录身份，用于 Hub 登录、Node 所有权、恢复、订阅和 Marketplace entitlement。它不等于
Node 内的 Agent、Runtime 或 transport session。稳定身份必须带 identity issuer；V1 不把 Hosted
与 Self-hosted HubService 中的同名 subject 自动联邦。

### 4.2 Workspace 与 HubService

Workspace 是用户可拥有或加入的唯一逻辑租户，拥有 Membership、WorkspaceNode、共享资源逻辑
身份和授权。HubService 是可选的服务部署，提供 Account authentication、Workspace/Node Directory、
enrollment、presence、opaque Fleet envelope、connection ticket 和 Relay。Hosted 形态下一个
HubService 承载多个 Workspace；自托管形态通常只承载一个 Personal Workspace。

现有代码和协议字段中的 `hub_id` 暂时表示已部署的单租户控制中心；目标 schema 会明确拆为
`workspace_id` 与 `hub_service/identity_issuer_id`，不长期维护两个租户聚合。

### 4.3 Node

安装了 Knoa Core 的执行电脑。Node 拥有本地 Runtime、MCP deployment、Skill package、Secret、
Conversation 正文/ChatTurn、TaskExecution、AgentInvocation、ExecutionAttempt、Artifact bytes、Config
Registry 和最终授权判断。Conversation 目录和 Task Definition/Deployment 的产品归属属于 Workspace；
V1 与执行事实同库存储只是部署简化。

`NodeIdentity` 至少包含两把用途隔离的长期密钥：

- Ed25519 signing key：Node 身份证明、握手 transcript 签名；
- X25519 configuration encryption key：接收逐 Node sealed Fleet candidate。

两把私钥都留在 Node。禁止直接把 Ed25519 signing key 当作静态加密 key，密钥轮换也必须按
用途独立版本化。

### 4.4 Client Device

手机、浏览器或桌面客户端。Client Device 生成并持有自己的设备私钥，通过 pairing 或 Account
enrollment 获得访问指定 Node 的资格。它不能与 Node 混称为 Device。

Client Device 使用两层身份：

- `AppInstallationIdentity`：本次 App 安装面向 Hub 的设备身份，用于登录设备管理、连接 ticket
  和全局撤销；
- `NodeDeviceBinding`：Node 本地保存的 Client public key、Principal、状态和权限绑定，用于
  最终业务授权。

Hub 登录成功不自动创造 `NodeDeviceBinding`。同一 App installation 可以访问多个 Node，但每个
Node 都必须存在独立 binding。第一版允许复用同一个 App public key，binding 和撤销记录仍按
Node 独立；需要更强不可关联性时再增加 per-Node derived key，不能在第一版同时维护两套模式。

### 4.5 Principal

Node 内部的数据和权限作用域。Account Subject 可以映射为各 Node 上的稳定 Principal，但
Account service 不能替代 Node 的 principal authorization。

### 4.6 Relay

在 Client Device 和 Node 之间转发连接的传输服务。Relay 根据 Node ID 路由、执行限流和保活，
但不理解 Task、Conversation、Tool 或 Config 语义。

### 4.7 Catalog、Package 与 Installed Extension

- Catalog：可搜索的来源元数据，不执行代码；
- Package：下载或上传到 staging 的不可变候选内容；
- Installed Extension：通过校验、审查和 Config publish 后在某个 Node 生效的 Skill 或 MCP。

## 5. 总体架构

```text
                           Optional Knoa HubService
                    hosted by Knoa / self-hosted / absent
                  ┌────────────────────────────────────┐
                  │ Account │ Workspace/Node Directory │
                  │ Presence │ opaque rollout │ Relay   │
                  └──────────────────┬─────────────────┘
                                     │
                           optional Relay transport
                                     │
              ┌──────────────────────┼──────────────────────┐
              v                      v                      v
        ┌───────────┐          ┌───────────┐          ┌───────────┐
        │  Node A   │          │  Node B   │          │  Node C   │
        │ Local Core│          │ Local Core│          │ Local Core│
        └─────┬─────┘          └─────┬─────┘          └─────┬─────┘
              │                      │                      │
     Config Registry        Config Registry        Config Registry
     Secret Store           Secret Store           Secret Store
     Runtime/Tools          Runtime/Tools          Runtime/Tools
     Local data             Local data             Local data

        Mobile / Web / CLI selects one Node for each interactive operation
```

Node 内的扩展执行链：

```text
Extension Center
  -> ExtensionImportService
  -> staging + bounded inspection
  -> permission/provenance review
  -> Config Draft
  -> ConfigurationService preflight/publish
  -> ExtensionManager
  -> ToolRegistry
  -> Capability Gateway
  -> Agent Invocation
```

## 6. 状态与信任所有权

| 状态 | 唯一权威所有者 |
| --- | --- |
| Account、Hub membership、Node ownership | Hub identity service |
| Node ID、signing/encryption public keys、enrollment state | Hub directory；Node 持有对应私钥 |
| Node online presence、relay connection | Presence/Relay |
| AppInstallationIdentity 私钥 | Client Device 本地安全存储 |
| NodeDeviceBinding、Node-local revoke | 对应 Node Gateway identity repository |
| Workspace Conversation/Task 稳定定义与目录 | WorkspaceRegistry |
| Node Principal、AgentInvocation、ExecutionAttempt、Artifact bytes | 对应 Node Core |
| Node ManagedConfig/Revision | 对应 Node ConfigurationService |
| API Key、MCP Secret | 对应 Node Secret Store |
| Installed package bytes | 对应 Node managed extension store |
| Catalog listing、来源和可用版本 | Catalog service/cache |
| Tool grant 与副作用授权 | 对应 Node Capability Gateway |
| Runtime-native capability | 对应 Node Runtime/sandbox |
| Hosted Account 的 Mobile APK release | Hosted Hub 根级平台 release repository |
| No-Hub/Self-hosted Mobile APK release | 对应 Node Gateway 本地 release repository |

Hub 可以表达 desired intent，但不能伪造 Node 的 applied state。Catalog 可以表达“可安装”，但
不能表达“已授权”。Relay 可以表达“可连接”，但不能表达“可执行”。

Hub 全局撤销 App installation 后不再签发 connection ticket，并向在线 Node 发送撤销通知；Node
本地 revoke 立即生效。离线 Node 依赖 ticket/session 的短 TTL 最终收敛。Account recovery 不恢复
已经丢失的设备或 Node 私钥，必须重新 enrollment/pairing。

## 7. 扩展生态设计

### 7.1 Skill：canonical data-only package

Knoa canonical SkillPackage 继续保持：

```text
skill.yaml
instructions.md
resources/**          # 受支持的静态文本资源
```

manifest 至少描述：

- stable Skill ID、name、version；
- instructions 路径；
- 静态 resources；
- required Tool names/capabilities；
- 可选触发 metadata；
- source、license、author metadata；
- schema version。

Skill 不包含：

- 可执行脚本；
- Node Secret；
- Runtime command；
- API endpoint credential；
- capability grant；
- 自动安装依赖的代码 hook。

不同生态的 Skill 通过显式 adapter 导入：

```text
Claude/Codex/Agent Skill source
  -> ecosystem-specific adapter
  -> canonical SkillPackage
  -> preview + warnings
  -> immutable digest
```

Adapter 只能转换数据，不能执行来源包中的安装脚本。无法无损转换时必须展示 warning，并要求
用户确认或拒绝导入。

### 7.2 MCP：唯一第三方可执行扩展边界

MCP 支持两类来源：

1. Connection：已有 stdio command 或 streamable HTTP URL；
2. Package：包含 `mcp.yaml` 的本地/下载包，在 Node managed runtime 中启动 stdio server。

MCP inspect 必须发现：

- Tool schema、description 和 annotations；
- Resource 与订阅能力；
- Prompt；
- Elicitation；
- server/version identity；
- transport、网络和 Secret requirements。

自动 onboarding 只允许明确 `readOnlyHint=true` 的 Tool 进入候选 enabled set。写入、破坏性、
开放网络或 annotations 缺失的 Tool 默认 withheld，必须经过显式权限审查。

#### 7.2.1 远程 MCP inventory drift

远程 MCP URL 是可变外部依赖，不能像本地 package 一样冻结实现字节。首次 inspect 和每次成功
重连都必须计算 `inventory_digest`，至少包含：

- server name/version identity；
- Tool name、description、完整 input schema 和 annotations；
- Resource/Prompt capability 摘要；
- transport endpoint identity 和配置的 TLS trust 信息。

Config Revision 保存 expected inventory digest。重连后若 digest 变化：

```text
running
  -> inventory changed
  -> state=drifted
  -> remove affected Tool from new grants
  -> existing grant fingerprint validation fails closed
  -> owner re-inspects and publishes a new Config Revision
```

Server 仅改变内部行为但保持相同 schema 时，Platform 无法通过 digest 检测。文档和 UI 必须把
远程 MCP 标为外部信任边界；高风险或强可复现能力应优先使用固定版本的本地 package、受控
容器或组织自有 proxy。

### 7.3 第三方 Tool

第三方 Tool 不新增独立插件类型：

```text
第三方能力
  -> 包装为 MCP Server
  -> Tool schema discovery
  -> Tool policy review
  -> Capability Gateway
```

Built-in Tool 和 Platform Tool 仍由可信代码随 Knoa 发布。网络下载内容不能注册进程内
`ToolBase` implementation。

### 7.4 支持的来源

ExtensionImportService 最终支持：

| 来源 | 处理方式 |
| --- | --- |
| Local directory | 复制受控 snapshot 到 staging |
| Archive upload | 有界解包，拒绝路径逃逸、链接和特殊文件 |
| HTTPS URL | 限制重定向、大小、内容类型和下载超时 |
| Git URL/ref | 通过受限 fetch adapter 获取固定 commit snapshot |
| Catalog entry | 解析为明确 source、version、digest 和 license |
| MCP URL | 不下载包，执行远程 MCP inspect |

第一阶段只需要 Local、Archive 和 MCP URL。Git 和 Catalog 在出现真实分发需求后加入，避免
先建设通用包管理器。

所有自动下载来源还必须执行 egress/SSRF policy：

- Catalog 和自动更新默认拒绝 loopback、link-local、私网地址、云 metadata 地址和带 URL
  credential 的 locator；
- 每次 DNS 解析和 redirect 后重新检查目标地址，限制 redirect 次数并禁止协议降级；
- Git adapter 禁止 hook、submodule、LFS helper 和来源仓库中的可执行安装步骤；
- 用户显式输入的局域网 MCP URL 可以访问私网，但必须标记“本地网络目标”，不能由 Catalog
  条目静默获得该例外；
- 下载不继承 Provider/MCP Secret，也不读取任意代理 credential；
- 日志只记录规范化 host、source type、大小和 digest，不记录 URL credential/query Secret。

### 7.5 安装状态机

```text
discovered
  -> downloading/copying
  -> staged
  -> inspected
  -> awaiting_review
  -> draft_created
  -> preflight_passed
  -> published

任何阶段失败 -> rejected/failed

active 不是 ImportService 的持久状态：
active = package_id 被当前 applied Config Revision 引用且对应 provider 已成功 apply
```

关键规则：

1. 下载和解包永远发生在 staging；
2. package root、文件数量、单文件大小和总大小有上限；
3. 拒绝 symlink、device file、socket、路径逃逸和隐藏执行 metadata；
4. Skill 在 staging 中只按数据读取；
5. MCP package 在受限 preflight 环境中启动和 inspect；
6. 安装动作本身不直接扩大 Agent policy；
7. 安装结果进入 Config Draft，发布后才成为 active；
8. active package 内容由 digest 冻结；
9. 更新使用新版本 snapshot，不原地覆盖正在使用的内容；
10. 卸载前展示引用它的 Profile、Task policy 和 Agent。

#### 7.5.1 Package 与 Config Revision 的事务合同

Package 使用内容寻址：

```text
package_id = kind + canonical content SHA-256
package path = managed store / kind / package_id
```

导入、发布和 apply 的顺序固定为：

```text
1. 将候选 package 原子写入 immutable PackageStore
2. 创建引用 package_id 的 Config Draft
3. validate/preflight 验证 package 存在、digest 匹配且可启动
4. 发布 immutable Config Revision
5. apply coordinator 再次验证 package，并构建新 provider snapshot
6. provider swap 成功后推进 applied_revision
7. 失败时保持 previous applied Revision/provider 继续服务
```

Config Revision 是“是否 active”的唯一逻辑真相；PackageStore 只拥有不可变字节和 provenance，
不能单独声明安装已生效。

崩溃恢复规则：

- `desired != applied` 时按现有 ConfigurationService 状态恢复或保持失败；
- applied Revision 引用的 package 缺失或 digest 不匹配时 fail closed，保留上一个仍可服务的
  applied provider，不从网络静默重新下载；
- staging import 没有被任何 Draft 引用时可以按 TTL 清理；
- PackageStore 采用 mark-and-sweep，不建设通用 refcount 服务；保留集合来自 open Draft、保留的
  Config Revision、active/draining provider generation；
- 只有 Revision retention 明确删除历史版本，且 package 不再属于上述保留集合时才允许 GC；
- V1 不提供 rollback；新 generation 预检必须确认全部 package 存在，失败时不切换并保留旧 provider。

### 7.6 Provenance 与更新

每个安装记录至少保存：

- extension ID、kind、version；
- source type 和规范化 source locator；
- resolved Git commit 或远程版本；
- package SHA-256；
- manifest/instruction canonical digest；
- author、license 和 homepage metadata；
- installed revision、installed by、installed at；
- inspection result digest；
- requested Secret names；
- enabled Tool policy；
- previous version reference。

签名和信誉是辅助信号，不是授权依据。第一阶段 SHA-256 + source provenance 即可；只有在真实
Marketplace 供应链出现后再增加作者签名、透明日志、SBOM 和信誉系统。

### 7.7 权限审查

Extension Center 在发布前必须展示：

- Tool 名称、参数和来源；
- read/write/destructive/open-world annotations；
- Platform effect、risk 和 capability；
- shell、host write、desktop control、network 等敏感能力；
- MCP 将继承的 env allowlist 和 working directory；
- Secret requirements；
- 哪些 Agent/Profile 可以引用；
- 与当前 Revision 相比新增或移除的权限。

最终权限仍是：

```text
Principal grant
  ∩ RuntimeSpec ceiling
  ∩ Profile ceiling
  ∩ Invocation policy
  ∩ installed/enabled Tool policy
  = Capability grant
```

### 7.8 Secret

Secret 是独立写入动作，不进入普通 Config JSON 和 diff：

- UI 只显示 configured/missing、引用名和最后轮换时间；
- 原始值写入 Node Secret Store 后不可读取回显；
- Config 只保存 secret reference；
- MCP 只获得声明且获批的 Secret；
- 不默认继承 Node 进程全部环境变量；
- 删除或轮换 Secret 后，受影响 connection/generation 执行 preflight 和 reload；
- Hub 默认不保存 Node Secret。

## 8. LLM Provider 与模型中心

### 8.1 Provider driver

当前可信 driver 保持小集合：

- `openai`；
- `anthropic`；
- `openai_compatible`；
- `llamacpp`。

`openai_compatible` 覆盖大多数兼容云服务、本地服务和代理，不为每个厂商创建 Provider plugin。
只有出现真实协议差异和第二个 consumer 时才增加 driver。

### 8.2 Provider、Model 与 Runtime 的关系

```text
Secret reference
       │
Provider account/endpoint
       │
ManagedModel alias
       │
RuntimeSpec model binding
       │
AgentDefinition
```

- Provider 描述协议、endpoint、credential reference 和 timeout；
- ManagedModel 描述 provider、model ID、vision/context/thinking metadata；
- RuntimeSpec 决定模型所有权和绑定；
- AgentProfile 不包含 endpoint 或 API key；
- Codex Runtime 的模型仍由 Codex 自己管理。

### 8.3 Provider wizard

```text
选择 Provider 类型
  -> endpoint
  -> 写入 Secret
  -> connection test
  -> 获取或填写 model ID
  -> 检查基础生成、stream、vision/thinking metadata
  -> 创建 model alias
  -> 选择 default/fallback/Agent binding
  -> Config Draft + preflight + publish
```

连接测试必须有超时、响应大小限制和稳定错误码。测试结果不能把响应 body、token 或 API key
写入审计日志。

OpenAI-compatible endpoint 对 vision、thinking、context window 等能力没有统一可靠的发现合同。
自动检测结果只能作为 `detected` metadata，不能直接成为安全或预算事实；用户可以在 UI 中
显式覆盖，最终值记录来源为 `detected | provider_declared | owner_override`。上下文和输出预算仍由
Platform 上限强制执行。

### 8.4 热生效

Provider、模型执行参数或 RuntimeSpec 变化时：

```text
build affected generation
  -> provider/model health check
  -> acquire publish barrier
  -> swap Resolver + active generation
  -> existing invocation drains old generation
  -> deadline 后 interrupt
```

只替换实际消费变化配置的 Agent generation，不因无关 Provider、Skill 或 policy 变化重建所有
Runtime。

## 9. 产品 UI

### 9.1 顶层信息架构

```text
Settings
├── Account & Hub
├── Nodes
├── Extension Center
│   ├── Installed
│   ├── Add MCP
│   ├── Import Skill/Package
│   └── Updates
├── Model Center
│   ├── Providers
│   ├── Models
│   └── Runtime bindings
├── Agents & Profiles
├── Operational & Approval
└── Configuration History
```

移动端采用列表 -> 详情 -> 向导 -> 发布确认；桌面/Web 可以使用双栏。它们共享同一个 typed
API、Draft 和 Config Revision，不为 UI 创建第二套配置后端。

### 9.2 Extension Center

Installed 列表展示：

- Skill/MCP kind、name、version；
- running/disabled/invalid/update available；
- source、digest 和 license；
- Tool 数量和最高风险；
- 引用它的 Agent/Profile；
- health 和最近错误。

添加 MCP 向导：

```text
URL / stdio / local package
  -> transport configuration
  -> Secret requirements
  -> inspect
  -> select Tools
  -> permission review
  -> validate/preflight
  -> publish
```

导入 Skill 向导：

```text
local/archive/URL/Git/Catalog
  -> detect format
  -> adapter normalization
  -> instructions/resources preview
  -> required Tool check
  -> affected Profile selection
  -> publish
```

### 9.3 Model Center

Provider 页面提供：

- Provider template/custom OpenAI-compatible；
- endpoint、timeout；
- Secret configured/missing；
- connection test；
- recent health/error；
- 被哪些 Model 引用。

Model 页面提供 alias、provider/model identity、vision/context/thinking、default/fallback 和 Agent
bindings。扩大 capability 或更换 credential 需要 owner step-up authentication。

### 9.4 Node Center

```text
Nodes
├── 家里电脑      online · direct
├── 公司电脑      online · relay
└── 家庭服务器    offline · last seen ...
```

Node 详情展示：

- Node ID、显示名、版本和平台；
- online、transport、last seen；
- Runtime/Provider/MCP health summary；
- Config applied revision；
- pending rollout；
- storage/capability summary；
- revoke、rename、re-enroll；
- 打开该 Node 的 Extension/Model/Agent 配置。

App 的每次 AgentInvocation、ExecutionAttempt、Node-local 配置或 live control 必须绑定明确 target
WorkspaceNode；Conversation/Task 本身按 Workspace ID 寻址。禁止把多个 Node 的本地 Runtime Session
混成一个隐式全局 Session。

## 10. Hub、Relay 与多节点

### 10.1 三种部署形态

#### Hosted HubService

普通用户登录 Knoa Account。Knoa 托管服务承载用户的 Personal Workspace，并提供 Node Directory、
Presence、opaque rollout 和 Relay。用户不配置域名、TLS、端口映射或 Cloudflare。

#### Self-hosted HubService

高级用户部署一个 `knoa-hub`：

```text
knoa-hub.example.com
  -> Account/local owner
  -> Node Directory
  -> Enrollment
  -> Presence
  -> Relay
  -> optional Catalog cache
```

用户只配置一个 Hub 域名。N 个 Node 都主动建立出站连接。可提供单一容器或 Docker Compose；
Node Core 不与 Hub 合并进程。

#### No Hub

App 通过 QR 与 Node 直接 pairing，使用 LAN、Tailscale/WireGuard 或用户自定义 Gateway URL。
此模式保留完整本地能力，但没有自动 Node discovery、Relay、跨设备恢复和 Fleet rollout。

### 10.2 Personal Workspace 与 membership

产品默认体验是“一个用户一个 Personal Workspace”，但数据模型不能固化 Account 与 Workspace 的
一对一：

```text
issuer-scoped Account Subject
  -> WorkspaceMembership[]
      -> default Personal Workspace
      -> optional family / work Workspace

Workspace
  -> N WorkspaceNodes
  -> M AppInstallationIdentity records
```

第一版 UI 只要求一个 active Personal Workspace。App 可以登录不同 Hosted/Self-hosted HubService，
但 V1 不自动联邦不同 issuer 的 AccountSubject。Hosted 形态不为每个用户启动一套物理服务；
Self-hosted 形态通常是一套单 Workspace 实例。第一阶段 membership 只有 `owner`，不建设组织层级
或通用 RBAC。

### 10.3 Node Identity 与 enrollment

Node 首次启动生成独立 Ed25519 signing keypair 和 X25519 configuration encryption keypair。
私钥留在 Node，Hub 只保存 public keys、key versions 和 metadata。

```text
Owner creates enrollment grant
  -> Node receives one-time secret
  -> Hub sends challenge
  -> Node signs challenge + encryption public key binding
  -> grant atomically consumed
  -> NodeRecord active
  -> Node opens outbound presence/relay connection
```

重新安装或密钥丢失产生新的 Node identity，不静默复活旧记录。Node revoke 立即终止 Hub/Relay
connection；Node 本地已有短期 Session 依照本地策略过期。

enrollment transcript 必须把 node ID、Hub ID、两把 public key、key version、grant ID 和 challenge
nonce 一起签名，防止 encryption key 被替换。配置加密 key 轮换需要旧 signing key 或 owner
重新 enrollment 授权；旧 key 在仍有未过期 sealed candidate 时按有界窗口保留。

### 10.4 Client-to-Node Trust Protocol

Hub Account authentication、Relay ticket 和 Node business authorization 必须分离。V1 使用以下
固定信任链：

```text
Account session
  -> Hub issues short-lived connection ticket
  -> Client proves AppInstallationIdentity private key possession
  -> Node proves pinned NodeIdentity private key possession
  -> Node checks local NodeDeviceBinding -> Principal
  -> authenticated encrypted Node session
```

Connection ticket 是 Hub 签名的有界声明，至少绑定：

- hub ID、node ID；
- App installation ID 和 public key digest；
- single-use ticket ID、issued-at、expires-at；
- audience=`knoa-node-session-v1`；
- transport type/direct-or-relay；
- protocol version 和最大 session lifetime。

Ticket 不包含 Principal 权限，也不能创建 NodeDeviceBinding。Node 必须先存在 active binding；首次
binding 只能通过 Node 本地 pairing、owner-approved Hub enrollment 或已有 owner binding 的明确
授权流程创建。

会话握手使用长期 Ed25519 identity key 签署临时 X25519 key 和完整 transcript，经 HKDF-SHA-256
派生双向 session keys，payload 使用带递增 sequence number 的 AEAD。实现可以选择经过审计的
Noise pattern/library，但必须满足同一 transcript 和绑定字段；禁止自创不兼容的简化模式。

握手双方验证：

1. Client 从 pairing/enrollment 获得并固定 Node signing public key，不信任 Relay 返回的新 key；
2. Node 验证 Hub ticket 签名、audience、node ID、client key digest、expiry 和 single-use nonce；
3. Client/Node 分别签署双方 ephemeral key、ticket digest、protocol version 和 transport binding；
4. Node 查询本地 NodeDeviceBinding 并得到 Principal；
5. 握手完成前不发送业务 payload；
6. direct 和 relay 使用同一应用层握手，TLS 只作为额外 transport protection；
7. ticket、handshake nonce 和 session sequence 防 replay；协议版本不匹配 fail closed；
8. session 短期有效，reconnect 必须重新获取 ticket 或重新执行 direct challenge。

No-Hub direct pairing 不使用 Hub ticket，但仍使用固定 Node key、Client key proof、Node-local
binding 和相同 session key agreement。Account recovery 不恢复丢失的 identity private key。

### 10.5 Connection Resolver

App 只持久化 Node ID 和 trust identity，不把某个 URL 当成 Node 身份。连接时按策略解析：

```text
1. LAN direct endpoint
2. private network endpoint（Tailscale/WireGuard）
3. user custom direct endpoint
4. Hub Relay fallback
```

Transport endpoint 可以变化，Node ID 和已信任的 Node signing public key 不变。高级用户可以配置每 Node 独立
域名，但这不是默认产品要求。

LAN endpoint 只能由本机 discovery 或用户显式配置产生；Hosted Hub 不向 App 注入任意私网 URL。
所有 direct endpoint 在连接后仍必须通过固定 Node key 验证，防止 DNS rebinding、LAN spoofing
和 endpoint 被替换。

### 10.6 Relay

Relay 负责：

- Node 出站 connection registration；
- 根据 authenticated Node ID 路由；
- Client/Node connection ticket 校验；
- bounded stream、backpressure、heartbeat、rate limit；
- connection accounting 和滥用防护。

Relay 不负责：

- Agent execution；
- CoreClient business methods；
- Config/Task/Approval 状态机；
- Tool permission；
- LLM API Key；
- payload 业务日志；
- Node 本地数据同步。

Hub 与 Relay 可以同部署，但合同和数据所有权必须分离。

### 10.7 Relay Transport Protocol V1

Relay V1 使用一个 Node outbound WebSocket 和按 Client session 创建的逻辑双向 stream。它不是
任意 HTTP/Core method proxy。Relay 可见 routing header，但 payload 始终是 Client-to-Node
session ciphertext。

最小 routing frame：

```text
session_id
stream_id
frame_type = open | data | window_update | half_close | reset
sequence
ciphertext_length
ciphertext
```

Node Gateway 在加密 payload 内提供复用现有 typed schema 的 Node Protocol：

- unary command/query；
- resumable event stream；
- bounded Artifact upload/download stream；
- interaction/approval response。

规则：

- 每个 command 带现有 client request/idempotency key；
- event reconnect 使用 Node 持久 event cursor，不依赖 Relay replay；
- Relay 不持久化业务 frame，Node offline 立即返回确定性 unavailable；
- 每个 session/stream 有 byte、frame、并发和 idle limits；
- `window_update` 提供显式 backpressure；
- V1 Artifact 传输断线后重新请求，不建设通用随机分片恢复协议；
- 未完成 Client-to-Node handshake 的 stream 只能承载握手 frame；
- Relay 不能解码 typed Node payload，也不能根据 method 名称路由。

Self-hosted 和 Hosted Relay 使用相同协议。V1 frame schema、1 MiB decoded frame 上限、64 MiB
单请求上限、完整 transcript、双向 sequence、跨 Node 拒绝与 Python/TypeScript HKDF 互操作向量
已固化为测试合同。首轮使用固定 192 KiB application chunk 和有界请求并发；`window_update` 字段
保留在 Relay V1 合同中，但显式滑动窗口在出现真实吞吐/内存压力前不启用。

### 10.8 端到端安全

Hosted Hub/Relay 不能被视为 Node 本地数据的可信执行者。目标会话：

```text
Client Device <==== authenticated encrypted session ====> Node Gateway
                              │
                            Relay
                         forwards frames
```

Hub/Relay 可以看到账号、Node ownership、online 状态、连接时间和流量大小等必要 metadata，但
默认不应读取 Prompt、Tool 参数、Artifact、Conversation 或 Secret。Node 最终验证 Client
Device 和 Principal scope，Relay ticket 不能单独授权业务操作。

Cloudflare 可以保护 Hosted/Self-hosted Hub 的一个固定域名，但 Cloudflare Access、Tunnel
identity 或 TLS termination 不能替代 Knoa 的 Node/Client Device authentication。

### 10.9 离线与降级

- Hub 不可用时，已知 LAN/private direct endpoint 仍可连接；
- Node 离线时，Workspace Task/Conversation 仍可见，但 Hub 不假装新的 Invocation/Attempt 已投递；
- 第一阶段不在 Hub 持久化离线业务命令；
- V1 TaskExecution 只有在目标 Node admission 并持久化首个 Attempt 后才进入活动执行状态；
- Relay 断线不取消 Node 上已经运行的 TaskExecution；
- App 恢复连接后从 Node event cursor 继续。

## 11. 多节点配置

### 11.1 Node Config 仍是权威

每个 Node 有独立：

- BootstrapConfig；
- ManagedConfig Revision；
- desired/applied state；
- Runtime generation；
- installed package store；
- Secret Store。

Hub 不共享 SQLite，也不直接修改 live Runtime。

### 11.2 Fleet rollout V1：逐 Node 完整候选

Hosted Hub V1 不保存明文 Agent/Profile/Prompt 配置，也不定义 patch、selector 或自动 merge DSL。
可复用模板保存在 owner App/CLI 本地；批量操作为每个显式目标 Node 生成一个完整、独立候选。

```text
FleetNodeCandidateV1
  node_id
  expected_base_revision_digest
  normalized ManagedConfig document
  candidate_digest
  created_at/expires_at
  owner AppInstallationIdentity signature
```

候选 document、required Secret refs 和业务配置先在 Client 端使用目标 Node 的 X25519
configuration encryption public key，通过版本化 HPKE envelope（X25519 + HKDF-SHA-256 +
ChaCha20-Poly1305）加密，Hub
只保存：

```text
rollout_id
node_id
expected_base_revision_digest
candidate_digest
sealed_candidate
created_at/expires_at
delivery/apply status
```

HPKE associated data 固定绑定 `hub_id、rollout_id、node_id、expected_base_revision_digest、
candidate_digest、expires_at、encryption_key_version`，防止 Hub 或 Relay 在 Node 之间替换 envelope。
Node 解密后重新计算 candidate digest，并验证 owner signature 和 associated data。

Rollout 流程：

```text
Owner App reads each target Node current Config Revision
  -> applies local reusable template in the client
  -> produces one complete normalized candidate per explicit Node
  -> signs candidate digest + base digest + node ID
  -> seals candidate to target Node configuration encryption public key
  -> Hub stores immutable opaque rollout envelopes
  -> Node fetches and decrypts its envelope
  -> Node verifies owner NodeDeviceBinding signature
  -> expected base digest must match current applied Revision
  -> Node creates local Config Draft
  -> local normalize/validate/preflight
  -> owner confirmation or Node-local pre-authorized rollout policy
  -> Node publishes local Revision
  -> Node reports applied/failed + stable code
```

Hub 只汇总状态：

```text
Node A applied revision 52
Node B failed: provider_unreachable
Node C skipped: incompatible_platform
```

不能把 Hub rollout ID 当成 Node Revision ID。base digest 不匹配时返回 `revision_conflict`；V1 不
执行三方合并、JSON Patch 或 last-write-wins。用户必须刷新该 Node、重新生成候选并再次确认。

### 11.3 Candidate Secret 引用与平台差异

Fleet template 只能引用逻辑 Secret name，不能携带 Secret value。每个 Node 独立满足引用：

```text
template requires secret: openai_primary

Node A -> configured locally
Node B -> missing, rollout blocked
Node C -> configured with different provider account
```

Node 还可以因 OS、CPU/GPU、workspace、模型部署和可用 Tool 不同而产生合法差异。V1 始终
使用显式目标 Node，不建设 selector DSL。Client 本地模板应用必须输出每个 Node 的完整候选，
不能把同一个未解析 patch 广播给所有 Node。

### 11.4 配置同步边界

允许进入 sealed candidate：

- Agent/Profile/Runtime 模板；
- Skill/MCP source intent；
- Provider/model 非 Secret metadata；
- operational policy；
- approval policy。

默认不同步：

- API Key/MCP Secret；
- Runtime session；
- Task/Conversation；
- Artifact；
- 本地文件路径内容；
- Tool execution history。

Hub 只能看到 candidate digest、base digest、目标 Node、时间和 apply status，看不到 sealed
candidate 内的 Prompt、MCP URL、Provider metadata 或 Secret reference。Self-hosted Hub 默认也
使用同一 opaque contract，避免 Hosted/Self-hosted 形成两套 Fleet 协议。可选的端到端加密模板
备份不属于 V1。

## 12. 最小数据模型

### 12.1 Workspace 与 HubService

```text
AccountSubject
  identity_issuer_id
  subject_id
  login_identity
  state

Workspace
  workspace_id
  identity_authority_id
  kind=personal

WorkspaceMembership
  workspace_id/identity_issuer_id/subject_id
  role=owner
  state

AppInstallationRecord
  installation_id
  hub_service_id
  subject_identity
  public_key
  display_name/state
  created_at/last_seen/revoked_at

NodeRecord
  node_id
  workspace_id
  hub_service_id
  display_name
  signing_public_key/key_version
  config_encryption_public_key/key_version
  platform/version
  state
  created_at/last_seen/revoked_at

NodeEndpoint
  node_id
  transport_type
  endpoint_hint
  priority
  observed_at/expires_at

FleetRollout
  rollout_id
  workspace_id
  hub_service_id
  created_by_installation_id
  state

FleetRolloutEnvelope
  rollout_id/node_id
  expected_base_revision_digest
  candidate_digest
  sealed_candidate
  expires_at
  local_revision_id
  status/error_code
```

Hosted 第一版只需要 issuer-scoped AccountSubject、Workspace、WorkspaceMembership、
AppInstallationRecord、NodeRecord、presence 和 Relay connection。Fleet 表在多节点配置 UI 开始
实现时再加入；Hosted HubService 不保存
`ManagedConfig`、Prompt 或可解密模板正文。

### 12.2 Node

优先复用现有 Config Registry 和 ExtensionManager，不建立第二份“installed extension truth”。新增
安装 provenance 可以作为 package store metadata，并由 ManagedConfig 的 Skill/MCP 引用决定
是否 active。

Node Gateway identity repository 增加明确的 `NodeDeviceBinding` 语义；它可以复用当前
GatewayDevice 表演进，但必须保持 Node-local `principal_id`、client public key、active/revoked
状态和 last-seen。Hub 的 AppInstallationRecord 不能替代该表。

## 13. API 边界

### 13.1 Node owner API

建议新增 typed API：

```text
POST /extensions/inspect
POST /extensions/imports
GET  /extensions/imports/{id}
POST /extensions/imports/{id}/draft
GET  /extensions
GET  /extensions/{id}
POST /extensions/{id}/check-update
POST /secrets/{name}              # write-only
DELETE /secrets/{name}
POST /providers/test
```

安装最终仍调用 ConfigurationService，不提供“跳过 Draft 直接 active”的管理后门。

### 13.2 Hub API

```text
POST /account/session
GET  /account/installations
DELETE /account/installations/{id}
POST /nodes/enrollment-grants
POST /nodes/enroll/challenge
POST /nodes/enroll/complete
GET  /nodes
GET  /nodes/{id}
DELETE /nodes/{id}
POST /nodes/{id}/connection-ticket
POST /relay/node/connect
POST /relay/client/connect
```

Fleet API 后置：

```text
POST /fleet/rollouts
POST /fleet/rollouts/{id}/envelopes
GET  /fleet/rollouts/{id}
```

Hub API 不代理任意 Core method。业务请求通过 Node Gateway 的 typed protocol，经 direct 或
Relay transport 到达目标 Node。

## 14. 热生效矩阵

| 变化 | Node 生效策略 |
| --- | --- |
| Skill enable/profile allowlist | publish barrier 后新 Invocation 生效 |
| Skill content/version | 新 snapshot + digest；Extension reload；旧 Invocation 使用旧 context snapshot |
| MCP enable/tool policy | Extension reload；新 grant 生效；旧 grant fingerprint fail closed |
| MCP package/version/command | 新 provider preflight，原子替换，失败恢复旧 provider |
| MCP Secret | Secret 写入后显式 connection reload/preflight |
| Provider/model | 构建受影响 Agent generation，health check，swap + drain |
| Profile/approval/limits | policy snapshot 切换；不重建无关 Runtime |
| Hub Node rename/presence | Hub 控制面即时生效，不触碰 Node Runtime |
| Fleet sealed candidate | base digest 匹配且各 Node 成功发布本地 Revision 后才算 applied |
| Node TLS/runtime root/listener | restart-required |

“热生效”不等于修改正在运行对象。所有需要替换的组件继续使用 generation/provider swap。

## 15. 安全威胁与控制

| 威胁 | 控制 |
| --- | --- |
| 恶意 Skill prompt injection | Skill data-only、来源预览、Profile allowlist、权限不由 Prompt 决定 |
| 恶意 MCP executable | staging、文件限制、独立进程、env allowlist、sandbox、Tool policy |
| Tool annotations 造假 | annotations 只作提示；用户审查和 Platform policy 决定权限 |
| Supply-chain replacement | fixed version/commit、digest、immutable snapshot、update diff |
| Archive path traversal | 有界安全解包、拒绝链接和特殊文件 |
| Secret 泄漏 | write-only Secret Store、引用传递、日志脱敏、最小注入 |
| Marketplace compromise | Catalog 不授予权限；Node 重新 inspect/digest/preflight |
| 下载 SSRF/DNS rebinding | 自动来源禁止私网/metadata；每次解析与 redirect 复核；本地 MCP 需显式例外 |
| 远程 MCP inventory drift | expected inventory digest；变化时 drifted 并从新 grant 移除 |
| Hub/Relay compromise | Node/Client keys、端到端会话、Node 最终授权、最小 metadata |
| Hub 读取 Fleet 配置 | 逐 Node configuration-encryption-key sealed candidate；Hub 只保存 opaque envelope 和 digest |
| Account takeover | step-up、Node/Client revoke、短期 ticket/session、审计 |
| Node theft | Node key revoke、本地系统磁盘保护、Secret Store、Session 过期 |
| 跨 Node 数据混淆 | Work ID 使用 Workspace scope；Invocation/Attempt 显式 placement Node；Node-local opaque ID 保持 Node-scoped |
| Fleet 错误扩散 | expected base digest、完整候选、逐 Node preflight；失败时不切换并保留旧 active generation |

## 16. 模块边界

### 16.1 Node 内部

| 模块 | 职责 | 禁止职责 |
| --- | --- | --- |
| ExtensionCatalogClient | 搜索和解析来源 metadata | 下载执行、授予权限 |
| ExtensionImportService | staging、格式适配、inspect、provenance | 直接写 live Registry/Runtime |
| PackageStore | immutable package snapshot 和 metadata | 决定 Agent policy |
| SecretStore | write-only Secret 生命周期 | 普通配置 diff |
| ConfigurationService | Draft/validate/preflight/publish、desired/applied generation（现有 rollback 非目标产品能力） | 下载任意包 |
| ExtensionManager | active provider lifecycle | 用户身份和权限决策 |
| CapabilityGateway | invocation grant 与 Tool enforcement | Marketplace/安装 |
| ProviderService | Provider connection/preflight | Agent 角色语义 |
| NodeGateway | Client transport/auth/protocol | Hub account 或 Agent execution |

### 16.2 Hub

| 模块 | 职责 | 禁止职责 |
| --- | --- | --- |
| AccountIdentity | 登录、恢复、step-up | Node local authorization |
| NodeDirectory | ownership、key、metadata、revoke | Node Config/Task 数据 |
| Presence | online/last seen/transport hints | 声称业务 Task 成功 |
| Relay | bounded opaque forwarding | Core method interpretation |
| Catalog | listing/source/version metadata | active authorization |
| FleetControl | sealed rollout envelope 和状态汇总 | 解密配置、直接写 Node live Runtime |

这些是逻辑模块。第一版 Self-hosted Hub 可以同一进程部署，但代码和数据所有权不能合并成
一个无边界服务。

YAGNI 约束：Phase 1 的 Node 侧只新增一个 `ExtensionImportService` 应用边界，PackageStore、
adapter 和 inspector 可以先作为其内部组件；继续复用现有 ConfigurationService、ExtensionManager、
CapabilityGateway 和 Secret storage。Hub 第一版最多拆为 Hub control process 与 Relay process，
不按上表逐项创建微服务。

## 17. 分阶段落地

### Phase 1：Node Extension Center 与 Model Center（首轮已交付）

- 本地目录/Archive/远程 MCP URL；
- Skill preview 和 canonical import；
- MCP inspect、Tool selection、permission review；
- Secret write-only API；
- Provider wizard、connection test、model alias；
- Config Draft/preflight/publish；
- content-addressed PackageStore、Revision retention/GC 和 crash recovery；
- 远程 MCP inventory digest/drifted；
- 下载 SSRF/redirect/DNS policy；
- 安装 provenance 和 update diff。

验收：普通用户无需编辑 YAML 即可安全接入一个 Skill、一个远程 MCP、一个本地 MCP package
和一个 OpenAI-compatible Provider；Config publish/apply 任意中断不会产生“package active 但
Revision 未应用”或“Revision 引用丢失 package”的状态。

### Phase 2：App 多 Node 与 NodeIdentity（direct 模式已交付）

- `ClientDevice`/`Node` 术语和合同分离；
- AppInstallationIdentity 与 NodeDeviceBinding 分离；
- App 保存多个 Node connection profile；
- QR direct pairing；
- LAN/private/custom endpoint resolver；
- Node selector；
- 所有 Invocation/Attempt/Node-local Config 操作显式 target Node；Conversation/Task 按 Workspace 寻址。

验收：一个 App 可管理至少三台独立 Node，无 Hub 也可工作。

### Phase 3：Self-hosted Hub 与 Relay（首轮已交付）

- 单 owner Account；
- Node enrollment/directory/presence/revoke；
- 一个 Hub 域名；
- Node outbound relay connection；
- Client connection ticket；
- Node key pinning 和 Client-to-Node Trust Protocol；
- versioned Relay frame/Node Protocol；
- direct 优先、relay fallback；
- 独立 `knoa-hub` 进程入口；标准容器/systemd 制品与备份恢复仍属于生产化缺口，见
  `knoa-deployment-architecture.md`。

验收：N 个家庭/私有 Node 不配置独立域名即可从外网访问；Relay 无法解码业务 payload，伪造
ticket、替换 Node key、replay handshake 和跨 Node 使用 ticket 均被拒绝。

当前首轮已达到该验收的架构闭环：Hub account、Node directory、enrollment、presence、single-use
ticket、opaque Fleet storage、Relay server、Node outbound connector、App consumer、端到端握手、
Gateway business tunnel、Artifact chunk 与事件有限轮询已接通。正式 Hosted 上线前仍需补齐容量压测、
显式 flow-control、Account recovery、key rotation 与 abuse control；Self-hosted 的生产 Docker、TLS
proxy、备份恢复和单 worker 启动约束也尚未形成完整标准部署制品。

### Phase 4：Knoa Hosted Hub

- logical personal Hub 隔离；
- Hosted Account login/recovery/step-up；
- Relay capacity、rate limit、abuse control；
- Node/App 版本和兼容性治理；
- metadata 隐私和数据保留策略。

验收：普通用户登录后可 enrollment 多台 Node，无需理解网络和 TLS。

当前已完成 Phase 4 的 `hosted_single_node` MVP：Hosted 控制面保存 Account、LoginIdentity、scrypt
PasswordCredential、Session digest、Workspace、Membership 与一次性 Account/密码恢复 grant；每个
Workspace 使用隔离的 Hub SQLite 与 RelayBroker；全部 tenant 共享 Hosted issuer/signing identity；
member 与 owner/admin 权限分离；HTTP、Node enrollment/directory、跨 Workspace Session 拒绝、重启
持久化、App 帐号/Workspace 流程和一致性备份恢复已有自动化验证。该实现可供个人和受控小规模部署，
但不包含 MFA/step-up、Relay fleet、多实例路由、自动异地灾备、计费与运维 SLO，因此不得标记为
Phase 4 HA 公有云生产完成。

### Phase 5：Catalog、Marketplace 与 Fleet Config

- Git/Catalog source adapters；
- update notifications；
- source provenance、license、可选签名；
- Client-local reusable template 和显式 target Node；
- 每 Node 完整候选、expected base digest 和 sealed rollout envelope；
- 分批发布、失败停止；每个 Node 新 generation 失败时保留旧 active generation；
- Marketplace entitlement。

验收：Catalog compromise 不会自动扩大权限；Hub 不能解密 Fleet candidate；base revision
冲突不会隐式 merge；Fleet rollout 失败不会破坏其他 Node 的 active Revision。

## 18. 明确拒绝的方案

### 18.1 每个 Node 一个公网域名

拒绝作为默认方案。它要求用户管理 N 份 DNS、TLS、Tunnel 和生命周期。仅保留为高级 direct
transport 选项。

### 18.2 Cloudflare 身份等于 Knoa 身份

拒绝。Cloudflare 可以提供边缘 TLS/Tunnel，但不能替代 Account、Node key、Client Device key、
Principal 和 Node authorization。

### 18.3 Hub 代理所有 Core API 并读取内容

拒绝。Hub/Relay 不应成为集中式业务执行层。业务协议终止在目标 Node Gateway。

### 18.4 Hub 是所有 Node 的配置数据库

拒绝。Hub 只保存 rollout metadata 和 sealed candidate；每个 Node 保持独立 Config Registry 和
applied revision。

### 18.5 Hub 保存明文 Fleet 配置或广播通用 patch

拒绝。Hosted Hub V1 只保存逐 Node sealed candidate；App/CLI 在本地解析模板并生成完整候选。
不引入 selector DSL、JSON Patch、隐式 merge 或 last-write-wins。

### 18.6 任意第三方代码进程内插件

拒绝。第三方可执行能力必须通过 MCP 独立进程/远程 Server 和 Capability Gateway。

### 18.7 一开始建设完整 Marketplace 信誉系统

拒绝。先完成安全导入、provenance、digest、权限审查和更新；真实分发规模出现后再增加签名、
SBOM、透明日志和信誉。

### 18.8 一开始建设通用企业 IAM

拒绝。先做单 owner Personal Workspace。共享家庭、团队和组织 RBAC 需要真实产品需求后另行设计。

## 19. 最终架构决策

1. Knoa 的第三方可执行扩展标准是 MCP，不新增任意进程内 Tool plugin。
2. Skill 保持 data-only，不因外部生态格式变化放弃安全边界。
3. Catalog 负责发现，ImportService 负责隔离验证，ConfigurationService 负责发布，Capability
   Gateway 负责运行授权。
4. Provider driver 保持小集合，优先使用 OpenAI-compatible 覆盖生态。
5. API Key 和 MCP credential 是 Node-local write-only Secret，不进入普通配置和 Hub。
6. 产品默认一个 Personal Workspace，但 issuer-scoped Account 数据模型允许多个
   WorkspaceMembership；Hosted HubService 共享物理服务但 Workspace 逻辑隔离。
7. 普通用户可以使用 Knoa Hosted HubService；高级用户可以 Self-hosted HubService；完全本地用户
   由 owner Node 承担本地 Workspace Registry，不需要 HubService。
8. Node 使用稳定 Node ID 和用途隔离的 signing/config-encryption public keys 标识，域名/IP/Relay
   只是可替换 Transport。
9. 默认远程路径是单一 Hub/Relay 域名和 Node 出站连接，不是每 Node 独立域名。
10. Relay 只转发端到端加密 frame，Node 是业务协议终点以及执行、数据、Secret 和最终授权权威。
11. 多节点配置由 Client 本地模板生成逐 Node 完整候选，经 Node configuration encryption key
    加密后由 Hub 保存 opaque envelope；每个 Node 本地 Draft/preflight/publish，不共享数据库、
    不隐式 merge。
12. Local-first 能力不能因 Account、Hub 或 Relay 离线而被整体取消。
13. PackageStore 保存 immutable bytes/provenance；Extension active 状态只由 applied Config Revision
    和成功 provider apply 派生。
14. Hub ticket 只提供连接资格；Node key pinning、Client key proof 和 NodeDeviceBinding 共同建立
    Client-to-Node session。
15. 远程 MCP inventory 变化进入 drifted 并 fail closed；相同 schema 下的实现行为仍属于外部
    信任风险。

## 20. 一句话定义

Knoa 扩展与多节点平台是一个以 Workspace 为唯一逻辑租户、以 WorkspaceNode 为本地执行和数据
权威、以 MCP 为第三方可执行能力边界、以 data-only Skill 为知识扩展、以 Config Revision 驱动
热发布，并通过可托管或自托管 HubService 提供 Account、Workspace/Node Directory 和可选
Relay/Fleet 控制面的 local-first Agent Platform。
