# Knoa 跨平台 Runtime 重构实施计划

> 状态：执行计划
>
> 日期：2026-08-19
>
> 目标架构：[Knoa 跨平台 Runtime 演进架构](./knoa-cross-platform-runtime-architecture.md)
>
> 原则：正向设计、持续可运行、单一写权威、无永久兼容层、Windows/Linux 同步验收

## 1. 目标与完成定义

重构完成时必须满足：

1. Windows/Linux 都安装同一个 Universal Host Bundle，并可激活 `hub`、`node`、`all`；
2. 终端用户安装和更新不依赖源码、Git、系统 Python、pip 或手工 venv；
3. Rust Node Host 管理 Python Agent Runtime Worker，并通过版本化私有 IPC 执行 Invocation；
4. Hub 与 Node Host 分别内置对应 Console；只增加无 UI 的特权 Lifecycle Broker，不增加 Console 版本线；
5. App/Node 与 Node/Node 使用 ICE P2P 优先，Relay fallback；
6. Rust Hub 接管 Account、Workspace、目录、票据、信令和 Relay；
7. 现有 HubRoot/NodeRoot 数据经过受测迁移继续可用；
8. 当前环境运行 Linux `all`、Windows `node`，但产品代码不写死该组合。

## 2. 执行方法

采用“替换一个边界、删除一个旧入口”的 strangler 策略。每个阶段必须有：

- 入口条件；
- 明确写权威；
- 新实现和故障回退；
- Windows/Linux 测试；
- 删除旧入口的出口条件。

禁止同时开始多个没有共同验收面的核心迁移。P2P、Console UI 可以共享 Node Host 基础并行开发，但不能绕过
协议和安全测试直接接入产品。

## 3. Phase 0：冻结架构与建立基线

当前可执行基线及更新规则见 [Phase 0 基线](./knoa-cross-platform-runtime-baseline.md)。

### 工作

- 把产品支持矩阵、目标进程和数据权威写入权威文档；
- 建立现有 Python Hub/Node 的 API、数据库、协议和性能基线；
- 固定 golden fixtures：Hub backup、Node data、Relay transcript、ManagedConfig、APK release；
- 建立 Windows/Linux CI matrix；
- 为跨语言协议建立 schema 生成和兼容检查入口；
- 标记现有文档中的过期领域模型，不再让旧 implementation plan 覆盖当前架构。
- 建立“当前能力 / 目标能力”标记，特别是 ICE P2P、Rust Host、Console 和 Desktop Companion。

### 验收

- Python 全量测试通过；
- Android tests/typecheck 通过；
- Windows/Linux 部署资产测试通过；
- 基线包含 Relay 首字节、吞吐、长会话、Node 重连和数据备份恢复指标。

### 出口

未完成 schema/golden fixture 前不得实现 Rust repository。

## 4. Phase 1：产品化跨平台交付

> 实现状态：Universal Host Bundle、跨平台 lock、自包含 payload、Rust updater、WinSW/systemd service
> orchestration、localhost Console、Host Lifecycle Broker、签名更新与回退代码合同已完成；原生 Setup/`.deb`、
> bootstrap 代码签名和干净 VM 矩阵仍待发布环境验收。部署合同见
> [产品 Bundle 部署与更新](./knoa-product-bundle-deployment.md)。

本阶段不改变领域实现，先消除用户面对源码和 Python 工具链的问题。

### 工作

- 定义签名 Release Manifest；
- Release Manifest 的 artifact kind 覆盖产品 Hub/Node Bundle 与可选 Agent Runtime Extension Bundle；
- Windows/Linux/Runtime Extension 统一构建签名 ZIP Bundle；Linux 可执行位由签名 Manifest 记录和恢复；
- Node Bundle 内置 Python Runtime、Wheel 和依赖；
- 以仓库 Python lock 在目标 OS CI 物化 application tree，终端安装阶段不解析或下载依赖；
- 实现跨平台 updater：下载、校验、解包、原子切换、健康检查、自动回退；
- Windows/Linux 都交付包含 Hub、Node、Lifecycle Broker 的 Universal Host Bundle，安装时只选择激活角色；
- 将源码安装保留为开发入口，不再作为产品部署手册主路径。
- 定义 schema migration 与 binary rollback 的兼容门；不可逆数据迁移必须先备份并明确不支持自动降级。

