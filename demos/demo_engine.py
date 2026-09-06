"""Skills 入口 demo：文件级技能渐进披露 + 执行闭环。

manifest 只暴露 ``skill`` 单入口；技能概要不常驻清单，按需点名逐层展开。

运行： python demos/demo_engine.py
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from libcore.kernel.bus import EventBus
from libcore.kernel.bus import Dispatch
from libcore.plugins.capabilities import skill as skill_plugin
from libcore.plugins.aspects import permission


def _run(coro):
    return asyncio.run(coro)


def _dispatch(bus, op, payload):
    return _run(bus.dispatch(Dispatch(target="skill", op=op, payload=payload)))


def main():
    print("== Skills 单入口：渐进披露（目录 → 正文 → 执行）==")
    bus = EventBus()
    skill_plugin.register(bus)
    permission.register(bus)   # 护栏收口：只审 skill 的 exec 执行信号
    eng = skill_plugin.SkillEngine()

    # L1/L2 find：索引
    idx = _dispatch(bus, "find", {})
    print(f"[find] 技能库 {idx.data['count']} 个技能:")
    for s in idx.data["skills"]:
        print(f"   - [{s['category']}] {s['name']}: {s['description']}")

    # L3 read：打开某技能，取指令全文 + filemap
    rd = _dispatch(bus, "read", {"skill": "devops/deploy-k8s"})
    print(f"[read] {rd.data['name']} | filemap=", rd.data["filemap"])
    print(f"       指令预览: {rd.data['instruction'].splitlines()[-1]}")

    # L3' resource：按需读附件
    rs = _dispatch(bus, "resource", {"skill": "devops/deploy-k8s", "path": "references/cheatsheet.md"})
    print(f"[resource] cheatsheet.md = {rs.data['content']}")

    # L4 exec：进程内执行 scripts 脚本
    ex = _dispatch(bus, "exec", {
        "skill": "devops/deploy-k8s", "script": "scripts/gen_deployment.py",
        "args": {"name": "web", "replicas": 2},
    })
    m = ex.data["manifest"]
    print(f"[exec] 生成 manifest: apiVersion={m['apiVersion']} "
          f"namespace={m['metadata']['namespace']} replicas={m['spec']['replicas']}")

    # 护栏收口：同一 exec 信号，注入危险命令被拒
    deny = _dispatch(bus, "exec", {
        "skill": "devops/deploy-k8s", "script": "scripts/gen_deployment.py",
        "args": {"command": "rm -rf /"},
    })
    print(f"[exec] 危险 args 被权限闸门拦截: {deny.error}")


if __name__ == "__main__":
    main()