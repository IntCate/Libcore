"""端到端任务测试：发布一个真实任务，走通 AgentOS 完整链路。

覆盖链路（对应验收目标 8 个子系统）：
1. 装配：EventBus + 能力插件（mcp/tools/cli）+ 横切面（permission/audit/telemetry/tracing）；
2. 通道入站：CLI 通道 ingest 一条消息 → 广播 channel.inbound；
3. 常驻内核：wire_inbound_to_kernel 把入站接到 ResidentKernel；
4. AgentLoop：E2EReason 决策者驱动 reason→dispatch→observe 闭环；
5. 渐进披露：mcp find（发现 calculator）→ mcp run（执行 add）；
6. 能力执行：calculator 计算 3+4=7；
7. 横切面：permission 拦截危险命令、audit 记录执行、telemetry 聚合指标、tracing 还原轨迹；
8. 出站回复：cli send 经 Channel.reply 送回原会话。

audit 用临时文件路径，避免污染工作区。
"""
from __future__ import annotations

import asyncio
import json

from libcore.kernel.bus import EventBus, Notice, Dispatch, CapabilityResult
from libcore.kernel.agent import ReasonProvider, Action, ResidentKernel
from libcore.channels import ChannelRegistry, InboundMessage, CliChannel
from libcore.channels.bridge import wire_inbound_to_kernel
from libcore.plugins.capabilities import mcp as mcp_cap
from libcore.plugins.capabilities import tool as tools_cap
from libcore.plugins.capabilities import cli as cli_cap
from libcore.plugins.aspects import permission as perm_cap
from libcore.plugins.aspects import telemetry as tele_cap
from libcore.plugins.aspects import tracing as trace_cap
from libcore.plugins.aspects.audit import AuditAspect


class E2EReason(ReasonProvider):
    """端到端决策者：模拟"计算 3+4 并回复"任务。

    第1轮 mcp find（渐进披露发现 calculator）→ 第2轮 mcp run add（执行计算）
    → 第3轮 cli send（回复结果）→ 第4轮 finish。
    """

    def __init__(self):
        self.step = 0

    async def decide(self, ctx):
        self.step += 1
        if self.step == 1:
            return Action(target="mcp", op="find", payload={"keyword": "calc"})
        if self.step == 2:
            return Action(target="mcp", op="run",
                          payload={"name": "calculator", "tool_op": "add",
                                   "args": {"a": 3, "b": 4}})
        if self.step == 3:
            return Action(target="cli", op="send",
                          payload={"channel_id": "cli:stdin", "text": "3+4=7"})
        return Action(finish=True)


class TestEndToEndTask:
    def test_full_chain(self, tmp_path):
        """发布"计算 3+4"任务，走通 装配→入站→内核→闭环→披露→执行→横切面→出站。"""
        asyncio.run(self._run(tmp_path))

    async def _run(self, tmp_path):
        # ---- 1. 装配：总线 + 能力 + 横切面 ----
        bus = EventBus()
        mcp_cap.register(bus)
        tools_cap.register(bus)
        cli_cap.register(bus, channels=None)  # 出站走默认 sink（print），下面单独验证 reply
        perm_cap.register(bus)
        tele = tele_cap.TelemetryAspect()
        bus.add_aspect(tele)
        tracing = trace_cap.TracingAspect()
        bus.add_aspect(tracing)
        audit_path = tmp_path / "audit.jsonl"
        audit = AuditAspect(path=str(audit_path))
        bus.add_aspect(audit)

        # ---- 2. 通道入站：CLI 通道 + 注册表 ----
        channels = ChannelRegistry()
        cli_sent: list = []

        def cli_sink(text: str) -> None:
            cli_sent.append(text)

        cli_ch = CliChannel(bus, sink=cli_sink)
        channels.register(cli_ch)
        # 重新注册 cli 能力节点，注入 channels 使 send 走 Channel.reply
        cli_cap.register(bus, channels=channels)

        # ---- 3. 常驻内核 + 薄桥接 ----
        kernel = ResidentKernel(bus, E2EReason(), max_concurrency=2)
        wire_inbound_to_kernel(bus, kernel)
        serve_task = asyncio.create_task(kernel.serve())
        await asyncio.sleep(0.05)
        assert kernel.is_running

        # ---- 4. 入站：CLI 消息 ----
        cli_ch.ingest(InboundMessage(channel_id=cli_ch.channel_id, kind="cli",
                                     platform="cli", user_id="u", text="计算 3+4"))
        await asyncio.sleep(0.3)

        # ---- 5. 出站回复：cli send 经 Channel.reply 送回原会话 ----
        assert cli_sent, "CLI 应收到回复"
        assert cli_sent[0] == "3+4=7", f"回复内容不符：{cli_sent[0]}"

        # ---- 6. 内核执行结果 ----
        assert kernel.results, "内核应有执行结果"
        r = kernel.results[0]
        assert r["done"] is True, "任务应完成"
        assert r["steps"] >= 3, f"应至少 3 步（find/run/send），实际 {r['steps']}"

        # ---- 7. 横切面验证 ----
        # tracing：应记录 mcp find / mcp run / cli send 三步调度
        targets = [rec["target"] for rec in tracing.records]
        assert "mcp" in targets and "cli" in targets, f"tracing 缺调度：{targets}"
        mcp_run = [rec for rec in tracing.records if rec["target"] == "mcp" and rec["op"] == "run"]
        assert mcp_run, "tracing 应含 mcp run"
        assert mcp_run[0]["ok"] is True, "mcp run 应成功"
        # mcp run 的 data 应含 _internal 子调用细节（calculator add）
        assert mcp_run[0]["data"].get("_internal", {}).get("sub_target") == "calculator"

        # telemetry：应聚合 mcp 与 cli 指标
        summary = tele.summary()
        assert "dispatch.mcp" in summary, f"telemetry 缺 mcp 指标：{list(summary)}"
        assert summary["dispatch.mcp"]["calls"] >= 2, "mcp 应至少被调 2 次（find+run）"
        assert summary["dispatch.mcp"]["success_rate"] == 1.0

        # audit：应记录 mcp run 执行（append-only + hash 链）
        assert audit_path.exists(), "audit 文件应生成"
        lines = audit_path.read_text(encoding="utf-8").strip().splitlines()
        assert lines, "audit 应有记录"
        mcp_audits = [json.loads(l) for l in lines if json.loads(l)["target"] == "mcp"]
        assert mcp_audits, "audit 应含 mcp 执行记录"
        assert mcp_audits[0]["action_type"] == "mcp_run"
        assert mcp_audits[0]["ok"] is True
        # hash 链：每条 prev_hash 等于上一条 hash
        for i in range(1, len(lines)):
            prev = json.loads(lines[i - 1])
            cur = json.loads(lines[i])
            assert cur["prev_hash"] == prev["hash"], "audit hash 链断裂"

        # ---- 8. permission 拦截：危险命令被拒 ----
        deny = await bus.dispatch(Dispatch(
            target="tools", op="run",
            payload={"name": "bash", "tool_op": "exec", "args": {"command": "rm -rf /"}},
        ))
        assert deny.ok is False, "危险命令应被 permission 拦截"
        assert "权限拒绝" in deny.error

        # ---- 9. 优雅关闭 ----
        await kernel.shutdown()
        await serve_task
        assert kernel.is_running is False
