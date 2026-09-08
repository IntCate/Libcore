# Knowledge 知识库

> 从零开始理解 libcore 的 Knowledge 能力：如何把"知识库"作为 agent 可点名的门面能力渐进披露地检索与入库，以及背后的架构设计决策（门面薄到什么程度、CRUD 归属哪里、知识库与会话/存储为什么是三件分开的事）。

## 1. 一句话设计哲学

**agent 只看到一层薄门面（`knowledge`），内部检索是开发者黑盒，agent 永远只消费语义结果。**

```text
Agent（模型）                        —— 只拨门面，消费语义结果
   │  dispatch(Dispatch(target="knowledge", op="search", payload={...}))
   ▼
knowledge.py（门面，薄）             —— 只做 op 分发 + 数据契约校验，零检索逻辑
   │  委托
   ▼
注入的 KnowledgeStore（开发者黑盒）  —— 内部随便 keyword / 向量 / hybrid / rerank
                                          纯开发者实现，框架不知道也不关心
   │
   ▼
CapabilityResult(hits=[{text, score, metadata}])  ──> 回给 agent
```

**Knowledge** 与 `skill` / `tools` / `mcp` 平级，是 manifest 里**唯一**的"知识库入口"。agent 通过 `dispatch(Dispatch(target="knowledge", op=..., payload=...))` 点名它，检索或写入知识库。

## 2. 三层分离（总纲）

| 层          | 是什么           | 例子                                         | 是否暴露给 agent    |
| ---------- | ------------- | ------------------------------------------ | -------------- |
| **能力/门面层** | 模型可调/可注入的语义入口 | `knowledge` 门面、`context`/`memory` 输入节点     | 暴露（agent 消费语义） |
| **领域状态层**  | 实体在内存里的形状     | `AgentSessionRecord`、知识库索引                 | 不暴露（内部状态）      |
| **存储介质层**  | 持久化实现         | SQLite / 文件 / 内存 store、`KnowledgeStore` 后端 | 不暴露（开发者黑盒）     |

- **会话** 属于"领域状态层" + "存储介质层"这两层的事，不是能力层的事。

- **存储** 从来不该被 agent 直接点名——agent 面前只有"领域门面"。

当前代码已经天然体现了这层分离：

- [libcore/storage/session\_store.py](../../libcore/storage/session_store.py)：`SessionStore(ABC)` 只定义契约（get/save/list/delete），不绑定介质。

- [libcore/storage/sqlite\_store.py](../../libcore/storage/sqlite_store.py)：把 dataclass 落成 SQL 行的介质实现。

- [libcore/plugins/capabilities/knowledge.py](../../libcore/plugins/capabilities/knowledge.py)：门面，委托 store。

## 3. CRUD 该不该暴露给 agent？

**答：暴露「领域语义门面」，不暴露「存储接口」。**

存储的机械 CRUD（get/save/list/delete、upsert）**不**暴露给 agent，那是 `KnowledgeStore` / `SessionStore` 的事。但知识库这一领域的**语义入口**要暴露，因为 agent 会主动检索：

| 领域      | 对 agent 暴露哪些                                           | 底层 CRUD 藏在哪                         |
| ------- | ------------------------------------------------------ | ----------------------------------- |
| **知识库** | `search`(查)、`ingest`(增)、`clear`(删)、`find`/`read` —— 暴露 | upsert / delete / get（藏在门面后）        |
| **会话**  | **什么都不暴露**                                             | get / save / list / delete（宿主/内核调用） |

### 为什么知识库暴露、会话不暴露

判断标准只有一个：**agent 会不会主动"去取内容"？**

- **知识库是 agent 主动检索的。** agent 说"查一下这个知识库关于 X 的资料"，`search` 必须暴露；随之的 `ingest`/`clear` 是运维维护，一并给 agent（它能自我灌数据让回答更好）。它暴露的**不是存储接口，而是门面语义**——数据格式约定，agent 只消费结果，不碰底层怎么存。

