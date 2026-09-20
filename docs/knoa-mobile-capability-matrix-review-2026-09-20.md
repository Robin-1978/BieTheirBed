# Knoa Mobile APP 完整评审：全部界面 × Knoa 能力矩阵

> 日期：2026-09-20
> 范围：`apps/knoa-mobile` 全部 38 路由 + `src/` 核心模块，对照 Knoa 后端能力基线
> 立场：自有项目产品设计评审，不用 JIRA 形式；增量聚焦“能力闭环”，不重复 08-24/09-09 的 token/对比度单点债
> 方法：只读核查（chat 1209行、tasks/new 855行、memories 917行、assets 676行、tasks/index 696行、extensions 344行、models 476行、Gateway `openapi.py/adapter.py`），逐格验证

## 0. 一句话结论

APP 已是“功能完备的远程控制面板”（连接/对话/任务/执行/记忆/模型/扩展/资产/通知/更新/工作区全闭环皆有），但离“个人虚拟员工”还差三件事：**① P0 价值闭环没有一键可达的模板式入口（能力有、包装无）；② 后台持续工作不可信（通知非事实源、跨节点状态弱）；③ Node/模型选择仍让用户做运维决策。** 视觉债（09-09 已列）是次要矛盾。

Knoa 能力基线（本次评审对照源）：

- Gateway `/v1/*`：会话/Turn（list/create/retry/cancel/stream）、任务（create/list/get/preflight/execute/continue/pause/resume/archive/restore + executions/events/glance）、审批/交互 resolve、记忆 CRUD+clear、Artifact 搜索/上传/下载/transcribe、Config draft→validate→preflight→publish、Capability catalog/selection/prepare + extensions import（skill/local_mcp/remote_mcp）、钉钉通道、P2P offer/ice-servers、Android release latest、events SSE + poll 兜底、device audit。
- 35 内置工具：文件（read/write/edit/glob/grep/attach/read_artifact/config_draft/run_command）、记忆、调度（create_task/task_control/sleep/spawn_subagent/await_subagent/tool_help）、视觉桌面（screenshot/screen_look/image_inspect/window_control/ui_control/mouse/type_text/press_key/hotkey/clipboard/notify）、联网（web_fetch/web_search/weather/currency）、MCP（inspect/connect/disable/deploy）。
- Task：`TaskLaunchPolicy immediate/scheduled{one_time,interval,cron}/event{webhook,mcp:}`，preflight 拦截，`queued→running→waiting_approval→paused→completed|failed|cancelled`，通知三开关。
- 记忆：`core/relevant + confidence`，按 principal 全局生效。
- 内置 Skill 5 个：file_organizer / health_check / image_doc / monitor / research_report。

---

## 1. 界面总览（38 路由，10 域）

| 域 | 路由 | 一句话职责 | 能力映射 |
|---|---|---|---|
| 启动框架 | `index`、`_layout`、`(tabs)/_layout`、`connect` | 恢复 Hub/会话/节点，4 Tab（聊天/任务/资产/设置）+ 全局提醒横幅 | Hub session、GatewayProvider、TaskReminder |
| 连接账号 | `pair`、`account/login`、`account/index` | 扫码配对（相机/手输/高级URL）、Hub 登录注册找回、工作区列表/新建/落地偏好 | pair/challenge、device身份、Hub REST |
| 对话 | `chat`、`conversations/index`、`capture`、`capabilities` | 流式对话+多会话+拍照附件+8场景一键问 | sessions/turns/stream/retry/cancel、approvals resolve、Artifact上传/transcribe |
| 任务 | `tasks/index`、`tasks/new`、`tasks/[id]`、`tasks/[id]/edit` | Bento列表+最复杂新建表单（模板/NL解析/Agent/节点/MCP资源/附件/文件夹快照/启动策略/通知）+详情/编辑 | tasks CRUD + preflight + launch_policy + offline队列 |
| 执行 | `task-executions/[id]`、`event-sources/index` | 单次执行时间线（步骤折叠/审批/交互/Artifact/追问）+ Webhook/MCP事件源 | executions/events、approvals、interactions、event-sources CRUD/test/rotate-secret |
| 记忆 | `memories` | 7分类过滤+新增编辑Modal+置信度+删除清空 | memories list/upsert/delete/clear |
| 资产 | `(tabs)/assets`（+`artifacts/results/node`兼容跳转） | 任务+制品混合搜索/预览/保存/分享+节时统计 | tasks list + artifacts search/download |
| 设置族 | `settings/node,app,models,extensions,system,agents,agent-editor` | 节点详情/解绑、主题语言通知缓存诊断、模型增删共享、扩展四类导入+钉钉、配置版本流、Agent管理编辑 | node/config/models/sharing/extensions/dingtalk/agents |
| 更新 | `update` | Android APK自更新（查版/断点续传/sha256/调安装） | mobile/releases/android/latest |
| 工作区 | `workspaces/[workspaceId]/index,nodes,members,resources,work` | 仪表盘/节点目录+注册码/成员/共享资源授权/跨节点工作投影 | Hub workspaces/nodes/members/resources/work/notifications |

