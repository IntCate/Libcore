"""横切面插件：沙箱（隔离执行环境）。

沙箱是"虚拟环境隔离"的护栏：把执行类信号（tools run / skill exec）从
宿主进程/宿主 shell 中隔离出来，放到受限子进程里执行，遏制恶意或失控代码
对宿主系统的影响。与 permission（黑名单：拒绝危险命令）互补：
- permission：命中危险命令 -> 拒绝（黑名单，拒绝"不该跑的"）；
- sandbox：把执行重定向到隔离环境（子进程 + 受限环境，遏制"跑坏系统"）。

隔离策略（Windows 可用的轻量级方案，无外部依赖）：
- bash：在独立子进程执行，强制超时 + 临时工作目录 + 环境变量白名单；
- skill：在受限 Python 子进程执行，屏蔽危险模块（os/subprocess/socket/...）
  与危险 builtins（open/exec/eval/...）。

沙箱接管执行：拦截执行类信号后，自己调用隔离执行器并返回结果（bus 据此短路
原 handler）。``enabled`` 由 aspects.yaml 的 ``config`` 注入（缺省 False = 不接管，
保持最小，执行走原 handler）。
"""
from __future__ import annotations

import builtins
import os
import subprocess
import sys
import tempfile
import textwrap

from libcore.kernel.bus import Aspect
from libcore.kernel.bus import Dispatch, CapabilityResult
from libcore.plugins.capabilities.skill import _default_skills_dir, _safe_resolve


def is_exec_signal(signal) -> bool:
    """执行类信号判定：工具执行（``tools`` run）/ 技能脚本执行（``skill`` exec）/ MCP 工具执行（``mcp`` run）。

    护栏 / 沙箱只对该类信号生效——真正"跑代码 / 动系统"的调用从这里过，
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


# bash 子进程允许透传的环境变量白名单（其余一律丢弃，隔离宿主环境）
_BASH_ENV_ALLOWLIST = ("PATH", "HOME", "TEMP", "TMP", "SYSTEMROOT", "USERPROFILE")

# bash 命令白名单：只允许这些安全命令（其余一律拒绝，遏制任意命令执行）
# 注意：Windows 上 echo/dir 等是 cmd 内建，shell=False 无法执行，故不列入；
# python 用于沙箱内执行受限脚本（已隔离工作目录 + 环境白名单）。
_BASH_CMD_ALLOWLIST = (
    "ls", "cat", "grep", "head", "tail", "wc", "pwd", "date",
    "find", "sort", "uniq", "cut", "tr", "sed", "awk", "basename", "dirname",
    "stat", "file", "which", "env", "printf", "true", "false", "python",
)


class SandboxExecutor:
    """隔离执行器：把执行类信号放到受限子进程里跑，返回 CapabilityResult。"""

    def run_bash(self, payload: dict) -> CapabilityResult:
        """隔离执行 bash 命令：独立子进程 + 强制超时 + 临时工作目录 + 环境白名单 + 命令白名单。

        安全加固：命令白名单 + ``shell=False`` 参数列表执行，杜绝命令注入
        （``echo hi; rm -rf /`` 这类 shell 拼接无法再执行）。
        """
        command = payload.get("command")
        if not command:
            return CapabilityResult(ok=False, error="缺少必填参数 command")
        # 命令白名单：只允许安全命令，其余拒绝
        argv = command.split()
        if not argv or argv[0] not in _BASH_CMD_ALLOWLIST:
            return CapabilityResult(
                ok=False,
                error=f"命令不在白名单内：{argv[0] if argv else ''!r}",
                data={"command": command, "sandboxed": True},
            )
        try:
            timeout = int(payload.get("timeout") or 30)
        except (TypeError, ValueError):
            timeout = 30
        env = {k: v for k, v in os.environ.items() if k in _BASH_ENV_ALLOWLIST}
        try:
            with tempfile.TemporaryDirectory(prefix="libcore_sandbox_") as tmp:
                completed = subprocess.run(
                    argv,             # shell=False：参数列表直投，杜绝 shell 注入
                    cwd=tmp,          # 隔离工作目录：命令写文件只落在临时目录
                    env=env,          # 环境白名单：丢弃宿主敏感变量
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                )
        except subprocess.TimeoutExpired:
            return CapabilityResult(
                ok=False, error=f"命令超时（{timeout}s）",
                data={"command": command, "timed_out": True},
            )
        except Exception as e:  # noqa: BLE001
            return CapabilityResult(ok=False, error=str(e), data={"command": command})
        return CapabilityResult(
            ok=completed.returncode == 0,
            data={
                "command": command,
                "stdout": completed.stdout,
                "stderr": completed.stderr,
                "exit_code": completed.returncode,
                "sandboxed": True,
            },
        )

    def run_skill(self, payload: dict) -> CapabilityResult:
        """隔离执行 skill 脚本：受限 Python 子进程，屏蔽危险模块与 builtins。"""
        script_path = payload.get("script_path")
        args = payload.get("args") or {}
        if not script_path or not os.path.isfile(script_path):
            return CapabilityResult(ok=False, error=f"脚本不存在：{script_path!r}")
        runner = _SKILL_RUNNER_TEMPLATE.format(
            script_path=script_path,
            args_repr=repr(args),
        )
        # 把 runner 写入临时 .py 文件再执行，避免 Windows 命令行传参的换行/引号转义问题
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".py", encoding="utf-8", delete=False
            ) as f:
                f.write(runner)
                runner_path = f.name
            try:
                completed = subprocess.run(
                    [sys.executable, runner_path],
                    capture_output=True,
                    text=True,
                    timeout=60,
                )
            finally:
                try:
                    os.unlink(runner_path)
                except OSError:
                    pass
        except subprocess.TimeoutExpired:
            return CapabilityResult(ok=False, error="技能执行超时（60s）")
        except Exception as e:  # noqa: BLE001
            return CapabilityResult(ok=False, error=f"技能执行失败：{e}")
        if completed.returncode != 0:
            return CapabilityResult(
                ok=False,
                error=completed.stderr.strip() or "技能执行失败",
                data={"sandboxed": True},
            )
        return CapabilityResult(ok=True, data={"result": completed.stdout.strip(), "sandboxed": True})


# 受限 skill 执行器模板：在子进程里**先屏蔽**危险模块与 builtins，再加载脚本并调用 run(args)
_SKILL_RUNNER_TEMPLATE = textwrap.dedent("""\
    import builtins as _b, importlib as _i, importlib.util as _iu, sys as _s
    import json as _json

    # 屏蔽时机前移：在加载脚本**之前**屏蔽危险模块，
    # 脚本顶层代码无法 import 危险模块（os/subprocess/socket/...）。
    # 注意：importlib 不屏蔽——exec_module 加载脚本需要它；危险的是用它加载
    # os/subprocess 等，而这些已在屏蔽列表，importlib 加载它们同样会失败。
    for _m in ("os", "subprocess", "socket", "shutil", "ctypes",
               "multiprocessing", "pty", "signal"):
        _s.modules[_m] = None

    _spec = _iu.spec_from_file_location("_sandbox_skill", {script_path!r})
    _mod = _iu.module_from_spec(_spec)
    _s.modules["_sandbox_skill"] = _mod
    _spec.loader.exec_module(_mod)
    _run = getattr(_mod, "run", None)
    if not callable(_run):
        raise RuntimeError("脚本未暴露 run(args)->dict 约定")

    # 脚本已加载，此刻屏蔽危险 builtins（open/exec/eval/input），再调用 run()。
    # 注意：open 必须在加载后屏蔽——exec_module 内部用 open 读脚本文件。
    for _danger in ("open", "exec", "eval", "input"):
        if hasattr(_b, _danger):
            setattr(_b, _danger, None)

    _out = _run({args_repr})
    if not isinstance(_out, dict):
        _out = {{"result": _out}}
    print(_json.dumps(_out, ensure_ascii=False))
