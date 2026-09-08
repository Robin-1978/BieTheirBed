# Knoa 产品本质审视与 Jira 工程分析能力进阶

> **版本**: 1.0  
> **日期**: 2026-09-08  
> **状态**: 已定稿  

---

## 1. 概述

### 1.1 背景与目标
随着大模型 Agent 技术的快速迭代，业界充斥着大量以“对话框套壳”、“单体 Prompt 堆砌工具”为主的玩具型应用。这些应用普遍存在长时运行崩溃、上下文无限膨胀、单次请求 Token 成本失控、以及无法真正穿透本地物理与工程环境等致命问题。

本文件旨在完成两大核心使命：
1. **产品本质与核心价值审视**：彻底厘清 Knoa 究竟是什么、它如何真正为用户创造高杠杆价值、以及它与传统 Chatbot / Agent 的本质代差。
2. **Jira MCP 与工业级缺陷诊断能力演进**：结合实际工程中成熟的全局 Jira 分析体系（`oss://` 匿名存储解析、Tempo 机器人日志/Bag 提取、机器 SN 缺陷聚类分析、领域业务信号提取），给出将通用 Jira MCP 升级为“工业级缺陷智能分析闭环”的完整架构方案。

---

## 2. Knoa 产品本质审视：我们究竟是什么？

### 2.1 Knoa 绝不是什么（反向定义）
- **不是网页套壳 Chatbot**：不是给 Claude 或 ChatGPT 简单包一个 Web 界面；页面关闭，一切终止。
- **不是单体巨石 Prompt 运行时**：不是把 50 个工具直接塞给一个万能 Prompt，让它在同一个对话上下文里从头猜到尾，导致单轮消耗数万 Token 且执行频频翻车。
- **不是虚幻的云端 SaaS 玩具**：不要求用户把企业代码库、本地私有日志和高权限凭证拱手上传到第三方云端服务。

### 2.2 Knoa 的核心定义
> **Knoa 是一款面向个人与工程师的“主权常驻型虚拟数字员工与执行操作系统”（Sovereign Personal AI OS & Autonomous Co-Worker）。**

用户只需要描述意图与期望达成的目标，Knoa 负责自主理解、安全预检、任务分解、调度专职 Agent、调用本地与网络工具执行、在关键风险点请求人类确认、保存不可变证据，并在用户离线时**7×24 小时不间断运行、自愈并跨端同步结果**。

```mermaid
flowchart TD
    subgraph MultiTerminal["多端交互与监控面 (User Experience)"]
        MobileApp["Knoa Mobile (React Native / Expo)"]
        DesktopApp["Desktop Companion (Tray / Shell)"]
        IMChannels["IM (飞书 / 钉钉 / 企业微信)"]
        WebConsole["Web / Hub Console"]
    end

    subgraph KnoaCore["Knoa 主权执行操作系统 (7x24 Host Daemon)"]
        CEO["Knoa 主 Agent (Orchestrator / Dispatcher)"]
        ContextGuard["上下文边界与记忆分层引擎 (Context Engine)"]
        TaskEngine["持久化任务调度与事件总线 (Task & Schedule)"]
        StorageEngine["自愈型存储与产物中枢 (WAL SQLite & ArtifactStore)"]
        
        CEO -->|专职委托 / 隔离上下文| Coder["Knoa Coder (代码工程专家)"]
        CEO -->|专职委托 / 隔离上下文| Researcher["Knoa Researcher (深度调研专家)"]
        CEO -->|兜底隔离 / 最小权限| Worker["Knoa Worker (通用沙箱执行单元)"]
    end

    subgraph Actuation["物理与工程连接面 (Real-World Actuation)"]
        LocalHost["宿主环境: Shell / 文件编辑 / 进程 / 服务"]
        MCPNetwork["MCP 工具网络 (Jira / GitLab / 机器人系统)"]
        RemoteCloud["外部网络 / 浏览器自动化 / 报告投递"]
    end

    MultiTerminal <-->|WebSocket / 局域网 mDNS / 安全中继| KnoaCore
    Coder & Researcher & Worker <--> Actuation
```

