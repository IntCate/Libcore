# libcore：引入 tools 工具门面（manifest 收敛到 skill + tools 两入口）

## Context（为什么做）

当前 `manifest()` 是"所有 handler 的投影"，具体工具（`tool.file/bash/web/todo/pdf`）与技能节点（`skill`、`skill.code`）**全体平铺在清单一级**。这违反我们已确立的"具体能力是二级、入口才是一级"模型，也带来 token 浪费。

已确认的模型（与 Anthropic Tool Search 同构，此前查证）：

- 技能 `skill`：manifest 只露 `skill` 单入口，具体技能是文件资源，`find→read→exec` 渐进披露。✅ 已落地

- 工具 `tools`：**本轮新增**对称门面，具体工具进 `tools`，schema 按需披露，不再各自占 manifest。

- **范围**：收纳 `tool.*`（file/bash/web/todo/pdf）进 `tools` 门面；收纳 `code_skill` 进 `skill` 门面（内置代码技能）。skills 归 skills，tools 归 tools。

- **护栏**：判定从"拦 `tool.*` 前缀"改为"拦 `tools` 的 run / `skill` 的 exec"。

行为变化要写进 README：模型要调用一个文件工具，需 `tools find`（名+一句）→ `tools read`（完整 schema）→ `tools run`；manifest 只剩 `skill` + `tools` 两个真入口（+ `storage.vector` 占位，本轮不动）。

## 一图看清改造前后

```
改造前 manifest： skill, skill.code, tool.file, tool.bash, tool.web.*, tool.todo.*, tool.pdf, storage.vector
改造后 manifest： skill, tools, storage.vector
```

## 实施步骤

### 1. 新增工具门面 `plugins/capabilities/tools.py`

对称 `skill.py` 设计：

- 闹钟 `ToolEngine`：扫描 `tools/` 子包的 `*.py`，读每个模块暴露的 `TOOLS: dict[str, ToolSpec]` 聚合为 `{key: spec}`。

- `ToolSpec = dataclass(name, description, ops, schema, handler)`；`handler(d) -> CapabilityResult`。

- 门面 op（`bus.on("tools", handle)`，加载前可加 `load`）：

  - `find`：按 keyword 返回 `[{key, name, ops, description}]`（**不含完整 schema** → 省 token）

  - `read`：`name` 返回该工具完整 `schema`+参数说明（渐进披露）

  - `run`：`name` + `op` + `args`，构造 payload 调对应 `handler`

- meta：`{"description": DESCRIPTION, "ops": ["find","read","run"], "schema": {}}`。

