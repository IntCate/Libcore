"""插件加载器：配置文件 = 唯一真相源 + 文件驱动热更新。

核心模型：
- 一份 ``plugins.yaml`` 是插件的**唯一真相源**，同时服务三方：
  * 框架：``load()``/``reconcile()`` 读它来对总线 on/off 能力；
  * Agent：其清单元数据（description/ops）经 manifest 注入决策；
  * 人：直接编辑它来控制开关、描述（可读、可进 git）。
- 自发现 = **写回**：``discover()`` 扫目录，把发现的新插件登记进配置
  （enabled:true + 模块 DESCRIPTION 作为默认描述），保留已设的开关/描述。
- 热更新 = **看配置文件**：``watch()`` 监听配置文件变化，重新 ``reconcile()``，
  对总线做 on/off diff，并广播 ``capability.changed``，Agent 下一轮自动刷新清单。
- 手动开关 = 改配置里 ``enabled`` 保存即可；自举 = 首次无配置自动生成一份。

插件约定：文件放 ``plugins_dir``（*.py、非 _ 开头），模块暴露 ``register(bus)``，
可选模块级 ``DESCRIPTION`` 供盘点填默认描述。

配置示例（唯一真相源）:
.. code-block:: yaml

    plugins:
      - name: pdf_tool
        enabled: true
        description: 解析 PDF 提取文本与分块
        ops: [parse]
      - name: vector_store
        enabled: true
        description: 向量化入库文档
"""
from __future__ import annotations

import asyncio
import importlib.util
import pathlib
import sys
from typing import Dict, List, Optional

from ..core.bus import EventBus
from ..core.event import Notice