---

## 3. Knoa 如何给用户提供无可替代的价值？

### 3.1 价值一：从“同步实时问答”升维到“异步目标委托”
- **传统方式**：用户守在屏幕前，打一段字，等模型吐字，发现格式不对再纠偏，耗费大量脑力与时间。
- **Knoa 价值**：
  - 用户说：“每天早上 9 点帮我生成一份包含国内要闻与前沿科技的晨报并弹窗提醒”；
  - 用户说：“帮我巡检 Jira 综测缺陷，把分配给我的工单关联的现场机器人 Log 下载并排查是否有崩溃栈，整理出初步根因”；
  - 用户交代完即可离开，Knoa 在后台以小时为周期异步推进、重试恢复、自愈网络波动，并在完成时通过手机推送最终交付物。

### 3.2 价值二：组织化架构击碎“Token 成本与上下文污染”
- **工程痛点**：单 Agent 处理复杂任务（如一边阅读 100 篇网页，一边修改 5 个代码文件）会导致对话历史急剧膨胀，单次交互 Token 突破十万，既慢又贵，且极易产生注意力分散与幻觉。
- **Knoa 价值**：
  - 遵循 **“Direct First, Specialist When Proven, General Worker as Isolation Fallback”** 原则；
  - 主 Agent 仅维持精简上下文与编排计划，重型探索完全委托给专职 Agent（如 `researcher` 跑 30 步网页检索后只带回 500 字提炼精华；`coder` 在独立会话读写代码测试后只返回根因补丁证据）；
  - 主会话永远纯净高效，Token 消耗降低 70%~90%。

### 3.3 价值三：工业级可信性与长周期免维护自愈
- **真实生产环境运行验证**：
  - **存储自愈**：SQLite 数据库引入分级截断与自动 WAL 检查点，超时任务轨迹与大字段自动压缩清理，空闲页高效复用；
  - **产物全生命周期**：区分 `borrowed`（借用用户文件绝不冗余拷贝和误删）、`temporary`（1小时 TTL 自动物理回收）、`persistent`（持久归档），彻底避免磁盘无限膨胀；
  - **安装包与日志自动淘汰**：移动端历史安装包保留最新 3 代（释放 2.6GB+ 空间），Windows 服务更新脚本滚动保留，无需人工删日志打补丁。

---

## 4. 全局 Jira 分析 Skill 深度调研与能力对比

当前开发机全局环境下，存在一套成熟且经历过工业实践检验的 Jira 分析技能（`~/.agents/skills/jira/`），其能力远超传统的通用 Jira 工具，具有极高的借鉴与整合价值。

### 4.1 全局 Jira Skill 的核心优势
1. **中文业务字段动态映射**：
   - 传统 MCP 只能输出 `customfield_11118` 等随机哈希字段名，模型无法理解；
   - 该 Skill 会自动缓存并双向映射为 `"机器SN"`、`"客户名称"`、`"归属产品"`、`"发生时间"` 等直观中文键。
2. **打通机器人/工业日志大文件下载链路（破局点）**：
   - 现场机器人发生故障时，几百 MB 到数 GB 的 ROS 日志、Bag 包和音频文件**不可能直接挂在 Jira 附件中**；
   - 实际工单是通过 `oss://gs-public-shared/...` 匿名公网链接，或者 Tempo 云平台分享链接 `shared-record-list/...` 进行传递；
   - 该 Skill 内置了免鉴权直接拉取阿里云 OSS 的客户端（`oss_client.py`），以及集成机器人统一云认证、按机器所在大区（中国/欧洲/北美）动态路由终端节点的 Tempo 客户端（`tempo_client.py`），使 Agent 能直接下载原始运行日志！
3. **同设备硬件缺陷关联分析（`--sn-relate`）**：
   - 提取工单中的机器 SN，自动聚合该机器的历史同类工单，帮助研发一眼识别究竟是“单机偶发硬件老化”还是“共性软件缺陷”。
