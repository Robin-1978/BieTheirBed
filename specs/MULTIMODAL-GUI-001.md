---
jira_key: "MULTIMODAL-GUI-001"
title: "多模态 + 屏幕理解 + 精准鼠标键盘控制"
created_at: "2026-08-04"
updated_at: "2026-08-04"
status: "DRAFT"
---

# MULTIMODAL-GUI-001: 多模态 + 屏幕理解 + 精准鼠标键盘控制

## 1. 业务功能概述 (Business Overview)

为 Knoa agent 增加两类能力:

1. **多模态输入**:agent 能阅读并分析图片(用户发送的图片、工具返回的截图)。
2. **屏幕理解与精准控制**:agent 能"看懂"屏幕,使 `mouse`/`keyboard` 工具的执行更准确。

目标效果:

- 用户可以直接丢图片让 agent 分析(CLI / Feishu 均可)。
- agent 可以截图"看屏幕",然后基于语义元素(而非盲猜像素)操作鼠标键盘,完成 GUI 任务。

## 2. 系统设计说明 (Technical Design)

### 2.1 总体分层

```
┌─────────────────────────────────────────────────────────────┐
│ Agent Loop (agent.py)         不变，只接新的消息格式        │
├─────────────────────────────────────────────────────────────┤
│ 语义层 ui 工具 (无障碍树)     首选：元素名+bbox → 精确坐标  │
│ 视觉层 screen 工具 (截图+网格) 兜底：Look→Act→Verify       │
│ grounding 模型 (可选)          把像素截图转成元素列表        │
├─────────────────────────────────────────────────────────────┤
│ 多模态消息管道 (ContentBlock IR)                            │
│  ├── 会话存储 Message.content: str | list[ContentBlock]     │
│  ├── provider 适配: OpenAI/llamacpp → image_url 块          │
│  │                 Anthropic  → image source 块             │
│  └── 图片预处理 vision/preprocess.py (缩放/压缩/base64)     │
├─────────────────────────────────────────────────────────────┤
│ ProviderProfile.supports_vision 能力探测 + 优雅降级         │
└─────────────────────────────────────────────────────────────┘
```

### 2.2 多模态消息管道

#### 2.2.1 内容块中立 IR

新增 `model_adapter/content.py`:

```python
ContentText  = {"type": "text", "text": str}
ContentImage = {"type": "image", "image_url": "data:image/jpeg;base64,...",
                "media_type": "image/jpeg"}
ContentBlock = ContentText | ContentImage

# 兼容旧的 str 形式，str 等价于 [{"type": "text", "text": ...}]
Content = str | list[ContentBlock]
```

关键改动点:

| 位置 | 现状 | 改动 |
|---|---|---|
| `model_adapter/types.py:11` | `LLMMessage.content: str` | `str \| list[ContentBlock]` |
| `context/conversation.py:12` | `Message.content: str` | `str \| list[ContentBlock]` |
| `context/conversation.py` `add_user` | 只收 str | 新增 `add_user_attachment(blocks)` / 附件参数 |
| `model_adapter/parsers/openai.py` | content 原样透传 | 增加 `image_url` 块输出 |
| `model_adapter/parsers/anthropic.py` | content 原样透传 | 增加 `{type:image, source:{...}}` 转换 |

#### 2.2.2 Provider 适配规则

| Provider | content 序列化 | 降级策略 |
|---|---|---|
| openai / openai_compatible | `[{"type":"text",...},{"type":"image_url","image_url":{"url":...}}]` | 无 |
| llamacpp (多模态版本) | 同 OpenAI | 不支持则返回明确错误 |
| anthropic | `[{"type":"image","source":{"type":"base64","media_type":...,"data":...}}]` | 无 |
| 纯文本模型 (`supports_vision=False`) | 拒绝携带图片 | 报错提示更换视觉模型 |

`ProviderProfile` 新增 `supports_vision: bool = False`,各 profile 按模型能力配置。

