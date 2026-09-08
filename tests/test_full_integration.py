"""全链路组合联动测试：存储 + prompt + context + session + 内核 + 横切面叠加。

覆盖现有单测未覆盖的**深度组合**（各种穿插、各种组件组合）：
1. SQLite 存储 + prompt + context + session + 内核：多轮对话历史持久化回灌；
2. JSON 文件存储 + prompt + context + session + 内核：文件持久化多轮对话；
3. 存储 + 横切面叠加（audit/telemetry/tracing/permission/retry/budget_guard）：
   全链路执行 + 审计 hash 链 + 遥测聚合 + 权限拦截 + 重试 + 预算熔断；
4. 端到端工具执行闭环：reason → dispatch → 横切面 → 结果 → 出站。

不依赖 pytest-asyncio：用 asyncio.run() 在同步测试函数内跑异步闭环。
"""
from __future__ import annotations

import asyncio
import json

from libcore.kernel.bus import EventBus, Dispatch, CapabilityResult, Notice
from libcore.kernel.agent import AgentLoop, ReasonProvider, Action
from libcore.plugins.capabilities import context as context_cap
from libcore.plugins.capabilities import prompt as prompt_cap
from libcore.plugins.capabilities import session as session_cap
from libcore.plugins.capabilities import tool as tools_cap
from libcore.plugins.aspects.audit import AuditAspect
from libcore.plugins.aspects import telemetry as tele_cap
from libcore.plugins.aspects import tracing as trace_cap
from libcore.plugins.aspects import permission as perm_cap
from libcore.plugins.aspects import retry as retry_cap
from libcore.plugins.aspects import budget_guard as bg_cap
from libcore.storage.sqlite_store import SqliteDataStore
from libcore.storage.json_file_store import JsonFileSessionStore


class CaptureReason(ReasonProvider):
    """决策者：捕获注入的 input_fragments（system/user），然后 finish。"""

    def __init__(self):
        self.seen: list = []
        self.seen_goal: str = ""

    async def decide(self, ctx):
        for target in ("prompt", "context"):
            for frag in ctx.input_fragments.get(target, []):
                self.seen.append((frag.role, frag.content))
        self.seen_goal = str(ctx.goal.get("goal", "")) if isinstance(ctx.goal, dict) else str(ctx.goal)
        return Action(finish=True)


def _run_chat(bus, reason, goal, session_id=None, input_nodes=None):
    loop = AgentLoop(bus, reason, input_nodes=input_nodes)
    return asyncio.run(loop.run({"goal": goal}, session_id=session_id))


def _build_chat_bus(backend, *, with_prompt=True):
    """装配 prompt + context + session 后端 + 横切面（audit/telemetry/tracing）。"""
    bus = EventBus()
    if with_prompt:
        prompt_cap.register(bus, segment=lambda d: "你是测试助手。\n风格：简洁。")
    context_cap.register(bus, backend=backend)
    return bus


# ---- 1. SQLite 存储 + prompt + context + session + 内核 ----

class TestSqliteFullChat:
    def test_sqlite_multi_turn_persisted(self, tmp_path):
        """SQLite 存储：多轮对话历史持久化，第 2 轮回灌给决策者。"""
        db = SqliteDataStore(str(tmp_path / "chat.db"))
        bus = _build_chat_bus(db)
        session_id = "sess-sql-1"

        # 第 1 轮：写入历史
        db.append_message(session_id, "user", "我叫小明")
        db.append_message(session_id, "assistant", "你好，小明")

        reason = CaptureReason()
        _run_chat(bus, reason, "我叫什么？", session_id=session_id, input_nodes=[
            {"target": "context", "slot": "user"},
            {"target": "prompt", "slot": "system"},
        ])
        roles = [r for r, _ in reason.seen]
        texts = [c for _, c in reason.seen]
        assert roles[0] == "system", f"system 应最先：{reason.seen}"
        assert any("我叫小明" in t for t in texts), f"应回灌历史：{texts}"
        assert any("你好，小明" in t for t in texts), f"应回灌 assistant：{texts}"
        assert any("我叫什么？" in t for t in texts), f"应含当前目标：{texts}"
        db.close()

    def test_sqlite_persists_across_instances(self, tmp_path):
        """SQLite 存储跨实例持久化：新连接能读到旧会话。"""
        db_path = str(tmp_path / "chat2.db")
        db1 = SqliteDataStore(db_path)
        db1.append_message("sess-sql-2", "user", "跨实例历史")
        db1.close()

        db2 = SqliteDataStore(db_path)
        bus = _build_chat_bus(db2)
        reason = CaptureReason()
        _run_chat(bus, reason, "继续", session_id="sess-sql-2", input_nodes=[
            {"target": "context", "slot": "user"},
            {"target": "prompt", "slot": "system"},
        ])
        texts = [c for _, c in reason.seen]
        assert any("跨实例历史" in t for t in texts), f"新实例应读到旧历史：{texts}"
        db2.close()


