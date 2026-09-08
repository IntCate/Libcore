"""libcore.secrets —— 装配层密钥助手（可选，不约束插件）。

定位：给"应用装配层"提供统一的密钥取数来源，替代"注释里承诺、代码里不读"的假约定。

边界（不违背插件自由开发）：
- 插件契约不变：插件仍通过构造函数接收密钥，不关心来源；
- 本模块只服务装配层（如 ``UiBridge.attach_feishu`` / LLM 后端装配），是"可选便利"；
- 插件开发者可完全不用本模块，自行注入字符串 / 接 KMS / 读自己的配置。

取数优先级（从高到低）：
1. 显式传入（调用方已给值，直接返回）
2. 环境变量（``os.environ``）
3. ``.env`` 文件（可选加载，默认从项目根找 ``.env``）
4. ``default``（缺省值）

安全：绝不打印/记录密钥值；``repr`` 脱敏。
"""
from __future__ import annotations

import os
import pathlib
from typing import Optional

# 已加载的 .env 键值（进程内缓存，避免重复解析）
_ENV_FILE_CACHE: Optional[dict] = None


def _project_root() -> pathlib.Path:
    """项目根：以本文件（libcore/secrets.py）往上两级定位。"""
    return pathlib.Path(__file__).resolve().parents[1]


def _env_file_path() -> pathlib.Path:
    """默认 .env 路径：项目根/.env；可用环境变量 LIBCORE_ENV_FILE 覆盖。"""
    override = os.environ.get("LIBCORE_ENV_FILE")
    if override:
        return pathlib.Path(override)
    return _project_root() / ".env"


def _load_env_file() -> dict:
    """解析 .env 文件为 dict（手写极简解析器，零外部依赖）。

    规则：
    - 每行 ``KEY=VALUE``，忽略空行与 ``#`` 注释；
    - 值可带可选引号（单/双引号），解析时剥掉；
    - 不展开变量引用（保持极简，够用即可）。
    """
    global _ENV_FILE_CACHE
    if _ENV_FILE_CACHE is not None:
        return _ENV_FILE_CACHE
    result: dict = {}
    path = _env_file_path()
    if path.exists():
        try:
            # utf-8-sig 自动剥 BOM，避免首个 key 被 \ufeff 污染
            for raw in path.read_text(encoding="utf-8-sig").splitlines():
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip()
                if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
                    value = value[1:-1]
                if key:
                    result[key] = value
        except OSError:
            pass
    _ENV_FILE_CACHE = result
    return result


def get_secret(name: str, *, required: bool = False, default: Optional[str] = None) -> Optional[str]:
    """读取一个密钥，取数优先级：环境变量 > .env 文件 > default。

    - ``required=True`` 且取不到时抛 ``RuntimeError``（诚实披露，不静默降级）；
    - ``required=False`` 取不到时返回 ``default``（可为 None）。
    """
    value = os.environ.get(name)
    if value is None:
        value = _load_env_file().get(name)
    if value is None:
        value = default
    if value is None and required:
        raise RuntimeError(
            f"缺少密钥 {name!r}：请设置环境变量或在 {_env_file_path()} 中配置"
        )
    return value


def get_secret_or(name: str, default: str) -> str:
    """读取一个密钥，取不到时返回给定默认值（非 None 兜底）。"""
    value = get_secret(name, required=False)
    return value if value is not None else default


class Secret:
    """密钥值包装：repr 脱敏，避免日志/调试误打印明文。

    用法：``Secret(get_secret("FEISHU_APP_SECRET"))``，传给插件时用 ``.value``。
    """

    def __init__(self, value: Optional[str]) -> None:
        self.value = value

    def __repr__(self) -> str:
        return f"Secret({'***' if self.value else None})"

    def __bool__(self) -> bool:
        return bool(self.value)


__all__ = ["get_secret", "get_secret_or", "Secret"]
