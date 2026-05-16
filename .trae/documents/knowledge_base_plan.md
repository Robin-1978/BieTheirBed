# PC Assistant 知识库方案

## 现状分析

当前项目已有 `UserMemory` 记忆系统，但它本质是一个**扁平的 key-value 存储**：
- 存储：key-value 键值对（如 `location: Shanghai`）
- 搜索：纯关键词匹配（字符串包含评分）
- 注入：取 confidence 前 10 条拼入 system prompt
- 适用场景：用户偏好、身份信息、习惯等短文本

**不适合**作为知识库的原因：
- 无语义搜索能力（"怎么部署" 搜不到 "安装步骤"）
- 不支持长文档存储和分块
- 无文档摄入/索引流程
- 搜索质量太低，无法做 RAG

## 方案对比

### 方案 A：增强现有 Memory（轻量级）

在 `UserMemory` 基础上添加：
- 长文本存储（value 支持大段内容）
- 标签分类
- TF-IDF 相似度搜索
- 文件导入

**优点**：改动最小，无新依赖
**缺点**：搜索质量仍有限，无语义理解，长文档检索差

### 方案 B：ChromaDB + RAG（推荐）

使用 ChromaDB 作为本地向量数据库，实现 RAG 流程：
- 文档分块 → 向量化 → 存储 → 语义检索 → 上下文注入

**优点**：语义搜索质量高，本地运行无需服务器，轻量级 Python 包
**缺点**：新增 chromadb 依赖，需要 embedding 模型

### 方案 C：纯文件索引（极简）

知识库目录下放 markdown 文件，通过文件系统工具搜索：
- 用 `grep`/`ripgrep` 搜索文件内容
- 不需要额外依赖

**优点**：零依赖，人类可读可编辑
**缺点**：搜索质量最低，无语义能力，与现有 filesystem 工具重叠

## 推荐：方案 B — ChromaDB RAG 知识库

### 架构设计

```
┌─────────────────────────────────────────────────┐
│                   Agent                          │
│                                                  │
│  system_prompt = base_prompt                     │
│               + memory_context  (UserMemory)     │
│               + knowledge_context (RAG检索结果)   │
│                                                  │
│  tools: [...现有14个, knowledge(新增)]            │
└─────────────────────────────────────────────────┘
         │                              │
         ▼                              ▼
┌─────────────────┐        ┌──────────────────────┐
│   UserMemory    │        │   KnowledgeBase      │
│  (偏好/身份)     │        │  (文档/知识)          │
│  data/memory.json│       │  data/knowledge_db/  │
│  key-value       │        │  ChromaDB collections│
└─────────────────┘        └──────────────────────┘
                                    │
                          ┌─────────┴─────────┐
                          │  Embedding Function│
                          │  (chromadb默认     │
                          │   all-MiniLM-L6-v2)│
                          └────────────────────┘
```

### 模块设计

#### 1. `src/pc_assistant/context/knowledge_base.py` — 核心知识库

```python
class KnowledgeBase:
    """基于 ChromaDB 的本地知识库"""

    def __init__(self, persist_dir: str = "data/knowledge_db"):
        self._client = chromadb.PersistentClient(path=persist_dir)
        self._collection = self._client.get_or_create_collection(
            name="knowledge",
            metadata={"hnsw:space": "cosine"}
        )

    def add_document(self, doc_id: str, content: str,
                     metadata: dict = None) -> int:
        """添加文档，自动分块，返回块数"""

    def add_file(self, file_path: str) -> int:
        """导入文件（txt/md/py等），返回块数"""

    def add_directory(self, dir_path: str,
                      patterns: list[str] = None) -> int:
        """批量导入目录，返回总块数"""

    def search(self, query: str, top_k: int = 5,
               filter_metadata: dict = None) -> list[SearchResult]:
        """语义搜索，返回相关片段"""

    def delete_document(self, doc_id: str) -> None:
        """删除文档的所有块"""

    def list_documents(self) -> list[dict]:
        """列出所有已导入文档"""

    def build_context_string(self, query: str,
                             max_chunks: int = 3) -> str:
        """根据查询构建 RAG 上下文字符串"""

    @property
    def stats(self) -> dict:
        """返回知识库统计信息"""
```

