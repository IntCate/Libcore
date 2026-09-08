"""组合测试：把内核 + 能力 + 横切面按"积木"方式组合，验证组合层面的行为。

覆盖现有单测未覆盖的组合：
1. sandbox + tools/bash：沙箱接管 bash 执行（隔离子进程 + 环境白名单）；
2. circuit_breaker + tools：熔断（连续失败超阈值 → OPEN 拒绝）；
3. budget_guard + AgentLoop：预算熔断（100% 时 ctx.done=True）；
4. permission + skill exec：权限拦截 skill 执行；
5. sandbox + skill exec：沙箱隔离 skill 脚本；
6. memory + AgentLoop：记忆检索注入决策输入。

bus.dispatch()/publish() 是异步的，用 asyncio.run() 包裹（不依赖 pytest-asyncio）。
"""
from __future__ import annotations

import asyncio

from libcore.kernel.bus import EventBus, Dispatch, CapabilityResult, Notice
from libcore.kernel.agent.spi import Action
from libcore.kernel.agent import AgentLoop, ReasonProvider
from libcore.plugins.capabilities import tool as tools_cap
from libcore.plugins.capabilities import skill as skill_cap
from libcore.plugins.capabilities import memory as memory_cap
from libcore.plugins.capabilities import knowledge as knowledge_cap
from libcore.plugins.aspects import sandbox as sandbox_cap
from libcore.plugins.aspects import permission as perm_cap
from libcore.plugins.aspects import circuit_breaker as cb_cap
from libcore.plugins.aspects import budget_guard as bg_cap
from libcore.plugins.aspects import loop_governor as lg_cap


def _dispatch(bus, target, op, payload):
    return asyncio.run(bus.dispatch(Dispatch(target=target, op=op, payload=payload)))


def _publish(bus, topic, payload):
    return asyncio.run(bus.publish(Notice(topic=topic, payload=payload)))


def _ctx():
    from libcore.kernel.bus import Scope
    return Scope(goal="demo")


# ---- 1. sandbox + tools/bash ----

class TestSandboxBash:
    def test_sandbox_disabled_passes_through(self):
        """沙箱未启用时，bash 执行走原 handler（不接管）。"""
        bus = EventBus()
        tools_cap.register(bus)
        sandbox_cap.register(bus, enabled=False)
        r = _dispatch(bus, "tools", "run",
                      {"name": "bash", "tool_op": "exec", "args": {"command": "echo hi"}})
        assert r.ok, f"bash 应正常执行：{r.error}"
        assert "hi" in r.data["stdout"]

    def test_sandbox_enabled_isolates_execution(self):
        """沙箱启用时，bash 执行被重定向到隔离子进程（sandboxed=True）。"""
        bus = EventBus()
        tools_cap.register(bus)
        sandbox_cap.register(bus, enabled=True)
        r = _dispatch(bus, "tools", "run",
                      {"name": "bash", "tool_op": "exec", "args": {"command": "python -c print(2+2)"}})
        assert r.ok, f"沙箱 bash 应成功：{r.error}"
        assert r.data.get("sandboxed") is True, "应标记 sandboxed"
        assert "4" in r.data["stdout"]


# ---- 2. circuit_breaker + tools ----

class TestCircuitBreakerTools:
    def test_continuous_failures_open_circuit(self):
        """同一工具连续失败达阈值 → 熔断 OPEN，后续调用被拒绝。"""
        bus = EventBus()
        tools_cap.register(bus)
        cb_cap.register(bus, threshold=3)
        # 连续 3 次失败（bash 执行不存在的命令）
        for _ in range(3):
            r = _dispatch(bus, "tools", "run",
                          {"name": "bash", "tool_op": "exec",
                           "args": {"command": "nonexistent_cmd_xyz"}})
            assert r.ok is False, "不存在的命令应失败"
        # 第 4 次：熔断已 OPEN，直接拒绝
        r = _dispatch(bus, "tools", "run",
                      {"name": "bash", "tool_op": "exec",
                       "args": {"command": "echo should_be_blocked"}})
        assert r.ok is False, "熔断后应拒绝"
        assert "熔断" in r.error, f"应提示熔断：{r.error}"

    def test_success_resets_failure_count(self):
        """成功后复位失败计数，不触发熔断。"""
        bus = EventBus()
        tools_cap.register(bus)
        cb_cap.register(bus, threshold=3)
        # 失败 2 次（未达阈值）
        for _ in range(2):
            _dispatch(bus, "tools", "run",
                      {"name": "bash", "tool_op": "exec",
                       "args": {"command": "nonexistent_cmd_xyz"}})
        # 成功 1 次 → 复位
        r = _dispatch(bus, "tools", "run",
                      {"name": "bash", "tool_op": "exec", "args": {"command": "echo ok"}})
        assert r.ok, f"成功应复位：{r.error}"
        # 再失败 2 次（未达阈值，因为已复位）
        for _ in range(2):
            _dispatch(bus, "tools", "run",
                      {"name": "bash", "tool_op": "exec",
                       "args": {"command": "nonexistent_cmd_xyz"}})
        r = _dispatch(bus, "tools", "run",
                      {"name": "bash", "tool_op": "exec", "args": {"command": "echo ok2"}})
        assert r.ok, "复位后未达阈值不应熔断"


