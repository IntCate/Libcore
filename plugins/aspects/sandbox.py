"""横切面插件：沙箱（按信号匹配——只拦 ``tool.*`` 执行类信号，其余不命中）。

这是"横切面按信号匹配"的一等公民演示：沙箱并不对总线上所有信号生效，
只有发往 ``tool.*`` 这类"执行"信号的调用才会被它拦截/审视；
``storage.*`` / ``agent.*`` 等其他信号不会命中它。因此"哪些能力需要沙箱"
由信号自身决定，能力插件无需声明任何依赖，也不写 order。
"""
from __future__ import annotations

from libcore.core.bus import Aspect
from libcore.core.event import Dispatch


class SandboxAspect(Aspect):
    """沙箱闸口：只审视发往 ``tool.*`` 的执行类信号（示例性放行）。"""

    def matches(self, signal) -> bool:
        # 只匹配"执行类"点名信号；广播(Notice)与其他 target 天然不命中
        return isinstance(signal, Dispatch) and signal.target.startswith("tool.")

    async def before(self, signal):
        print(f"  [sandbox] 审视执行信号 {signal.target}（tool.* 才命中）")


def register(bus) -> None:
    bus.add_aspect(SandboxAspect())