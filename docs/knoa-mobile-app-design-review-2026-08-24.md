# 小诺 Knoa 项目功能与 APP 页面设计评审(2026-08-24)

评审范围:平台功能面(README、`src/knoa_platform/` 模块、`docs/` 设计文档)+ Knoa Mobile
全部约 30 个 Expo Router 页面(精读启动页与聊天页,其余页面按分组逐文件通读)。
本文记录当时评审结论,后续修复不必回填本文,以实际代码为准。

**总体结论:工程成熟度很高,设计一致性是主要短板。**

- 平台侧功能体系完整:Agent 运行时、持久任务、审批、MCP 扩展、多通道、Hub/Node 架构。
- 移动端可靠性工程(缓存优先、SSE 增量刷新、离线队列、幂等键、乐观锁)达到商业产品水准。
- 设计层面存在系统性"半成品感":设计 token 定义了却没人用、异步三态/危险操作确认/键盘处理各页各自为政、
  深色模式下主按钮对比度不达标。

评分概括:**功能与可靠性工程 9 分,设计系统与一致性 6 分。**

---

## 一、功能评审

### 1.1 平台能力

功能面完整,架构文档与代码模块对应关系清晰:

- **Agent 运行时**:ReAct 循环、SDB 验证门、Plan-Execute、反思、多 LLM 适配
  (llamacpp/OpenAI/Anthropic/兼容 API)、prompt 缓存、token 校准、视觉分离运行时。
- **持久化与自动化**:Durable Tasks(事件重放、审批暂停/恢复、Attempt/ToolStep 检查点)、
  一次性/间隔/Cron 调度、webhook 触发器(HMAC 签名 + 事件级幂等)。
- **安全边界**:工具策略、能力清单、危险命令拦截、确认门、类型化拒绝码、审计 JSONL、
  MCP 工具逐个声明策略——安全设计是项目的突出强项。
- **通道**:飞书 WebSocket、钉钉 Stream(免公网回调)、TUI、移动 Gateway;
  审批审阅 Agent(suggest/auto 模式,高风险永远人工)。
- **部署形态**:Hosted Hub + Node、工作区/成员/共享资源/远程模型共享、
  mDNS 局域网发现 + TLS 远程网关、单次性配对授权、Android 私有更新通道。

### 1.2 功能层缺口

1. **App 内不能在运行中排队第二条消息**:`apps/knoa-mobile/app/chat.tsx:285` 的 `canSend`
   要求 `!activeTurn`,而飞书通道明确支持"新消息可在前序任务运行时入队"。
   同一产品两个通道行为不一致,App 侧体验反而更弱。
2. **artifacts 页要求手输"会话句柄"**(`apps/knoa-mobile/app/artifacts.tsx:104`):
   内部技术概念直接暴露给用户,应改为会话列表选择器。全 App 最伤用户的功能缺口。
3. 语音转写依赖 Node 侧配置映射,App 内无引导告知用户"语音为什么不可用"。
4. iOS 无更新通道(仅 Android 私有发布),`update.tsx` 对 iOS 用户是死胡同页面。

### 1.3 移动端功能覆盖

登录/注册/扫码恢复 → 工作区 → 节点配对(扫码/注册码)→
聊天(附件/拍照/语音/审批/人机交互表单)→ 会话历史 →
任务 CRUD + 模板 + 离线队列 → 执行详情(时间线/审批/follow-up)→
结果筛选与三格式分享 → 事件源管理 →
设置(应用/Agent 编辑器/模型/节点诊断/扩展含钉钉/系统配置草稿工作流)→ 自更新。

核心闭环完整,且大量页面配有 vitest 表现层测试,质量意识好。

---

## 二、设计系统与全局架构评审

### 2.1 做得好的

- `apps/knoa-mobile/src/theme.ts` 用 `PlatformColor`/`DynamicColorIOS` 双平台原生语义色,
  暗色模式根基扎实;几乎所有页面颜色纪律良好。
- `AppPressable`(统一按压/禁用/ripple)、`AppIcon`(统一 Ionicons)、
  `AsyncStateView`(三态卡片)三个基件思路正确。
