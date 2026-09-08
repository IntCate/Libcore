"""插件加载器测试：动态加载时相对导入必须可解析。

loader 用 spec_from_file_location 动态加载插件模块，若未设置 __package__，
模块内的相对导入（如 knowledge.py 的 from ..resources.knowledge.documents）会失败。
"""
from __future__ import annotations

import pathlib

import libcore
from libcore.plugins.loader import CapabilityLoader, AspectLoader, _load_module, _config_digest


def _capabilities_dir() -> pathlib.Path:
    return pathlib.Path(libcore.__file__).parent / "plugins" / "capabilities"


class TestLoaderRelativeImport:
    def test_knowledge_plugin_relative_import_resolves(self):
        """knowledge.py 含相对导入，动态加载时必须可解析（不抛 ImportError）。"""
        path = _capabilities_dir() / "knowledge.py"
        module = CapabilityLoader._load_module("__plugin__.knowledge", path)
        assert hasattr(module, "register"), "knowledge 插件应暴露 register"

    def test_loader_reconcile_loads_knowledge(self):
        """CapabilityLoader 加载 capabilities 目录时，knowledge 插件能成功注册。"""
        from libcore.kernel.bus import EventBus
        bus = EventBus()
        loader = CapabilityLoader(bus, _capabilities_dir())
        # 只启用 knowledge，避免依赖其他插件的外部库
        cfg = {"plugins": [{"name": "knowledge", "enabled": True}]}
        loaded = loader.reconcile(cfg)
        assert "knowledge" in loaded
        assert "knowledge" in bus._handlers


def _aspects_dir() -> pathlib.Path:
    return pathlib.Path(libcore.__file__).parent / "plugins" / "aspects"


class TestSharedLoaderHelpers:
    def test_module_level_load_module_shared_by_both_loaders(self):
        """能力与横切面 loader 应共用同一个模块级 _load_module（消除重复）。"""
        cap_path = _capabilities_dir() / "knowledge.py"
        asp_path = _aspects_dir() / "permission.py"
        # 两个 loader 的 _load_module 都能加载模块（委托给模块级函数）
        cap_mod = CapabilityLoader._load_module("__shared__.knowledge", cap_path)
        asp_mod = AspectLoader._load_module("__shared__.permission", asp_path)
        assert hasattr(cap_mod, "register")
        assert hasattr(asp_mod, "register")
        # 模块级函数能加载能力与横切面两类模块
        cap_mod2 = _load_module("__shared2__.knowledge", cap_path)
        asp_mod2 = _load_module("__shared2__.permission", asp_path)
        assert hasattr(cap_mod2, "register")
        assert hasattr(asp_mod2, "register")

    def test_module_level_config_digest_shared_by_both_loaders(self):
        """两个 loader 的 _config_digest 应委托给同一个模块级函数。"""
        # 对不存在的路径返回 None（不抛异常）
        assert CapabilityLoader._config_digest("/nonexistent/path.yaml") is None
        assert AspectLoader._config_digest("/nonexistent/path.yaml") is None
        assert _config_digest("/nonexistent/path.yaml") is None
