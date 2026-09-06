"""工具定义：执行 shell 命令（收进 tools 门面，不再独立注册总线节点）。

由旧架构 BashExecTool 重写而来。handler 统一签名 ``(d: Dispatch) -> CapabilityResult``。
"""
from __future__ import annotations

import subprocess

from libcore.kernel.bus import Dispatch, CapabilityResult


def _handle(d: Dispatch) -> CapabilityResult:
    command = d.payload.get("command")
    if not command:
        return CapabilityResult(ok=False, error="缺少必填参数 command")
    try:
        timeout = int(d.payload.get("timeout") or 30)
    except (TypeError, ValueError):
        timeout = 30
    cwd = d.payload.get("cwd")
    try:
        completed = subprocess.run(
            command,
            shell=True,
            cwd=cwd,
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
        },
    )


TOOLS = {
    "bash": {
        "name": "bash",
        "description": "执行一条 shell 命令，返回标准输出、标准错误与退出码",
        "ops": ["exec"],
        "schema": {
            "exec": {
                "command": "str, 要执行的 shell 命令",
                "timeout": "int, 超时秒数（默认 30）",
                "cwd": "str, 工作目录（可选）",
            }
        },
        "handler": _handle,
    }
}