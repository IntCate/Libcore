"""libcore.llm —— LLM 适配器库（中立库，被内核引导与 model 能力共享）。

既是内核大脑（LlmReasonProvider）的 backend 来源，也是 model 能力节点
创建/切换 LLM 的资源池。不属内核决策层私有，独立于 kernel/。

配置：libcore/config/llm.yaml 为唯一真相源。各 backend 的连接参数由配置
提供默认值；配置缺失时回退到模块内建默认值（见 _BUILTIN_DEFAULTS），
保证零配置可导入、不报错。调用方显式传参始终覆盖配置默认。
"""
from __future__ import annotations

import pathlib
from typing import Any, Callable, Dict

from .spi import ChatBackend, ChatResult, LLMMsg, ToolCall

_FACTORIES: Dict[str, Callable[..., Any]] = {}
_DEFAULTS: Dict[str, Dict[str, Any]] = {}
_CONFIG_DEFAULTS: Dict[str, Any] = {}

# 内置调度员系统指令兜底：配置 llm.yaml 的 system_prompt 未提供时回退到它。
# 该文本是唯一兜底源，prompt 输入节点 / Agent / LlmReasonProvider 统一经 defaults() 读取。
_BUILTIN_SYSTEM_PROMPT = (
    "你是 libcore 的调度员。你只能调用给你列出的能力；"
    "根据任务选择一个要调用的能力并带好参数；"
    "所有必须做的事都做完了，就结束（不要再调用任何工具）。"
)


def register(name: str, factory: Callable[..., Any]) -> None:
    """注册一个 backend 工厂，之后 create(name, **kwargs) 即可用。"""
    _FACTORIES[name] = factory


def names() -> list[str]:
    return sorted(_FACTORIES)


def has(name: str) -> bool:
    return name in _FACTORIES


def _config_root() -> pathlib.Path:
    """libcore/config/：以本文件（libcore/llm/）往上两级定位。"""
    return pathlib.Path(__file__).resolve().parent.parent / "config"


def _load_config() -> Dict[str, Any]:
    """读取 config/llm.yaml；缺失或解析失败返回空 dict（零配置可用）。"""
    path = _config_root() / "llm.yaml"
    if not path.exists():
        return {}
    try:
        import yaml
        return (yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get("llm", {})
    except Exception:
        return {}


def _load_defaults() -> None:
    """从配置加载 backend 默认参数 + 引导默认值；缺失时回退内建。"""
    _CONFIG_DEFAULTS.clear()
    cfg = _load_config()
    _CONFIG_DEFAULTS["default_backend"] = cfg.get("default_backend") or "ollama"
    _CONFIG_DEFAULTS["default_model"] = cfg.get("default_model") or "qwen3:0.6b"
    _CONFIG_DEFAULTS["temperature"] = cfg.get("temperature", 0.2)
    _CONFIG_DEFAULTS["system_prompt"] = (cfg.get("system_prompt") or _BUILTIN_SYSTEM_PROMPT).strip()
    backends = cfg.get("backends") or {}
    _DEFAULTS.clear()
    for name, params in backends.items():
        if isinstance(params, dict):
            _DEFAULTS[name] = dict(params)


def defaults(name: str = "") -> Dict[str, Any]:
    """当前生效的引导默认值；传 backend 名则返回该 backend 的连接默认参数。"""
    if name:
        return dict(_DEFAULTS.get(name, {}))
    return dict(_CONFIG_DEFAULTS)


def create(name: str, **kwargs) -> Any:
    if name not in _FACTORIES:
        raise KeyError(f"未知 backend：{name!r}（已注册：{names()}）")
    merged = dict(_DEFAULTS.get(name, {}))
    merged.update(kwargs)
    return _FACTORIES[name](**merged)


# ---- 内建注册（配置只提供默认参数，注册逻辑保持代码内建） ----

def _ensure_defaults() -> None:
    if _FACTORIES:
        return
    from .ollama import OllamaBackend

    def ollama(base_url: str = "http://localhost:11434") -> Any:
        return OllamaBackend(base_url)

    register("ollama", ollama)
    try:
        from .langchain import LangChainOllamaBackend

        def langchain_ollama(model: str, base: str = "http://localhost:11434") -> Any:
            return LangChainOllamaBackend(model=model, base_url=base)

        register("langchain-ollama", langchain_ollama)
    except Exception:
        pass


_load_defaults()
_ensure_defaults()

__all__ = ["register", "names", "has", "create", "defaults",
           "ChatBackend", "ChatResult", "LLMMsg", "ToolCall"]