总体评价：信息架构完整，无缺页。问题在“深浅”：设置族 7 页极深（模型/扩展/系统全是运维表单），工作入口（对话/任务）反而浅。

---

## 2. 逐域评审：现状 → 缺口 → 建议

### 2.1 连接配对（`pair` + `account/*` + `workspaces/*/nodes`）

**现状（好）：** 扫码+手输+高级直连URL三路；设备命名；Hub登录/注册/找回三态+自托管URL；多节点绑定/切换/重连/解绑；Direct/LAN/mDNS/P2P/Relay自动选路+诊断；401回登录；首次连工作区自动建欢迎体检任务。

**缺口：**
1. P0：`nodes.tsx:106` 注册码曾是 `JSON.stringify` 直接上屏。已改为结构化展示 + 对账短码 + 实时倒计时 + 复制/分享（2026-09-20 落地）。注意方向证伪：曾提议"注册码改二维码"是错的——Enrollment Code 流向是 App → Node Console 粘贴框（`console_ui.py:165` textarea），消费方无扫码能力；本产品二维码只朝 Console → App 方向流（App 配对 QR 由 Node Console 生成、`pair` 页扫码）。首连优化到此为止，不再做 QR。
2. P1：配对载荷手输、网关URL、Hub自托管URL全是长串，手机键盘易错，无“扫码优先、其他方式折叠”渐进披露。
3. P1：传输状态（Direct/P2P/Relay）只在 header 小字展示，Relay 兜底时用户不知道“会慢但可用”，断线后无一键诊断（哪条路失败、重试哪条）。
4. P2：iOS 无更新通道但配对流程不做任何说明，iOS 用户配对后进 `update` 死胡同。

**建议：** 注册码改为二维码 + 短码（6位数字备用）+ 有效期倒计时；`pair/login` 只留扫码主按钮，长串收进“其他方式”；header 传输做三色点（绿Direct/蓝P2P/黄Relay）+ 点开展示诊断；iOS 在配对成功页直接提示“iOS 暂无自更新，走 TestFlight/侧载”。

### 2.2 对话（`chat` 1209行 + `conversations` + `capture` + `capabilities`）

**现状（App 内最高水准）：** pending回显/queued排队（已补，较飞书一致）、上传重试/幂等键、滚动管理、草稿加密存、离线缓存合并；文本+图片+文件+语音+拍照+剪贴板一键问；Agent切换；审批卡/交互表单卡/ActionCard（Markdown/KeyValue/CodeDiff/Artifact/Callout）；失败重试/反馈；ProactiveDeck 空态示例；DesktopGlance 桌面快照；Artifact 预览保存分享。

