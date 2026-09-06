"""pytest 共享 fixture：总线 + 通道注册表。"""
from __future__ import annotations

import pytest

from libcore.kernel.bus import EventBus
from libcore.channels import ChannelRegistry


@pytest.fixture
def bus() -> EventBus:
    return EventBus()


@pytest.fixture
def channels() -> ChannelRegistry:
    return ChannelRegistry()