- **会话是内核自动处理的。** 会话不是 agent 去"查"的东西——每轮决策前内核经 `context`/`memory` 自动注入素材，loop 结束由内核/宿主 `SessionStore.save` 落库。会话是**内部运行状态**，暴露给 agent 等于让它翻改自己的运行现场，破坏"agent 专注决策、宿主管状态"的边界。

> 一句话记忆：**知识库是 agent 的"图书馆"（主动借阅，故开门面）；会话是 agent 的"运行现场"（内核自动管理，故关闭口）。**

## 4. 门面对 agent 暴露的 op 集

manifest 只暴露**一个**知识库入口 `knowledge`，**具体知识库的概要不常驻清单**——agent 需要时才逐层索取（渐进披露，省 token）：

| 层级     | op                          | 作用                                |
| ------ | --------------------------- | --------------------------------- |
| **L1** | `knowledge find`            | 列知识库索引（name + 一句 description），供选择 |
| **L2** | `knowledge read`            | 取某知识库元信息（文档文件 / 统计）               |
| **L3** | `knowledge search`          | 在某知识库内按查询召回相关片段                   |
| **L4** | `knowledge ingest`          | 加载文件 → 切分 → 入库                    |
| **L5** | `knowledge stats` / `clear` | 统计 / 清空某知识库                       |

- **查（检索，agent 高频用）**：`find` / `read` / `search`（`data["hits"]=[{text,score,metadata}]`）

- **维护（增/删，agent 偶尔用）**：`ingest`（= 增）、`clear`（= 删）

- **改（update）：不单独加 op。** `ingest` 已按 `source` 幂等覆盖，重传同源文件即"改"。真需"删某条"再补 `drop by id`，否则不为对称性堆 op。

## 5. 知识库目录结构

```
data/knowledge_bases/
└── libcore/                 # 一个知识库 = 一个子目录
    ├── kb.yaml              # 可选元信息（name / description）
    ├── capabilities.md      # 文档文件（可多个，支持 txt/md/pdf/docx 等）
    └── ...
```

`kb.yaml` 可选，缺省用目录名作为知识库名：

```yaml
name: libcore
description: libcore 框架自身的设计文档知识库（示例）
```

## 6. 数据契约（框架与开发者之间的约定）

`KnowledgeStore` 就是"框架 ↔ 开发者"的边界，开发者只要实现它并注入：

```python
class KnowledgeStore:
    def upsert(self, kb: str, docs: List[KBDocument]) -> Dict[str, Any]: ...
    def search(self, kb: str, query: str, *, top_k=5,
               score_threshold=0.0) -> List[KBDocument]: ...
    def stats(self, kb: str) -> Dict[str, Any]: ...
    def clear(self, kb: str) -> bool: ...
```

内部用什么检索完全由开发者决定（关键词 / 向量 / 混合 / rerank），框架不感知。请求参数 + 返回 `data["hits"]` 是唯一需要对齐的数据格式。

其中知识库文档模型 **`KBDocument`** **字段统一为** **`text`**（[documents.py](../../libcore/plugins/resources/knowledge/documents.py)），内部实体与门面出口共用同一命名，无翻译层：

```python
@dataclass
class KBDocument:
    text: str                 # 文档/文本块正文 —— 内部与对外统一叫 text
    metadata: Dict[str, Any]  # 元信息（kb / source / page / chunk_idx 等）
    score: Optional[float]    # 归一化相似度 [0,1]；写入时 None，search 返回时由后端填充
```

门面 `search` 返回的 `data["hits"]` 元素与之一一对应（`text` / `score` / `metadata`），**字段名与** **`KBDocument`** **完全一致**，外部消费方直接取 `hit.text` 即可，不必在 `page_content` 与 `text` 之间做翻译。

### 默认后端：保留零依赖内存实现

默认提供 **`InMemoryKnowledgeStore`**（按 `kb` 分桶 + 关键词召回 + 命中率排序，零外部依赖），对齐 skill/tool 的"开箱即用"，搜 `knowledge_bases/` 已有文档可裸跑命中。**生产请注入你自己的 store**（内存实现仅作示例/裸跑）。

## 7. 装配：唯一门面