**缺口：**
1. P0：语音链路断点。转写依赖 Node 映射 `mcp__speech__transcribe`，App 内无“为什么不可用”引导，录音发出去才报错。`useVoiceRecorder` 有、解释无。
2. P0：图片安全策略用户不可见。App 侧压缩到 1024px 是对的，但超限/主模型无 vision 时必须走 `image_inspect`，失败只回 typed error，用户看到“发图没反应”。
3. P1：AI 侧仍在气泡里（09-09 §3.3 已建议去气泡化）。`assistantBubble` 白底圆角暗示 IM 回复，实际应是工作成果流：正文占满宽无底色，Thinking/工具 capsule/ActionCard 嵌流即可。
4. P1：`capabilities` 8 场景（file-organization/health-check/web-research/monitor/image-docs/desktop-control/task-automation/custom）只发一句话 prompt，无范围预览/预检，直达 chat 后 Agent 再问一遍“整理哪个目录”，多一轮。
5. P2：`capture` 仍是全 App 视觉最差页（文本快门、无 safe area、裸 Pressable），拍照→压缩→回传链路可用但丑。

**建议：** 语音按钮旁加可用性探针（无映射则置灰 + tooltip“去 Node Console 配置转写模型”）；图片发送前显“已压缩 1024px / 该模型走专用视觉理解”一行小字；AI 去气泡化；8 场景卡改为“场景→范围选择→预填带参数 prompt”（如整理目录默认跳任务创建而非聊天）。

### 2.3 任务（`tasks/index` + `tasks/new` 855行 + `[id]` + `edit`）

**现状（好设计多）：** Bento卡+分组+未读角标；8 模板+NL首行解析标题；Agent/节点/MCP资源选择；附件≤8+文件夹快照上传；`TaskLaunchEditor`（立即/定时/高级）；通知三开关；离线队列补发；详情预检拦截/警告；暂停/恢复/归档；事件驱动刷新。

**缺口：**
1. P0：Node 必选手选（`selectedNodeId` 默认当前节点，无推荐）。产品战略 P2 要求“按目标自动选即时/后台/周期+最优电脑模型”，现全量让用户选，与“不懂 Node”承诺直接冲突。
2. P0：模板覆盖已输入内容无确认（09-09 已点）。`selectedTemplate` 一点即覆盖 title/goal，用户写一半被吞。
3. P1：NL 解析只做首行截断（`parseNaturalLanguagePrompt` 取首行 30 字），不解析时间（“每天18:30”不会自动填 cron/interval），“自然语言创建”名不副实。
4. P1：Cron/时区/event_source 全在高级里，但渐进披露只有“做什么/何时做/是否重复/通知”四问的一半，普通用户看到 `TaskLaunchEditor` 仍懵。
5. P2：preflight blocked 只在详情页展示，新建页无原位高亮，失败才知道缺什么。

**建议：** 新建页顶部加“在哪跑”自动推荐行（默认推荐在线+模型可用节点，可手动改）；模板选择前 Alert；NL 解析加时间抽取（每天/每周/每X小时→interval/cron 预填 + 自然语言摘要“每天 18:30，北京时间”二次确认）；preflight 前移到新建页提交前一步。

### 2.4 执行（`task-executions/[id]` 599行 + `event-sources`）

**现状（严谨）：** 时间线合并（`mergeTaskTimeline`）、步骤/技术细节两层折叠、审批解决/交互回复、Artifact 查看保存、追问+加文件、取消/重试、缓存+事件增量刷新、深链直达。

**缺口：**
1. P0：`waiting_approval` 无常驻强提醒。执行页内有卡，但从列表/通知进来不置顶，用户错过审批导致任务挂起超时（`expired` 后无恢复引导）。
2. P1：技术细节（revision/快照/原始参数/错误码）折叠是对的，但失败四要素（原因/影响/恢复动作/继续入口）不齐，失败只显 `resultOutcome` 成功失败二值。
3. P1：`event-sources`（Webhook/MCP）创建要填资源前缀/secret，全是运维概念，无“当 Jira/GitLab 有新 issue 时叫我”式业务向导。`connectionWizard` 只在 extensions 里，未贯通到事件源。
4. P2：`glance` 桌面缩略图只在任务卡弹窗看，执行失败时无自动附快照，排查靠猜。

**建议：** 全局 `waiting_approval` 横幅（08-24 已有 TaskReminder，需升级为阻塞式）；失败页强制四要素模板；事件源加 3 个业务模板（Jira新issue/GitLab MR/定时网页监控）；失败执行自动挂最近 glance 图。

### 2.5 记忆（`memories` 917行）

