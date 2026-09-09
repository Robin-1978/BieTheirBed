# 小诺 Knoa Mobile 设计评审与重构建议（2026-09-09）

评审范围：`apps/knoa-mobile` 全部 38 个 Expo Router 页面、`src/theme.ts` 设计 token、
`src/state/GatewayProvider.tsx`、`src/components/` 组件层、i18n 与原生主题桥接；
并对照《小诺 Knoa 项目功能与 APP 页面设计评审（2026-08-24）》逐项验证修复状态。

**关于"网页端"：仓库中不存在独立的 Web 前端。** 面向用户的客户端只有 Knoa Mobile
（Expo/React Native，Android 为主，iOS 无发布通道）；README 中的 Rich TUI 与
飞书/钉钉机器人是终端与 IM 通道。本文因此聚焦移动 App；文末第九节给出 Web 管理台
的战略建议（如需）。

**总体结论：设计债较 8 月评审还清大半，但清得不彻底——老问题根因仍在，新问题集中
在三个 900+ 行巨石屏幕、一个 693 行上帝 Context，以及 ActionCard 新组件绕过语义
token 造成的色板倒退。** 功能与可靠性工程维持 9 分，设计一致性由 6 分升至约 7.5 分。

---

## 一、上次评审 Top 10 修复验证（2026-08-24 → 2026-09-09）

| # | 上次问题 | 状态 | 证据 |
|---|---------|------|------|
| 1 | 深色 accent 上白字对比度 2.2:1 | ✅ 已修复 | `theme.ts:20` 新增 `onAccent`（暗色 `#102E24`，实测 6.58:1 达 WCAG AA）；`android/.../values-night/colors.xml:15` 原生侧同步 |
| 2 | AsyncStateView 复用率低 | ✅ 已修复 | 现 18 个页面引用，覆盖 settings/workspaces/tasks/event-sources 全部点名页面 |
| 3 | 不可逆操作无确认 | ⚠️ 部分修复 | 事件源删除（`event-sources/index.tsx:76`）、取消草稿（`system.tsx:161`）已加 Alert；`workspaces/[workspaceId]/nodes.tsx` 撤销授权仍无确认 |
| 4 | radii/spacing/shadows token 零引用 | ✅ 已修复 | app 下 30 文件、components 下 24 文件引用；`chat.tsx` 7 个死样式已清除 |
| 5 | 键盘处理缺失 | ❌ 未系统解决 | 仍只有 chat/pair/new 做了规避；login/agent-editor/event-sources/follow-up 依旧 |
| 6 | artifacts 页手输会话句柄 | ✅ 已修复 | `artifacts.tsx` 现为带参 Redirect 至 assets 页 |
| 7 | results 节点筛选误触全局切换 | ✅ 已修复 | `results.tsx` 同为纯 Redirect，`switchNode` 副作用已移除 |
| 8 | chat 无时间戳/长按复制 | ✅ 已修复 | `ChatTurnItem.tsx:82,97` 长按复制；时间戳按 `TIMESTAMP_GROUP_MS` 分组 |
| 9 | 任务详情状态口径断裂 | ✅ 已修复 | `tasks/[id].tsx:276` 现读 `execution.work_status` |
| 10 | 注册码展示原始 JSON | ❌ 未修复 | `workspaces/[workspaceId]/nodes.tsx:106` 仍 `JSON.stringify(payload)` 直接上屏 |

另：上次功能缺口"运行中不能排队第二条消息"已解决（`chat.tsx:901` 引入 `queued` 态，
pending 气泡显示"排队中"）。

---

## 二、当前问题清单（按严重度）

### P0 — 架构层，不修会越来越痛

1. **上帝 Context**：`src/state/GatewayProvider.tsx` 693 行、28 个字段、16 个方法，
   连接状态、传输诊断（p2p/lan/relay 各 4 字段）、节点列表、Agent 列表、更新检查、
   会话管理挤在一个 `useState`。任何字段变化触发全树重渲染；chat 页为此出现
   `gatewayRef.current = gateway` 逃逸舱。
2. **巨石屏幕**：`chat.tsx` 1259 行（61 个 hooks、11 个 useEffect、174 行样式表）、
   `workspaces/[workspaceId]/index.tsx` 944 行、`memories.tsx` 917 行、
   `tasks/new.tsx` 855 行。chat 页把录音、剪贴板建议、附件上传、审批、滚动管理、
   草稿、会话预热揉在一个组件里。

### P1 — 设计系统层

3. **字阶失控**：17 种字号（9–31px），9 处 10px、2 处 9px 低于可读下限（角标除外）；
   字重 700×165、800×123，"全都在喊"。`typography` token 只有 20 个屏幕文件在用，
   一半纪律比没纪律更乱。