- i18n 中英双语全覆盖;无障碍认真(accessibilityRole/Label/State 系统性使用,
  tasks 列表卡是范本)。
- 全局导航模型聪明:chat ↔ tasks 主双屏左右滑切换(`PrimarySwipeNavigation`)
  + Header 双 tab 带未读角标 + "更多"进节点菜单,符合个人助理高频双场景定位。

### 2.2 系统性问题(设计债务核心)

1. **设计 token 用了一半**:`theme.ts:32-50` 定义的 `radii/spacing/shadows` 在全部页面零引用,
   圆角 9–20、间距 13/14/16/17/18 各写各的;卡片无阴影全靠 1px 描边,层级扁平。
   所谓一致性实际是"每页手工对齐"的伪一致。
2. **深色模式主按钮对比度不达标**:暗色 accent 是 `#7DBAA8`(`theme.ts:16`),
   其上叠白字/白图标(send 按钮、用户气泡 `chat.tsx:1392-1393`、批准按钮 `chat.tsx:1412-1413`、
   启动页"诺"字、工作区 startWork 等)对比度仅约 **2.2:1**,远低于 WCAG AA 的 4.5:1。
   暗色模式需要改用深色文字或压深 accent。
3. **字阶缺失**:`fontWeight: "800"` 无差别覆盖标题/行标题/按钮,页面"全都在喊";
   同时存在 10–11px 辅助文字(`capabilities.tsx:183` 的 fontSize 10 低于可读下限)。
4. **禁用态两套并存**:页面局部 0.45 与 `AppPressable` 全局 0.52 叠加,
   `pair.tsx:101` 双重叠加后约 0.26,禁用按钮淡到看不见。

---

## 三、分组页面设计评审

### 3.1 启动页 `app/index.tsx`

品牌化启动动画(轨道+呼吸+光晕)有品质感,且尊重 `reduceMotion` 无障碍设置
(`index.tsx:26-42`)是亮点;错误态带重试。

问题:

- 恢复逻辑失败时静默落到 `/account`(`index.tsx:47`),用户不知道为什么。
- 动画只覆盖加载路径,弱网下"恢复中"文字无进度感。

### 3.2 聊天页 `app/chat.tsx`(最核心页面)

全 App 体验工程最好的页面,多处达到精品水准:

- **发送可靠性链路完整**:pending 气泡本地回显 → 附件逐个上传状态
  (上传中/失败可单独重试 `chat.tsx:425-446`)→ 整体失败可"重试/编辑回填"
  (`chat.tsx:1220-1229`),幂等键 `clientRequestId` 防重复。
- **滚动管理精细**:`followLatest` 引用 + 距底 80px 阈值 + "回到底部"悬浮按钮 +
  加载历史时 `maintainVisibleContentPosition` 防跳动(`chat.tsx:777-828`)。
- 会话预热(打开即建会话省一轮往返,`chat.tsx:223-226` 有清晰注释)、
  草稿按会话持久化、SSE watcher 合并快照、图片能力预检
  (失败引导去配置 Agent,`chat.tsx:378-401`)、四色反馈横幅 + 成功自动消失。
- 空态带可点示例 prompt(`chat.tsx:841-855`),新用户引导优秀。

问题:

- **消息无时间戳、无长按复制**——聊天产品两个基础缺项;
  语音录制只有秒数计时,无取消手势。
- 同文件内交互语言不一致:失败重试/编辑在 `ChatTurn` 用裸 `Pressable`
  (`chat.tsx:1171,1174`),在 `PendingTurn` 用 `AppPressable`(`chat.tsx:1222,1225`)。
- **死代码**:`previewRoot` 到 `previewHint` 七个样式(`chat.tsx:1479-1486`)
  是旧版内联图片预览遗留,已被 `ArtifactViewer` 取代,应删除;
  `styles.remove`(`chat.tsx:1434`)也未使用。
- `#FFD1CC` 硬编码(`chat.tsx:1399`)绕过 danger token;
  更新横幅用裸 `Pressable`(`chat.tsx:754,763`)。

### 3.3 连接与账户组(account/login、pair、node、update)