**现状（超预期全）：** 7 分类过滤、置信度%、新增编辑Modal（key/value/category/core-relevant）、删除/清空。Gateway 侧 principal 隔离、`core` 常驻 prompt 语义正确。

**缺口：**
1. P0：记忆无“来源/生效”解释。用户不知道这条记忆是哪次对话写入的、影响哪个 Agent、下次何时生效，删了有什么影响 → 不敢管，等于不可管。
2. P1：`load` 失败静默 `catch {}`，空态与加载失败不可分，用户以为“我没记忆”实际是没连上。
3. P1：无搜索。条目一多只能按分类翻，`searchMemories` 能力后端有（memory tool search），App 未暴露。
4. P2：清空只有 Alert，无“导出备份”选项，误删不可回。

**建议：** 每条加来源行（手动/对话提炼 + 时间）+ 生效范围小字（全部会话生效）；失败显重试；加搜索框；清空前提供导出 JSON。

### 2.6 能力/模型/MCP/Skill（`capabilities` + `settings/models` 476行 + `settings/extensions` 344行 + `settings/agents/agent-editor` + `settings/system`）

**现状（治理范本级）：** 能力速览（Agent数/共享模型/工具数）；扩展四类导入（检查→计划→确认安装+风险展示）；模型增删（driver/endpoint/secret/vision/context/默认+跨节点共享+并发）；Agent 启用/默认/可见性+编辑器（Prompt/模型/委派）；系统配置 draft→validate→publish/回滚。这是全 App 最严谨的配置链。

**缺口（全是“太运维”）：**
1. P0：普通用户必须理解 provider/driver/endpoint/secret_ref/并发/共享授权才能加模型。LLM endpoint、API Key、MCP command、本地路径本应只在 Node Console（设计文档 §6 已定），但 App 全量暴露，无“手机只选模型、复杂配置去电脑”分流。
2. P1：Skill 5 个（file_organizer/health_check/image_doc/monitor/research_report）藏在扩展中心三级页，无一键启用+示例，`capabilityScenarios` 8 卡与 Skill 包两套平行入口，互相不知。
3. P1：MCP 本地路径/远端 URL 手输，无扫码/粘贴 manifest 快捷；`mcp_inspect` 结果（读写 hint）不展示，用户盲装。
4. P2：钉钉通道（client_id/secret/robot_code/receive_id）与模型表单同页，Secret 明文输入无遮蔽二次确认，长 Secret 手机输入灾难。

**建议：** App 模型页只做“选/启用/共享开关”，新增模型向导第一步即分流（简单：选 Workspace 共享模型；高级：去 Node Console）；8 场景卡与 5 Skill 包合并为同一“能力商店”（一键启用+跑示例）；MCP 接入加 manifest 粘贴解析；Secret 输入统一遮蔽+确认。

### 2.7 文件/资产（`assets` 676行）

**现状（合并方向对）：** 任务+制品混合搜索/过滤/刷新、`ArtifactViewer` 预览、落盘保存、分享 JSON/Text/PDF、复制、节时统计与类型分类。

**缺口：**
1. P0：制品只按 `sessionHandle` 检索（`searchArtifacts({sessionHandle})`），跨会话/跨任务全局搜索无。用户记得“上周那份 PDF”但忘了哪次对话 → 找不到。
2. P1：`q/kind/limit` 后端支持，但 App 无 kind 过滤（图片/文档/音频），只有 all/tasks/artifacts 三档。
3. P1：版本化无。同一任务多次产出覆盖式列表，无“v1/v2 + 继续处理”链（产品战略 P3 明确要求资产版本化）。
4. P2：大文件 Base64 中间副本性能隐患仍在（polish plan §3.6 已点），下载无局部进度。

**建议：** 资产搜索去 session 化（默认全 Node 搜）；加格式过滤；同一任务制品按版本分组 + “基于此版继续”一键跳 chat 带上下文；大文件下载加进度条。

### 2.8 通知提醒（`TaskReminderProvider` + `notifications/*` + Tab Badge）