4. **业务信号与领域识别引擎（`assess_issue.py`）**：
   - 基于规则与正则自动提炼核心信号：是否涉及电梯联动（梯控）、是否属于运动规划避障（PNC）、是否缺失关键日志、是否需要总部二线研发介入。
5. **结构化质量分析输出模板**：
   - 强制采用工业级闭环规范：`*1/4问题描述*`、`*2/4原因分析*`、`*3/4改善措施*`，并自动转换为 Jira Wiki Markup 格式。

### 4.2 现状能力鸿沟（Gap Analysis）

| 维度 | Knoa 现有 Jira MCP (`examples/jira_mcp_server`) | 全局 Jira 分析 Skill (`~/.agents/skills/jira`) | 演进目标（Knoa Jira 进阶方案） |
|---|---|---|---|
| **协议标准** | 标准 MCP（stdio / JSON-RPC），具备资源订阅模型 | Python 独立 CLI 脚本（标准输出 JSON） | 保持标准 MCP 规范，封装底层能力 |
| **自定义字段** | 仅支持原始 `customfield_xxx` 字段 | 自动拉取元数据映射为中文人类可读键 | **MCP 原生支持字段元数据翻译** |
| **大文件日志链** | 仅支持 Jira 本地附件下载（无法下载现场日志） | 原生支持 `oss-cp`、`tempo-cp` 下载 Bag/Log | **MCP 集成 OSS 与 Tempo 工业日志下载器** |
| **硬件同频关联** | 无 | 支持 `--sn-relate` 聚合同机器历史故障 | **新增 `jira.find_related_by_sn` 工具** |
| **缺陷诊断闭环** | 仅停留在工单字段读写 | 具备信号提炼，但无法全自动解包诊断代码 | **联动 Coder / Worker 完成本地日志解包排查** |

---

## 5. Jira MCP 与 Agent 能力进阶架构设计

### 5.1 进阶架构全景

```mermaid
flowchart TD
    User([用户发起委托: "排查 SELLSERVIC-12345 缺陷根因"]) --> KnoaDispatcher[Knoa 主 Agent / 调度器]
    
    subgraph DefectWorkflow["缺陷诊断专属流水线 (Defect Analysis Pipeline)"]
        KnoaDispatcher -->|委派任务 + 隔离上下文| DefectAgent[Knoa Defect Specialist / Worker]
        
        subgraph JiraMCPEnhanced["增强型 Jira MCP Server"]
            ToolIssue["jira.get_issue (含中文字段映射 & 信号提炼)"]
            ToolOSS["jira.download_oss_evidence (零依赖 OSS 下载)"]
            ToolTempo["jira.download_tempo_records (Tempo 云日志直连)"]
            ToolSN["jira.correlate_by_sn (同机器历史故障聚类)"]
            ToolComment["jira.add_quality_comment (标准三段式确认回写)"]
        end

        DefectAgent -->|1. 获取工单与外部日志链接| ToolIssue
        DefectAgent -->|2. 聚合同设备历史故障| ToolSN
        DefectAgent -->|3. 下载现场 Log / Bag| ToolOSS & ToolTempo
        
        subgraph LocalDiagnostics["本地诊断与源码定位 (Host Actuation)"]
            ExtractLog["解包日志 / 归档 (tar / zip / zstd)"]
            GrepErrors["grep_search / 正则提取 Fatal / Crash 栈"]
            CodeInspection["read_file / inspect_code 定位出错行"]
        end

        ToolOSS & ToolTempo -->|下载至 evidence 目录| ExtractLog
        ExtractLog --> GrepErrors --> CodeInspection
        CodeInspection --> DefectAgent
    end

    DefectAgent -->|4. 提炼根因与补丁建议| KnoaDispatcher
    KnoaDispatcher --> UserReview{用户审查 & 审批确认}
    UserReview -->|批准| ToolComment
    UserReview -->|修改后批准| ToolComment
```

### 5.2 增强型 Jira MCP 工具集详细规划