""")


class SandboxAspect(Aspect):
    """沙箱闸口：把执行类信号重定向到隔离子进程执行。"""

    def __init__(self, enabled: bool = False):
        self.enabled = enabled
        self._executor = SandboxExecutor()

    def matches(self, signal) -> bool:
        return is_exec_signal(signal)

    async def before(self, signal):
        if not self.enabled:
            return None  # 沙箱未启用 -> 不接管，执行走原 handler
        payload = getattr(signal, "payload", None) or {}
        if signal.target == "tools" and (signal.op or "") == "run":
            if payload.get("name") == "bash":
                # tools 门面把命令嵌套在 args 里（bash 工具 handler 读 payload["command"]）
                args = payload.get("args")
                if isinstance(args, dict):
                    return self._executor.run_bash(args)
                return self._executor.run_bash(payload)
        elif signal.target == "skill" and (signal.op or "") == "exec":
            # 解析脚本绝对路径（复用 skill 的防越权解析），再交给隔离执行器
            rel = str(payload.get("skill") or payload.get("name") or "")
            script = str(payload.get("script") or "")
            sp = _safe_resolve(_default_skills_dir(), rel)
            if sp is None or not sp.is_dir():
                return CapabilityResult(ok=False, error=f"技能不存在：{rel!r}")
            fp = _safe_resolve(sp, script)
            if fp is None or not fp.is_file() or fp.suffix != ".py":
                return CapabilityResult(ok=False, error=f"脚本不存在：{rel}/{script}（需 .py）")
            return self._executor.run_skill({"script_path": str(fp), "args": payload.get("args")})
        return None  # 其他执行类信号（如 mcp run）暂不接管，放行


def register(bus, enabled: bool = False) -> None:
    """注册沙箱护栏。``enabled`` 可由 aspects.yaml 的 ``config`` 注入（缺省 False = 不接管）。"""
    bus.add_aspect(SandboxAspect(enabled=enabled))
