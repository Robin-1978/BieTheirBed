# Knoa 优化计划

基于与 `~/ws/per-staging/agent` 的架构对比，识别出的差距与优化方案。
目标：在不破坏现有工具链与安全护栏的前提下，补齐 Agent 基础设施短板。

## 1. 对比结论概览

| 能力维度 | per-staging (基准) | BieTheirBed (现状) | 差距 |
|---|---|---|---|
| 模型适配层 | model_adapter：Canonical IR + 多后端 + 多解析器 + 模型 profile | 手写 httpx + provider 分支 + think 正则 | 大 |
| LLM 重试/取消 | 重试分级、退避、墙钟预算、CancelToken、thinking 预算 | httpx 固定指数退避、全局取消 | 大 |
| 上下文管理 | token 校准、缓存计划、LLM 压缩、多模态、风格/详细度 | 固定预算截断 + 启发式压缩 | 大 |
| 工具选择 | BGE 语义选择 + trigger 回退 + 意图路由 | 每轮全量注入全部 schema | 大 |
| 证据校验 | turn_evidence 要求回答有工具证据 | 无 | 大 |
| 会话生命周期 | 每 session Harness、取消、对话回滚、LRU | 单实例单会话、全局取消 | 中 |
| 端口抽象 | ports/ 协议边界、传输层解耦 | confirm 回调临时传入 | 中 |
| 调用观测 | debug_trace 逐次落盘、usage 记录、turn 指标 | 只累计 token 计数 | 中 |
| PC 侧护栏 | 交易确认端口 | 危险命令/路径阻断 + 审计 + 限流 | 反向优势，保留 |

## 2. 优化项

### P0-1 模型适配层 (ModelAdapter)

**现状**：`src/knoa_platform/llm_provider.py` 一个类内置 openai / anthropic / openai_compatible / llamacpp 四个分支，流式与整响应解析各自实现；thinking 依赖 `_strip_think_tags` 正则（`agent.py:49`）。

**方案**：
1. 新增 `src/knoa_platform/model_adapter/`，定义统一 IR：
   - `ParsedResponse`：`content / reasoning / tool_calls / finish_reason / usage / error`
2. Backend 抽象（复用现有 HTTP 逻辑迁移）：
   - `backends/openai.py`、`backends/anthropic.py`、`backends/llamacpp.py`（含 `openai_compatible`）
3. Parser 链：`parsers/think_tags.py`、`parsers/json_tool_calls.py`、`parsers/xml_tool_calls.py`（qwen3/hermes 风格，为本地小模型兜底）
4. `profile.py`：按 provider+model 声明能力（支持 streaming / thinking / tool_choice / max_tokens 上限），缺失能力时静默降级（如不支持 thinking 则不再发思考请求）。
5. `bridge.py`：唯一入口 `chat()/chat_stream()`，agent 循环不再感知厂商差异。

**涉及文件**：新增 `src/knoa_platform/model_adapter/**`；`llm_provider.py` 降级为后端注册表；`agent.py` 改调 bridge。

**验收**：现有 pytest 全绿；同一循环代码可跑通 llamacpp、OpenAI、Anthropic；加入新的本地 XML 模型无需改动 agent 循环。

### P0-2 LLM 调用健壮性

**现状**：`_request_with_retry`（`llm_provider.py:62`）对网络错误与 429/5xx 统一指数退避，不区分瞬时/永久错误；无总重试墙钟预算；`cancel` 是模块级布尔，无法按请求/会话隔离。

**方案**：
1. `retry.py`：`is_transient_error(err)` 分类；退避 `clamp_backoff_to_wall`；`retry_wall_exhausted()` 总预算。
2. `CancelToken`：贯穿 `agent.py → bridge → backend`，流式循环逐 chunk 检查，可精确取消单次调用。
3. thinking 预算：`thinking_budget` 参数经 bridge 透传，超时策略可配。
4. 超时分级：connect/read/write/pool 分离（Anthropic 已这么做，推广到各 backend）。

**涉及文件**：新增 `model_adapter/retry.py`、`model_adapter/cancel.py`；改造 `agent.py` 的流式与工具执行循环。

**验收**：注入 5xx/超时故障时按预算重试而非无限退避；取消操作在 1s 内生效；单测覆盖瞬时/永久错误分类。

### P0-3 工具离线选择

**现状**：`agent.py:340` 每轮把全部 15 个工具 schema 注入。

