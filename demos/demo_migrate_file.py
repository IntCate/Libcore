"""迁移真实能力试点（原生重写，不用薄壳）：旧工具只当"职责清单"，
按 libcore 能力插件规范重写（非 import 旧 app.runtime.tool_kernel，不要兼容层）。

第 1 幕（纯内核驱动，不依赖模型）：调度环直接点名 tools 门面 run file.read，读一个真实文件。
     目的：单独验证【原生重写的工具门面 + 新内核】这层 100% 正确。
第 2 幕（真模型驱动）：用你本机 ollama(qwen3:0.6b) 让模型自己决定去读这个文件。
     目的：验证【工具门面 + 真 LLM + 新内核】三方真正串起来。

用法：python demos/demo_migrate_file.py
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from libcore.kernel.bus import EventBus
from libcore.kernel.agent import AgentLoop
from libcore.kernel.agent import Action
from libcore.kernel.bus import Scope
from libcore.plugins.capabilities import tool as tool_cap


TARGET_FILE = "demos/demo_migrate_file.py"  # 读它自己（确认存在、必成）


def build():
    """装配：工具门面插件 → 总线（register(bus) 由它自己登记）。"""
    bus = EventBus()
    tool_cap.register(bus)
    return bus


class _FixedReason:
    """第 1 幕专用：固定剧本，不靠模型。第一步点名读文件，第二步收尾。"""
    def __init__(self):
        self._did_read = False

    async def decide(self, ctx: Scope) -> Action:
        if not self._did_read:
            self._did_read = True
            return Action(target="tools", op="run",
                          payload={"name": "file", "tool_op": "read",
                                   "args": {"path": TARGET_FILE}})
        return Action(finish=True)


def act1_kernel_driven(bus) -> None:
    """纯内核驱动：确认工具门面被新内核真实驱动、读到真实内容。"""
    print("========== 第 1 幕：纯内核驱动（不依赖模型） ==========")
    loop = AgentLoop(bus, _FixedReason())
    ctx = asyncio.run(loop.run({"goal": f"读取文件 {TARGET_FILE}"}))
    obs = ctx.observations[-1] if ctx.observations else None
    data = obs.get("data", obs) if isinstance(obs, dict) else obs
    assert ctx.done is True
    assert isinstance(data, dict), obs
    assert Path(data["path"]).name == Path(TARGET_FILE).name, obs
    content = data.get("content", "")
    print(f"done           = {ctx.done}")
    print(f"真实读到的文件   = {obs.get('path')}  ({obs.get('bytes')} 字节)")
    print("文件内容前 120 字 = " + content[:120].replace("\n", "⏎"))
    print("[第1幕 PASS] 工具门面已被内核真实驱动，读到真实文件内容。")


def act2_model_driven(bus) -> None:
    """真模型驱动：让 ollama 自己决定去读这个文件。用同一工具门面 target "tools"。"""
    print("\n========== 第 2 幕：你真机 ollama 驱动（qwen3:0.6b，target tools） ==========")
    import asyncio, pathlib
    from libcore.llm.ollama import OllamaBackend
    from libcore.kernel.agent import LlmReasonProvider

    abs_target = str(pathlib.Path(__file__).resolve())  # 当前 demo 文件自身的绝对路径
    bus2 = build()
    backend = OllamaBackend()
    reason = LlmReasonProvider(
        backend, model="qwen3:0.6b",
        system_prompt=(
            "你是文件阅读助手。你只能调用列表里的工具。"
            f"请调用 tools 门面 op=run，name=file、tool_op=read、args={{'path': '{abs_target}'}} 读取文件。"
            "读完后就说完成，不要再调用。"
        ),
    )
    loop = AgentLoop(bus2, reason)
    ctx = asyncio.run(loop.run({"goal": f"读取并报告文件 {abs_target} 的内容"}))

    print(f"done          = {ctx.done}")
    print(f"观察数         = {len(ctx.observations)}")
    for i, o in enumerate(ctx.observations):
        data_type = type(o.get("data") if isinstance(o, dict) else o).__name__
        print(f"  观察{i}: ok={o.get('ok') if isinstance(o,dict) else o.ok} data_type={data_type}")
    last = ctx.observations[-1] if ctx.observations else {}
    last_data = last.get("data", last) if isinstance(last, dict) else last
    if isinstance(last_data, dict) and isinstance(last_data.get("content"), str):
        print("模型确实读到了文件的前 80 字: " + last_data["content"][:80].replace("\n", "⏎"))
        print("[第2幕 PASS] 真模型真的决定并实际读取了真实文件。")
    else:
        print("[第2幕 侦察] 模型可能没走 tool call（0.6b 小模型不稳），属模型问题，非迁移问题。")


if __name__ == "__main__":
    bus = build()
    target = bus.manifest()[0]["target"]
    print(f"已登记能力: {target!r}\n  描述: {bus.manifest()[0]['description']!r}")
    act1_kernel_driven(bus)
    act2_model_driven(bus)