4. **硬编码色 36 处**，集中于 ActionCard 区块（`CardCalloutBlock`、`CardCodeDiffBlock`、
   `CardHeader` 整套 Tailwind 色 `#DCFCE7/#15803D/#0284C7` 等）——最新组件完全绕过
   语义 token，暗色模式下必然翻车，是**新引入的倒退**。另有 `#10B981`（workspaces、
   tasks/new）、`#050706`（ArtifactViewer）等零散残留。
5. **裸 Pressable 31 处**：capture 页 5 处（上次已点名）、update 页自定义按钮、
   chat 遮罩层；`ChatTurnItem.tsx:97` 长按复制亦未用 AppPressable。
6. **触控目标不足**：16 处 `paddingVertical: 4–7` 小按钮低于 44pt 标准。

### P2 — 一致性尾巴

7. i18n 单文件 3171 行、3116 个 key 塞在 `src/i18n/index.tsx`。
8. 中文注释残留 JSX（`app/(tabs)/settings.tsx:36,81,101,136`），与全英文代码库不符。
9. `expo-clipboard` 在 package.json 声明但 node_modules 缺失，`tsc --noEmit` 报错。
10. `(tabs)/settings.tsx` 一屏平铺"节点状态 + 工作区入口 + 治理 + 应用设置"四层内容，
    与 header 节点入口、account 页职责三重叠。

---

## 三、App UI 设计的方向性想法

总判断：工程底子是商业产品水准，但视觉与交互语言仍停留在"工程师审美"——每个页面
单看不差，合起来缺少让人记住的性格。它现在像"功能完备的远程控制面板"，而产品定位
（Know-You Agent、个人助理）要求它像"一个活生生的伙伴"。

### 3.1 色彩：方向对（鼠尾草绿 + 米色的纸感助理气质），执行太"灰"

整个色板饱和度与明度过于接近，页面没有焦点——输入框、气泡、按钮、卡片全是相近的
灰绿灰白，用户打开 chat 视线无处安放。暗色模式反而比浅色好，因为 accent 终于亮了。

**建议：保持中性底，让 accent 在浅色模式也"敢"一点；并建立"行动色 > 界面色"原则。**
ActionCard 硬编码的 Tailwind 状态色（蓝/琥珀/红）虽然破坏 token 纪律，却证明了一件事：
Agent 的"行动"（审批请求、任务完成、桌面一瞥）必须比界面镀铬更醒目。正确做法是
收编这套语义色进 theme 并配暗色变体（`success/info/warningSoft` 等），而不是删掉它。

### 3.2 字重：123 处 800 是最大视觉噪音

立硬规矩：

- **800 只给两类**：页面大标题、需要立刻决策的 CTA（批准/拒绝）
- 列表标题 700、正文 600、辅助 500（现 500 仅用 9 次，严重闲置）
- 字阶收敛为 11/13/15/17/20 五档，lint 禁裸 fontSize，强制走 `typography` token

### 3.3 聊天流：AI 侧"去气泡化"

`userBubble`（accent 圆角 + 尾巴）保留——轻、短、右对齐，符合"人对 Agent 下指令"。
但 `assistantBubble` 的白底 + 圆角仍在暗示"这是 IM 回复"。小诺不是聊天对象，是
**替你干活的同事**，它的输出是工作成果流：

- AI 正文左对齐占满宽，无边框无底色，markdown 长文可读性远超 84% 宽气泡
- ThinkingCard、工具 capsule、ActionCard 以卡片形式嵌在流中（现有组件方向已正确，
  去掉那层气泡壳即可）

### 3.4 信息架构：设置 tab 是"杂物间"

按心智模型重排：

- **底部 tab 只留"我和小诺的关系"**：对话（它此刻在干嘛）、任务（它替我做的事）、
  成果（它交付的东西）
- **第四个 tab 从齿轮改为"小诺"本身**（AppIcon logo）：Agent 人格页——连着哪台电脑、
  记住了什么（memories）、装了什么能力（extensions）、模型配置。齿轮暗示"系统设置"，
  logo 暗示"这是你的 Agent"——后者才是产品叙事
- 纯 App 设置（主题/语言/更新）收进该页角落入口

### 3.5 情感化：Trophy/Bento 方向对，但孤悬在主路径之外

`calculateTotalSavedHours`、TaskBentoCard、Desktop Glance 是"让 Agent 显得在为你
干活"的好设计，但藏在 assets tab 和详情页里。两个低成本动作：

- chat 空态（ProactiveDeck）放一句"这周小诺替你节省了 X 小时"
- 任务完成时在 chat 流里插一张小成果卡（ActionCard 已挂消息流，顺手的事）

---

## 四、整体页面建议

### 4.1 启动页 `app/index.tsx`