**方案**：
1. 每个工具声明 `triggers`（关键词集合），`tool_registry.schema_for_query(text)` 返回匹配子集；无匹配时回退全量（保证安全护栏工具始终在内）。
2. 可选：embedding 语义选择（bge-small-zh），与 trigger 形成 OR 合并；依赖缺失时自动回退 trigger-only。
3. 意图路由：轻量正则分类（桌面操作 / 文件 / 网络 / 系统 / 记忆），决定优先注入的工具组。

**涉及文件**：`tools/registry.py`、`tools/base.py`（加 triggers 字段）、`agent.py`。

**验收**：普通查询的注入 schema 数量显著下降（<5）；触发类查询（如"查天气"）正确注入对应工具；安全/审计逻辑不受影响。

### P1-4 上下文管理与 token 校准

**现状**：`context/truncator.py` 按固定 budget 截断；`context/compact.py` 线性启发式压缩，每工具保留字段硬编码（`_TOOL_RESULT_KEYS`）。

**方案**：
1. `token_estimate.py`：按模型 family 校准字符→token 比率（llamacpp/OpenAI tiktoken/claude tokens），供截断决策。
2. 压缩分两级：预算足够时用启发式（现状），预算紧张时触发 LLM 结构化摘要（异步、降级失败）。
3. 缓存计划：系统提示 + 工具 schema 等静态前缀可构造 prompt 前缀，为支持缓存的 backend（Anthropic/DeepSeek/llama.cpp 缓存）附 `cache_control` 标记。
4. 压缩结果带来源标记与"有损提示"，现有 `COMPACTED_HISTORY_ACK` 机制已具备，扩展开关。

**涉及文件**：`context/` 下新增文件；`agent.py` 截断点改走新流程。

**验收**：长对话下 token 估计误差 <15%；预算临界时优先压缩工具结果而非丢弃用户问题；缓存前缀命中率可观测。

### P1-5 会话生命周期与隔离

**现状**：单例 Agent 持有单一 `ConversationManager`，`cancel()` 全局生效；出错时已写入的历史不回滚。

**方案**：
1. `SessionHarness`：每 session 一份 conversation + cancel token，LRU 上限淘汰（参考 `per-staging/agent/core.py:86`）。
2. 对话快照与回滚：turn 开始时记录 `snapshot_len`，取消/出错时回删（参考 `core.py:163`），避免污染后续轮次。
3. 状态统计按 session 聚合（token、迭代、工具调用），支持多通道（CLI/飞书）并行互不干扰。

**涉及文件**：新增 `session.py` / `harness.py`；`agent.py` 拆分 run 入口。

**验收**：两个并发会话各自独立；取消会话 A 不影响 B；出错后历史正确回滚。

### P1-6 调用观测与审计增强

**现状**：`agent.py` 只累计 `_total_prompt_tokens` 等运行期计数；审计仅记工具调用。

**方案**：
1. 每次 LLM 调用落 `logs/llm_calls.jsonl`：时间、session、model、iteration、prompt/completion tokens、延迟、finish_reason、首 token 延迟（TTFT）。
2. 每次 turn 输出 `turn_metrics`：迭代数、工具数、token 成本、是否成功收尾。
3. `/status` 增加按 session 与按模型维度的聚合视图。

**涉及文件**：新增 `observability/`；`agent.py`、`ui/chat.py`。

**验收**：一次问答后可定位最贵的调用点；TTFT/延迟异常可预警。

### P2-7 证据校验（可选，收益高但改动大）

**现状**：模型可直接凭记忆作答，无事实校验。

**方案**：按需引入 `turn_evidence` 的最小版本：对包含数值/事实断言的问题，判定"该轮是否应带工具证据"，在系统提示中要求引用工具结果；对引用缺失的回答标记置信度降级。先做提示层约束，再做硬校验。

**涉及文件**：新增 `context/evidence.py` + 系统提示词。

### P2-8 提示词外置

**现状**：`context/system_prompt.py` 内联构建。

**方案**：迁移到 `src/knoa_platform/prompts/*.md`，运行时加载；可版本化、可单测、可被 `/config` 覆盖。

## 3. 建议实施顺序

```
Phase 1 (P0):  1 → 2 → 3    (模型适配层 → 健壮性 → 工具选择)
Phase 2 (P1):  4 → 5 → 6    (上下文 → 会话 → 观测)
Phase 3 (P2):  7 → 8        (证据校验 → 提示词外置)
```

每阶段结束跑 `pytest`，并人工回归一次 CLI 与飞书通道。

## 4. 保留项（反向优势，勿破坏）

- 危险命令 / 受保护路径阻断与用户确认
- 工具调用审计 JSONL
- 限流
- 丰富桌面工具集（mouse/keyboard/window/scheduler）