### 写权威

仍为现有 Python Hub/Node。Updater 只管理版本和服务，不写领域数据库。

### 验收

- 无 Python、Git、pip 的干净 Windows/Linux 虚拟机可安装；
- 两种 OS 的 `hub/node/all` 六种激活组合完成安装、更新、回退、停用和卸载测试；
- 更新不删除 HubRoot、NodeRoot、Secret 或 APK；
- 同机 `all` 仍表现为两个独立服务。

### 删除

产品文档删除以 `git clone + pip install` 为主的用户路径；开发文档保留。

## 5. Phase 2：Hub/Node 内置管理 Console

### 2A Hub Console

- 作为 Hub Bundle 内置静态 UI，由 Hub 同 origin 提供；
- 使用 Hub Account/Membership API；
- 管理 Workspace、成员、Node directory、共享 Model/MCP、Grant、App release 与诊断；
- 不读取 Node Secret 或 Work 正文。

### 2B Node Console

- 作为 Node Bundle 内置静态 UI，由 Node Host 现有管理 surface 提供；
- 使用 Node Configuration/Core API，不直接写 SQLite；
- 管理 Agent、Model、API Key、MCP、Skill、Tool policy 与组件状态；
- Key 只写 Node Secret Store；
- 提供清晰的 Agent→Model 绑定页面；Codex Agent 显示为 runtime-owned。

### 跨平台部署

```text
role=hub  -> 一个 Hub 服务（内置 Hub Console）
role=node -> 一个 Node Host 服务 + Agent Worker 子进程（内置 Node Console）
role=all  -> Hub 服务 + Node Host 服务（各自内置 Console）
```

### 验收

- Windows/Linux 的 Hub/Node Bundle 均包含对应 Console，不能出现宿主与 Console 版本漂移；
- Hub Console 固定使用 `127.0.0.1:9532`，公网 Hub origin 不提供 Console；Node Console 固定 loopback `9531`；
- CSRF、认证、Secret redaction、权限和审计测试通过；
- App 删除不适合移动端的高级 Key/endpoint 编辑，但保留查看、日常选择和快捷入口；
- Console 不直接打开 Hub/Node SQLite；禁用 UI route 不影响宿主服务的数据面和后台执行。

## 6. Phase 3：Agent IPC 合同和 Python Worker 分离

先在 Python 内完成进程拆分，再替换 Host 语言，避免同时改变进程边界和业务行为。

### 工作

- 建立 `knoa-agent-ipc` Protobuf schema，不引入临时 MessagePack 协议；
- 将 Agent reasoning、Provider、Skill/MCP adapter 组合为 `knoa-agent-worker`；
- Python Node Host 通过匿名管道启动 Worker；
- 将 Invocation command、RuntimeEvent、interaction resolution、Usage 和取消改走 IPC；
- IPC 复用 Agent Runtime `RuntimeEvent` 与 session-scoped Capability MCP Grant，不建立私有 Tool/MCP callback；
- Node Host 保持 Conversation、Task、Config、Secret、Artifact 与 Capability authority；
- Worker heartbeat、drain、crash 和 bounded restart；
- 内置 Knoa Worker 可服务 `knoa` 与 `reviewer_agent`；Codex Adapter 和后续签名 Runtime Extension 使用同一
  Host/Worker 版本握手与监管合同；
- 保留单进程模式只用于测试直到跨进程 parity 完成，随后删除。

### 写权威

Python Node Host 是唯一 Node 写权威；Worker 无 repository 写权限。

### 验收

