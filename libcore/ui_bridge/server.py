"""UI 桥接服务启动入口：``python -m libcore.ui_bridge.server``。

用法：
    python -m libcore.ui_bridge.server [--port 5000] [--dist web/web_dist]
"""
from __future__ import annotations

import argparse
import os

from .bridge import PORT, WEB_DIST, run


def main() -> None:
    parser = argparse.ArgumentParser(description="libcore UI bridge")
    parser.add_argument("--port", type=int, default=PORT, help="监听端口")
    parser.add_argument("--dist", default=None, help="前端构建产物目录（默认 web/web_dist）")
    args = parser.parse_args()
    run(dist_dir=args.dist or WEB_DIST, port=args.port)


if __name__ == "__main__":
    main()