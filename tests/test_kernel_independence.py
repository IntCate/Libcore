"""内核独立性测试：验证 Kernel.bootstrap 不反向依赖插件层。

背景：内核（kernel/）本应只依赖自身，但旧实现里 ``Kernel._input_nodes()``
会 ``from ..plugins.loader import CapabilityLoader``，导致内核在无插件环境下
无法 bootstrap。本测试验证修复后内核接受显式 input_nodes、不再 import 插件。
"""
from __future__ import annotations

import pytest

from libcore.kernel import Kernel, EventBus, AgentLoop
from libcore.kernel.bus import CapabilityResult, Dispatch


def _echo_handler(d: Dispatch) -> CapabilityResult:
    return CapabilityResult(ok=True, data={"echo": d.payload})


def test_bootstrap_accepts_explicit_input_nodes():
    """Kernel.bootstrap 应接受显式 input_nodes 参数（不再自己读插件配置）。"""
    kernel = Kernel.bootstrap(
        targets={"echo": _echo_handler},
        input_nodes=[{"target": "context", "slot": "user"}],
    )
    assert isinstance(kernel.bus, EventBus)
    assert isinstance(kernel.loop, AgentLoop)


def test_bootstrap_without_input_nodes_uses_builtin_default():
    """不传 input_nodes 时，内核退化为内置默认（context/prompt），不依赖插件。"""
    kernel = Kernel.bootstrap(targets={"echo": _echo_handler})
    assert kernel.loop._input_nodes == [
        {"target": "context", "slot": "user"},
        {"target": "prompt", "slot": "system"},
    ]

def test_bootstrap_does_not_import_plugins(monkeypatch):
    """内核 bootstrap 不应 import 插件层（无 plugins 也能运行）。"""
    import sys

    # 模拟"无插件环境"：让 libcore.plugins 不可导入
    saved = sys.modules.pop("libcore.plugins", None)
    saved_loader = sys.modules.pop("libcore.plugins.loader", None)
    monkeypatch.setitem(sys.modules, "libcore.plugins", None)
    monkeypatch.setitem(sys.modules, "libcore.plugins.loader", None)
    try:
        # 修复前：_input_nodes() 会 from ..plugins.loader import CapabilityLoader → 失败
        kernel = Kernel.bootstrap(targets={"echo": _echo_handler})
        assert kernel.bus is not None
    finally:
        if saved is not None:
            sys.modules["libcore.plugins"] = saved
        if saved_loader is not None:
            sys.modules["libcore.plugins.loader"] = saved_loader


def test_bootstrap_resident_accepts_explicit_input_nodes():
    """bootstrap_resident 也应接受显式 input_nodes，不依赖插件。"""
    kernel = Kernel.bootstrap_resident(
        targets={"echo": _echo_handler},
        input_nodes=[{"target": "context", "slot": "user"}],
    )
    assert kernel.resident is not None


def test_agentloop_accepts_wait_interval_param():
    """AgentLoop 应接受 wait_interval 构造参数（内核不读配置，只接受参数）。"""
    loop = AgentLoop(EventBus(), None, wait_interval=0.5)
    assert loop._wait_interval == 0.5


def test_agentloop_wait_interval_default():
    """不传 wait_interval 时，内核用内置默认 0.02 兜底（不依赖配置）。"""
    loop = AgentLoop(EventBus(), None)
    assert loop._wait_interval == 0.02


def test_bootstrap_accepts_wait_interval():
    """Kernel.bootstrap 应透传 wait_interval 给 AgentLoop。"""
    kernel = Kernel.bootstrap(
        targets={"echo": _echo_handler},
        wait_interval=0.5,
    )
    assert kernel.loop._wait_interval == 0.5
