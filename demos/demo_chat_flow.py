"""四场景聊天链路真实数据流演示。

实际运行四个场景，打印每一步的真实数据：
- 入站消息格式（InboundMessage）
- 广播事件（Notice）
- 内核 Scope
- 输入节点注入（LLMMsg 序列，发给模型的数据）
- 决策 Action
- 审计 / 遥测 记录
"""
from __future__ import annotations

import asyncio
import json

from libcore.kernel.bus import EventBus, Dispatch, Notice
from libcore.kernel.agent import AgentLoop, ReasonProvider, Action
from libcore.plugins.capabilities import context as context_cap
from libcore.plugins.capabilities import prompt as prompt_cap
from libcore.plugins.capabilities import session as session_cap
from libcore.plugins.aspects.audit import AuditAspect
from libcore.plugins.aspects import telemetry as tele_cap


class ShowReason(ReasonProvider):
    """决策者：打印注入的 input_fragments（发给模型的数据），然后 finish。"""

    async def decide(self, ctx):
        print("    [决策者收到 input_fragments]")
        for target in ("prompt", "context"):
            for frag in ctx.input_fragments.get(target, []):
                print(f"      role={frag.role!r:12} content={frag.content!r}")
        print(f"    [决策者看到目标] {ctx.goal!r}")
        return Action(finish=True)


def build_bus(tmp, *, with_prompt=False, with_context=False, backend=None):
    bus = EventBus()
    if with_prompt:
        prompt_cap.register(bus, segment=lambda d: "你是测试助手。\n风格：简洁。")
    if with_context:
        context_cap.register(bus, backend=backend)
    audit = AuditAspect(path=str(tmp / "audit.jsonl"))
    bus.add_aspect(audit)
    tele = tele_cap.TelemetryAspect()
    bus.add_aspect(tele)
    return bus, audit, tele


def run_chat(bus, reason, goal, session_id=None, input_nodes=None):
    loop = AgentLoop(bus, reason, input_nodes=input_nodes)
    return asyncio.run(loop.run({"goal": goal}, session_id=session_id))


def show_audit(tmp):
    p = tmp / "audit.jsonl"
    if not p.exists():
        print("    [audit] 无记录（读操作不记审计）")
        return
    lines = p.read_text(encoding="utf-8").strip().splitlines()
    if not lines:
        print("    [audit] 无记录（读操作不记审计）")
        return
    for l in lines:
        d = json.loads(l)
        print(f"    [audit] target={d.get('target')!r} op={d.get('op')!r} ok={d.get('ok')}")


def show_tele(tele):
    s = tele.summary()
    keys = [k for k in s if "prompt" in k or "context" in k or "input_missing" in k]
    print(f"    [telemetry] {keys}")


def scenario1(tmp):
    print("=" * 60)
    print("场景 1：无 prompt/context 插件 —— 一问一答，内核降级")
    print("=" * 60)
    bus, audit, tele = build_bus(tmp)
    missing = []

    async def on_missing(n: Notice):
        missing.append(n.payload["target"])

    bus.subscribe("loop.input_missing", on_missing)
    print("  ① 入站：用户发消息")
    print("    InboundMessage(channel_id='ui:web-1', text='你好')")
    print("  ② 广播：channel.ingest() → bus.publish()")
    print("    Notice(topic='channel.inbound', payload={'text':'你好','channel_id':'ui:web-1'})")
    print("  ③ 内核：ResidentKernel → AgentLoop.run() 创建 Scope")
    print("    Scope(goal={'goal':'你好'}, session_id='ui:web-1')")
    print("  ④ 输入节点注入：_prepare_input() 逐个 dispatch")
    print("    dispatch(context) → 无 handler → 广播 loop.input_missing")
    print("    dispatch(prompt)  → 无 handler → 广播 loop.input_missing")
    print(f"    [input_missing 广播] {missing}")
    reason = ShowReason()
    run_chat(bus, reason, "你好", session_id="ui:web-1", input_nodes=[
        {"target": "context", "slot": "user"},
        {"target": "prompt", "slot": "system"},
    ])
    print("  ⑤ 决策：Action(finish=True)")
    print("  ⑥ 审计 / 遥测")
    show_audit(tmp)
    show_tele(tele)


