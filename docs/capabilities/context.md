# Context 上下文

> 从零开始理解 libcore 的 Context 能力：如何采集本次会话的上下文素材，作为决策输入节点。

## 1. 什么是 Context

**Context** 是决策输入节点，产出**本次会话**要喂给决策者的上下文素材（会话历史消息 / 上传文本 / 用户指令）。

产出约定：`data["messages"]` 为注入决策者的消息序列（历史 + 素材）；无任何素材 → 降级为仅携带任务目标（单核最小可运行，不报错、不阻塞）。

## 2. 采集来源（Collectors）

素材**不是一个一个独立节点**，而是 context 节点内部的采集来源（collectors）。默认从 dispatch payload 采集：

| 来源 | payload 字段 | 说明 |
| --- | --- | --- |
| 会话历史 | `session_messages` | 历史消息（shape: list[{role, content}]） |
| 上传文本 | `uploaded` | list[str] 上传文本 |
| RAG 引用 | `rag` | list[str] RAG 引用 |

## 3. 历史裁剪（Trimmer）

`trim_history` 裁剪会话历史：保留最近 N 条；若最后一条是 user 则排除（避免与当前指令重复）。

```python
from libcore.plugins.capabilities.context import trim_history
trimmed = trim_history(messages, last_n=10, exclude_last_user_if_present=True)
```

裁剪策略由配置驱动（`input_nodes` 里的 `trim`），决定"模型看会话的哪些内容"。

## 4. RAG 召回采集器

`build_rag_retriever` 把"查询→命中文档文本"的检索函数并入 context：

```python
from libcore.plugins.capabilities.context import build_rag_retriever

def my_search(query, options):
    # 接真实向量库召回
    return ["命中文本1", "命中文本2"]

retriever = build_rag_retriever(search=my_search, top_k=3)
```

## 5. 组合采集器

`build_context` 把若干采集器合并成单节点产出（缺省来源自动跳过）：

```python
from libcore.plugins.capabilities.context import build_context

collector = build_context(
    history=my_history_collector,   # 可注入自定义历史采集
    uploaded=my_uploaded_collector,
    rag_search=my_search,           # RAG 向量检索
    rag_top_k=3,
    last_n=10,
)
```

## 6. 装配

```python
from libcore.plugins.capabilities import context
context.register(bus)   # 默认从 payload 采集
# 或注入自定义采集器 + SessionStore
context.register(bus, collector=collector, store=session_store)
```

当 payload 带 `session_id` 且注入 `store` 时，优先从 SessionStore 读历史；否则回退到 `payload["session_messages"]`。

## 7. 代码位置

- `libcore/plugins/capabilities/context.py`：Context 输入节点
