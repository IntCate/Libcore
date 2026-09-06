"""统一通道闭环 demo：UI、飞书 IM、CLI 走同一条"收→想→回"链路。

验证点（对应 docs/libcore-channel-design.md）：
1. 统一入站：UI/飞书/CLI 消息都广播到 ``channel.inbound``，由薄桥接接到常驻内核；
2. 统一调度：复用 ResidentKernel（不新增调度器），收到消息即用 AgentLoop 推理；
3. 统一出站：agent 决策 dispatch("ui"/"im"/"cli", {channel_id, text})，能力节点经
   Channel.reply 送回**原会话**（消息从哪来回哪去）；
4. 零内核改动：只依赖 EventBus 的 publish/dispatch 两个原语。

决策者用无状态 ``EchoReason``（不依赖真实 LLM，可离线运行）：
把收到的消息原样回复回原会话，前缀 [UI回]/[IM回]/[CLI回] 便于区分通道。

运行： python demos/demo_channel_loop.py
"""
from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.basicConfig(level=logging.INFO, format="%(message)s")

from libcore.kernel.bus import EventBus, Notice, CapabilityResult
from libcore.kernel.agent import ReasonProvider, Action, ResidentKernel
from libcore.channels import ChannelRegistry, InboundMessage, OutboundMessage
from libcore.channels.ui_channel import UiChannel
from libcore.channels.im_channel import ImChannel
from libcore.channels.cli_channel import CliChannel
from libcore.channels.bridge import wire_inbound_to_kernel
from libcore.plugins.capabilities import ui as ui_cap
from libcore.plugins.capabilities import im as im_cap
from libcore.plugins.capabilities import cli as cli_cap


class EchoReason(ReasonProvider):
    """无状态决策者：把收到的消息原样回复回原会话（演示统一出站链路）。

    薄桥接 wire_inbound_to_kernel 把入站消息规整为 ``{"goal": text, "channel_id": ...}``
    投递给内核；本决策者据此按 channel_id 前缀决定出站目标：
    ui:* → dispatch("ui", op="render")；im:* → dispatch("im", op="send")；
    cli:* → dispatch("cli", op="send")。
    第一轮 dispatch 出站，第二轮 finish（AgentLoop 在 finish 时直接 break，不 dispatch）。
    """

    async def decide(self, ctx):
        goal = ctx.goal
        if not isinstance(goal, dict):
            return Action(finish=True)
        channel_id = goal.get("channel_id")
        text = goal.get("goal", "")
        if not channel_id:
            return Action(finish=True)
        if len(ctx.observations) > 0:
            return Action(finish=True)  # 已回复过，结束
        if channel_id.startswith("ui:"):
            return Action(target="ui", op="render",
                          payload={"channel_id": channel_id, "text": f"[UI回] {text}"})
        if channel_id.startswith("im:"):
            platform = channel_id.split(":")[1]
            return Action(target="im", op="send",
                          payload={"channel_id": channel_id, "platform": platform,
                                   "text": f"[IM回] {text}"})
        if channel_id.startswith("cli:"):
            return Action(target="cli", op="send",
                          payload={"channel_id": channel_id, "text": f"[CLI回] {text}"})
        return Action(finish=True)


async def main() -> None:
    print("=== 统一通道闭环：UI、飞书 IM、CLI 走同一条 收→想→回 链路 ===")

    # ---- 装配：总线 + 能力节点 + 通道注册表 ----
    bus = EventBus()
    channels = ChannelRegistry()
    ui_cap.register(bus, renderer=None, manifest_store=None, channels=channels)
    im_cap.register(bus, registry=None, channels=channels)
    cli_cap.register(bus, sink=None, channels=channels)

    # ---- 出站捕获：UI sink、飞书 sender、CLI sink ----
    ui_sent: list = []
    im_sent: list = []
    cli_sent: list = []

    def ui_sink(spec: dict) -> None:
        ui_sent.append(spec)

    def feishu_sender(platform: str, chat_id: str, text: str) -> dict:
        im_sent.append((platform, chat_id, text))
        return {"message_id": f"msg-{len(im_sent)}"}

    def cli_sink(text: str) -> None:
        cli_sent.append(text)

    # 注册三个通道：UI 会话 web-1、飞书会话 oc_123、CLI 终端
    ui_ch = UiChannel(bus, session_id="web-1", sink=ui_sink)
    im_ch = ImChannel(bus, platform="feishu", chat_id="oc_123",
                       user_id="om_456", sender=feishu_sender)
    cli_ch = CliChannel(bus, sink=cli_sink)
    channels.register(ui_ch)
    channels.register(im_ch)
    channels.register(cli_ch)
    print(f"   已注册通道: {[c.channel_id for c in channels.channels()]}")

    # ---- 常驻内核 + 薄桥接（统一调度，不新增调度器）----
    kernel = ResidentKernel(bus, EchoReason(), max_concurrency=2)
    wire_inbound_to_kernel(bus, kernel)
    serve_task = asyncio.create_task(kernel.serve())
    await asyncio.sleep(0.05)
    print(f"   常驻内核已启动: is_running={kernel.is_running}")

    # ---- 收：UI 入站（前端消息 → UiChannel.ingest → 广播 channel.inbound）----
    print("\n--- 收：UI 消息入站 ---")
    ui_ch.ingest(InboundMessage(channel_id=ui_ch.channel_id, kind="ui", platform="ui",
                                user_id="web-user", text="你好，UI"))
    await asyncio.sleep(0.2)
    print(f"   UI 出站: {ui_sent}")
    assert ui_sent and "[UI回] 你好，UI" in ui_sent[0]["data"]["text"], "UI 闭环失败"

    # ---- 收：飞书 IM 入站（平台消息 → ImChannel.ingest → 广播 channel.inbound）----
    print("\n--- 收：飞书 IM 消息入站 ---")
    im_ch.ingest(InboundMessage(channel_id=im_ch.channel_id, kind="im", platform="feishu",
                                user_id="om_456", text="你好，飞书"))
    await asyncio.sleep(0.2)
    print(f"   飞书出站: {im_sent}")
    assert im_sent and im_sent[0] == ("feishu", "oc_123", "[IM回] 你好，飞书"), "飞书闭环失败"

    # ---- 收：CLI 入站（终端输入 → CliChannel.ingest → 广播 channel.inbound）----
    print("\n--- 收：CLI 消息入站 ---")
    cli_ch.ingest(InboundMessage(channel_id=cli_ch.channel_id, kind="cli", platform="cli",
                                 user_id="cli-user", text="你好，CLI"))
    await asyncio.sleep(0.2)
    print(f"   CLI 出站: {cli_sent}")
    assert cli_sent and cli_sent[0] == "[CLI回] 你好，CLI", "CLI 闭环失败"

    # ---- 想：内核执行结果（每个请求独立 AgentLoop）----
    print("\n--- 想：内核执行结果 ---")
    for r in kernel.results:
        print(f"   goal={r['goal']} done={r['done']} steps={r['steps']}")

    # ---- 优雅关闭 ----
    print("\n--- 优雅关闭 ---")
    await kernel.shutdown()
    await serve_task
    print(f"   已停止: is_running={kernel.is_running}  完成请求数={len(kernel.results)}")

    print("\n=== 闭环验证通过：UI、飞书 IM、CLI 走同一条 收→想→回 链路 ===")


if __name__ == "__main__":
    asyncio.run(main())