1. **`jira.get_issue` 升级**：
   - 自动包含动态字段映射，输出中文属性字典（如 `"客户名称"`、`"机器SN"`、`"归属产品"`）；
   - 自动识别并提取内容中的外部凭据：解析出 `oss_links: ["oss://gs-public-shared/..."]` 与 `shared_record_links: [".../shared-record-list/..."]`；
   - 自动提取业务特征标签（梯控、PNC、二线流转、数据缺失）。
2. **`jira.download_oss_evidence`（新增）**：
   - **输入**：`issue_key`, `oss_url`（如 `oss://gs-public-shared/defect_logs/2026/09/xxx.log.tar.gz`）；
   - **行为**：利用公共读快速下载通道，直接以流式写入当前工单的 `evidence/` 目录，计算 SHA-256 并返回本地路径。
3. **`jira.download_tempo_records`（新增）**：
   - **输入**：`issue_key`, `record_ids`, 可选 `share_id`；
   - **行为**：调用机器人云端开放 API，拉取状态为 `AVAILABLE` 的 Bag/Log 原始记录，写入本地工作区。
4. **`jira.correlate_by_sn`（新增）**：
   - **输入**：`serial_number`, `limit`；
   - **行为**：执行 JQL `cf[机器SN] ~ "SN" ORDER BY created DESC`，返回同设备历史工单列表与解决状态，识别惯发故障。
5. **`jira.add_quality_comment`（升级写工具）**：
   - 强制格式规范化校验（1/4 现象，2/4 根因，3/4 措施），自动添加 AI 标识与 Wiki 标记语法，并纳入高风险确认阻断。

### 5.3 联动 Agent 的本地自动化诊断闭环
当增强型 Jira MCP 提供完整的外部证据后，Knoa 的多 Agent 架构优势将彻底释放：
- **第一阶段（数据就绪）**：Jira MCP 将远程分散在云端、机器端的工单元数据、图片和几百 MB 日志收集到受控目录；
- **第二阶段（本地算力闭环）**：Agent 在本地调用 `run_command`（解包）、`grep_search`（正则搜索段错误、`FATAL`、`Exception`）、`read_file`（比对本地代码库相关行）；
- **第三阶段（高可信输出）**：不靠虚无的想象，而是基于真实的日志堆栈、代码行号和同机器历史记录，生成具备极高可信度的技术排查报告。

---

## 6. L3 远期愿景架构：自进化的专属数字副手 (Autonomous Self-Evolving Coworker)

在夯实 L1（7×24h 健壮常驻底座、多终端与 CEO-Specialist 组织架构）与推进 L2（Jira 工业级日志分析与工程闭环）的基础上，Knoa 的终极产品形态是演进为具备**自繁衍、主动感知与物料化决策**能力的专属数字副手。

```mermaid
flowchart TD
    subgraph EnvSensing["1. 主动环境感知层 (Proactive Environmental Sensing)"]
        CronTrigger["7x24h 守护调度 (Cron / Events)"]
        HostSensors["宿主探针 (Git / 磁盘 / 异常进程)"]
        DomainSensors["业务探针 (Jira 新指派 / GitLab CI / 外部资讯)"]
        Tier0Model["本地端侧小模型 (7B/8B 零成本粗筛 & 模式匹配)"]
        
        CronTrigger --> HostSensors & DomainSensors
        HostSensors & DomainSensors --> Tier0Model
    end

    subgraph CognitiveCore["2. 认知与决策中枢 (Dual-Tier Cognitive Core)"]
        StandupSynthesizer["晨间早报与行动简报生成 (Morning Standup)"]
        Tier1Model["云端旗舰推理模型 (Claude / DeepSeek SOTA)"]
        
        Tier0Model -->|发现异常或重点事项| Tier1Model
        Tier1Model --> StandupSynthesizer
    end

    subgraph ActionStaging["3. 物料化草稿沙盒 (Staged Action Sandbox)"]
        ActionCard["决策行动卡片 (Action Card)"]
        StagedDiff["工程补丁 (Git Patch / Code Diff)"]
        StagedEvidence["复现与测试证据 (Logs / Test Output)"]
        StagedJira["标准三段式工单回写建议"]
        
        Tier1Model --> StagedDiff & StagedEvidence & StagedJira
        StagedDiff & StagedEvidence & StagedJira --> ActionCard
    end

    subgraph SelfEvolution["4. 技能自繁衍闭环 (Skill Self-Synthesis & Evolution)"]
        TaskPostMortem["任务终态复盘 (Execution Post-Mortem)"]
        PatternExtractor["特征签名提取 (Error Signature & Runbook)"]
        SkillSynthesizer["标准 Skill.md 自动生成 (Sandboxed Validation)"]
        SkillRegistry["~/.knoa/skills/ 用户技能库热重载"]
        
        ActionCard -->|用户批准执行并成功闭环| TaskPostMortem
        TaskPostMortem --> PatternExtractor
        PatternExtractor --> SkillSynthesizer
        SkillSynthesizer -->|安全检验通过| SkillRegistry
    end

    StandupSynthesizer & ActionCard --> MobileClient["Knoa Mobile / Desktop / IM (一键审阅与批复)"]
```

