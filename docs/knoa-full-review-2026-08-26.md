# 小诺 Knoa 完整评审（能力 + App 设计与易用性，2026-08-26）

评审范围：平台功能面（`README.md`、`src/knoa_platform/` 全模块、`src/knoa_agent/`、`src/knoa_codex_agent/`、`docs/` 设计文档）+ Knoa Mobile 全部约 30 个 Expo Router 页面（精读聊天/配对/节点/结果/Artifacts 页，其余按分组通读）+ 本机测试信号（`PYTHONPATH=src pytest`：1024 passed / 77 failed / 1 skipped，失败集中在 POSIX 专属用例，见 1.3）。

本文记录评审当日结论，后续修复不必回填，以实际代码为准。

**总体结论：工程成熟度远超"个人项目"水准的个人电脑 Agent 平台。能力面在同类开源产品里属于第一梯队，安全设计是突出强项；移动端 App 的可靠性工程达到商业产品水准，设计一致性自 8-24 评审后已明显改善（设计债从"系统性"降为"局部残留"），但仍有若干易用性硬伤和测试环境问题值得处理。**

评分概括：**平台能力 9 分，安全设计 9.5 分，App 可靠性工程 9 分，App 设计与易用性 7 分**（8-24 评审时为 6 分）。

---

## 一、Knoa 能力评审

### 1.1 定位与架构

Knoa 是"个人电脑 Agent"平台：Agent 常驻在用户自己的电脑（Node）上，能操作桌面、执行任务、接收定时/事件触发，用户通过 TUI、飞书、钉钉或手机 App 与它交互。代码为 Python 主体（`src/knoa_platform/`）+ 少量 Rust（`crates/`），移动端是 Expo/React Native 独立目录（`apps/knoa-mobile/`）。分层清晰：

- **Core 服务**：本机 WebSocket API（`127.0.0.1:9527`），CLI/TUI 走自动管理的本地凭证（`~/.knoa/config/service.token`，0600）；
- **Hosted Hub 控制面**：账号、工作区、成员、共享资源、远程模型共享；
- **Secure Gateway**：面向移动端的独立鉴权 API，LAN（mDNS `_knoa-node._tcp.local.` 发现）+ TLS 远程双形态，只暴露白名单协议，配套 OpenAPI 3.1 供移动端代码生成；
- **Node**：承载通道（飞书 WebSocket / 钉钉 Stream，均免公网回调）。

"Core 不含通道逻辑、通道不进 Core 包"的边界纪律贯穿整个代码库，是架构上最值得肯定的一点。

### 1.2 能力全景（按域）

