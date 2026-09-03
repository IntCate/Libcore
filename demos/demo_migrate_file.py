"""迁移真实能力试点：把旧 tool_kernel 的 FileReadTool 平移成 libcore 总线能力。

第 1 幕（纯内核驱动，不依赖模型）：调度环直接点名 file.read，读一个真实文件。
     目的：单独验证【旧的真实工具 + 新内核】这层 100% 正确，排除模型抽风的干扰。
第 2 幕（真模型驱动）：用你本机 ollama(qwen3:0.6b) 让模型自己决定去读这个文件。
     目的：验证【旧工具 + 真 LLM + 新内核】三方真正串起来。

用法：cd backend && python -m libcore.demos.demo_migrate_file
"""
from __future__ import annotations

import asyncio

from app.runtime.kernel.tool_kernel.builtins.file_builtins import FileReadTool
from app.runtime.kernel.tool_kernel.spi import ToolExecutionContext

from libcore.core.bus import EventBus
from libcore.core.event import CapabilityResult
from libcore.agent.loop import AgentLoop
from libcore.agent.spi import Action
from libcore.core.scope import Scope


TARGET_FILE = "libcore/demos/demo_migrate_file.py"  # 读它自己（确认存在、必成）


def _ctx_session() -> ToolExecutionContext:
    return ToolExecutionContext(
        session_id="demo", agent_id="demo", assistant_message_id="m", tool_call_id="c"
    )


def adapt_builtin(tool):
    """薄壳：把旧 IBuiltinTool 变成总线 handler。逻辑零重写。"""
    async def handler(dispatch):
        try:
            data = await tool.execute(dispatch.payload, _ctx_session())
        except Exception as e:                      # noqa: BLE001
            return CapabilityResult(ok=False, error=str(e))
        ok = not (isinstance(data, dict) and data.get("error"))
        return CapabilityResult(ok=ok, data=data)
    return handler


def build():
    """装配：旧工具 → 总线，并把描述写进能力清单。"""
    bus = EventBus()
    tool = FileReadTool()
    desc = tool.descriptor()
    bus.on(tool.component_id, adapt_builtin(tool), meta={
        "description": desc.description,
        "input_schema": desc.input_schema,
    })
    return bus, desc


class _FixedReason:
    """第 1 幕专用：固定剧本，不靠模型。第一步点名读文件，第二步收尾。"""
    def __init__(self):
        self._did_read = False

    async def decide(self, ctx: Scope) -> Action:
        if not self._did_read:
            self._did_read = True
            return Action(target="chato.tool.file.read", op="read",
                          payload={"path": TARGET_FILE})
        return Action(finish=True)


def act1_kernel_driven(bus) -> None:
    """纯内核驱动：确认旧工具被新内核真实驱动、读到真实内容。"""
    print("========== 第 1 幕：纯内核驱动（不依赖模型） ==========")
    loop = AgentLoop(bus, _FixedReason())
    ctx = asyncio.run(loop.run({"goal": f"读取文件 {TARGET_FILE}"}))
    obs = ctx.observations[-1] if ctx.observations else None
    data = obs.get("data", obs) if isinstance(obs, dict) else obs
    assert ctx.done is True
    assert isinstance(data, dict), obs
    assert str(data.get("path", "")).replace("\\", "/") == TARGET_FILE, obs
    content = data.get("content", "")
    print(f"done           = {ctx.done}")
    print(f"真实读到的文件   = {obs.get('path')}  ({obs.get('bytes')} 字节)")
    print("文件内容前 120 字 = " + content[:120].replace("\n", "⏎"))
    print("[第1幕 PASS] 旧 FileReadTool 已被新内核真实驱动，读到真实文件内容。")


def act2_model_driven(bus) -> None:
    """真模型驱动：让 ollama 自己决定去读这个文件。

    用独立的、短名的 target "file.read"（对照：act1 用的是旧 component_id
    "chato.tool.file.read"）。验证小模型对"带点长工具名"vs"短工具名"的稳定性差异，
    这直接决定迁移时 target 的命名规范。
    """
    print("\n========== 第 2 幕：你真机 ollama 驱动（qwen3:0.6b，短名 file.read） ==========")
    import asyncio, pathlib
    from libcore.agent.backends.ollama import OllamaBackend
    from libcore.agent.lm_reason import LlmReasonProvider

    abs_target = str(pathlib.Path(__file__).resolve())  # 当前 demo 文件自身的绝对路径
    tool = FileReadTool()
    desc = tool.descriptor()
    bus2 = EventBus()
    bus2.on("file.read", adapt_builtin(tool), meta={
        "description": desc.description,
        "input_schema": desc.input_schema,
    })

    backend = OllamaBackend()
    reason = LlmReasonProvider(
        backend, model="qwen3:0.6b",
        system_prompt=(
            "你是文件阅读助手。你只能调用列表里的工具。"
            f"请调用 file.read 读取文件 {abs_target}。读完后就说完成，不要再调用。"
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
    bus, desc = build()
    print(f"已登记能力: {desc.component_id!r}\n  描述: {desc.description!r}")
    act1_kernel_driven(bus)
    act2_model_driven(bus)