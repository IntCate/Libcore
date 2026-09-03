"""验证 backend 通用性：同一个框架，分别喂「ollama 官方」和「langchain-ollama」两种 backend。

运行：python -m libcore.demos.demo_llm_ollama
依赖：本机 ollama 已在 11434 运行，且存在 qwen3:0.6b（支持 tools）。
"""
from __future__ import annotations

import asyncio
import datetime
import sys

from libcore.agent.backends.langchain import LangChainOllamaBackend
from libcore.agent.backends.ollama import OllamaBackend
from libcore.agent.lm_reason import LlmReasonProvider
from libcore.agent.loop import AgentLoop
from libcore.core.bus import EventBus
from libcore.core.event import CapabilityResult

MODEL = "qwen3:0.6b"
BASE = "http://localhost:11434"
SYSTEM = (
    "你是 libcore 的调度员。你唯一可调用的能力是 cap.now（返回当前时间）。\n"
    "请先调用 cap.now 获取时间；看到结果后立即结束，不要再调用任何工具。"
)
GOAL = "获取当前时间"
ATTEMPTS = 3


async def run_once(backend, *, tag: str) -> dict:
    """跑一次完整闭环，返回实际观察到的东西（不美化结果）。"""
    bus = EventBus()
    hits: list[dict] = []

    def cap_now(d):
        now = datetime.datetime.now().isoformat(timespec="seconds")
        hits.append({"op": d.op, "now": now})
        return CapabilityResult(ok=True, data={"now": now})

    bus.on("cap.now", cap_now)
    reason = LlmReasonProvider(backend, model=MODEL, system_prompt=SYSTEM, temperature=0.2)
    try:
        ctx = await AgentLoop(bus, reason).run({"goal": GOAL})
    except Exception as e:  # 如实记录异常，不吞
        return {"tag": tag, "ok": False, "err": f"{type(e).__name__}: {e}"}

    return {
        "tag": tag,
        "ok": bool(hits),
        "dispatch_hits": len(hits),
        "observed": len(ctx.observations),
        "done": ctx.done,
        "detail": hits,
    }


def report(name: str, make_backend):
    print(f"\n===== backend 类型：{name} =====")
    best = None
    for i in range(ATTEMPTS):
        async def _go():
            return await run_once(make_backend(), tag=name)

        r = asyncio.run(_go())
        print(f"  试跑 {i+1}: ok={r.get('ok')} dispatch命中={r.get('dispatch_hits')} "
              f"observations={r.get('observed')} done={r.get('done')} "
              f"{r.get('detail', r.get('err'))}")
        if r.get("done") and best is None:
            best = r
    return best


def main() -> int:
    print(f"模型: {MODEL}  base_url: {BASE}\n")
    official = report("ollama 官方（httpx 直连）", lambda: OllamaBackend(BASE))
    lc = report("langchain-ollama", lambda: LangChainOllamaBackend(model=MODEL, base_url=BASE))
    print("\n===== 结论 =====")
    print(f"  ollama 官方  : {'✅ 框架被 backend 驱动，dispatch 命中' if official else '❌ 未命中'}")
    print(f"  langchain    : {'✅ 框架被 backend 驱动，dispatch 命中' if lc else '❌ 未命中'}")
    ok = bool(official) and bool(lc)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())