| 域 | 能力 | 评价 |
|---|---|---|
| Agent 运行时 | ReAct 循环、SDB 验证门、Plan-Execute、反思、子 Agent 委派（`spawn_subagent`/`subagent`）、Codex Agent 适配器 | 完整，且 SPI 化（`agent_runtime/`），可插拔 |
| 多模型 | llamacpp/OpenAI/Anthropic/兼容 API，canonical IR + provider profiles，prompt 缓存、token 校准、流前失败自动回退本地模型 | 双账号多模型目录（provider=账号，model=引用）设计合理 |
| 视觉 | 分离视觉运行时（纯文本主模型 + 本地 Qwen-VL 感知工具 `image_inspect`）、a11y 树语义 GUI（`ui` 工具）、网格叠加视觉定位（`screen` 工具） | GUI 自动化双路径（语义 + 视觉 grounding）是差异化能力 |
| 工具 | 34 个工具文件：shell/文件/剪贴板/窗口/键鼠/截图/web 搜索抓取/记忆/天气汇率/任务编排（`create_task`/`task`）/MCP 管理（`mcp_inspect`/`mcp_connect`/`mcp_disable`/`mcp_deploy`）/配置助手 | 覆盖面广，全部走统一 ToolStep 边界与统一 MCP 兼容 schema 契约 |
| 持久任务 | 事件重放、Attempt/ToolStep 检查点、审批暂停恢复、fail-closed 重启恢复、幂等 | 设计文档与实现对应，是平台核心资产 |
| 自动化 | 一次性/间隔/Cron 调度、webhook 触发（HMAC-SHA256 + 事件级幂等 ingress）、MCP Resource 作为任务源 | 闭环完整，有 Jira/GitLab 参考实现（`examples/`） |
| MCP 扩展 | Streamable HTTP + 受监管 stdio 子进程、逐工具策略声明、确认门控 connect/deploy（原子安装、隐藏元数据剔除、大小限额） | "服务器元数据永不授予权限"原则执行得很彻底 |
| 安全 | 危险命令拦截、保护路径、类型化拒绝码、能力网关（Capability Gateway + grants）、审计 JSONL、审批审阅 Agent（suggest/auto，高风险永远人工，无效输出/超时一律回退人工） | **项目最突出的强项** |
| 自我改进 | `improvement/service.py`：证据 → 离线回放 → 审批 → 金丝雀 → 回滚的 prompt/skill 进化管线，结构上无法改安全边界 | 少见的认真设计 |
| 通道 | 飞书（WebSocket、卡片实时更新、长文续卡、语音转 Artifact）、钉钉（Stream、AI 卡片、审批、GitLab 通知），`/agent` 按会话切换 Agent、`/tasks`/`/task`/`/stop` | 近 20 个提交都在修钉钉卡片，说明在真用、也在真磨 |
| 记忆 | Principal 级偏好 + 会话级情节，SQLite；可选 fastembed 语义召回 | 够用 |
| 部署与分发 | 版本双轨（platform/mobile 独立语义版本）、Android 私有更新通道（断点续传 + SHA-256 校验 + 系统 installer）、`bump-version.sh` 校验 | 自洽 |

### 1.3 能力层缺口与风险

1. **测试有环境敏感问题**：本机 Windows 上以 `PYTHONPATH=src` 跑出 **77 failed / 1024 passed / 1 skipped**（55s），失败集中在 POSIX 专属用例（`test_posix_popen_path`、shell 进程组取消、home 目录展开等）。这更像"测试套件 POSIX 偏科"而非产品缺陷（README 明确主打原生 Windows 同机运行，测试却没跟上 Windows 路径），建议 CI 加 Windows 矩阵或本地跳过 POSIX 专属用例，否则测试信号持续失真。另：移动端 vitest 本机 Git Bash 无 npm PATH，未能在评审环境执行（脚本本身齐全）。
2. **无 OS 级沙箱**：stdio 进程隔离只隔离崩溃与依赖冲突，README 已诚实声明"强隔离需 OS 级沙箱策略"。对一个能操作键鼠和 shell 的 Agent，这是最值得投入的下一块安全拼图。
3. **语音转写无内建 provider**：依赖用户自配 MCP 工具映射（`audio_transcription.tool`），手机端没有"为什么语音不可用"的引导，能力存在但可发现性差。
4. **iOS 无更新通道**（仅 Android 私有发布），`update.tsx` 对 iOS 用户是死胡同页面。
5. **文档与代码比例**：`docs/` 50+ 篇设计文档质量高，但已出现"文档说 A、代码已是 B"的漂移风险（8-24 评审自己也声明"后续修复不必回填"），建议给关键设计文档加"以代码为准"的过期标记。

---

## 二、App 设计与易用性评审

### 2.1 信息架构与导航 —— 聪明且贴合场景

导航模型为个人助理的双高频场景定制：**chat ↔ tasks 主双屏左右滑切换**（`PrimarySwipeNavigation`）+ 头部双 tab 带未读角标（`HeaderActions`）+ "更多"进节点菜单。层级为 启动 → 账号/工作区 → 节点（配对）→ 主功能，路由约 30 屏，命名和归组清楚。i18n 中英双语全覆盖（`src/i18n/index.tsx`，2866 行，en 回退 zh）；无障碍系统性认真（accessibilityRole/Label/State 普遍使用，tasks 列表卡是范本）；启动页动画尊重系统"减弱动态效果"设置（`app/index.tsx:26-42`）是亮点。

