"""统一通道闭环测试：UI / IM / CLI 走同一条"收→想→回"链路。

验证点（对应 docs/core/channels.md）：
1. 统一入站：UI/IM/CLI 消息都广播到 channel.inbound，由薄桥接接到常驻内核；
2. 统一调度：复用 ResidentKernel（不新增调度器）；
3. 统一出站：agent 决策 dispatch("ui"/"im"/"cli", {channel_id, text})，
   能力节点经 Channel.reply 送回原会话（消息从哪来回哪去）；
4. 零内核改动：只依赖 EventBus 的 publish/dispatch 两个原语。

不依赖 pytest-asyncio：用 asyncio.run() 在同步测试函数内跑异步闭环。
"""
from __future__ import annotations

import asyncio

import pytest

from libcore.kernel.bus import EventBus
from libcore.kernel.agent import ReasonProvider, Action, ResidentKernel
from libcore.channels import ChannelRegistry, InboundMessage
from libcore.channels.ui_channel import UiChannel
from libcore.channels.im_channel import ImChannel
from libcore.channels.cli_channel import CliChannel
from libcore.channels.bridge import wire_inbound_to_kernel
from libcore.plugins.capabilities import ui as ui_cap
from libcore.plugins.capabilities import im as im_cap
from libcore.plugins.capabilities import cli as cli_cap


class EchoReason(ReasonProvider):
    """无状态决策者：把收到的消息原样回复回原会话（演示统一出站链路）。"""

    async def decide(self, ctx):
        goal = ctx.goal
        if not isinstance(goal, dict):
            return Action(finish=True)
        channel_id = goal.get("channel_id")
        text = goal.get("goal", "")
        if not channel_id:
            return Action(finish=True)
        if len(ctx.observations) > 0:
            return Action(finish=True)
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


def _build_env():
    """装配总线 + 能力节点 + 三个通道 + 常驻内核 + 薄桥接。"""
    bus = EventBus()
    channels = ChannelRegistry()
    ui_cap.register(bus, renderer=None, manifest_store=None, channels=channels)
    im_cap.register(bus, registry=None, channels=channels)
    cli_cap.register(bus, sink=None, channels=channels)

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

    ui_ch = UiChannel(bus, session_id="web-1", sink=ui_sink)
    im_ch = ImChannel(bus, platform="feishu", chat_id="oc_123",
                      user_id="om_456", sender=feishu_sender)
    cli_ch = CliChannel(bus, sink=cli_sink)
    channels.register(ui_ch)
    channels.register(im_ch)
    channels.register(cli_ch)

    kernel = ResidentKernel(bus, EchoReason(), max_concurrency=2)
    wire_inbound_to_kernel(bus, kernel)

    return {
        "bus": bus, "channels": channels, "kernel": kernel,
        "ui_ch": ui_ch, "im_ch": im_ch, "cli_ch": cli_ch,
        "ui_sent": ui_sent, "im_sent": im_sent, "cli_sent": cli_sent,
    }


async def _run_loop(env, ingest_fn, wait=0.2):
    """启动内核，执行一次入站，等待闭环完成，优雅关闭。"""
    kernel = env["kernel"]
    serve_task = asyncio.create_task(kernel.serve())
    await asyncio.sleep(0.05)
    ingest_fn()
    await asyncio.sleep(wait)
    await kernel.shutdown()
    await serve_task


def test_ui_channel_loop():
    env = _build_env()
    asyncio.run(_run_loop(env, lambda: env["ui_ch"].ingest(InboundMessage(
        channel_id=env["ui_ch"].channel_id, kind="ui", platform="ui",
        user_id="web-user", text="你好，UI"))))
    assert env["ui_sent"], "UI 出站为空"
    assert "[UI回] 你好，UI" in env["ui_sent"][0]["data"]["text"]


def test_im_channel_loop():
    env = _build_env()
    asyncio.run(_run_loop(env, lambda: env["im_ch"].ingest(InboundMessage(
        channel_id=env["im_ch"].channel_id, kind="im", platform="feishu",
        user_id="om_456", text="你好，飞书"))))
    assert env["im_sent"], "IM 出站为空"
    assert env["im_sent"][0] == ("feishu", "oc_123", "[IM回] 你好，飞书")


def test_cli_channel_loop():
    env = _build_env()
    asyncio.run(_run_loop(env, lambda: env["cli_ch"].ingest(InboundMessage(
        channel_id=env["cli_ch"].channel_id, kind="cli", platform="cli",
        user_id="cli-user", text="你好，CLI"))))
    assert env["cli_sent"], "CLI 出站为空"
    assert env["cli_sent"][0] == "[CLI回] 你好，CLI"


def test_all_channels_same_link():
    """三个通道走同一条链路：入站都广播 channel.inbound，出站都送回原会话。"""
    env = _build_env()
    kernel = env["kernel"]

    async def _run():
        serve_task = asyncio.create_task(kernel.serve())
        await asyncio.sleep(0.05)
        env["ui_ch"].ingest(InboundMessage(channel_id=env["ui_ch"].channel_id, kind="ui",
                                           platform="ui", user_id="u", text="A"))
        env["im_ch"].ingest(InboundMessage(channel_id=env["im_ch"].channel_id, kind="im",
                                           platform="feishu", user_id="u", text="B"))
        env["cli_ch"].ingest(InboundMessage(channel_id=env["cli_ch"].channel_id, kind="cli",
                                            platform="cli", user_id="u", text="C"))
        await asyncio.sleep(0.4)
        await kernel.shutdown()
        await serve_task

    asyncio.run(_run())

    assert env["ui_sent"] and "[UI回] A" in env["ui_sent"][0]["data"]["text"]
    assert env["im_sent"] and env["im_sent"][0] == ("feishu", "oc_123", "[IM回] B")
    assert env["cli_sent"] and env["cli_sent"][0] == "[CLI回] C"
    assert len(kernel.results) == 3, "三个请求都应被内核执行"
