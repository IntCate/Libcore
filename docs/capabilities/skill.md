# Skill 技能库

> 从零开始理解 libcore 的 Skill 能力：如何用"目录 + SKILL.md + 附件"组织技能，agent 如何渐进披露地调用。

## 1. 什么是 Skill

**Skill（技能）** 是文件/指令资源，遵循 Claude/Anthropic Skills 约定：**技能 = 目录 + SKILL.md + 可选附件**。

```
libcore/plugins/resources/skills/
├── builtin/
│   └── code/
│       └── SKILL.md
├── devops/
│   └── deploy-k8s/
│       ├── SKILL.md
│       ├── references/cheatsheet.md
│       └── scripts/gen_deployment.py
└── mlops/
    └── axolotl/
        └── SKILL.md
```

## 2. 渐进披露（省 token）

manifest 只暴露**一个**能力入口 `skill`，**具体技能的概要不常驻清单**——agent 需要时才逐层索取：

| 层级 | op | 作用 |
| --- | --- | --- |
| **L1** | `skill find` | 列技能目录（每个技能的 name + 一句 description），供选择 |
| **L2** | `skill read` | 取某技能 SKILL.md 指令全文 + 附件 filemap |
| **L3** | `skill resource` | 按需读某技能下一个附件（references/templates/scripts/assets） |
| **L4** | `skill exec` | 进程内执行某技能 scripts/ 下的脚本（约定暴露 `run(args)->dict`） |

选具体技能用 payload 的 `skill` 字段（如 `devops/deploy-k8s`），不必进 manifest。

## 3. SKILL.md 格式

Claude 风格：`---` 包裹的 YAML frontmatter + 正文。

```markdown
---
name: deploy-k8s
description: 部署应用到 Kubernetes 集群
---

# 部署到 K8s

（指令正文）
```

- `name`：技能名（缺省用目录名）
- `description`：技能描述（find 时展示）

## 4. 调用示例

```python
# L1：列技能目录
await bus.dispatch(Dispatch(target="skill", op="find", payload={}))
# → {"skills": [{"name": "deploy-k8s", "category": "devops", "description": "..."}]}

# L2：读某技能指令
await bus.dispatch(Dispatch(target="skill", op="read", payload={"skill": "devops/deploy-k8s"}))
# → {"instruction": "...", "filemap": {"scripts": ["gen_deployment.py"]}}

# L3：读附件
await bus.dispatch(Dispatch(target="skill", op="resource",
                            payload={"skill": "devops/deploy-k8s", "path": "references/cheatsheet.md"}))

# L4：执行脚本
await bus.dispatch(Dispatch(target="skill", op="exec",
                            payload={"skill": "devops/deploy-k8s", "script": "gen_deployment.py", "args": {...}}))
```

## 5. 安全设计

- **防越权**：`_safe_resolve` 把相对路径解析到技能目录内，拒绝 `..` / 绝对路径。
- **执行收敛**：执行信号收敛于 `skill` 的 exec op，aspect 护栏（sandbox/permission）只签此处。
- **进程内执行**（首期）：脚本在当前进程加载，护栏好接。

## 6. 装配

```python
from libcore.plugins.capabilities import skill
skill.register(bus)   # 默认技能库目录 libcore/plugins/resources/skills
```

可注入自定义技能库目录：

```python
skill.register(bus)   # SkillEngine(skills_dir=...) 可注入
```

## 7. 代码位置

- `libcore/plugins/capabilities/skill.py`：Skill 门面 + SkillEngine
- `libcore/plugins/resources/skills/`：技能库目录