### 6.1 核心支柱一：经验沉淀与技能自繁衍 (Skill Self-Evolution)
- **痛点**：当前绝大多数 Agent 在遇到长链路复合问题（如特定硬件环境的依赖冲突、特殊网关鉴权、某款机器的特定异常排查）时，每次都需要反复探索、多轮试错，消耗大量 Token 且容易遗忘。
- **机制设计**：
  1. **事后复盘（Post-Mortem）**：当一个探索步数超过 10 步的复杂运维或排查任务成功交付后，系统触发后台轻量复盘，分析中间探索的无效路径与最终生效的关键步骤；
  2. **提炼经验模板**：抽象出 `Error Signature`（触发特征）、`Preflight Checks`（前置检查）、`Deterministic Steps`（确定性工具链命令）与 `Verification Rule`（验收断言）；
  3. **沙箱单测验证**：系统在隔离环境中尝试回放该经验步骤，确保不依赖临时上下文；
  4. **资产沉淀**：自动生成符合规范的 `SKILL.md` 并归档入 `~/.knoa/skills/custom/`，主控 Agent 下次遇到相同或相似场景直接以 `O(1)` 的代价调用成熟技能，**实现越用越聪明的正向资产积累**。

### 6.2 核心支柱二：主动环境感知与晨间早会 (Proactive Morning Standup)
- **痛点**：传统助手处于“完全被动”状态——用户不主动敲字提问，助手就毫无作为；而用户往往是在出了事故或被催促时才手忙脚乱地让助手查问题。
- **机制设计**：
  1. **静默巡检**：利用 7×24h 守护特性，在低峰期（如清晨 6:00~8:30）自主巡检关键数据源：
     - Jira 综测与现场缺陷（是否新增指派给自己的 P0/P1 工单？是否有等待复核的回归？）；
     - GitLab / GitHub（昨天提交的代码合并与 CI 流水线状态）；
     - 本地硬件宿主（端口冲突、磁盘可用量、关键后台服务心跳）；
     - 行业技术动态（arXiv 推荐、前沿财报/研报抓取）。
  2. **晨会卡片（Standup Digest）**：在用户开启一天工作时（通过手机 App 实时推送或桌面通知），投递结构化早报卡片：
     - *“主人，已为您准备好今日晨报：1) 现场有 1 个高危崩溃工单（已预下载日志排查出段错误行，草稿已就绪）；2) 昨天提交的 PR 已全部通过 CI；3) 磁盘空间健康。”*