**现状（链路全）：** 完成/失败/待审批三类 + Hub 通知箱轮询；前台横幅+震动+后台系统通知（Android 通道）；FCM token 注册；点击深链（任务/执行/会话/节点/更新）；按节点未读计数；查看中免打扰；标已读；测试通知。

**缺口（信任级）：**
1. P0：通知非事实源。SSE `after_id` 续播 + poll 兜底有，但杀进程/断网/重启后是否漏、是否消重（`mark_notification_intent_projected`），App 无“同步到最新”显式状态，用户不敢信“没通知=没事”。
2. P1：三端不一致。App 通知与飞书/钉钉各发各的，无统一已读（App 已读飞书还红点）。
3. P2：提醒只按节点计数，无按紧急度（approval/critical 淹没在完成流里）。

**建议：** 通知中心加“已同步到 HH:MM”时间戳 + 手动同步按钮；已读打通（App 读即调 Hub acknowledge，清全端）；审批/critical 置顶红区。

### 2.9 更新（`update` 268行）

**现状（Android 闭环完整）：** 查版、强制/可选判定、断点续传/暂停/sha256、调安装、未知来源跳转、前台回扫。

**缺口：** P1：iOS 死胡同页（08-24 已点）。`update.tsx` 对 iOS 无可用动作，应直接隐藏入口或转说明页。P2：更新说明（release notes）不展示，用户不知道“更了什么”决定更不更。

**建议：** 非 Android 隐藏更新入口；iOS 显示侧载指引；更新页显 release notes + 大小 + 强制原因。

### 2.10 设置/账号/工作区（`settings/app,node` + `account` + `workspaces/*` 5页）

**现状（模式优秀）：** 缓存优先+新鲜度横幅；传输诊断；主题/语言即时生效；账号多工作区切换/落地偏好；工作区仪表盘（人/机/资源/Work计数）+节点直连+下载链接；成员邀删；共享资源授权撤销；工作投影跨节点聚合+切节点深链。

**缺口：**
1. P0：工作投影只读不可写回是对的（Task 归属 Node），但“Stop/审批必须回权威 Node”一键回跳弱，跨节点审批要手动切节点再找任务，两跳以上。
2. P1：`(tabs)/settings` 一屏平铺四层（节点状态+工作区入口+治理+应用设置），与 header 节点入口、account 职责三重叠（09-09 已点）。
3. P1：共享资源撤销授权无确认（09-09 P0-3 未修），点错即断其他节点模型。
4. P2：`settings/app` 传输诊断（stages/switches/probes）全文暴露，普通用户看不懂，应折叠进“高级诊断”。

**建议：** 审批/Stop 深链自动切节点（`switchNode` + 跳执行页一气呵成）；settings tab 按 09-09 §3.4 改“小诺人格页”（对话/任务/成果+Agent本身），纯 App 设置收进角落；撤销授权加 destructive Alert；诊断折叠。

---

## 3. P0 五条价值闭环走查（文件整理/健康/图片资料/项目维护/简报）

| 闭环 | 入口 | 范围预览 | 预检计划 | 确认执行 | 证据交付 | 撤销继续 |  verdict |
|---|---|---|---|---|---|---|---|
| 文件整理 | capabilities卡→chat一句话，无目录选择 | 无 | 无（直接对话） | 有（tool确认） | 有（Artifact） | 撤销无 | **断在前两步**：应进任务创建带目录范围+移动计划预览 |
| 电脑健康 | 同上 | 无 | 无 | 有 | 有 | 一键修复无 | **断**：health_check Skill 有，App 无“一键体检→修复”按钮 |
| 图片资料 | capture拍照→chat附件，压缩OK | 有（预览） | 无（vision fallback 不可见） | 有 | 有（MD/PDF via分享） | 继续处理弱 | **半断**：OCR分类比较/MD导出链路有，失败重试引导无 |
| 项目维护 | task模板 project-maintenance | 有（goal手写） | preflight有 | 有 | 有（补丁摘要靠Agent自觉） | 无 | **最完整**，缺“定位改测审”结构化报告模板 |
| 简报 | research-brief 模板 | 无 | 无 | 有 | 有 | 分享有 | **断首步**：无数据源选择（MCP资源/网址），NL建任务不填资源 |