class CapabilityLoader:
    def __init__(self, bus: EventBus, plugins_dir) -> None:
        self.bus = bus
        self.plugins_dir = pathlib.Path(plugins_dir)
        self._live: Dict[str, List[str]] = {}   # plugin_name -> [它登记的 target]
        self._modules: Dict[str, object] = {}   # plugin_name -> 已加载模块（本 loader 缓存）
        self._default_config = self.plugins_dir / "plugins.yaml"

    # ---- 盘点（只看目录，不注册、不写）----

    def scan(self) -> List[dict]:
        """扫目录，返回所有插件 ``[{name, description}]``。"""
        out: List[dict] = []
        for path in sorted(self.plugins_dir.glob("*.py")):
            if path.name.startswith("_"):
                continue
            out.append({"name": path.stem, "description": self._module_description(path)})
        return out

    # ---- 自发现 = 写回配置 ----

    def discover(self, config_path=None) -> List[str]:
        """扫目录，把"尚未登记"的新插件写回配置文件（保留已设的开关/描述）。

        返回本次新登记（新增）的插件名。无新增则不改动文件。
        """
        cfg, path = self._ensure_config(config_path or self._default_config)
        entries = list(cfg.get("plugins", []))
        by_name = {e["name"] for e in entries}
        added = []
        for item in self.scan():
            if item["name"] in by_name:
                continue
            entries.append({"name": item["name"], "enabled": True,
                            "description": item["description"]})
            added.append(item["name"])
        if added and path is not None:
            self.write_config(path, {"plugins": entries})
        return added

    # ---- 加载 / 同步（配置文件 = 唯一依据）----

    def load(self, config_path=None) -> List[str]:
        """把总线同步到配置文件描述的状态（配置 = 唯一真相源）。

        - 配置里 enabled 且在目录的插件 -> 注册进总线；
        - 配置里被关 / 配置里没有的 -> 从总线下线（off）；
        - 首次无配置 -> 自动自举（生成一份再加载）。
        可反复调用（幂等），供 watch 复用。
        返回当前在线的插件名。
        """
        return self.reconcile(config_path)

    def reconcile(self, config_path=None) -> List[str]:
        cfg, _path = self._ensure_config(config_path or self._default_config)
        entries = list(cfg.get("plugins", []))
        enabled = {e["name"] for e in entries if e.get("enabled", True) is not False}

        # 1) 下线：之前在线、但配置里被关/被删的
        for name in [n for n in list(self._live) if n not in enabled]:
            self._unload(name)
        # 2) 上线：配置里启用、但尚未在线的
        for e in entries:
            if e["name"] in enabled and e["name"] not in self._live:
                self._register(e["name"], e)
        # 3) 清单元数据覆盖（配置里的 description/ops 为准）
        for e in entries:
            if e["name"] in enabled:
                self._apply_meta(e)
        return list(self._live)

    # ---- 文件驱动热更新 ----

    async def watch(self, config_path=None, interval: float = 1.0) -> None:
        """监听配置文件变化，变了就 ``reconcile`` 并广播 ``capability.changed``。

        由调用方以异步任务运行并负责取消。可用于进程内，生产可换更强的 watch 后端。
        """
        config_path = config_path or self._default_config
        last = self._config_digest(config_path)
        while True:
            await asyncio.sleep(interval)
            now = self._config_digest(config_path)
            if now is None or now == last:
                continue
            changed = self.reconcile(config_path)
            await self.bus.publish(Notice(topic="capability.changed",
                                          payload={"plugins": changed}))
            last = now

    # ---- 配置读写（自举 / 展示）----

    def generate_yaml(self) -> str:
        """把目录里所有插件按配置格式生成文本（enabled:true + 默认描述）。"""
        lines = ["# libroce 插件配置（唯一真相源）",
                 "# 编辑本文件 -> watch() 会自动热更新，Agent 下一轮生效", "plugins:"]
        for item in self.scan():
            lines.append(f"  - name: {item['name']}")
            lines.append(f"    enabled: true")
            lines.append(f"    description: {item['description'] or ''}")
        return "\n".join(lines) + "\n"

    @staticmethod
    def write_config(config_path, cfg: dict) -> None:
        """把 dict 写为 yaml 配置文件（供自举 / discover / 演示用）。"""
        import yaml
        pathlib.Path(config_path).write_text(
            yaml.dump(cfg, allow_unicode=True, sort_keys=False), encoding="utf-8")

    # ---- 内部 ----

    def _ensure_config(self, config_path):
        """解析输入为 (cfg, path)。无文件时自动自举生成一份。"""
        if isinstance(config_path, dict):
            return dict(config_path), None
        path = pathlib.Path(config_path)
        if not path.exists():
            self.write_config(path, {"plugins": self.scan()})
        import yaml
        cfg = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return cfg, path

    def _register(self, name: str, entry: dict) -> None:
        path = self.plugins_dir / f"{name}.py"
        if not path.exists():
            print(f"[loader] 配置了 {name}，但目录无此插件文件，跳过")
            self._live[name] = []
            return
        # 模块按本 loader 缓存；每次调用 register 重登记（on 幂等覆盖），
        # 确保不同总线复用同一插件时各自拿到 handler。
        module = self._modules.get(name)
        if module is None:
            module = self._load_module(f"__plugin__.{name}", path)
            self._modules[name] = module
        register = getattr(module, "register", None)
        before = set(self.bus._handlers)          # 归因：登记前后新增的 target
        if callable(register):
            try:
                register(self.bus)
            except Exception as exc:   # 单插件失败不阻塞整体
                print(f"[loader] 插件 {name} 注册失败: {exc}")
        after = set(self.bus._handlers)
        self._live[name] = sorted(after - before)

    def _unload(self, name: str) -> None:
        for target in self._live.get(name, []):
            self.bus.off(target)
        self._live.pop(name, None)

    def _apply_meta(self, entry: dict) -> None:
        """用配置里的 description/ops 覆盖该插件所登记 target 的清单元数据。"""
        if not (entry.get("description") or entry.get("ops")):
            return
        if entry.get("enabled", True) is False:
            return
        for target in self._live.get(entry["name"], []):
            handler = self.bus._handlers.get(target)
            if handler is None:
                continue
            meta = dict(self.bus._metas.get(target, {}))
            if entry.get("description"):
                meta["description"] = entry["description"]
            if entry.get("ops"):
                meta["ops"] = entry["ops"]
            self.bus.on(target, handler, meta=meta)

    @staticmethod
    def _config_digest(config_path) -> Optional[int]:
        try:
            return pathlib.Path(config_path).stat().st_mtime_ns
        except OSError:
            return None

    @staticmethod
    def _module_description(path: pathlib.Path) -> str:
        try:
            module = CapabilityLoader._load_module(f"__peek__.{path.stem}", path)
            return getattr(module, "DESCRIPTION", "") or ""
        except Exception:
            return ""

    @staticmethod
    def _load_module(name: str, path: pathlib.Path):
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        return module