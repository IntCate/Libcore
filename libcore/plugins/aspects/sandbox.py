"""横切面插件：沙箱（按信号匹配——只拦 ``tool.*`` 执行类信号，其余不命中）。

这是"横切面按信号匹配"的一等公民演示：沙箱并不对总线上所有信号生效，
只有发往 ``tool.*`` 这类"执行"信号的调用才会被它拦截/审视；
``storage.*`` / ``agent.*`` 等其他信号不会命中它。因此"哪些能力需要沙箱"
由信号自身决定，能力插件无需声明任何依赖，也不写 order。
"""
from __future__ import annotations

from libcore.kernel.bus import Aspect
from libcore.kernel.bus import Dispatch


def is_exec_signal(signal) -> bool:
    """执行类信号判定：工具执行（``tools`` run）或技能脚本执行（``skill`` exec）。

    护栏/沙箱只对该类信号生效——真正"跑代码/动系统"的调用从这里过，
    广播(Notice)与其他只读操作（tools find/read、skill find/read/resource）不拦截。
    """
    if not isinstance(signal, Dispatch):
        return False
    # 三类门面的"执行"op：
    #   tools run   —— 执行内置工具
    #   skill exec  —— 执行技能脚本
    #   mcp   run   —— 执行 MCP 工具（透发远端 server）
    # find/read/resource 是"读"，不需沙箱；Notice 等广播不拦截。
    if signal.target in ("tools", "mcp"):
        return (signal.op or "") == "run"
    if signal.target == "skill":
        return (signal.op or "") == "exec"
    return False


class SandboxAspect(Aspect):
    """沙箱闸口：只审视执行类信号（tools run / skill exec，示例性放行）。"""

    def matches(self, signal) -> bool:
        return is_exec_signal(signal)

    async def before(self, signal):
        print(f"  [sandbox] 审视执行信号 {signal.target}{'/' + signal.op if signal.op else ''}（执行类才命中）")


def register(bus) -> None:
    bus.add_aspect(SandboxAspect())