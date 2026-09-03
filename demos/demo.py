"""M0 最小内核 demo：跑通"Agent 事件驱动调度"闭环（不依赖旧代码）。

验证点：
1. Agent 通过点名 dispatch 调度能力（非谓词，不隐性指定流程）；
2. Agent 通过"可呼叫清单"(manifest) 发现能力，target 从清单动态取出而非写死；
3. 横切面（日志 / 追踪）作为插件包围所有信号，不参与调度；
4. 迭代上限护栏生效；
5. Notice 广播（系统级感知）与 Dispatch 点名是两条独立原语；
6. 热更新闭环：运行中 bus.on 新增能力 + capability.changed 广播，Agent 下一轮即从更新后的清单发现并使用；
7. 插件加载器 + 配置文件=唯一真相源：目录自发现写回配置 -> 配置驱动加载 -> Agent 自动发现并调用；
8. 文件驱动热更新：编辑配置文件（enabled 开关 / 描述）保存 -> watch 监听 -> 总线 on/off -> Level 广播 -> Agent 下一轮生效。

运行： cd backend && python -m libcore.demos.demo
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from ..core.event import Dispatch, Notice, CapabilityResult
from ..core.bus import EventBus
from ..core.scope import Scope
from ..agent.spi import ReasonProvider, Action
from ..agent.loop import AgentLoop
from ..plugins.loader import CapabilityLoader, AspectLoader


# ---- 横切面：经 plugins/aspects（文件插件 + config/aspects.yaml 列表序）统一装配 ----
# ---- 占位能力（未来从旧 tool_kernel / services 平移） ----

_LIBROOT = Path(__file__).parent.parent
ASPECTS_DIR = _LIBROOT / "plugins" / "aspects"
CONFIG_DIR = _LIBROOT / "config"


def _install_aspects(bus: EventBus, config_path=None) -> list:
    """装配横切面，返回总线当前有序的横切面插件实例列表（含顺序语义）。"""
    AspectLoader(bus, ASPECTS_DIR).reconcile(config_path)
    return bus._aspects


def _trace(bus: EventBus):
    """取总线上的追踪横切面（演示读取调度轨迹）。

    用鸭子类型而非 isinstance：横切面经 AspectLoader 以独立模块名加载，
    类对象可能与模块内 import 的不是同一份，故按 ``records`` 属性收敛。
    """
    for a in bus._aspects:
        if hasattr(a, "records"):
            return a
    return None

def tool_pdf_parse(d: Dispatch) -> CapabilityResult:
    print(f"   >> tool.pdf.parse")
    return CapabilityResult(ok=True, data={"pages": 300, "chunks": 42})

def storage_vector_upsert(d: Dispatch) -> CapabilityResult:
    print(f"   >> storage.vector.upsert  chunks={d.payload.get('chunks')}")
    return CapabilityResult(ok=True, data={"stored": True})


# ---- 决策提供者（未来接 LLM）----

class ManifestReason(ReasonProvider):
    """真实版决策者：不再写死 target，而是从"可呼叫清单"里发现能力。

    它只根据目标关键词在天花。清单里找描述匹配的能力，
    因此 target 是从清单动态取出的 name，而非硬编码字符串。
    """

    def __init__(self) -> None:
        self._step = 0

    def _find(self, ctx: Scope, keyword: str):
        """在注入的可呼叫清单里，按描述关键词找到第一个能力的 target。"""
        for cap in ctx.capabilities:
            if keyword in (cap.get("description") or ""):
                return cap["target"]
        return None

    async def decide(self, ctx):
        if self._step == 0:
            t = self._find(ctx, "解析")
            if t:
                self._step += 1
                return Action(target=t, op="parse", payload={"file": "doc.pdf"})
            return Action(finish=True)
        if self._step == 1:
            t = self._find(ctx, "入库")
            if t:
                self._step += 1
                return Action(target=t, op="upsert", payload={"index": "kb"})
            return Action(finish=True)
        return Action(finish=True)


class UseAllReason(ReasonProvider):
    """自动用上清单里所有能力：每轮取一个未用过的 target 调用，用完全部后结束。

    演示"自动发现"：它不做任何能力假设，完全遍历当前清单，
    因此新增插件后，无需改这里也能自动调用它。
    """

    def __init__(self) -> None:
        self.used: list[str] = []

    async def decide(self, ctx):
        for cap in ctx.capabilities:
            t = cap["target"]
            if t not in self.used:
                self.used.append(t)
                op = (cap.get("ops") or ["run"])[0]
                return Action(target=t, op=op, payload={"file": "doc.pdf"})
        return Action(finish=True)


class WaitForUpsertReason(ReasonProvider):
    """热更新演示决策者：先解析，再等清单里出现入库能力。

    它不写死 storage.vector —— 第一轮清单里根本没有它。
    清单里没有目标时返回 Action(wait=True)，由调度环回到轮顶刷新清单、
    稍后重试；直到运行期热更新把"入库"挂上总线，它从新清单里发现并点名。
    """

    def __init__(self) -> None:
        self._parsed = False
        self._upserted = False

    def _get(self, ctx: Scope, keyword: str):
        for cap in ctx.capabilities:
            if keyword in (cap.get("description") or ""):
                return cap["target"]
        return None

    async def decide(self, ctx):
        if not self._parsed:
            t = self._get(ctx, "解析")
            if t:
                self._parsed = True
                return Action(target=t, op="parse", payload={"file": "doc.pdf"})
            return Action(wait=True)
        if not self._upserted:
            t = self._get(ctx, "入库")
            if t:
                self._upserted = True
                return Action(target=t, op="upsert", payload={"index": "kb"})
            return Action(wait=True)
        return Action(finish=True)


class NeverFinishReason(ReasonProvider):
    """永不结束的决策者，用来触发迭代上限护栏。"""

    async def decide(self, ctx):
        return Action(target="tool.pdf", op="parse", payload={})


async def run_limited(bus: EventBus, reason: ReasonProvider, max_steps: int) -> Scope:
    """带显式上限的最小声调环，专用于演示护栏。"""
    ctx = Scope(goal="guard-test")
    ctx.max_steps = max_steps
    while not ctx.done:
        if len(ctx.observations) >= ctx.max_steps:
            ctx.done = True
            ctx.observations.append({"error": "迭代上限达成（护栏）"})
            break
        action = await reason.decide(ctx)
        if action.finish:
            ctx.done = True
            break
        result = await bus.dispatch(Dispatch(
            target=action.target, op=action.op, payload=action.payload))
        ctx.observe(result)
    return ctx


async def main() -> None:
    print("=== 1) Agent 从可呼叫清单发现并调度能力（带日志 + 追踪）===")
    bus = EventBus()
    _install_aspects(bus)
    trace = _trace(bus)
    # 每个能力登记时自报 description，汇成"可呼叫清单"
    bus.on("tool.pdf", tool_pdf_parse, meta={"description": "解析 PDF 提取文本与分块", "ops": ["parse"]})
    bus.on("storage.vector", storage_vector_upsert, meta={"description": "向量化入库文档", "ops": ["upsert"]})
    loop = AgentLoop(bus, ManifestReason())
    print("   注入 Agent 的可呼叫清单:")
    for cap in bus.manifest():
        print(f"     {cap['target']}  -  {cap['description']}")
    ctx = await loop.run({"goal": "归档 PDF 到知识库"})
    print("   完成，调度的 target（从清单发现，非写死）：")
    for r in trace.records:
        print(f"     {r['target']}.{r['op']}  cid={r['cid'][:8]} ok={r['ok']}")

    print("\n=== 2) 迭代上限护栏（决策者永不结束）===")
    guard_bus = EventBus()
    guard_bus.on("tool.pdf", tool_pdf_parse)
    ctx2 = await run_limited(guard_bus, NeverFinishReason(), max_steps=3)
    print(f"   停在第 {len(ctx2.observations)} 轮，末条={ctx2.observations[-1]}")

    print("\n=== 3) Notice 广播（系统级感知，与点名独立）===")
    bus = EventBus()
    seen = []
    async def on_doc(_n): seen.append("change")
    bus.subscribe("doc.changed", on_doc)
    await bus.publish(Notice(topic="doc.changed", payload={"path": "x"}))
    print(f"   广播命中订阅者 {len(seen)} 次")

    print("\n=== 4) 热更新闭环：运行中新增能力，Agent 下一轮即发现并使用 ===")
    hb = EventBus()
    _install_aspects(hb)
    trace2 = _trace(hb)
    changed_log = []
    async def on_changed(_n): changed_log.append(_n.payload)
    hb.subscribe("capability.changed", on_changed)
    # 初始清单只有"解析"，没有"入库"
    hb.on("tool.pdf", tool_pdf_parse, meta={"description": "解析 PDF 提取文本与分块", "ops": ["parse"]})
    print("   初始清单:", sorted(cap["target"] for cap in hb.manifest()))

    async def hot_update():
        await asyncio.sleep(0.05)   # 模拟"运行一段时间后"挂上新能力
        hb.on("storage.vector", storage_vector_upsert,
              meta={"description": "向量化入库文档", "ops": ["upsert"]})
        await hb.publish(Notice(topic="capability.changed", payload={"target": "storage.vector"}))

    loop2 = AgentLoop(hb, WaitForUpsertReason())
    task = asyncio.create_task(hot_update())
    await loop2.run({"goal": "归档 PDF 到知识库"})
    await task
    print("   热更新后最终清单:", sorted(cap["target"] for cap in hb.manifest()))
    print("   收到 capability.changed:", changed_log)
    print("   运行期被实际调度的 target:", [r["target"] for r in trace2.records])

    print("\n=== 5) 配置=唯一真相源：自举生成 -> 加载 ===")
    lb = EventBus()
    cfg_path = CONFIG_DIR / "auto_generated.yaml"
    if cfg_path.exists():
        cfg_path.unlink()
    loader5 = CapabilityLoader(lb, _LIBROOT / "plugins" / "capabilities")
    loaded = loader5.load(cfg_path)          # 首次无配置 -> 自动生成一份再加载
    print(f"   自举生成配置: {cfg_path.exists()}  在线插件: {loaded}")
    print("   加载后清单:", sorted(c["target"] for c in lb.manifest()))
    loop3 = AgentLoop(lb, UseAllReason())
    await loop3.run({"goal": "自动发现所有插件"})
    print("   Agent 自动调用了所有能力，无需写死任何 target（清单来自配置文件）。")

    print("\n=== 6) 文件驱动热更新：改配置文件保存，Agent 下一轮即生效 ===")
    hbus = EventBus()
    hot_cfg = CONFIG_DIR / "hot_config.yaml"
    CapabilityLoader.write_config(hot_cfg, {"plugins": [
        {"name": "pdf_tool", "enabled": True, "description": "解析 PDF"},
        {"name": "vector_store", "enabled": True, "description": "向量化入库"},
    ]})
    hl = CapabilityLoader(hbus, _LIBROOT / "plugins" / "capabilities")
    hl.reconcile(hot_cfg)
    print("   初始清单:", sorted(c["target"] for c in hbus.manifest()))
    # 订阅变更广播
    changes = []
    async def on_changed(n): changes.append(n.payload)
    hbus.subscribe("capability.changed", on_changed)
    # 启动 watch（后台监听配置文件）
    task = asyncio.ensure_future(hl.watch(hot_cfg, interval=0.05))

    async def human_edits_config():
        await asyncio.sleep(0.08)             # 人打开配置编辑
        CapabilityLoader.write_config(hot_cfg, {"plugins": [
            {"name": "pdf_tool", "enabled": True, "description": "解析 PDF"},
            {"name": "vector_store", "enabled": False},    # 改为关闭
        ]})
        await asyncio.sleep(0.14)             # 再打开一次，改为开启 + 新描述
        CapabilityLoader.write_config(hot_cfg, {"plugins": [
            {"name": "pdf_tool", "enabled": True, "description": "解析 PDF"},
            {"name": "vector_store", "enabled": True, "description": "向量化入库(新)"},
        ]})

    await human_edits_config()
    await asyncio.sleep(0.12)                 # 等 watch 收敛
    task.cancel()
    print("   收到 capability.changed:", changes)
    print("   最终清单:", sorted(f"{c['target']}『{c.get('description')}』" for c in hbus.manifest()))

    print("\n=== 7) 清理演示生成的临时配置文件 ===")
    for tmp in ("auto_generated.yaml", "hot_config.yaml"):
        p = CONFIG_DIR / tmp
        if p.exists():
            p.unlink()
            print(f"   已删除 {tmp}")


if __name__ == "__main__":
    asyncio.run(main())