共性：后端 Skill/Tool 全有，前端无“预检-计划-阶段-证据-撤销”一致脚手架。建议抽 `ValueTaskScaffold` 组件，五条复用。

---

## 4. 多通道一致性（App / 飞书 / 钉钉 / TUI）

| 行为 | App | 飞书 | 钉钉 | TUI | 缺口 |
|---|---|---|---|---|---|
| 运行中排队第二条 | 有（queued） | 有（入队） | 同飞书 | 有 | ✅ 已一致（08-24 缺口已修） |
| 审批 resolve | 卡片+详情两处 | 卡片 | 卡片 | 命令 | ⚠️ 飞书/钉钉过期态无恢复入口，App 有详情可重试 |
| 通知事实源 | SSE+poll+Hub箱 | 主动推卡 | 主动推卡 | 无 | ❌ 三端已读不通，App杀进程后与 IM 不一致 |
| 语音转写 | 录音+transcribe | 语音直转（需映射） | 同飞书 | 无 | ⚠️ 三端同依赖 Node 映射，但只有 App 录音后报错，IM 端静默丢 |
| Agent 切换 | `/agent` 选择器 | `/agent <id>` | 同飞书 | `/config` | ✅ 语义一致，UI 不同可接受 |
| 更新 | Android 自更新 | 无 | 无 | 无 | ⚠️ iOS/桌面无统一说明 |

---

## 5. Top 缺口清单与路线图

### P0（不修不像产品，1-2天可解大半）
1. 注册码展示优化已落地（短码对账+倒计时+复制/分享；二维码方向已证伪，不做）。
2. 任务 Node 自动推荐（默认在线+模型可用，可手改）——兑现“不懂 Node”。
3. 通知“已同步到 HH:MM”+ 已读打通三端 —— 后台信任状。
4. 语音/图片不可用引导（一行小字探针）——消灭“发了没反应”。
5. `waiting_approval` 全局阻塞横幅 + 过期恢复 —— 任务不挂起。

### P1（闭环补齐，3-5天）
6. `ValueTaskScaffold`：范围预览→预检→计划→证据→撤销，五条 P0 复用；模板覆盖确认；NL 时间抽取（每天18:30→cron预填+摘要确认）。
7. 资产全局搜索（去 session 化）+ 格式过滤 + 版本分组 + 继续处理。
8. 记忆来源/生效行 + 搜索 + 失败重试 + 清空前导出。
9. 能力商店合并（8场景卡 + 5 Skill包，一键启用+示例）；模型页分流（手机只选，复杂去 Node Console）；事件源业务模板 3 个。
10. settings tab 改人格页；撤销授权/删事件源/取消草稿确认补齐；iOS 隐藏更新。

### P2（护城河）
11. AI 去气泡化 + chat 空态节时卡 + 完成成果卡回流 chat。
12. 多 Node 自动选优（按目标选即时/后台/周期）；资产版本化；主动建议（注明范围原因）。
13. 诊断折叠、触控 44pt、字阶收敛（09-09 第一梯队剩余）。

### 验收（发布门）
- 新用户 60 秒首胜：扫码→欢迎体检任务→可下载结果。
- 任务创建不问 Node/Cron；执行页必显 Node+权限；高风险无确认不写。
- 杀进程/断网/重启后通知→深链→审批→结果一致。
- 失败页四要素（原因/影响/恢复动作/继续入口）。
- 全端无 transport/runtime/provider/MCP 术语暴露（诊断页除外）。

---

## 7. 界面本身：合理吗，有什么建议

总判：**骨架合理，视觉系统刚及格，交互细节偏工程师审美。** 4 Tab + Node 胶囊方向对，`src/theme.ts` 语义色已收敛（含暗色变体，`onAccent` 对比度已修），但合起来仍像“远程控制面板”而不像“个人助理”——焦点散、字重吵、AI 还在聊天气泡里。

### 7.1 肯定（保留）

