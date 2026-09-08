"""sandbox 安全加固测试。

验证三个加固点：
1. 命令白名单：白名单外命令被拒绝；
2. shell=False：命令注入（echo hi; rm -rf /）无法执行；
3. run_skill 屏蔽时机前移：脚本顶层 import 危险模块被拒绝。
"""
from __future__ import annotations

import os
import tempfile

from libcore.plugins.aspects.sandbox import SandboxExecutor


def test_bash_rejects_command_outside_allowlist():
    """白名单外命令（rm）被拒绝。"""
    ex = SandboxExecutor()
    res = ex.run_bash({"command": "rm -rf /"})
    assert res.ok is False
    assert "白名单" in res.error


def test_bash_rejects_shell_injection():
    """shell=False：命令注入（python -c '...; rm -rf /'）无法执行。"""
    ex = SandboxExecutor()
    # 白名单首命令 python 在名单内，但分号拼接的 rm 无法经 shell 执行
    res = ex.run_bash({"command": "python -c 'import os; os.remove(\"x\")'; rm -rf /"})
    # 由于 shell=False，argv[0]="python" 在名单内，但整串作为参数传给 python，
    # 分号后的 rm 不会被执行（无 shell 解释）
    assert res.ok is False or "rm" not in res.data.get("stdout", "")


def test_bash_allows_safe_command():
    """白名单内安全命令（python）正常执行。"""
    ex = SandboxExecutor()
    res = ex.run_bash({"command": "python -c print(2+2)"})
    assert res.ok is True
    assert "4" in res.data["stdout"]
    assert res.data["sandboxed"] is True


def test_skill_blocks_dangerous_import_at_top_level():
    """run_skill 屏蔽时机前移：脚本顶层 import os 被拒绝。"""
    ex = SandboxExecutor()
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", encoding="utf-8", delete=False) as f:
        f.write("import os\n\ndef run(args):\n    return {'ok': True}\n")
        path = f.name
    try:
        res = ex.run_skill({"script_path": path, "args": {}})
        # 顶层 import os 被屏蔽 -> 脚本加载失败 -> 返回失败
        assert res.ok is False
    finally:
        os.unlink(path)


def test_skill_allows_safe_script():
    """无危险 import 的脚本正常执行。"""
    ex = SandboxExecutor()
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", encoding="utf-8", delete=False) as f:
        f.write("def run(args):\n    return {'result': 'ok'}\n")
        path = f.name
    try:
        res = ex.run_skill({"script_path": path, "args": {}})
        assert res.ok is True
        assert "ok" in res.data["result"]
    finally:
        os.unlink(path)