- 单进程与 IPC 模式 golden event stream 一致；
- Worker 被杀死不损坏数据库，活动 Invocation 得到确定终态；
- cancel/approval/tool race tests 通过；
- Windows/Linux 匿名管道和大消息背压测试通过；
- Worker stdout 协议污染、超大帧、慢消费者和 Host 关闭时的故障测试通过；
- Secret 不出现在 IPC trace fixture；
- ExistingResourceArtifact / InlineArtifact 跨进程 parity 通过；本阶段不实现动态 fd/handle 传递。

### 删除

删除 Agent Runtime 直接访问 Conversation/Task repository 的路径；删除产品单进程入口。

## 7. Phase 4：Rust Node Host 分段接管

Phase 4 不是一个大爆炸发布。下列子阶段必须逐个发布、验证和切换 writer，不能在同一个 release 同时接管全部领域。

### 4A Supervisor 与 transport edge

- 建立 Cargo workspace：domain/protocol/agent-ipc/node-host/platform/update；
- Rust Host 启动现有 Python Worker 并完成版本握手；
- 首先接管 service lifecycle、health、日志、Runtime generation 和 diagnostics；
- Python Core 仍是全部 Node 领域 writer。

### 4B Gateway、identity 与 enrollment

- 读取既有 Node identity/enrollment，使用跨语言 golden fixture 验证；
- Rust 接管 Secure Gateway、session、Node–Hub edge 和 transport；
- identity/signing key 原地复用，不生成第二身份。

### 4C Configuration 与 Secret

- 先建立 Configuration/Secret 只读 parity；
- 在同一个 writer gate 中切换 Configuration publish/apply 与 Secret Store；
- Python Worker 只使用 Host 提供的 generation 与 Provider credential lease。

### 4D Work transaction cluster

- 按共同事务与恢复不变量迁移 Conversation、Task、Invocation、Approval 和 Artifact metadata；
- 每次切换前验证跨 repository transaction、Task 恢复、Stop/Approval race 和 Artifact 提交；
- 切换完成即删除对应 Python writer，不保留永久双写或双向同步。

### 过渡限制

允许短期 Rust Host 代理到 Python Core，但只允许一个 writer。每个 bridge 必须声明 schema 兼容范围、
writer gate、binary/data rollback cutoff、删除 issue、截止子阶段和测试；不允许形成永久“Rust 外壳 + Python 整机服务”。

### 验收

- Rust Node Host + Python Worker 在 Windows/Linux 运行；
- 现有 App API contract 和数据 fixture 通过；
- Node restart、Worker restart、配置 generation drain 和 Task 恢复通过；
- 性能不低于 Python 基线，内存和启动时间有记录。

### 最终删除

删除 `python -m knoa_platform.service` 产品入口；Python 只保留 Agent Worker 入口。

## 8. Phase 5：ICE P2P 和 Relay 性能

该阶段优先解决当前 Relay 慢的问题，可在 Phase 4 的 Rust transport edge 稳定后立即推进。

### 5A Relay 立即修复

- Node 到 App 响应流式转发，不等待完整 response body；
- 实施每 stream/window 背压；
- 减少 JSON/base64 复制，在协议升级时采用 binary frame；
- 对 Conversation SSE、Task events、Artifact download 建立性能测试。

### 5B Rust Hub transport edge

- ticket 支持 `p2p` transport；
- 建立可复用的 Rust signaling module，先作为现有 Hub bundle 的 transport edge 提供短期 ICE
  offer/answer/candidate signaling，不在 Python 中实现一套待删除的信令；
- 信令绑定 Account installation、Node ID、ticket、TTL 和签名；
- 信令状态有界、过期即删，不进入长期业务数据库。

### 5C Node/App ICE

- Rust Node Host 实现 ICE endpoint；
- Android App 集成原生 WebRTC data channel；
- Node-to-Node 复用相同 transport abstraction；
- STUN 配置属于 Hub/Node operational config；
- P2P 超时快速回落现有 E2E Relay；
- P2P 与 Relay 复用 Node session handshake、request frames 和 Invocation ID。