# ---- 2. JSON 文件存储 + prompt + context + session + 内核 ----

class TestJsonFullChat:
    def test_json_multi_turn_persisted(self, tmp_path):
        """JSON 文件存储：多轮对话历史持久化，第 2 轮回灌。"""
        store = JsonFileSessionStore(str(tmp_path / "sessions"))
        bus = _build_chat_bus(store)
        session_id = "sess-json-1"

        store.append_message(session_id, "user", "我叫小明")
        store.append_message(session_id, "assistant", "你好，小明")

        reason = CaptureReason()
        _run_chat(bus, reason, "我叫什么？", session_id=session_id, input_nodes=[
            {"target": "context", "slot": "user"},
            {"target": "prompt", "slot": "system"},
        ])
        roles = [r for r, _ in reason.seen]
        texts = [c for _, c in reason.seen]
        assert roles[0] == "system", f"system 应最先：{reason.seen}"
        assert any("我叫小明" in t for t in texts), f"应回灌历史：{texts}"
        assert any("你好，小明" in t for t in texts), f"应回灌 assistant：{texts}"

    def test_json_session_isolation(self, tmp_path):
        """JSON 文件存储：不同会话历史隔离，互不污染。"""
        store = JsonFileSessionStore(str(tmp_path / "sessions2"))
        store.append_message("sess-a", "user", "A的历史")
        bus = _build_chat_bus(store)
        reason_b = CaptureReason()
        _run_chat(bus, reason_b, "继续", session_id="sess-b", input_nodes=[
            {"target": "context", "slot": "user"},
            {"target": "prompt", "slot": "system"},
        ])
        texts_b = [c for _, c in reason_b.seen]
        assert not any("A的历史" in t for t in texts_b), f"会话 B 不应看到 A 历史：{texts_b}"


# ---- 3. 存储 + 横切面叠加：全链路执行 + 审计 + 遥测 + 权限 + 重试 + 预算 ----

class ToolReason(ReasonProvider):
    """决策者：第1轮调 bash 工具，第2轮 finish。"""

    def __init__(self):
        self.step = 0

    async def decide(self, ctx):
        self.step += 1
        if self.step == 1:
            return Action(target="tools", op="run",
                          payload={"name": "bash", "tool_op": "exec",
                                   "args": {"command": ".venv\\Scripts\\python.exe -c print(2+2)"}})
        return Action(finish=True)