亮点:

- pair 页相机权限三分支处理(canAskAgain 区分文案 + 永久拒绝深链设置)是全 App 范本。
- update 页断点续传状态机(后台自动暂停、装完自动恢复、从系统设置返回续装)工程体验好。
- 登录页扫码自动区分恢复/注册。

主要问题:

- 两个 QR 扫描器(login 与 pair)视觉规格完全不同,应合并为共享 ScannerScreen。
- node 页重连按钮无任何 busy 反馈(`node.tsx:55-57`);在线状态只有文字无状态点。
- account 页错误渲染在页面最底部(`account/index.tsx:221`),列表长时不可见。
- 三段模式切换在英文 locale 下会挤压换行;登录表单无 `returnKeyType`/客户端校验/KeyboardAvoidingView。

### 3.4 工作区与工作成果组(workspaces/*、results、artifacts、capture)

亮点:

- 工作区四子页统一"缓存优先渲染 + 聚焦静默刷新 + 新鲜度横幅(WorkspaceCacheBanner)"
  离线模式,弱网体验优秀。
- results 页是全 App 状态设计最佳实践(唯一三态齐全 + 下拉刷新 + 分享禁用态)。
- 审批流信息架构(动作/目标/审阅者/效果/风险/可逆性分层)成熟。

主要问题:

- **results 页"节点筛选"实为 `gateway.switchNode` 全局切换当前连接**(`results.tsx:94`),
  以筛选外观做全局副作用,是危险的可用性陷阱。
- **节点注册码直接展示原始 JSON 字符串**(`nodes.tsx:105`),应改二维码/短码。
- 撤销授权无确认对话框而移除成员有——不可逆操作确认策略自相矛盾。
- resources 页 `working` 单值键碰撞导致无关按钮同时 spinner。
- 工作区首页 7 项能力两列网格奇数收尾不对称、图标复用区分度差。
- capture 页完全绕开 AppPressable 和 token 纪律:快门是文本按钮、无前后摄/闪光灯、
  底部未处理 safe area。

### 3.5 任务组(tasks/*、task-executions、event-sources)

亮点:

- 任务列表的离线队列重试 + SSE 防抖刷新 + 未读红点无障碍隐藏。
- 新建任务的离线入队保留幂等键。
- 执行详情页审批按钮级 busy 追踪(精确到 `{id, approved}`,
  `task-executions/[id].tsx:397-414`)和时间线"进行中展开/结束后折叠"策略。
- preflight 警告项二次确认为"用户决策而非静默覆盖"的设计意识。

主要问题:

- **列表与详情状态口径断裂**:列表显示 work_status("等待审批"),
  详情只显示定义态"启用"(`tasks/[id].tsx:166` vs `tasks/index.tsx:241-250`)。
- **新建任务 `autoFocus` 在 goal 输入框**(`new.tsx:282`),
  进页即弹键盘遮挡节点/模板选择。
- **选模板静默覆盖已输入的标题/目标**(`new.tsx:242-246`),丢内容风险高。
- 编辑页加载失败仍渲染可提交的空表单(`edit.tsx:85`),且无脏检查返回拦截。
- event-sources 页状态设计最差:无 loading/空态、删除无确认、疑似双导航头
  (`event-sources/index.tsx:100-104` 自绘头 + 未注册 Stack Screen 的默认头)。
- 执行详情 follow-up 无键盘规避;时间线无耗时无虚拟化。

### 3.6 设置组(settings/*、capabilities、conversations)

亮点:

- conversations 页是全 App 数据交互最完整的一页(乐观删除+失败回滚、
  删除当前会话自动新建、归档预热缓存)。
- system 页草稿两态设计(非草稿全禁用)+ validate/preflight/publish 强制流程严谨。
- settings/app 是唯一使用 radiogroup 语义的页面。

主要问题:

- **Agent 编辑器无脏状态跟踪**,返回即丢全部编辑;
  system 页"取消草稿"是无确认的弱样式文字链,一次误触丢弃全部修改——
  与删除 Agent 的郑重程度倒挂。