### 验收

- 同 LAN、典型 cone NAT、移动网络、对称 NAT fixture；
- P2P 成功时业务数据不经过 Hub；
- P2P 失败不阻塞超过规定预算；
- fallback 期间授权、幂等和流顺序不改变；
- admission 前可以切 transport；admission 后断线只能 reconcile/attach，不得重放副作用；
- Relay 首字节与吞吐达到基线目标；
- 记录 P2P 成功率后再决定是否建设 TURN。

## 9. Phase 6：Rust Hub、Relay 与内置 Hub Console

### 工作

- Rust Hub 读取现有 `control.db`、tenant `hub.db` 和 signing key；
- 先实现只读 API parity，再切换 Account/Workspace 写入；
- 迁移 Node presence、ticket、resource directory、grant、projection；
- 将 Phase 5 的 Rust signaling module 直接组合进 Rust Hub，并迁移 RelayBroker；删除临时 transport edge 进程；
- 将 Hub Console 静态资产嵌入 Rust Hub，并继续使用冻结的 Hub API contract；
- 保持 Android Release Repository 和 backup/restore manifest 兼容。

### 单实例约束

在共享 Relay session ownership 和数据库生产化前仍保持单 Hub worker。Rust 并不自动赋予 HA；禁止仅因语言
迁移就宣称水平扩容。

### 验收

- Python/Rust Hub contract fixtures 一致；
- 原 Hub backup 可恢复到 Rust Hub；
- App session、Workspace membership、Node enrollment、resource grant 可继续使用；
- Hub signing identity 不改变；
- Relay/P2P 长连接、重启和背压测试通过。

### 删除

删除 Python `knoa-hub` 产品入口和 Python Hub writer；保留一次性离线迁移/诊断工具直到一个 release window 后移除。

## 10. Phase 7：Desktop Companion

当前状态：Windows 路径已实现第一版产品闭环；Linux 仍使用同用户图形 Session 环境恢复，尚未拆成独立
Companion，因此本 Phase 尚未整体完成。

### 工作

- Windows 和 Linux 各自实现登录用户 Companion；
- 与 Node Host 使用 OS 私有 IPC 和显式用户会话绑定；
- 截图、剪贴板、窗口、键鼠和通知通过 Capability Gateway；
- Companion 离线时 Node 不发布桌面能力；
- Windows Service 继续运行 Session 0，不启用交互式服务。
- Windows 安装器为 Node role 注册登录启动 Companion；Companion 监听按 Session ID 命名的认证 Pipe；
- Companion supervisor 观察 Universal Host 活动 Release 并在产品更新后自动重启子进程；
- 内置 screenshot、clipboard、window、mouse、keyboard、notification 统一经过该 IPC，不允许逐 Tool 绕过。

### 验收

- Windows 登录、锁屏、切换用户、RDP；
- Linux X11/Wayland 登录、锁屏和无图形会话；
- 多用户时不跨 Session；
- BitBlt/clipboard 权限错误转为明确 capability unavailable，而不是 Tool 执行失败。

## 11. Phase 8：当前环境迁移

在产品角色和 Bundle 稳定后执行实际部署变更：

```text
Linux: install/update role=all
Windows: uninstall role=hub, retain role=node
```

### Linux

- 从 Windows Hub 最终一致备份恢复 HubRoot；
- 保持 canonical domain、Hub ID、hub-signing.key；
- 安装 Hub 与 Node Host；对应 Console 已包含在各自 Bundle 中；
- Linux Node 作为新/既有独立 Node enrollment 加入 Workspace；
- 切换 Cloudflare connector 后验收 Account、Workspace、APK、Node directory。

### Windows

- 精确卸载 Windows Hub service；Hub Console 随 Hub 一并移除；
- 默认保留 Windows HubRoot 作为回退快照，观察期后再显式清理；
- 保留 Node Host、内置 Node Console、NodeRoot、Node identity、Conversation、Task、Secret 和 Qwen；
- 将 Windows Node enrollment 指向 canonical Linux Hub；
- 验证 P2P 与 Relay fallback。