# ---- 3. budget_guard + AgentLoop ----

class TestBudgetGuardLoop:
    def test_budget_exhausted_sets_done(self):
        """迭代达 100% 时，budget_guard 把 ctx.done 置 True（熔断治理）。"""
        aspect = bg_cap.BudgetGuardAspect(max_iterations=3, bus=None)
        bus = EventBus()
        bus.add_aspect(aspect)
        ctx = _ctx()
        action = Action(target="tools", op="run", payload={"name": "bash"})
        for _ in range(3):
            _publish(bus, "loop.iteration", {"ctx": ctx, "action": action})
        assert ctx.done, "预算耗尽应触发熔断（ctx.done=True）"
        assert any("最大迭代次数" in str(o) for o in ctx.observations), "应记录熔断原因"

    def test_budget_pressure_injected(self):
        """70% 时注入压力消息到 ctx.input_fragments['budget']。"""
        aspect = bg_cap.BudgetGuardAspect(max_iterations=10, bus=None)
        bus = EventBus()
        bus.add_aspect(aspect)
        ctx = _ctx()
        action = Action(target="tools", op="run", payload={"name": "bash"})
        for _ in range(7):  # 70%
            _publish(bus, "loop.iteration", {"ctx": ctx, "action": action})
        assert "budget" in ctx.input_fragments, "70% 时应注入 budget 压力消息"
        assert not ctx.done, "70% 不应熔断"


# ---- 4. permission + skill exec ----

class TestPermissionSkill:
    def test_dangerous_skill_args_blocked(self):
        """skill exec 的 args 含危险命令 → permission 拦截。"""
        bus = EventBus()
        skill_cap.register(bus)
        perm_cap.register(bus)
        r = _dispatch(bus, "skill", "exec",
                      {"skill": "devops/deploy-k8s", "script": "scripts/gen_deployment.py",
                       "args": {"command": "rm -rf /"}})
        assert r.ok is False, "危险命令应被拦截"
        assert "权限拒绝" in r.error

    def test_safe_skill_exec_passes(self):
        """skill exec 无危险命令 → 正常执行。"""
        bus = EventBus()
        skill_cap.register(bus)
        perm_cap.register(bus)
        r = _dispatch(bus, "skill", "exec",
                      {"skill": "devops/deploy-k8s", "script": "scripts/gen_deployment.py",
                       "args": {"name": "web", "replicas": 2}})
        assert r.ok, f"安全 skill 应执行：{r.error}"
        assert r.data["manifest"]["spec"]["replicas"] == 2


# ---- 5. sandbox + skill exec ----

class TestSandboxSkill:
    def test_sandbox_isolates_skill_script(self):
        """沙箱启用时，skill exec 被重定向到隔离子进程（sandboxed=True）。"""
        bus = EventBus()
        skill_cap.register(bus)
        sandbox_cap.register(bus, enabled=True)
        r = _dispatch(bus, "skill", "exec",
                      {"skill": "devops/deploy-k8s", "script": "scripts/gen_deployment.py",
                       "args": {"name": "web", "replicas": 2}})
        assert r.ok, f"沙箱 skill 应成功：{r.error}"
        assert r.data.get("sandboxed") is True, "应标记 sandboxed"


# ---- 6. memory + AgentLoop：记忆检索注入决策输入 ----

class MemoryReason(ReasonProvider):
    """决策者：第1轮读 memory 输入，第2轮 finish。"""

    def __init__(self):
        self.step = 0

    async def decide(self, ctx):
        self.step += 1
        if self.step == 1:
            return Action(target="echo", op="run", payload={"goal": ctx.goal})
        return Action(finish=True)


