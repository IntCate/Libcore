"""第三层 demo：agent 决策触发 UI 渲染（agentOS 端到端）。

链路（agent 集成已打通）：
    POST /api/agent/submit {goal}
      -> 桥事件循环内 AgentLoop（复用 agent 决策推理器）
      -> agent 点名 ui 能力（op=render + component/target/props）
      -> RenderSink 经 WS 下行
      -> 前端 events.js 消费并实时渲染

运行：
    python demos/demo_agent_ui.py [--auto "你的目标"] [--port 5000]

浏览器打开 http://localhost:5000/ 后，agent 的目标会自动触发前端渲染。
要点：agent 与桥共享同一总线（attach_agent），ui 能力进入 agent manifest 才能被点名。
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import uvicorn

from libcore.api import Agent
from libcore.ui_bridge.bridge import UiBridge

logging.basicConfig(level=logging.INFO, format="%(name)s | %(levelname)s | %(message)s")

_UI_SYSTEM = (
    "你是 libcore 的调度员。候选能力里有一个 ui 能力节点，用于把界面渲染到浏览器。"
    "只要任务要展示任意界面，你就必须发起一次工具调用："
    "target='ui'，op='render'，payload 里带 component（如 chat/table/card）、"
    "target（容器地址，如 /root/chat）、props（界面数据）。"
    "你必须调用工具，不要用纯文字回复来代替工具调用。调完即可结束。"
)


def main():
    parser = argparse.ArgumentParser(description="libcore UI layer3 agent demo")
    parser.add_argument(
        "--auto",
        default="渲染一个标题为 'Agent Conversation' 的聊天界面到 /root/chat",
        help="启动后自动投递的 agent 目标（POST /api/agent/submit 的 goal）",
    )
    parser.add_argument("--port", type=int, default=5000)
    args = parser.parse_args()

    bridge = UiBridge()
    agent = Agent(backend="ollama", model="qwen3:0.6b", system_prompt=_UI_SYSTEM)
    bridge.attach_agent(agent)

    @bridge.app.on_event("startup")
    async def _on_startup() -> None:
        async def _deliver() -> None:
            await asyncio.sleep(1.5)  # 等浏览器/WS 连上再下行，避免首帧丢失
            await bridge._drive_agent_goal(args.auto)
        asyncio.create_task(_deliver())

    server = uvicorn.Server(uvicorn.Config(
        bridge.app, host="127.0.0.1", port=args.port, log_level="info"))
    print(f"\n[ui-layer3] http://localhost:{args.port}/  (单端口同源)")
    print(f"[ui-layer3] agent 已挂接并共享总线；目标自动投递: {args.auto!r}")
    print(f"[ui-layer3] 手动投递: curl -X POST http://localhost:{args.port}/api/agent/submit"
          f" -H 'Content-Type: application/json' -d '{{\"goal\":\"渲染一个卡片到 /ext/card\"}}'")
    server.run()


if __name__ == "__main__":
    main()