### 验收

- Linux/Windows 两个 Node 同时在线；
- App 可独立选择两个 Node；
- Windows Qwen 可按 Grant 提供给 Linux Node；
- Linux Hub 离线不破坏两个 Node 的本地数据；
- 回退过程不产生第二个活动 Hub writer。

## 12. CI 与测试矩阵

每个 release 至少覆盖：

| 维度 | 组合 |
| --- | --- |
| OS | Windows x64、Linux x64 |
| Role | hub、node、all |
| Service profile | Windows Desktop/Server SCM；Linux system/user service |
| Desktop profile | Windows signed-in/locked/RDP；Linux X11/Wayland/headless |
| Transport | direct LAN、ICE P2P、Relay fallback |
| Runtime | Worker healthy、crash、version mismatch、drain |
| Update | clean install、upgrade、failed health rollback、uninstall retain data |
| Data | fresh、现有 Hub backup、现有 NodeRoot |
| Console | Hub 内置、Node 内置、all、UI route disabled、unauthorized、secret redaction |

单元测试之外必须有真实进程 E2E；mock ICE 或 mock pipe 不能替代至少一组 Windows/Linux 实机/VM 验证。
“跨平台对称”指领域、协议、角色和 available/unavailable 语义一致，不承诺不同桌面系统的底层能力完全相同。

## 13. 里程碑与优先级

| Milestone | 内容 | 用户价值 |
| --- | --- | --- |
| M0 | 文档、基线、CI | 停止架构继续分叉 |
| M1 | Release Bundle + updater | 用户不再接触 Python/Git/pip |
| M2 | 双内置 Console | 不增加服务即可在电脑浏览器完成 Hub/LLM/Key 管理 |
| M3 | Agent Worker IPC | 为 Rust Node Host 建立稳定边界 |
| M4 | Rust Node Host | 跨平台服务和安全边界统一 |
| M5 | P2P + streaming Relay | 显著降低远程交互延迟和 Relay 压力 |
| M6 | Rust Hub/Relay | 控制面和连接基础设施完成迁移 |
| M7 | Desktop Companion | Windows/Linux 桌面能力正确恢复 |
| M8 | Linux all + Windows node 切换 | 完成当前实际部署目标 |

性能问题优先级高，因此 M5 的 Relay streaming 子项可提前进入 M2/M3，但 ICE 数据面仍必须建立在统一 transport
合同和身份协议上。

## 14. 风险与控制

| 风险 | 控制 |
| --- | --- |
| 全量重写长期无可用版本 | 分阶段替换，每阶段可发布 |
| Rust/Python 双写数据分叉 | 单一 writer gate；切换后删除旧 writer |
| IPC 成为新复杂性中心 | 小消息集、生成 schema、golden fixture、严格版本握手 |
| WebRTC 原生依赖影响 App 构建 | 独立 build lane、真实 APK、Relay 保底 |
| Rust Hub 迁移破坏身份 | 原 signing key、backup restore fixture、禁止自动重建 |
| Console 泄露 Key | Node-only write、loopback default、redaction/CSRF/auth tests |
| 平台行为分叉 | 同一 contract suite 对 Windows/Linux adapter 执行 |
| 临时代理永久存在 | 每个 bridge 必须绑定删除 milestone 和 CI assertion |

## 15. 每阶段提交要求

每个 Phase 完成时必须同时提供：

1. 代码和 schema；
2. Windows/Linux 测试证据；
3. 数据迁移/回退说明；
4. 更新后的权威文档；
5. 删除的旧入口清单；
6. 版本变更和构建产物 manifest；
7. 不包含 Secret 的运行证据。

不得用“Rust 服务能启动”代替领域 parity，也不得用“测试通过”代替真实安装、更新和回退验证。