class TestMemoryAgentLoop:
    def test_memory_injected_as_input_node(self):
        """memory 作为 input_node 注入 AgentLoop，决策前检索记忆并聚合。"""
        bus = EventBus()
        # 预置一条记忆
        backend = memory_cap.DefaultMemoryBackend()
        backend.remember("用户偏好：喜欢用 Python 写脚本", session_id="s1", tags=["python"])
        memory_cap.register(bus, memory=backend)
        # echo handler 记录收到的 goal
        seen = {}

        def echo(d: Dispatch) -> CapabilityResult:
            seen["goal"] = d.payload.get("goal")
            return CapabilityResult(ok=True, data={"echo": True})

        bus.on("echo", echo)
        loop = AgentLoop(bus, MemoryReason(), input_nodes=[{"target": "memory", "slot": "user"}])
        ctx = asyncio.run(loop.run("用 Python 写个脚本", session_id="s1"))
        assert ctx.done, "任务应完成"
        # memory 输入应被聚合进 ctx.input_fragments
        assert "memory" in ctx.input_fragments, "memory 输入应被聚合"
        text = "".join(m.content for m in ctx.input_fragments["memory"])
        assert "Python" in text, f"记忆应注入：{text}"


# ---- 7. knowledge + AgentLoop：能力门面渐进披露 ----

class KnowledgeReason(ReasonProvider):
    """决策者：第1轮 knowledge find，第2轮 knowledge search，第3轮 finish。"""

    def __init__(self):
        self.step = 0

    async def decide(self, ctx):
        self.step += 1
        if self.step == 1:
            return Action(target="knowledge", op="find", payload={"keyword": ""})
        if self.step == 2:
            return Action(target="knowledge", op="search",
                          payload={"name": "libcore", "query": "AgentOS"})
        return Action(finish=True)


class TestKnowledgeAgentLoop:
    def test_knowledge_progressive_disclosure(self):
        """knowledge 作为能力门面：find 发现知识库 → search 召回片段。"""
        from libcore.plugins.resources.knowledge.documents import KBDocument
        bus = EventBus()
        store = knowledge_cap.InMemoryKnowledgeStore()
        store.upsert("libcore", [KBDocument(text="libcore 是 AgentOS 框架，支持插件化能力装配",
                                            metadata={"id": "d1"})])
        knowledge_cap.register(bus, store=store)
        loop = AgentLoop(bus, KnowledgeReason())
        ctx = asyncio.run(loop.run("AgentOS 是什么", session_id="s1"))
        assert ctx.done, "任务应完成"
        # 第1轮 find 应发现磁盘上的 libcore 知识库
        find_obs = ctx.observations[0]
        assert find_obs["ok"] is True, f"find 应成功：{find_obs}"
        assert find_obs["data"]["knowledge_bases"][0]["name"] == "libcore"
        # 第2轮 search 应召回含 AgentOS 的片段
        search_obs = ctx.observations[1]
        assert search_obs["ok"] is True, f"search 应成功：{search_obs}"
        assert search_obs["data"]["hits"][0]["text"].startswith("libcore")


# ---- 8. 横切面叠加：budget_guard + loop_governor ----

class TestAspectStacking:
    def test_budget_and_loop_governor_coexist(self):
        """预算护栏与死循环护栏叠加，互不干扰，各自生效。"""
        bus = EventBus()
        bg_cap.register(bus, max_iterations=5)
        lg_cap.register(bus, threshold=3)
        ctx = _ctx()
        action = Action(target="tools", op="run", payload={"name": "bash"})
        # 5 次成功重复调用：loop_governor 先触发（threshold=3），budget 未耗尽
        for _ in range(5):
            _publish(bus, "loop.iteration", {"ctx": ctx, "action": action})
            _publish(bus, "loop.result", {
                "ctx": ctx, "action": action,
                "result": CapabilityResult(ok=True, data={"ok": True}),
            })
        assert ctx.done, "死循环护栏应触发（threshold=3）"

    def test_permission_and_sandbox_coexist(self):
        """权限闸门与沙箱叠加：危险命令被 permission 拦截，安全命令被 sandbox 接管。"""
        bus = EventBus()
        tools_cap.register(bus)
        perm_cap.register(bus)
        sandbox_cap.register(bus, enabled=True)
        # 危险命令：permission 拦截（先于 sandbox）
        r = _dispatch(bus, "tools", "run",
                      {"name": "bash", "tool_op": "exec", "args": {"command": "rm -rf /"}})
        assert r.ok is False, "危险命令应被 permission 拦截"
        assert "权限拒绝" in r.error
        # 安全命令：sandbox 接管
        r = _dispatch(bus, "tools", "run",
                      {"name": "bash", "tool_op": "exec", "args": {"command": "python -c print(2+2)"}})
        assert r.ok, f"安全命令应执行：{r.error}"
        assert r.data.get("sandboxed") is True, "安全命令应被沙箱接管"
