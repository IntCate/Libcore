"""常驻协作内核 demo：验证 7×24 待机 + 事件驱动唤醒 + 优雅关闭。

验证点：
1. 7×24 常驻：serve() 启动后无请求时挂起待机（不轮询、不造活）；
2. 事件驱动唤醒：外部 submit(goal) 点名投递，内核收到即用 AgentLoop 执行；
3. 复用现有调度环：AgentLoop + ReasonProvider 零改动；
4. 并发上限：多个请求并发执行，受 max_concurrency 约束；
5. 优雅关闭：shutdown() 停止接收新请求、排空在途任务、广播 resident.stopped。

运行： python demos/demo_resident.py
"""
from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.basicConfig(level=logging.INFO, format="%(message)s")

from libcore.kernel.bus import Dispatch, Notice, CapabilityResult, EventBus, Scope
from libcore.kernel.agent import ReasonProvider, Action, ResidentKernel


# ---- 能力：协作请求要调用的目标 ----

def tool_pdf_parse(d: Dispatch) -> CapabilityResult:
    print(f"   >> tool.pdf.parse  file={d.payload.get('file')}")
    return CapabilityResult(ok=True, data={"pages": 300, "chunks": 42})


def storage_vector_upsert(d: Dispatch) -> CapabilityResult:
    print(f"   >> storage.vector.upsert  index={d.payload.get('index')}")
    return CapabilityResult(ok=True, data={"stored": True})


# ---- 决策者：从可呼叫清单发现能力（复用 demo.py 的 ManifestReason 思路）----

class ManifestReason(ReasonProvider):
    """无状态决策者：从"可呼叫清单"里发现能力，target 动态取出而非写死。

    无状态保证并发请求各自独立完整执行（不共享决策进度）。
    """

    def _find(self, ctx: Scope, keyword: str):
        for cap in ctx.capabilities:
            if keyword in (cap.get("description") or ""):
                return cap["target"]
        return None

    async def decide(self, ctx):
        step = len(ctx.observations)
        if step == 0:
            t = self._find(ctx, "解析")
            if t:
                return Action(target=t, op="parse", payload={"file": "doc.pdf"})
            return Action(finish=True)
        if step == 1:
            t = self._find(ctx, "入库")
            if t:
                return Action(target=t, op="upsert", payload={"index": "kb"})
            return Action(finish=True)
        return Action(finish=True)


async def main() -> None:
    print("=== 常驻协作内核：7×24 待机 + 事件驱动唤醒 + 优雅关闭 ===")

    # 装配：总线 + 能力 + 决策者
    bus = EventBus()
    bus.on("tool.pdf", tool_pdf_parse, meta={"description": "解析 PDF 提取文本与分块", "ops": ["parse"]})
    bus.on("storage.vector", storage_vector_upsert, meta={"description": "向量化入库文档", "ops": ["upsert"]})

    # 订阅生命周期广播，验证常驻内核的启动/完成/停止信号
    events = []
    async def on_resident(n: Notice):
        events.append(n.topic)
    for topic in ("resident.started", "resident.completed", "resident.stopped"):
        bus.subscribe(topic, on_resident)

    kernel = ResidentKernel(bus, ManifestReason(), max_concurrency=2)

    # 启动常驻主循环（7×24 待机）
    serve_task = asyncio.create_task(kernel.serve())
    await asyncio.sleep(0.05)   # 让主循环进入待机
    print(f"   常驻已启动: is_running={kernel.is_running}  pending={kernel.pending}")

    # 事件驱动唤醒：外部点名投递协作请求（每个请求独立 reason 实例）
    print("\n--- 事件驱动唤醒：submit 协作请求 ---")
    for _ in range(4):
        kernel.submit({"goal": "归档 PDF 到知识库"}, reason=ManifestReason())
    print(f"   已投递 4 个请求，待处理 pending={kernel.pending}")

    # 等待并发执行完成（max_concurrency=2，4 个请求分两批）
    await asyncio.sleep(0.3)
    print(f"   执行完成，results={kernel.results}")

    # 优雅关闭
    print("\n--- 优雅关闭 ---")
    await kernel.shutdown()
    await serve_task
    print(f"   已停止: is_running={kernel.is_running}")
    print(f"   生命周期广播: {events}")
    print(f"   完成请求数: {len(kernel.results)}")

    print("\n=== 便捷装配：Kernel.bootstrap_resident → serve/submit/shutdown ===")
    from libcore.kernel import Kernel as K
    sk = K.bootstrap_resident(
        targets={
            "tool.pdf": (tool_pdf_parse, {"description": "解析 PDF 提取文本与分块"}),
            "storage.vector": (storage_vector_upsert, {"description": "向量化入库文档"}),
        },
        reason=ManifestReason(),
        max_concurrency=2,
    )
    sk_task = asyncio.create_task(sk.serve())
    await asyncio.sleep(0.03)
    sk.submit({"goal": "归档 PDF 到知识库"}, reason=ManifestReason())
    await asyncio.sleep(0.1)
    await sk.shutdown()
    await sk_task
    print(f"   resident 已装配: {sk.resident is not None}  完成: {sk.resident.results}")

    print("\n=== 开箱即用：Agent.serve() 常驻入口 ===")
    from libcore.api import Agent

    # 说明：Agent 默认配 LLM 决策者（需真实模型）。这里仅验证 serve() 装配出
    # 一个挂载了相同 bus 与 reason 的 ResidentKernel，不触发真实执行。
    agent = Agent(backend="ollama", model="qwen3:0.6b")

    @agent.capability("cap.ping", description="返回 pong 确认存活")
    def ping(payload):
        return {"msg": "pong"}

    rk = agent.serve(max_concurrency=2)
    # 复用同一总线与决策者：注册的能力对常驻内核可见
    print(f"   Agent.serve() 返回 ResidentKernel，复用 bus(reason 同源): {rk.bus is agent._bus}")
    print(f"   常驻内核可见能力: {sorted(c['target'] for c in rk.bus.manifest())}")


if __name__ == "__main__":
    asyncio.run(main())