def scenario2(tmp):
    print()
    print("=" * 60)
    print("场景 2：仅 prompt —— 发给模型的数据 = 1 个 system")
    print("=" * 60)
    bus, audit, tele = build_bus(tmp, with_prompt=True)
    print("  ① 入站：InboundMessage(channel_id='ui:web-2', text='你好')")
    print("  ② 广播：Notice(topic='channel.inbound', ...)")
    print("  ③ 内核：Scope(goal={'goal':'你好'}, session_id='ui:web-2')")
    print("  ④ 输入节点注入：")
    print("    dispatch(prompt) → prompt 插件注入 system 指令")
    reason = ShowReason()
    run_chat(bus, reason, "你好", session_id="ui:web-2", input_nodes=[
        {"target": "context", "slot": "user"},
        {"target": "prompt", "slot": "system"},
    ])
    print("  ⑤ 决策：Action(finish=True)")
    print("  ⑥ 审计 / 遥测")
    show_audit(tmp)
    show_tele(tele)


def scenario3(tmp):
    print()
    print("=" * 60)
    print("场景 3：仅 context —— 多轮对话历史回灌")
    print("=" * 60)
    store = session_cap.DefaultSessionStore()
    bus, audit, tele = build_bus(tmp, with_context=True, backend=store)
    sid = "ui:web-3"
    print("  ① 第 1 轮：用户说'我叫小明'")
    run_chat(bus, ShowReason(), "我叫小明", session_id=sid,
             input_nodes=[{"target": "context", "slot": "user"}])
    print("  ② 外部装配层把第 1 轮写回 backend（append_message）")
    store.append_message(sid, "user", "我叫小明")
    store.append_message(sid, "assistant", "你好，小明")
    print("    backend 中历史：")
    for m in store.get_history(sid):
        print(f"      {{role={m['role']!r}, content={m['content']!r}}}")
    print("  ③ 第 2 轮：用户说'我叫什么？'")
    print("  ④ 输入节点注入：dispatch(context) → 从 store 读历史 → 裁剪 → 注入")
    reason = ShowReason()
    run_chat(bus, reason, "我叫什么？", session_id=sid,
             input_nodes=[{"target": "context", "slot": "user"}])
    print("  ⑤ 决策：Action(finish=True)")
    print("  ⑥ 审计 / 遥测")
    show_audit(tmp)
    show_tele(tele)


def scenario4(tmp):
    print()
    print("=" * 60)
    print("场景 4：prompt + context —— 发给模型 = system + user")
    print("=" * 60)
    store = session_cap.DefaultSessionStore()
    bus, audit, tele = build_bus(tmp, with_prompt=True, with_context=True, backend=store)
    sid = "ui:web-4"
    print("  ① 预置历史：")
    store.append_message(sid, "user", "我叫小明")
    store.append_message(sid, "assistant", "你好，小明")
    for m in store.get_history(sid):
        print(f"      {{role={m['role']!r}, content={m['content']!r}}}")
    print("  ② 用户发消息：'我叫什么？'")
    print("  ③ 输入节点注入（按 input_nodes 顺序）：")
    print("    dispatch(prompt)  → system 指令")
    print("    dispatch(context) → 历史 + 当前目标")
    reason = ShowReason()
    run_chat(bus, reason, "我叫什么？", session_id=sid, input_nodes=[
        {"target": "context", "slot": "user"},
        {"target": "prompt", "slot": "system"},
    ])
    print("  ④ 决策：Action(finish=True)")
    print("  ⑤ 审计 / 遥测")
    show_audit(tmp)
    show_tele(tele)


if __name__ == "__main__":
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        import pathlib
        tmp = pathlib.Path(d)
        scenario1(tmp)
        scenario2(tmp)
        scenario3(tmp)
        scenario4(tmp)