#### 2.2.3 图片预处理 `vision/preprocess.py`

- `resize_image(img, max_side=1280)` 长边缩放,保持宽高比。
- `encode_jpeg(img, quality=70)` 压缩,返回 bytes + media_type。
- `to_data_url(data, media_type)` → `data:image/jpeg;base64,...`。
- `estimate_image_tokens(img) -> int` 按 tile/像素规则估算,接入 `TokenEstimator` 与 `truncate_messages`,防止图片撑爆 context budget。
- `capture_block(region=None) -> ContentImage` 统一截图入口(复用 `mss`,与 pyautogui 坐标空间一致)。

#### 2.2.4 图片进入的两条入口

1. **用户消息**:`Agent.run()` 增加附件参数 `attachments: list[ImageAttachment]`;CLI `/attach <path>`、Feishu 图片消息映射为附件,存入会话 `Message`。
2. **工具结果**:`system screenshot` 增加 `inline: true` 选项,返回图片块(`{"image": "<data_url>", "path": ...}`),让模型真正"看到"截图而不是只拿到一个路径字符串。

### 2.3 屏幕理解与精准控制

核心原则:**坐标不靠视觉盲猜,语义元素优先。**

#### 2.3.1 语义层:`ui` 工具(首选)

跨平台无障碍树后端:

| 平台 | 后端库 |
|---|---|
| Windows | `pywinauto` / `uiautomation` (UIA) |
| macOS | `pyobjc` + AXUIElement |
| Linux | `pyatspi` (AT-SPI) |

工具 schema:

```
ui:
  - action: "list"            # 返回元素树 [{name, role, bbox{x,y,w,h}, ...}]
  - action: "click"  name: "搜索框"          # 坐标由 bbox 服务端计算
  - action: "type"   name: "搜索框"  text: "..." 
  - action: "find"   name: "保存"            # 定位元素,返回 bbox
  - action: "screenshot_element" name: "保存" # 截取元素区域图
```

- **坐标计算在服务端完成**:`click(name)` 内部用 bbox 中心点调用现有 `MouseTool`。
- 精确度 100%,不依赖模型估计。
- 保留原始 `mouse.move/click(x, y)` 供视觉层兜底。

#### 2.3.2 视觉层:`screen` 工具(兜底 + 验证)

```
screen:
  - action: "look"      region?: {x,y,w,h}  # 截图(带网格覆盖层)返回图片块
  - action: "verify"    before?: path        # 操作后重新截图,交给模型自比对
  - action: "understand"                      # 可选: 调本地 grounding 模型返回元素列表
  - action: "info"                            # 分辨率 / DPI / 缩放
```

- `look` 截图同时返回 `{scale_x, scale_y, screen_size}` 元数据,模型据此换算 pyautogui 坐标。
- 网格覆盖层:按坐标分区编号(如 A1–J10),模型引用格子估坐标,显著提升命中率。

#### 2.3.3 Grounding 模型(可选,二期)

本地部署 UI-TARS / OmniParser v2 或类似服务,暴露 HTTP API:

```
POST /understand   {image: <base64>}
→ [{label, bbox{x,y,w,h}, confidence}]
```

`screen.understand()` 调用它,把像素截图转成语义元素,agent 按元素操作。用于无障碍树不可用的场景(游戏、远程桌面、部分 Canvas 页面)。

#### 2.3.4 Look → Act → Verify 循环

- 对 `mouse`/`keyboard` 的高风险动作(点击、拖拽、快捷键),verifier 增加策略:如开启 `screen_verify` 配置,则动作执行后强制 `verify()` 截图回灌模型确认。
- 挂在现有 SDB verifier(`harness/verifier.py`)上,不改变验证语义,只新增一条"事后验证"规则。

### 2.4 联动改造(易漏的坑)

