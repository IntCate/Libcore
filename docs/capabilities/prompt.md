# Prompt 提示词

> 从零开始理解 libcore 的 Prompt 能力：如何组合系统指令（角色/风格/领域知识），作为决策输入节点。

## 1. 什么是 Prompt

**Prompt** 是决策输入节点，产出**system 指令**（角色 / 风格 / 领域知识）。与 context/memory 平级 peer，互不认识。

产出约定：`data["messages"]` 为注入决策者的系统指令；缺省仍返回内置默认指令（单核可运行），不降级为 None。

## 2. 渐进披露

manifest 只暴露 `prompt` 一个入口，op 为 `run`。内核每轮决策前点名它，产出强化系统指令消息序列注入决策者。

## 3. 组合片段

可用 `build_prompt` 组合若干片段（角色/风格/领域知识），替换默认指令。每个片段独立开关，缺失即跳过，不阻塞。

```python
from libcore.plugins.capabilities import prompt

def role_segment(d):
    return "你是一位资深 Python 工程师"

def style_segment(d):
    return "回答要简洁，先给结论再给理由"

def domain_segment(d):
    return "你熟悉 libcore 的架构"

compose = prompt.build_prompt(role=role_segment, style=style_segment, domain=domain_segment)
prompt.register(bus, segment=compose)
```

### build_prompt_chain（按 priority 排序）

更细的控制可用 `build_prompt_chain`，按 priority 排序拼接：

| 片段 | priority | 说明 |
| --- | --- | --- |
| safety | 10 | 安全对齐 |
| system | 20 | 基础系统指令 |
| style | 30 | 推理风格 |
| domain | 30 | 领域知识 |
| format | 99 | 输出格式约束 |

```python
compose = prompt.build_prompt_chain(
    safety=safety_segment,
    system=system_segment,
    style=style_segment,
    domain=domain_segment,
    format=format_segment,
)
```

## 4. 默认指令

缺省用内置调度员系统指令：

```
你是 libcore 的调度员。你只能调用给你列出的能力；
根据任务选择一个要调用的能力并带好参数；
所有必须做的事都做完了，就结束（不要再调用任何工具）。
```

## 5. 装配

```python
from libcore.plugins.capabilities import prompt
prompt.register(bus)   # 默认内置指令
# 或注入自定义片段
prompt.register(bus, segment=compose)
```

## 6. 代码位置

- `libcore/plugins/capabilities/prompt.py`：Prompt 输入节点