class TestStorageWithAspects:
    def test_full_chain_with_all_aspects(self, tmp_path):
        """存储 + 全横切面叠加：工具执行闭环 + 审计 hash 链 + 遥测聚合 + 权限拦截。"""
        bus = EventBus()
        # 能力：tools + prompt + context + session(SQLite)
        tools_cap.register(bus)
        prompt_cap.register(bus, segment=lambda d: "你是测试助手。")
        db = SqliteDataStore(str(tmp_path / "chain.db"))
        context_cap.register(bus, backend=db)
        # 横切面：audit + telemetry + tracing + permission
        audit_path = tmp_path / "chain_audit.jsonl"
        audit = AuditAspect(path=str(audit_path))
        bus.add_aspect(audit)
        tele = tele_cap.TelemetryAspect()
        bus.add_aspect(tele)
        tracing = trace_cap.TracingAspect()
        bus.add_aspect(tracing)
        perm_cap.register(bus)

        # 跑工具执行闭环
        loop = AgentLoop(bus, ToolReason(), input_nodes=[
            {"target": "context", "slot": "user"},
            {"target": "prompt", "slot": "system"},
        ])
        ctx = asyncio.run(loop.run({"goal": "计算 2+2"}, session_id="sess-chain-1"))
        assert ctx.done, "任务应完成"

        # 工具执行结果（observations 记录 ok/data/error，无 target 字段）
        assert ctx.observations, "应有执行观察"
        tool_obs = [o for o in ctx.observations if isinstance(o.get("data"), dict)
                    and "stdout" in o.get("data", {})]
        assert tool_obs, f"应有工具执行记录：{ctx.observations}"
        assert tool_obs[0]["ok"] is True, f"工具应成功：{tool_obs[0]}"
        assert "4" in tool_obs[0]["data"]["stdout"], f"应输出 4：{tool_obs[0]['data']}"

        # audit：应记录 tools 执行（append-only + hash 链）
        assert audit_path.exists(), "audit 文件应生成"
        lines = audit_path.read_text(encoding="utf-8").strip().splitlines()
        assert lines, "audit 应有记录"
        tool_audits = [json.loads(l) for l in lines if json.loads(l)["target"] == "tools"]
        assert tool_audits, "audit 应含 tools 执行记录"
        assert tool_audits[0]["ok"] is True
        for i in range(1, len(lines)):
            prev = json.loads(lines[i - 1])
            cur = json.loads(lines[i])
            assert cur["prev_hash"] == prev["hash"], "audit hash 链断裂"

        # telemetry：应聚合 tools 指标
        summary = tele.summary()
        assert "dispatch.tools" in summary, f"telemetry 缺 tools 指标：{list(summary)}"
        assert summary["dispatch.tools"]["calls"] >= 1

        # tracing：应记录 tools 调度
        targets = [rec["target"] for rec in tracing.records]
        assert "tools" in targets, f"tracing 缺 tools 调度：{targets}"

        # permission：危险命令被拦截
        deny = asyncio.run(bus.dispatch(Dispatch(
            target="tools", op="run",
            payload={"name": "bash", "tool_op": "exec", "args": {"command": "rm -rf /"}},
        )))
        assert deny.ok is False, "危险命令应被 permission 拦截"
        assert "权限拒绝" in deny.error
        db.close()

    def test_retry_and_budget_guard_coexist(self, tmp_path):
        """重试 + 预算护栏叠加：重试接管执行，预算熔断治理循环。"""
        bus = EventBus()
        tools_cap.register(bus)
        # 重试：接管执行，失败重试
        retry_cap.register(bus, retries=2, retry_delay=0.01)
        # 预算护栏：迭代上限
        bg_cap.register(bus, max_iterations=3)

        # 决策者：一直调 bash 直到预算熔断
        class LoopReason(ReasonProvider):
            async def decide(self, ctx):
                return Action(target="tools", op="run",
                              payload={"name": "bash", "tool_op": "exec",
                                       "args": {"command": "echo hi"}})

        loop = AgentLoop(bus, LoopReason(), input_nodes=[])
        ctx = asyncio.run(loop.run({"goal": "循环"}, session_id="sess-budget-1"))
        assert ctx.done, "预算耗尽应熔断"
        assert any("最大迭代次数" in str(o) for o in ctx.observations), "应记录熔断原因"

    def test_retry_recovers_transient_failure(self, tmp_path):
        """重试：handler 首次抛异常，重试后成功。"""
        bus = EventBus()
        attempts = {"n": 0}

        def flaky(d: Dispatch) -> CapabilityResult:
            attempts["n"] += 1
            if attempts["n"] == 1:
                raise RuntimeError("transient failure")
            return CapabilityResult(ok=True, data={"ok": True})

        bus.on("flaky", flaky)
        retry_cap.register(bus, retries=2, retry_delay=0.01)
        r = asyncio.run(bus.dispatch(Dispatch(target="flaky", op="run", payload={})))
        assert r.ok, f"重试后应成功：{r.error}"
        assert attempts["n"] == 2, f"应重试 1 次：{attempts['n']}"
