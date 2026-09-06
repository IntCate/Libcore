"""UI 能力节点测试：find / render / 降级 / 统一出站。

bus.dispatch() 是异步的，用 asyncio.run() 包裹（不依赖 pytest-asyncio）。
"""
from __future__ import annotations

import asyncio

from libcore.kernel.bus import Dispatch
from libcore.plugins.capabilities import ui as ui_cap
from libcore.channels import ChannelRegistry
from libcore.channels.ui_channel import UiChannel


def _dispatch(bus, target="ui", op="render", payload=None):
    return Dispatch(target=target, op=op, payload=payload or {})


def _call(bus, d):
    return asyncio.run(bus.dispatch(d))


class TestUICapability:
    def test_find_without_manifest_store(self, bus):
        ui_cap.register(bus)
        result = _call(bus, _dispatch(bus, op="find"))
        assert not result.ok
        assert "未接线" in result.error

    def test_find_with_manifest_store(self, bus):
        class Store:
            def snapshot(self):
                return {"components": [{"name": "Card"}, {"name": "Table"}]}

        ui_cap.register(bus, manifest_store=Store())
        result = _call(bus, _dispatch(bus, op="find"))
        assert result.ok
        assert result.data["count"] == 2
        assert result.data["components"][0]["name"] == "Card"

    def test_render_without_backend_not_implemented(self, bus):
        ui_cap.register(bus)
        result = _call(bus, _dispatch(bus, op="render", payload={"type": "card"}))
        assert not result.ok
        assert result.data["status"] == "not_implemented"

    def test_render_with_renderer(self, bus):
        def renderer(spec):
            return {"type": spec["type"], "target": spec["target"]}

        ui_cap.register(bus, renderer=renderer)
        result = _call(bus, _dispatch(bus, op="render",
                                      payload={"type": "card", "target": "/root/a"}))
        assert result.ok
        assert result.data["render"] == {"type": "card", "target": "/root/a"}

    def test_render_via_channel_reply(self, bus, channels):
        """payload 带 channel_id 且注册表有对应通道 → 经 Channel.reply 送回原会话。"""
        sent = []
        ui_ch = UiChannel(bus, session_id="web-1", sink=sent.append)
        channels.register(ui_ch)
        ui_cap.register(bus, channels=channels)
        result = _call(bus, _dispatch(bus, op="render",
                                      payload={"channel_id": "ui:web-1", "text": "hi"}))
        assert result.ok
        assert result.data["status"] == "sent"
        assert sent and sent[0]["data"]["text"] == "hi"

    def test_render_channel_id_but_no_channel_falls_back(self, bus):
        """channel_id 存在但注册表无对应通道 → 回退 renderer。"""
        def renderer(spec):
            return {"type": spec["type"]}

        ui_cap.register(bus, renderer=renderer, channels=ChannelRegistry())
        result = _call(bus, _dispatch(bus, op="render",
                                      payload={"channel_id": "ui:web-1", "type": "card"}))
        assert result.ok
        assert result.data["render"] == {"type": "card"}