| 位置 | 问题 | 改动 |
|---|---|---|
| `agent.py:261` `_smart_truncate` | 对图片块 `str()` 会吐 base64 | 图片结果跳过截断 |
| `harness/idempotency.py` 缓存 `str(cached)` | 同上 | 图片结果跳过缓存或仅缓存摘要 |
| `context/conversation.py` `add_tool_result` | `wrap_tool_result` XML 只适用文本 | 图片结果走无包装路径 |
| `context/assembly.py` `truncate_messages` | 图片按文本 token 算会误截 | 图片独立 token 预算 |
| `context/tags.py` 各类 `is_*` 判定 | 依赖 `content` 为 str | 增加对块列表的兼容 |
| `agent.py` `_record_llm_call` 校准 | `"\n".join(str(m["content"]))` | 跳过图片块 |
| 系统提示词 | 无 GUI 操作规范 | 增加坐标系/DPI/元素引用约定 |

### 2.5 配置项新增

```yaml
# config/default.yaml
vision_max_side: 1280          # 图片长边上限
vision_jpeg_quality: 70        # JPEG 压缩质量
screen_grid_enabled: true      # 截图网格覆盖层
screen_verify_enabled: false   # 高风险操作后强制 verify
grounding_server_url: ""       # 可选 grounding 模型地址
ui_backend: auto               # auto | pywinauto | ax | atspi
```

## 3. 修改影响面评估 (Impact Analysis)

| 模块 | 影响 |
|---|---|
| `model_adapter` | `LLMMessage.content` 类型变更,两个 parser 增加图片块,`ProviderProfile` 加能力字段 |
| `context/conversation` | `Message.content` 类型变更,附件存储,`add_tool_result` 分支 |
| `context/assembly` | token 估算与截断需感知图片 |
| `tools/system` | screenshot 支持 `inline` 返回图片块 |
| `tools/mouse` `tools/keyboard` | 无破坏性变更,作为底层原语被 `ui` 工具复用 |
| `harness/verifier` | 新增事后验证策略(可选开关) |
| `agent.py` | `run()` 增加附件参数;`_smart_truncate`/校准函数兼容图片 |
| `channels/feishu` | 图片消息 → 附件(下载后转 ContentBlock) |
| `config.py` | 新增 6 个配置字段 |
| 纯文本 provider 用户 | 无感知,不发图片即可,发图报清晰错误 |

## 4. 涉及代码变动文件清单 (Changed Files List)

**第一层(多模态管道,必做):**

- `src/knoa_platform/model_adapter/content.py` (新增)
- `src/knoa_platform/model_adapter/types.py`
- `src/knoa_platform/model_adapter/profiles.py`
- `src/knoa_platform/model_adapter/parsers/openai.py`
- `src/knoa_platform/model_adapter/parsers/anthropic.py`
- `src/knoa_platform/context/conversation.py`
- `src/knoa_platform/context/assembly.py`
- `src/knoa_platform/context/token_estimate.py`
- `src/knoa_platform/vision/preprocess.py` (新增)
- `src/knoa_platform/tools/system.py`
- `src/knoa_platform/agent.py`
- `src/knoa_platform/config.py`
- `config/default.yaml`

**第二层(屏幕理解,核心):**

- `src/knoa_platform/tools/ui.py` (新增,无障碍树)
- `src/knoa_platform/tools/screen.py` (新增,截图+网格+验证)
- `src/knoa_platform/vision/grid.py` (新增,网格覆盖层)
- `src/knoa_platform/vision/a11y.py` (新增,跨平台无障碍后端)
- `src/knoa_platform/tools/registry.py` (注册新工具)
- `src/knoa_platform/harness/verifier.py` (事后验证策略)

**第三层(grounding,可选):**

- `src/knoa_platform/vision/grounding.py` (新增,HTTP client)
- `src/knoa_platform/tools/screen.py` (`understand` action)

## 5. 变更历史日志 (Commit History Logs)

- 2026-08-04 | DRAFT: 初稿

version: 1.0.0
- 2026-08-04 | DRAFT: 创建设计文档
