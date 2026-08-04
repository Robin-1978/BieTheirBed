# 飞书 Channel 实现计划

## 概述

参考 `/home/robin/ws/per/notifier/lark.py` 的飞书通道实现，为 BieTheirBed 的 PC Assistant Agent 添加飞书 Channel 支持，使用户可以通过飞书与 Agent 交互。

## 架构设计

### 核心思路

- 参考 per 的飞书实现（WebSocket + 轮询混合架构、消息去重、emoji 确认等），但**去掉所有 ETF 业务逻辑**
- 设计 `ChannelBase` 抽象基类，为未来扩展其他通道（微信/Telegram/邮件等）预留接口
- 飞书消息全部路由到 BieTheirBed 的 `Agent.run()`，复用现有工具体系
- 飞书通道与 TUI 界面并行运行，共享同一个 Agent 实例

### 目录结构

```
src/pc_assistant/
├── channels/
│   ├── __init__.py        # ChannelManager + 通道工厂
│   ├── base.py            # ChannelBase 抽象基类
│   └── feishu.py          # FeishuChannel 实现
```

### 数据流

```
飞书用户发消息
    │
    ▼
WebSocket 回调 on_im_message_receive()
    │
    ▼
_msg_queue.put((open_id, text, msg_id))
    │
    ▼
Worker 线程取出消息
    ├── _add_reaction(msg_id, "OK")     ← 即时确认
    ├── 去重检查（msg_id + 30s文本去重）
    │
    ▼
FeishuChannel._handle_message(open_id, text)
    │
    ▼
在 Agent 事件循环中执行 Agent.run(text)
    │
    ▼
收集 AgentEvent → 提取 final_answer / tool_call / tool_result
    │
    ▼
_send_text(open_id, answer) / _send_card(open_id, card)
```

## 实现步骤

### Step 1: 创建 Channel 基类 (`channels/base.py`)

定义 `ChannelBase` 抽象基类：

```python
class ChannelBase(ABC):
    name: str = ""

    @abstractmethod
    async def start(self, agent) -> None: ...

    @abstractmethod
    async def stop(self) -> None: ...

    @abstractmethod
    def send_message(self, recipient_id: str, text: str) -> bool: ...

    @abstractmethod
    def send_card(self, recipient_id: str, card: dict) -> bool: ...
```

### Step 2: 创建 ChannelManager (`channels/__init__.py`)

- `ChannelManager` 管理所有活跃的 channel 实例
- 根据 config 启用/禁用 channel
- 提供 `start_all()` / `stop_all()` / `broadcast()` 方法

### Step 3: 实现飞书 Channel (`channels/feishu.py`)

从 per/notifier/lark.py 移植并改造，核心改动：

#### 保留的部分
- SSL 证书处理 + NO_PROXY 配置
- `lark_oapi` Client 懒初始化（线程安全）
- `_send_text()` / `_send_card()` / `_add_reaction()` 消息发送
- WebSocket 长连接 + 轮询补偿混合架构
- 消息去重三层机制（msg_id / recent_texts / 轮询去重）
- Worker 线程消费消息队列
- Watchdog 健康检查 + 自动重连
- open_id 自动保存/读取

#### 去掉的部分
- 所有 ETF 业务命令（信号/持仓/买入/卖出/入金/出金/绑定/确认等）
- `_register()` 命令注册装饰器和 `COMMAND_MAP`
- `_build_signal_data()` / `_build_positions_data()` 等数据构建函数
- `_request_confirm()` / `_pending_confirm` 二次确认机制
- `send_lark_notification()` / `build_signal_card()` 主动推送
- `_fee.py` 佣金计算
- Agent/LLM fallback 路由

#### 新增/改造的部分
- `FeishuChannel` 类，继承 `ChannelBase`
- `_handle_message(open_id, text)` 方法：将消息路由到 Agent
- Agent 事件收集：遍历 `Agent.run()` 的 AsyncGenerator，收集流式事件
- 流式反馈：Agent 思考/工具调用时通过飞书消息反馈进度
- 危险操作确认：通过飞书消息实现二次确认（替代终端 input）
- 按 open_id 隔离的对话上下文管理
- 长文本自动分段发送（飞书单条消息限制）

#### 关键实现细节

**Agent 调用桥接**（同步 Worker 线程 → 异步 Agent）：

