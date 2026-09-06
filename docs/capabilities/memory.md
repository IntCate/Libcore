# Memory 记忆

> 从零开始理解 libcore 的 Memory 能力：如何做跨会话记忆，作为决策输入节点。

## 1. 什么是 Memory

**Memory** 是决策输入节点，产出**跨会话**的持久记忆（用户偏好 / 历史事实 / 会话摘要）。与 context/prompt 平级、互不认识，独立 enabled:false 可拆卸。

产出约定：`data["messages"]`。

## 2. 记忆契约

```python
@dataclass
class MemoryRecord:
    id: str
    session_id: str = ""       # 归属会话；空 = 全局记忆
    content: str = ""          # 记忆正文（偏好/事实/摘要）
    kind: str = "fact"         # "summary" | "fact" | "skill_ref"
    importance: float = 0.5
    tags: List[str]
    created_at: float
```

## 3. 记忆后端

`MemoryBackend` 是记忆后端接口：

```python
class MemoryBackend:
    def retrieve(self, query, *, session_id, limit=5) -> List[str]:
        raise NotImplementedError
```

### 默认：DefaultMemoryBackend

默认提供**零依赖的内存情景记忆存储**（对齐旧 MemoryEpisodicFTSStore 的中文二元切分关键词检索 + 会话作用域过滤），保证"零配置可裸跑、有记忆可用"。

```python
backend = DefaultMemoryBackend()
backend.remember("用户偏好简洁回答", session_id="s1", kind="fact")
hits = backend.retrieve("偏好", session_id="s1")   # → ["用户偏好简洁回答"]
```

### 混合：HybridMemoryBackend

`HybridMemoryBackend` 复刻旧 `MemoryRetrieverHybrid` 的融合语义：向量召回 ∪ 全文召回 → 按 id 去重 → importance 排序 → top_k。任一存储不可用 → 跳过该路（降级可用）。

```python
backend = HybridMemoryBackend(
    vector_store=my_vector_store,   # 须实现 search_keyword(query, limit)
    fts_store=my_fts_store,
    skill_store=my_skill_store,
)
```

## 4. 会话摘要巩固

`SessionSummaryConsolidator` 把一次运行的对话巩固为摘要并写入记忆（写记忆闭环）：

```python
backend.consolidate(messages, session_id="s1")
# → 生成摘要并写入 kind="summary" 的记忆
```

LLM 未注入/调用异常 → 降级统计摘要（辅助任务可降级，不阻塞主流程）。

## 5. 装配

```python
from libcore.plugins.capabilities import memory
memory.register(bus)   # 默认内存后端
# 或注入自定义后端
memory.register(bus, memory=my_backend)
```

检索/存储异常不致命：降级为空记忆（无感原则）。

## 6. 代码位置

- `libcore/plugins/capabilities/memory.py`：Memory 输入节点 + 记忆后端