### 2.2 设计系统 —— 债务显著偿还，残留局部问题

8-24 评审的核心批评是"token 定义了没人用、暗色对比度 2.2:1"。**当前代码已系统性修复**：

- `radii/spacing/shadows/typography` 已在全部页面样式表落地（`node.tsx`、`artifacts.tsx`、`results.tsx`、`pair.tsx`、`chat.tsx` 等均引用）；
- 暗色 `on_accent` 改为深色文字 `#102E24` 叠在 `#7DBAA8` 上（`android/.../values-night/colors.xml`），对比度约 6.6:1，通过 WCAG AA；
- 主题用 `PlatformColor`/`DynamicColorIOS` 双平台原生语义色，暖纸浅色（`#F4F0E8`）+ 墨绿深色（`#141A18`）调性统一，品牌识别度好（"诺"字轨道 + 呼吸动画启动页有品质感）；
- `AppPressable`/`AppIcon`/`AsyncStateView` 三个基件复用率明显提升（artifacts、results 均三态齐全带重试）。

仍残留：

1. **字阶仍未真正收敛**：`fontWeight "800"` 无差别覆盖标题/行标题/按钮（`node.tsx:95,105,110`、`results.tsx` 多处），同时存在 10–11px 小字（`results.tsx` 的 `nodeScope`/`fact` fontSize 11、`node.tsx:98` transportDetail 11px），层级感靠"全加粗"堆出来。
2. **触控目标不达标**：筛选 pill `minHeight: 34–36`（`results.tsx`、`artifacts.tsx`），低于 44pt 指引。
3. 零星绕过基件：chat 更新横幅用裸 `Pressable`（`chat.tsx:795,804`）、停止图标硬编码 `color="white"`（`chat.tsx:1025`）。

### 2.3 关键体验逐屏

**聊天页（核心，1580 行）是全 App 体验工程最好的页面**，多处精品水准：

- 发送可靠性链路完整：乐观回显（pending 气泡）→ 附件逐个上传状态/单独重试 → 整体失败可"重试/编辑回填"，`clientRequestId` 幂等键防重复；
- 8-24 评审的硬伤已全部修复：消息时间戳（5 分钟分组）、长按复制、turnActions 统一 AppPressable、**运行中可继续输入发送第二条消息**（`canSend` 不再被 activeTurn 阻塞，`chat.tsx:302-308`），与飞书通道行为对齐；
- 滚动管理精细：`followLatest` 引用 + 距底 80px 阈值 + "回到底部"悬浮按钮 + 历史加载 `maintainVisibleContentPosition` 防跳动；
- 会话预热（打开即建会话省一轮往返）、草稿按会话持久化、SSE watcher 合并快照、发图前能力预检（失败引导去配置 Agent）、空态带可点示例 prompt、四色反馈横幅 + 成功自动消失。

**连接与配对**：pair 页相机权限三分支处理（可再询问 / 永久拒绝深链系统设置）依然是范本；QR 扫描 + 高级模式手贴 JSON 兜底。`GatewayProvider`（693 行）是多传输（direct/p2p/relay）自动协商，带每传输诊断状态和代际（generation）失效防串台，缓存按"账号 + 节点"作用域隔离（`setCacheIdentity`）——这是商业级连接层设计。

**离线体验**：工作区四子页"缓存优先渲染 + 聚焦静默刷新 + 新鲜度横幅（WorkspaceCacheBanner）"、任务离线入队保幂等键、本地提醒通知（expo-notifications），弱网体验好。

**artifacts 页已从"最伤用户"变为合格**：会话选择器弹层（pageSheet Modal + 会话列表）+ AsyncStateView 三态齐全，此前要求手输会话句柄的问题已消除（`artifacts.tsx:140-184`）。**results 页节点筛选已改为纯本地筛选**（`nodeFilter` 本地 state），不再以筛选外观做全局 `switchNode` 副作用（`results.tsx:28,88`），并有 nodeScope 文案明示范围。

**任务域**：列表离线队列重试 + SSE 防抖刷新 + 未读红点无障碍隐藏；执行详情审批按钮级 busy 追踪（精确到 `{id, approved}`）；新建任务离线入队保留幂等键。