`knowledge` 是知识库的**唯一**能力入口。原先与它同域的 `vector_store` 能力节点已**直接合并重写**进 `knowledge.py`——检索实现收敛为 `InMemoryKnowledgeStore` 的默认后端，`vector_store.py` 文件与配置条目已删除，manifest 只保留 `knowledge` 一个知识入口。

```python
from libcore.plugins.capabilities import knowledge
knowledge.register(bus)              # 默认零依赖内存后端 + 默认知识库目录
# 或注入自定义后端（Qdrant / Milvus / 真实向量库 / Hybrid）
knowledge.register(bus, backend=my_store)
```

检索/入库异常不致命：降级为空结果（无感原则）。

> 开放点：当前 `CapabilityLoader._register` 只调 `register(bus)` 不带 kwargs，无法经配置文件注入后端。若要让 knowledge/ui/memory 都能配后端，需给装配层补"后端工厂注入"通道——本设计文档不展开，留待实施。

## 8. 调用示例

```python
# L1：列知识库索引
await bus.dispatch(Dispatch(target="knowledge", op="find", payload={}))
# → {"knowledge_bases": [{"name": "libcore", "description": "...", "path": "libcore"}]}

# L2：读某知识库元信息
await bus.dispatch(Dispatch(target="knowledge", op="read", payload={"name": "libcore"}))
# → {"name": "libcore", "files": [...], "stats": {...}}

# L3：检索
await bus.dispatch(Dispatch(target="knowledge", op="search",
                            payload={"name": "libcore", "query": "能力插件", "top_k": 3}))
# → {"hits": [{"text": "...", "score": 0.8, "metadata": {...}}]}

# L4：入库
await bus.dispatch(Dispatch(target="knowledge", op="ingest",
                            payload={"name": "libcore", "path": "/path/to/doc.md"}))
# → {"ingested": True, "chunk_count": 5, "count": 5}
```

## 9. 与 context / session / memory 的边界

| <br />     | knowledge 知识库           | session（context）会话 | memory 记忆                       |
| ---------- | ----------------------- | ------------------ | ------------------------------- |
| 存的本质       | 答案素材（文档分块）              | 单次对话历史/上下文         | 跨会话偏好/事实/摘要                     |
| 对 agent 形态 | **可调能力门面**（主动 search）   | **决策输入节点**（每轮自动注入） | **决策输入节点**（跨会话注入）               |
| 生命周期       | 长驻、跨会话共享                | 随会话、有裁剪窗口          | 跨会话长驻                           |
| 语义         | 检索（query→相关块+score）     | 时序（消息先后顺序）         | 回忆（query→记忆）                    |
| CRUD 在哪    | 后端 store 的 upsert/clear | SessionStore（宿主调用） | MemoryBackend.remember/retrieve |
| 真实用户       | 多会话                     | 单会话宿主              | 框架/宿主                           |

- **`context`**：决策输入节点，其 `build_rag_retriever` 可注入 `knowledge.search` 作为 RAG 召回源，把知识库命中注入决策上下文。

- **它们重合的本质**：底层都是"存数据 + 取数据"，所以共享 Storage 纯库抽象是健康的、不可避免的，**不是功能重复**。它们不冲突，因为「领域语义 + 消费方 + 生命周期」三样都不同——knowledge 是检索语义、agent 主动调、跨会话共享；session/context 是时序语义、内核每轮自动注入、单会话作用域。

- 因此**不该**把 knowledge 与 session 合并成一个"通用数据能力"——合并会让一个门面既做检索又做会话状态，语义糊掉。

## 10. 代码位置

- `libcore/plugins/capabilities/knowledge.py`：Knowledge 门面 + KnowledgeEngine + `KnowledgeStore` / `InMemoryKnowledgeStore`

- `libcore/plugins/resources/knowledge/`：知识库纯库（文档模型 / 加载器 / 切分器 / 嵌入提供方）——纯库，非总线节点

- `../data/knowledge_bases/`：知识库目录数据（每子目录一个 KB，含可选 `kb.yaml`）

- `libcore/storage/`：存储纯库（`SessionStore` / `sqlite_store` 等介质实现）

<br />