- 参照 `skill.SkillEngine` 的目录/资源管理模式与 `bus.on` meta 写法：[skill.py](file:///e:/MyProject/Chato-main/backend/libcore/plugins/capabilities/skill.py)。

### 2. 工具实现迁入 `plugins/capabilities/tools/` 子包

把现有独立节点改造为"工具定义模块"，每模块只暴露 `TOOLS`，**不再** **`register(bus)`**；文件从顶层移入 `tools/` 子包（`CapabilityLoader.scan()` 用 `glob("*.py")` 非递归，顶层只剩门面，不会把它们当独立能力）：

- `tools/bash.py`：`TOOLS={"bash": ToolSpec(...)}`，逻辑复用现 [bash.py](file:///e:/MyProject/Chato-main/backend/libcore/plugins/capabilities/bash.py) 的 `handle`。

- `tools/web.py`：`TOOLS={"web": ToolSpec(name="web", ops=["fetch","search"], ...多一个 op 决定 fetch/search)}`，复用现 [web.py](file:///e:/MyProject/Chato-main/backend/libcore/plugins/capabilities/web.py)。

- `tools/todo.py`：`TOOLS={"todo": ToolSpec(ops=["add","list","done"], ...)}`，复用现 [todo.py](file:///e:/MyProject/Chato-main/backend/libcore/plugins/capabilities/todo.py) 的三个 handler。

- `tools/file.py`：`TOOLS={"file": ToolSpec(ops=["read","write","search"], ...)}`。

- `tools/pdf.py`：`TOOLS={"pdf": ToolSpec(ops=["parse"], ...占位)}`，吸收现 `pdf_tool.py`。

- 删除顶层 `bash.py/web.py/todo.py/file.py/pdf_tool.py`。

### 3. 收纳 `code_skill` 进 `skill` 门面（内置代码技能）

- 在 `SkillEngine` 增加**内置代码技能登记**（skills 归 skills）：`_BUILTIN = {"code": BuiltinSkill(...)}`。

- `SkillEngine` 构造时注入 `bus`（`register(bus)` 里 `SkillEngine(bus)`），供内置技能编排经 `bus.dispatch` 调 `tools` 门面的 file 读写。

- `find` 把内置技能并入索引（`category="builtin"`）；`read` 返回其说明；`exec` 匹配内置名则运行其 Python 实现。

- 删顶层 `code_skill.py`（不再是独立 `skill.code` 节点），其编排逻辑迁入内置 `code` 技能实现（内部：`tools run` file read→改→write）。

### 4. 护栏判据改造

`sandbox.py` / `permission.py` 的 `is_exec_signal`（目前 `target.startswith("tool.") or (skill and op=="exec")`，见 [sandbox.py](file:///e:/MyProject/Chato-main/backend/libcore/plugins/aspects/sandbox.py#L14-L27)）改为：

- `target == "tools" and op == "run"`（工具真实执行经此）

- `target == "skill" and op == "exec"`（保留）

- `permission._candidate_text` 适配 `tools run` 的 `payload["args"]`（危险命令在 `args["command"]`，仍需从 args 拼装收集）。

### 5. 配置收敛 `config/capabilities.yaml`

只登记两个门面：

```yaml
plugins:
  - name: skill
    enabled: true
  - name: tools
    enabled: true
```

移除 bash/web/todo/file/pdf\_tool/code\_skill/vector\_store 的独立条目与文件（vector\_store 不删除，见步骤 6）。

### 6. 占位与边界（诚实标注）

- `vector_store.py`（`storage.vector` 占位）本轮**保留在 manifest**，属 storage 范畴不收进 `tools`。后续建 `storage` 门面时统一收敛（记入 backlog）。

- `pdf`（工具类）按工具收进 `tools`。

### 7. 测试与 demo 迁移（引用最多，务必逐项）

- [test\_captured\_capabilities.py](file:///e:/MyProject/Chato-main/backend/tests/libcore/test_captured_capabilities.py)：`Dispatch(target="tool.bash")` → `Dispatch(target="tools", op="run", payload={"name":"bash","args":{...}})`；`tool.web.*`→`tools run web(op=seed/fetch)`；`tool.todo.*`→`tools run todo(op=add/list/done)`；`skill.code`→`skill exec(skill="code")`；断言同步。

- [test\_aspects.py](file:///e:/MyProject/Chato-main/backend/tests/libcore/test_aspects.py)：护栏用例改为 `tools run` 构造，验证被拦且 handler 未执行。

- [test\_bus.py](file:///e:/MyProject/Chato-main/backend/tests/libcore/test_bus.py)：manifest 断言 `tool.pdf` → `tools`；`OnlyPdf` 过滤 `s.target=="tool.pdf"` → `tools`。

- [test\_loader.py](file:///e:/MyProject/Chato-main/backend/tests/libcore/test_loader.py)：manifest 断言改 `{"tools","skill"}`(+`storage.vector`)；白名单/开关测试的插件名改用 `tools`/`skill`。

- [test\_lm\_reason.py](file:///e:/MyProject/Chato-main/backend/tests/libcore/test_lm_reason.py)：manifest 注入后断言 `tools` 映射。

- [demo.py](file:///e:/MyProject/Chato-main/backend/libcore/demos/demo.py)：改为注册 `tools` 门面（含 pdf 子工具）+ `storage.vector`；决策者从"manifest 找 tool.pdf"改为"点 `tools run` pdf / storage.vector"。

- [demo\_migrate\_file.py](file:///e:/MyProject/Chato-main/backend/libcore/demos/demo_migrate_file.py)：`Action(target="tool.file")` → `Action(target="tools", op="run", payload={"name":"file", ...})`。

- [demo\_engine.py](file:///e:/MyProject/Chato-main/backend/libcore/demos/demo_engine.py)：已是 `skill` 门面，若引用 `tool.*` 同步改。

- 新增 `tests/libcore/test_tools.py`：门面 find（不含 schema）/ read（完整 schema）/ run（执行）+ 护栏在 `tools run` 拦截危险命令。

- 解耦测试中硬编码的业务 target 断言 → 改为基于 `loader.scan()` 动态比较（沿用现有模式）。

### 8. 文档同步

- [README.md](file:///e:/MyProject/Chato-main/backend/libcore/README.md)：manifest 两入口说明、行为变化、目录树（`tools/` 子包）、运行命令、护栏判据、Anthropic Tool Search 数据背书。

- [libcore-migration-backlog.md](file:///e:/MyProject/Chato-main/docs/design/libcore-migration-backlog.md)：更新"已完成/待办"，记录 storage 门面与占位清理。

## 复用的现有实现

- `skill.SkillEngine`（技能门面渐进披露范式）：目录扫描 / op 分发 / meta 写法 — [skill.py](file:///e:/MyProject/Chato-main/backend/libcore/plugins/capabilities/skill.py)

- `CapabilityLoader.scan()` 非递归特性：保证 `tools/` 子包不被单独加载 — [loader.py](file:///e:/MyProject/Chato-main/backend/libcore/plugins/loader.py#L58-L65)

- `is_exec_signal`（护栏判据中心）— [sandbox.py](file:///e:/MyProject/Chato-main/backend/libcore/plugins/aspects/sandbox.py)

- 各工具现 handler 逻辑（bash/web/todo/file）原样迁入子包工具定义。

## 验证

- `cd backend && python -m pytest tests/libcore -q` 全绿（预计 \~70+，含新增 test\_tools）

- `python -m libcore.demos.demo_engine`：skill 门面闭环（find/read/exec + 护栏拦危险 args）

- `python -m libcore.demos.demo_tools`（新增）或改造后的 demo.py：tools 门面闭环（find 不含 schema → read schema → run 执行 + 护栏拦截）

- `python -m libcore.demos.demo_migrate_file.py`：原生工具经 `tools` 门面读到真实文件

- 目测 `manifest()` 输出只剩 `skill / tools / storage.vector`，无具体 `tool.*`。