### 2.4 8-24 评审问题修复核对（当前代码逐项验证）

| 8-24 问题 | 状态 |
|---|---|
| 深色 accent 上白字对比度 ~2.2:1 | ✅ 已修（`on_accent` 暗色改 `#102E24`） |
| radii/spacing/shadows token 零引用 | ✅ 已修（全页面落地，含 typography） |
| artifacts 页要求手输会话句柄 | ✅ 已修（会话选择器） |
| App 运行中不能排队第二条消息 | ✅ 已修（canSend 解除 activeTurn 阻塞） |
| 聊天无时间戳/无长按复制 | ✅ 已修 |
| results 节点筛选实为全局切换连接 | ✅ 已修（纯本地筛选） |
| event-sources 删除无确认 | ✅ 已修（Alert 确认 + secret 提示） |
| extensions `capabilityAction` 无 catch | ✅ 已修（`extensions.tsx:149`） |
| chat turnActions 裸 Pressable / 死样式 | ✅ 已修（统一 AppPressable） |
| Agent 编辑器无脏状态跟踪 | ❌ 仍存在 |
| node 页重连无 busy、状态无状态点 | ❌ 仍存在 |
| 任务编辑页失败渲染空表单、无脏检查 | ❌ 仍存在 |
| 触控目标 < 44pt、11px 小字 | ❌ 部分仍存在 |
| 语音可用性引导 / 录音取消手势 | ❌ 仍存在 |
| iOS 更新死胡同 | ❌ 仍存在 |

### 2.5 仍存在的易用性问题（按严重度）

| # | 问题 | 证据 |
|---|------|------|
| 1 | **Agent 编辑器无脏状态跟踪**，返回即丢全部编辑；对照删除 Agent 有郑重确认，轻重倒挂 | `settings/agent-editor.tsx`（仅见删除确认） |
| 2 | node 页重连按钮无 busy 反馈；在线状态纯文字无状态点，扫一眼分不清在线/连接中 | `node.tsx:55-57,37-39` |
| 3 | 任务编辑页加载失败仍渲染可提交空表单、无脏检查返回拦截 | `tasks/[id]/edit` |
| 4 | chat.tsx 1580 行单文件，交互/渲染/样式混载，已是维护性风险 | `app/chat.tsx` |
| 5 | 语音转写在 Node 未配映射时无引导（发完才失败）；录音无取消手势 | `chat.tsx:488-534` |
| 6 | 长列表（work 300 条全量、执行时间线）无虚拟化；下拉刷新未覆盖全部列表页 | `workspaceCache.ts` |
| 7 | iOS 更新死胡同；移动端 vitest 本机因 npm 不在 Git Bash PATH 无法直跑（工程脚本本身齐全） | `update.tsx` |

### 2.6 优先级建议（Top 5）

1. **表单脏检查**：agent-editor 与 tasks/edit 返回前拦截，一次性消灭"丢编辑"这一最伤人的问题类别。
2. **修测试信号**：加 Windows CI 矩阵或标记 POSIX 专属用例，让 77 个失败不再是噪音。
3. **字阶定案**：13/15/17/20 四档 + 600/800 两档字重收口，同时清 11px 小字和 34px 触控目标。
4. node 页连接状态可视化（状态点 + 重连 busy），这是用户对"在线与否"的第一感知点。
5. 拆分 chat.tsx（消息项/Composer/横幅各自成组件），趁行为正确时降维护成本。

---

## 三、一句话总结

能力侧是一个**功能面完整、安全边界纪律罕见的个人 Agent 平台**，短板只在测试跨平台信号与 OS 级沙箱；App 侧**可靠性工程 9 分、设计 7 分**——8-24 评审的设计债已偿还大半（token 落地、对比度达标、两大可用性硬伤修复、消息可排队），当前主要矛盾从"系统性不一致"收敛为"表单脏检查、字阶收口、状态反馈"三类局部债，以及 chat.tsx 这个单文件巨石的维护成本。