- models 页 alias 不可自定义(自动生成 `model_xxxx`,`models.tsx:105-111`),
  且 `aliasImmutable` 校验成为永不可达的死代码。
- extensions 页信息架构最混乱:操作结果渲染在触发表单上方需回滚查找;
  `capabilityAction` 甚至无 catch(失败产生未处理 Promise rejection,
  `extensions.tsx:139-149`)。
- agents/models 加载失败后页面永久 spinner 或列表消失、无重试——
  现成的 `AsyncStateView` 未被复用。
- chips 选中态出现第三种语言(实心 accent 底白字 vs 全局"accent 描边 + accentSoft 底")。

---

## 四、跨页面共性问题汇总(按严重程度)

| # | 问题 | 典型证据 |
|---|------|---------|
| 1 | 深色模式 accent 上白字对比度 ~2.2:1 | `chat.tsx:1392-1393`、send/批准按钮等全局 |
| 2 | 异步三态碎片化:error 常压页底无重试,AsyncStateView 复用率低 | `account/index.tsx:221`、`agents.tsx:85,140`、event-sources 全缺 |
| 3 | 不可逆操作确认策略不一致 | 撤销授权/事件源删除/取消草稿无确认 vs 移除成员/删任务有 |
| 4 | radii/spacing/shadows token 零引用,字号 10–24、字重 700/800 漂移 | 全部页面样式表 |
| 5 | 键盘处理:仅 chat/pair/new 做了规避;login/agent-editor/event-sources/follow-up 缺失 | `task-executions/[id].tsx:337` |
| 6 | 长文本缺 `numberOfLines`:work 标题兜底 UUID、artifacts 文件名、节点名等 | `work.tsx:123`、`node.tsx:34` |
| 7 | 下拉刷新仅 4/约 15 列表页有;列表均无虚拟化/分页(work 上限 300 条全量渲染) | `workspaceCache.ts:13` |
| 8 | 触控目标 31–36px 的按钮(filter pill、Small 按钮、移除文本链)低于 44pt | event-sources Small 按钮 `paddingVertical: 7` |
| 9 | 裸 `Pressable`/硬编码色零星绕过基件(update、capture、chat 局部) | `update.tsx:233`、`capture.tsx:61-93` |

---

## 五、优先级建议(Top 10)

1. **修深色模式对比度**:暗色 accent 背景改深色文字或压深 accent——
   影响全局所有主按钮与聊天气泡,改动集中在 theme 一处 + 白字引用点。
2. **统一异步状态语言**:强制所有列表/详情页走 `AsyncStateView`(含重试),
   错误横幅固定在视口顶部而非页面尾部;补齐 event-sources 三态。
3. **补齐不可逆操作确认**:撤销授权、删除事件源、取消系统草稿统一加 destructive Alert。
4. **砍掉两个可用性硬伤**:artifacts 页改会话选择器 + 复用 `ArtifactViewer`;
   results 节点 chips 改为纯筛选或明确标注"切换节点"。
5. **把 radii/spacing/shadows 真正用起来**,并定义字阶(如 13/15/17/20 + 600/800 两档字重),
   一次性收敛所有样式表。
6. **任务域口径统一**:详情页同时显示 work_status 与定义态;new.tsx 去掉 goal 的 autoFocus;
   模板覆盖已输入内容前弹确认。
7. **表单页加脏检查**(agent-editor、tasks/edit),返回前 Alert 拦截。
8. **chat 补时间戳与长按复制**,统一 turnActions 为 AppPressable,清理 7 个死样式。
9. **列表基建**:FlatList 统一挂 RefreshControl + onEndReached 分页,
   长列表(执行时间线、work)引入虚拟化。
10. **注册码改二维码展示**,两个 QR 扫描器合并为一个共享 ScannerScreen 组件。

---

## 六、一句话总结

功能与可靠性工程是 9 分,设计系统与一致性是 6 分。当前主要矛盾不是"缺功能",
而是把已经做对的基础(AppPressable/AsyncStateView/theme token)贯彻到每一个页面,
并把暗色模式对比度、危险操作确认、键盘处理这三类"低级但高频"的体验债清掉。