```python
def _handle_message(self, open_id: str, text: str):
    """在 Worker 线程中调用，桥接到异步 Agent"""
    if self._agent_loop is None:
        self._send_text(open_id, "❌ Agent 未就绪")
        return

    future = asyncio.run_coroutine_threadsafe(
        self._process_with_agent(open_id, text),
        self._agent_loop,
    )
    try:
        future.result(timeout=120)
    except Exception as e:
        self._send_text(open_id, f"❌ 处理失败: {e}")

async def _process_with_agent(self, open_id: str, text: str):
    """在 Agent 事件循环中执行"""
    # 获取/创建该 open_id 的对话上下文
    # 调用 Agent.run(text)
    # 收集事件并发送回复
    ...
```

**对话隔离**：每个 open_id 维护独立的对话历史，使用 Agent 的 ConversationManager。

**确认回调**：为飞书通道实现基于消息的确认机制，替代终端 input。

### Step 4: 扩展配置 (`config.py`)

在 `AppConfig` 中添加飞书相关配置：

```python
feishu_enabled: bool = False
feishu_app_id: str = ""
feishu_app_secret: str = ""
feishu_receive_id: str = ""
feishu_receive_id_type: str = "open_id"
```

对应环境变量：
- `PC_FEISHU_ENABLED`
- `PC_FEISHU_APP_ID`
- `PC_FEISHU_APP_SECRET`
- `PC_FEISHU_RECEIVE_ID`
- `PC_FEISHU_RECEIVE_ID_TYPE`

### Step 5: 更新默认配置 (`config/default.yaml`)

添加飞书配置项（默认禁用）。

### Step 6: 集成到主启动流程 (`__init__.py`)

在 `async_main()` 中：
1. 创建 Agent 实例后，创建 `ChannelManager`
2. 如果 `feishu_enabled`，创建并启动 `FeishuChannel`
3. Channel 与 TUI 并行运行
4. 退出时 `stop_all()` 清理

### Step 7: 添加依赖 (`pyproject.toml`)

将 `lark-oapi` 添加为可选依赖：

```toml
[project.optional-dependencies]
feishu = ["lark-oapi>=1.4"]
```

### Step 8: 飞书 Channel 的 Agent 适配

需要解决的核心问题：

1. **对话隔离**：Agent 当前只有一个 ConversationManager，飞书多用户需要隔离
   - 方案：FeishuChannel 内部维护 `dict[open_id, ConversationManager]`，在调用 Agent 前切换上下文
   - 或者：为每个 open_id 创建独立的 Agent 实例（资源开销大，不推荐）
   - **推荐方案**：FeishuChannel 维护独立的对话历史，调用 `Agent.run()` 前重置 conversation

2. **确认回调**：飞书通道无法使用终端 input
   - 方案：为 FeishuChannel 创建专用的 confirm_callback，通过飞书消息实现确认
   - 简化方案：飞书通道中自动确认所有操作（信任飞书用户），或发送确认卡片

3. **并发安全**：多个飞书用户可能同时发消息
   - 方案：每个 open_id 的消息串行处理（队列），不同 open_id 并行处理

## 文件变更清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `src/pc_assistant/channels/__init__.py` | 新建 | ChannelManager |
| `src/pc_assistant/channels/base.py` | 新建 | ChannelBase ABC |
| `src/pc_assistant/channels/feishu.py` | 新建 | FeishuChannel 实现 |
| `src/pc_assistant/config.py` | 修改 | 添加飞书配置字段 |
| `config/default.yaml` | 修改 | 添加飞书配置项 |
| `src/pc_assistant/__init__.py` | 修改 | 集成 ChannelManager 启动 |
| `pyproject.toml` | 修改 | 添加 lark-oapi 可选依赖 |

## 参考映射

| per/notifier 概念 | BieTheirBed/channels 对应 |
|---|---|
| `lark.py` 全局函数 | `FeishuChannel` 类方法 |
| `COMMAND_MAP` + `_dispatch_command` | `FeishuChannel._handle_message` → Agent.run() |
| `NotifierAgent` | 复用 `Agent` |
| `notifier/config.py` | `AppConfig.feishu_*` 字段 |
| `notifier/__init__.py` | `channels/__init__.py` ChannelManager |
| `start_message_listener()` | `FeishuChannel.start()` |
| `_send_text()` / `_send_card()` | `FeishuChannel.send_message()` / `FeishuChannel.send_card()` |