轨道 + 呼吸 + 光晕动画有品牌感，尊重 reduceMotion 是亮点。建议：

- 恢复失败静默落 `/account`（`index.tsx:47`）应带一句原因提示（"未找到已配对设备"）
- "恢复中"在弱网下无进度感——可加阶段性文案（连接 Hub → 校验会话 → 同步节点）
- 启动页是品牌第一印象，"诺"字核心 + KNOA eyebrow 的字号层级可以更大胆

### 4.2 页头（NodeHeaderTitle）

节点名 + 在线状态 + 传输方式（direct/p2p/relay）信息密度合理，但：

- "连接中"状态无视觉区分（无状态点、无 spinner），建议加绿/灰/红点
- 点击展开的切换器承载了太多功能（切节点、桌面一瞥、记忆入口），考虑把
  Desktop Glance 提升为更显著入口——它是"Agent 在场感"最强的功能
- 工作区名 fontSize 10（`NodeHeader.tsx:372`）低于可读下限

### 4.3 列表页共性

- 下拉刷新仍非全量覆盖（assets 页有，多个 settings 页无）
- 长列表无分页/虚拟化的隐患仍在（执行时间线、work 上限 300 条全量渲染）
- 空态质量参差：chat 空态（ProactiveDeck 可点示例 prompt）是范本，应抽象成
  共享 EmptyState 组件推广到 tasks/conversations/assets

### 4.4 表单页共性

- 键盘规避应做全局方案：`react-native-keyboard-controller` 已在依赖中，封装
  `FormScreen` 组件统一替换 ScrollView，一次性解决 login/agent-editor/
  event-sources/follow-up 的遮挡
- 脏检查拦截返回（agent-editor、tasks/edit）仍未做，配合 `FormScreen` 一并解决

### 4.5 分组页面速评

- **chat**：可靠性链路（pending 回显/上传重试/幂等键/滚动管理）是 App 内最高水准，
  按 3.3 去气泡化后即是定稿方向
- **tasks**：Bento 卡片 + 离线队列 + preflight 二次确认都是好设计；new.tsx 的
  模板覆盖已输入内容仍无确认（选模板前 Alert）
- **workspaces**：缓存优先 + 新鲜度横幅模式优秀；注册码必须改二维码（见 P2）
- **settings/system**：草稿两态 + validate/preflight/publish 流程严谨，是治理页范本
- **capture**：全 App 视觉纪律最差页面（文本快门、无 safe area、裸 Pressable），
  需要按 token 重做快门按钮与底部栏

---

## 五、重构路线（按投入产出排序）

### 第一梯队（1–2 天，纯还债）——已立项执行

1. ActionCard 36 个硬编码色收进 theme 语义 token，按"行动色比界面色醒目"原则配暗色变体
2. 统一字阶 + 清除 10px 以下正文；小按钮补 `minHeight: 44` / `hitSlop`
3. 注册码改二维码/短码展示（`nodes.tsx:106`）
4. capture/update/chat 裸 Pressable 换 AppPressable
5. `npm install` 修复 expo-clipboard 类型错误

### 第二梯队（3–5 天，结构性）

6. 拆 GatewayProvider 为三：`ConnectionContext`（状态机 + 传输诊断）、
   `SessionContext`（会话句柄 + 对话操作）、`FleetContext`（nodes/agents/update 慢变数据）
7. chat.tsx 抽 `useChatTurns`（watcher + 快照合并 + 分页）、`useVoiceRecorder`、
   `useClipboardSuggestion` 三个 hook，屏幕组件只留编排，目标 400 行内；
   同法处理 workspaces/index 与 memories
8. i18n 按域拆文件（chat/tasks/settings/...），key 加命名空间
9. `FormScreen` 组件统一键盘规避 + 脏检查拦截

### 第三梯队（产品决策）

10. AI 侧去气泡化 + chat 空态/任务完成情感化卡片（3.3、3.5）
11. 第四 tab 改"小诺"人格页（3.4）
12. Web 管理台战略：网关已有 OpenAPI（`scripts/export_gateway_openapi.py` +
    11642 行生成 schema），最划算路径是复用 `src/api` 层（47 个平台无关 vitest
    测试可直接复用）+ Expo Web 导出做管理台 MVP，而非另起 Next.js 项目

---

## 六、一句话总结

上次评审的矛盾是"做对的基础没贯彻到每个页面"；这次的矛盾变成了**"新功能
（ActionCard、Bento、Glance）以绕过设计系统的方式快速落地"**。第一梯队把这次
的倒退补上；第二梯队拆掉巨石文件，让"快速落地"未来不必以绕过系统为代价；
第三梯队让设计从"克制"走向"克制但有性格"——用户的注意力应该永远落在
"小诺正在为我做什么"上，而不是界面的框架上。