**分块策略**：
- 默认块大小：500 字符，重叠 100 字符
- Markdown 文件：按标题（##）分块
- 代码文件：按函数/类分块
- 每块携带 metadata：`doc_id`, `source`, `chunk_index`, `file_type`

**搜索结果**：
```python
@dataclass
class SearchResult:
    content: str
    score: float        # 相似度 0~1
    doc_id: str
    source: str         # 源文件路径
    metadata: dict
```

#### 2. `src/pc_assistant/tools/knowledge_tool.py` — LLM 工具

```python
class KnowledgeTool(ToolBase):
    """让 LLM 主动搜索知识库"""
    name = "knowledge"
    description = "Search the local knowledge base for relevant information"

    # actions:
    #   search   - 语义搜索知识库 (query, top_k=3)
    #   list     - 列出已导入文档
    #   add      - 导入文件/目录 (path)
    #   remove   - 删除文档 (doc_id)
```

#### 3. Agent 集成

在 `agent.py` 中：
- 初始化 `KnowledgeBase` 实例
- 注册 `KnowledgeTool`
- 每次 `run()` 时：先用用户输入查询知识库，将结果注入 system prompt
- 新增 `/knowledge` 命令支持

#### 4. UI 命令

| 命令 | 说明 |
|------|------|
| `/knowledge` | 显示知识库统计 |
| `/knowledge add <path>` | 导入文件或目录 |
| `/knowledge search <query>` | 搜索知识库 |
| `/knowledge list` | 列出已导入文档 |
| `/knowledge remove <doc_id>` | 删除文档 |

#### 5. System Prompt 更新

在 `system_prompt.py` 中新增：
```
## Knowledge Base Rules
- When answering questions about specific topics, search the knowledge base first via `knowledge` tool (action=search).
- If the knowledge base returns relevant results, use them to supplement your answer.
- If no relevant results found, answer from your own knowledge and note the limitation.
```

### Embedding 方案

ChromaDB 默认使用 `all-MiniLM-L6-v2`（sentence-transformers），首次使用自动下载（~80MB）。

**备选方案**（如不想安装 PyTorch）：
- 使用 OpenAI embeddings API（`text-embedding-3-small`）
- 通过配置 `knowledge_embedding_provider` 切换

### 依赖

```
# pyproject.toml 新增
chromadb >= 0.4.0
```

ChromaDB 会自动拉取 `sentence-transformers` + `torch`（CPU版）。

### 实现步骤

1. **添加依赖**：`pyproject.toml` 添加 `chromadb`
2. **实现 KnowledgeBase**：`src/pc_assistant/context/knowledge_base.py`
   - ChromaDB 客户端初始化
   - 文档分块逻辑（`_chunk_text`, `_chunk_markdown`, `_chunk_code`）
   - add/search/delete/list/build_context_string 方法
3. **实现 KnowledgeTool**：`src/pc_assistant/tools/knowledge_tool.py`
   - search/list/add/remove 四个 action
   - schema 定义
4. **集成到 Agent**：
   - `agent.py` 初始化 KnowledgeBase
   - 注册 KnowledgeTool
   - `run()` 中 RAG 上下文注入
5. **更新 System Prompt**：`system_prompt.py` 添加知识库规则
6. **UI 命令**：`chat.py` 添加 `/knowledge` 命令处理
7. **配置**：`config.py` + `default.yaml` 添加知识库相关配置
8. **测试**：`tests/test_knowledge_base.py`

### 配置项

```yaml
# default.yaml
knowledge:
  enabled: true
  persist_dir: "data/knowledge_db"
  chunk_size: 500
  chunk_overlap: 100
  max_context_chunks: 3
  embedding_provider: "local"    # local | openai
```

### 与 UserMemory 的分工

| | UserMemory | KnowledgeBase |
|---|---|---|
| 存储内容 | 用户偏好、身份、习惯 | 文档、知识、代码 |
| 数据形式 | key-value 短文本 | 分块的向量索引 |
| 搜索方式 | 关键词匹配 | 语义向量搜索 |
| 注入时机 | 始终注入 system prompt | 按查询相关性动态注入 |
| LLM 交互 | 主动存储/检索 | 主动搜索/导入 |
| 持久化 | JSON 文件 | ChromaDB 目录 |
