"""横切面插件：权限闸门（原生重写，不 import 旧 app.runtime.harness）。

由旧架构 ToolPermissionGate（pointcut=tool.call，priority=100）重写而来：
- 每个 Aspect 用 matches(signal) 声明只审发往 ``tool.*`` 的点名信号；
- before 阶段校验执行内容：命中危险命令等规则即返回一个 ``CapabilityResult(ok=False)``，
  总线据此阻断 handler 不执行（这是横切面护栏的"阻止"能力，不是观察）。

不引入旧权限体系：内置一份危险命令黑名单，后续可替换成权限表/策略引擎。
"""
from __future__ import annotations

from libcore.kernel.bus import Aspect
from libcore.kernel.bus import Dispatch, CapabilityResult
from libcore.plugins.aspects.sandbox import is_exec_signal

# 危险命令黑名单（子串匹配）：命中一律拒绝执行
DANGEROUS_TOKENS = ("rm -rf", "mkfs", ":(){:|:&};:", "dd if=", "> /dev/sda")


def _candidate_text(signal) -> str:
    """从执行信号里收集要审计的命令文本（tool.bash 的 command / skill 的 args）。"""
    text = str(signal.payload.get("command") or "")
    args = signal.payload.get("args")
    if isinstance(args, dict):
        text += " " + " ".join(str(v) for v in args.values())
    return text


class PermissionAspect(Aspect):
    """权限闸门：拒绝执行类信号（tool.* / skill exec）里的明显危险命令。"""

    def matches(self, signal) -> bool:
        return is_exec_signal(signal)

    async def before(self, signal):
        text = _candidate_text(signal)
        for token in DANGEROUS_TOKENS:
            if token in text:
                return CapabilityResult(
                    ok=False,
                    error=f"权限拒绝：命令含危险指令 {token!r}",
                    data={"target": signal.target, "denied": token},
                )
        return None  # 未命中规则 -> 放行


def register(bus) -> None:
    bus.add_aspect(PermissionAspect())