### 6.3 核心支柱三：物料化草稿沙盒与一键决策 (Staged Action Cards)
- **痛点**：Agent 完成复杂修改后直接修改真实环境容易带来不可逆风险；而仅仅在聊天界面打印几千行 Markdown 又需要用户自行复制粘贴、极为繁琐。
- **机制设计**：
  1. **物料隔离打包（Staging）**：Agent 执行结果统一打包为“交付物料（Artifacts Bundle）”：
     - 代码层：生成严格的 Patch/Diff 文件，附带本地测试执行日志截图；
     - 业务层：生成结构化三段式评论文本，附带建议流转的状态（如 `提交现场验证`）及必填字段建议值；
  2. **决策卡片（Action Card）**：移动端以卡片形式展示核心要点与影响面，底部仅提供两个动作：
     - `[查看 Diff / 证据详情]`
     - `[一键批准：自动合并补丁并同步回写 Jira]`。
  3. 用户从繁重的“执行者”彻底升维为掌控全局的“审批决策者”。

### 6.4 核心支柱四：端云双轨分级路由 (Dual-Tier Cognitive Engine)
- **痛点**：7×24h 全天候主动巡检如果完全依赖商业闭源大模型，Token 账单将呈指数级上涨；若只用端侧小模型，复杂推理又无法胜任。
- **机制设计**：
  1. **Tier 0（端侧轻量小模型，0 成本 / 100% 隐私）**：
     - 部署本地 Qwen2.5-7B / DeepSeek-8B；
     - 专职负责：周期性日志关键词过滤、JQL 轮询差异比对、外部通知提取、初步意图粗分类；
  2. **Tier 1（云端旗舰模型，高智商 / 强推理）**：
     - 调用 Claude 3.5 Sonnet / DeepSeek-V3 / GPT-4o；
     - 仅在 Tier 0 捕获到确凿的异常信号、或用户发起深度委托时唤醒，执行高难度的代码定位、架构设计和综合逻辑推理。

---

## 7. 演进路线图：从 L1 走向 L3

```
┌─────────────────────────────────────────────────────────────┐
│ 远期愿景 (L3): 自进化的专属数字副手                           │
│ • 技能自繁衍 (Skill Auto-Synthesis & Local Runbook Library)  │
│ • 主动环境感知与晨间早会 (Proactive Morning Standup)         │
│ • 物料化决策卡片 (Staged Action Cards & One-click Approve)  │
│ • 端云双轨认知路由 (Local Tier-0 7B + Cloud Tier-1 SOTA)    │
├─────────────────────────────────────────────────────────────┤
│ 中期突破 (L2 - 立即落地实施): 工业级工程闭环                 │
│ • Jira 工业级日志拓展: 集成 OSS 匿名下载与 Tempo 云日志直连   │
│ • Jira 中文业务字段动态缓存与双向映射                        │
│ • 机器 SN 同频缺陷聚类关联分析                              │
│ • 工业级三段式质量评论自动回写与安全确认                      │
│ • 联动 Coder / Worker 完成本地现场 Log/Bag 解包与代码定位    │
├─────────────────────────────────────────────────────────────┤
│ 近期地基 (L1 - 生产级已就绪):                               │
│ • 7×24h 无头常驻守护、自愈与自动轮转（WAL 截断、版本淘汰）     │
│ • CEO-Specialist 组织架构 (knoa + coder + researcher)       │
│ • 移动端 App / 跨平台部署 / 动态记忆防膨胀                    │
└─────────────────────────────────────────────────────────────┘
```

1. **第一阶段：L3 远期蓝图确立与文档定稿（已完成）**
   - 确立 Knoa 主权常驻虚拟员工的核心定位；
   - 梳理自繁衍技能、主动感知早会与端云双轨路由的完整架构。
2. **第二阶段：L2 工业级 Jira MCP 全面扩充（当前立即推进）**
   - 将 `oss_client` 与 `tempo_client` 整合进 `examples/jira_mcp_server`；
   - 补齐中文字段动态翻译与 `--sn-relate` 聚类工具；
   - 编写自动化测试保证工业下载链路高可用。
3. **第三阶段：L2+ 缺陷闭环流水线打通**
   - 联动 Coder 实现本地日志提取、Fatal 错误栈搜索与源码比对；
   - 在移动端与桌面端实现一键审批卡片。
