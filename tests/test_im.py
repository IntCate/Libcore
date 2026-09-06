"""IM 能力节点测试：send / list_platforms / 降级 / 统一出站。

bus.dispatch() 是异步的，用 asyncio.run() 包裹（不依赖 pytest-asyncio）。
"""
from __future__ import annotations

import asyncio

import pytest

from libcore.kernel.bus import Dispatch
from libcore.plugins.capabilities import im as im_cap
from libcore.channels import ChannelRegistry
from libcore.channels.im_channel import ImChannel


def _dispatch(bus, target="im", op="send", payload=None):
    return Dispatch(target=target, op=op, payload=payload or {})


def _call(bus, d):
    return asyncio.run(bus.dispatch(d))


class TestIMRegistry:
    def test_register_and_platforms(self):
        reg = im_cap.IMRegistry()
        reg.register("feishu", lambda op, p: {"ok": True})
        reg.register("weixin", lambda op, p: {"ok": True})
        assert reg.platforms() == ["feishu", "weixin"]

    def test_dispatch_routes_to_adapter(self):
        reg = im_cap.IMRegistry()
        seen = {}
        reg.register("feishu", lambda op, p: seen.update(op=op, p=p) or {"ok": True})
        result = reg.dispatch("feishu", "send", {"text": "hi"})
        assert result == {"ok": True}
        assert seen == {"op": "send", "p": {"text": "hi"}}

    def test_dispatch_unregistered_raises(self):
        reg = im_cap.IMRegistry()
        with pytest.raises(NotImplementedError):
            reg.dispatch("feishu", "send", {})


class TestIMCapability:
    def test_list_platforms_empty(self, bus):
        im_cap.register(bus)
        result = _call(bus, _dispatch(bus, op="list_platforms"))
        assert result.ok
        assert result.data["platforms"] == []

    def test_list_platforms_with_registry(self, bus):
        reg = im_cap.IMRegistry()
        reg.register("feishu", lambda op, p: {})
        im_cap.register(bus, registry=reg)
        result = _call(bus, _dispatch(bus, op="list_platforms"))
        assert result.data["platforms"] == ["feishu"]

    def test_send_missing_platform(self, bus):
        im_cap.register(bus)
        result = _call(bus, _dispatch(bus, op="send", payload={"text": "hi"}))
        assert not result.ok
        assert result.data["status"] == "missing_platform"

    def test_send_unregistered_platform_not_implemented(self, bus):
        im_cap.register(bus)
        result = _call(bus, _dispatch(bus, op="send", payload={"platform": "feishu", "text": "hi"}))
        assert not result.ok
        assert result.data["status"] == "not_implemented"

    def test_send_with_adapter(self, bus):
        reg = im_cap.IMRegistry()
        reg.register("feishu", lambda op, p: {"message_id": "m1"})
        im_cap.register(bus, registry=reg)
        result = _call(bus, _dispatch(bus, op="send", payload={"platform": "feishu", "text": "hi"}))
        assert result.ok
        assert result.data["status"] == "sent"
        assert result.data["message_id"] == "m1"

    def test_send_via_channel_reply(self, bus, channels):
        """payload 带 channel_id 且注册表有对应通道 → 经 Channel.reply 送回原会话。"""
        sent = []
        im_ch = ImChannel(bus, platform="feishu", chat_id="oc_1", user_id="u1",
                          sender=lambda plat, chat, text: sent.append((plat, chat, text)))
        channels.register(im_ch)
        im_cap.register(bus, channels=channels)
        result = _call(bus, _dispatch(bus, op="send",
                                      payload={"channel_id": "im:feishu:oc_1",
                                               "platform": "feishu", "text": "hi"}))
        assert result.ok
        assert result.data["status"] == "sent"
        assert sent == [("feishu", "oc_1", "hi")]

    def test_send_channel_id_but_no_channel_falls_back(self, bus):
        """channel_id 存在但注册表无对应通道 → 回退平台适配器。"""
        reg = im_cap.IMRegistry()
        reg.register("feishu", lambda op, p: {"message_id": "m1"})
        im_cap.register(bus, registry=reg, channels=ChannelRegistry())
        result = _call(bus, _dispatch(bus, op="send",
                                      payload={"channel_id": "im:feishu:oc_1",
                                               "platform": "feishu", "text": "hi"}))
        assert result.ok
        assert result.data["message_id"] == "m1"