- 底部 4 Tab（聊天/任务/资产/小诺），`app/(tabs)/_layout.tsx:40-102`，不跟输入框抢空间；任务 Badge 未读数合理。
- `NodeHeaderTitle` 节点名 + 在线点 + 传输方式，`src/components/NodeHeader.tsx:37-46` 三色点已有，信息密度对。
- `AsyncStateView`、`AppPressable`、`FormScreen` 基建齐；`src/components/chat/ChatTurnItem.tsx:80-92` 长按复制 + 时间戳分组正确。
- `ProactiveDeck` 空态可点示例、任务 `TaskBentoCard`、欢迎体检任务是仅有的“伙伴感”，不要删。

### 7.2 导航：设置 Tab 是杂物间

`app/(tabs)/settings.tsx:83-165` 一屏塞“Agent 能力 + 主机网络 + 系统偏好”三段 13 个入口，又与 header 节点设置、`/account` 三重叠。用户找“换模型”要猜三次。

建议：Tab 只留“我和小诺的关系”——对话（此刻在干嘛）、任务（替我做的事）、成果（交付的东西）、小诺本身（连哪台电脑/记住什么/会什么）；纯 App 设置（主题/语言/更新/缓存）收进角落入口。齿轮暗示“系统”，logo 暗示“你的 Agent”，后者才是产品叙事。

### 7.3 视觉：色对执行灰，字重吵

鼠尾草绿 + 米色纸感方向对，但浅色 accent `#1F7A5C` 太收，全页灰绿灰白无焦点；`theme.ts:40-59` 9 档字阶已有但 700 滥用、800 还有 100+ 处。

立规矩：800 只给大标题和批准/拒绝 CTA；列表标题 700、正文 600、辅助 500；行动色永远比界面镀铬醒目（ActionCard 的蓝/琥珀/红收编进 token 并配暗色变体，而不是删掉）。

### 7.4 聊天流：AI 去气泡化

`ChatTurnItem.tsx:94` `assistantStream` 仍在白底圆角里，暗示 IM 回复。小诺是替你干活的同事：用户气泡保留（右对齐短指令），AI 正文占满宽无底色，Thinking/工具 capsule/ActionCard 以卡片嵌流即可。Markdown 长文可读性远超 84% 宽气泡。

情感化两处低成本动作：chat 空态放一句“这周小诺替你节省了 X 小时”（`calculateTotalSavedHours` 已有）；任务完成时在 chat 流里插一张小成果卡（ActionCard 已挂消息流，顺手的事）。

### 7.5 分页硬伤（按修的性价比排序）

- `app/capture.tsx:29-57`：全 App 视觉最差（文本快门、无 safe area 底部栏）。按 token 重做快门按钮与底部栏即可。
- `app/memories.tsx:61-76`（917 行）：加载失败 `catch {}` 静默，空态与断网不可分，无搜索。每条加来源行（手动/对话提炼 + 时间）+ 搜索框。
- `app/tasks/new.tsx:69-93`：NL 解析只截首行 30 字，“每天 18:30”不会填 cron。加时间抽取 + 自然语言摘要二次确认；模板覆盖已输入内容前 Alert。
- `app/workspaces/[workspaceId]/nodes.tsx:106`：注册码 `JSON.stringify` 上屏，首连门神。改二维码 + 短码 + 有效期倒计时。
- `app/update.tsx`：iOS 是死胡同页，非 Android 隐藏入口，转侧载指引；更新页补 release notes + 大小。
- `app/pair.tsx` + `app/account/login.tsx`：双 QR 扫码器合并为共享 `ScannerScreen`。
- 表单共性：`react-native-keyboard-controller` 已在依赖，抽 `FormScreen` 统一键盘规避 + 脏检查拦截（agent-editor、tasks/edit），一次性解决 login/agent-editor/event-sources/follow-up 遮挡。

---

## 8. 附：本次未动、后续观测

- 视觉 token/字阶/硬编码色 36 处（09-09 已立项执行，不重复）。
- GatewayPedia `openapi.json` 11642 行与 `src/api` 47 个 vitest 复用做 Web 管理台 MVP（09-09 第三梯队建议，可选）。
- 真机长期手感、低内存恢复、私有签名发布链（polish plan 已验收 0.2.13，可沿用）。

*评审人：App 产品设计视角；证据均为仓库只读核查；无 JIRA、无占位符。*
