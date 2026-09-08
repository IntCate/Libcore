"""im 平台适配器子包：每个平台一个适配器模块，实现 ``adapter(op, payload) -> dict`` 契约。

适配器不持有连接生命周期（不自己 connect/disconnect），每次调用按需建立短连接
（或复用进程内缓存的客户端），符合 agentOS"能力节点 = 原子动作"的定位。

各平台接入逻辑借鉴 hermes-agent（只借鉴 SDK 调用方式，不引入其网关级抽象）。

装配层便利：``build_adapter(platform, **overrides)`` 按平台名构造适配器，
密钥缺省从密钥层读取（环境变量或 .env），显式传参优先。插件契约不变——
适配器仍通过构造函数接收密钥，不关心来源。
"""
from __future__ import annotations

from typing import Any


def build_adapter(platform: str, **overrides: Any) -> Any:
    """按平台名构造适配器（装配层便利，不约束插件）。

    - 显式 ``overrides`` 优先；
    - 缺省从密钥层读取对应环境变量（见各平台模块的配置注释）；
    - 未安装依赖的平台抛 RuntimeError（诚实披露，不静默降级）。
    """
    from libcore.secrets import get_secret

    if platform == "feishu":
        from .feishu import FeishuAdapter
        return FeishuAdapter(
            app_id=overrides.get("app_id") or get_secret("FEISHU_APP_ID", required=True),
            app_secret=overrides.get("app_secret") or get_secret("FEISHU_APP_SECRET", required=True),
            domain=overrides.get("domain", "feishu"),
        )
    if platform == "qqbot":
        from .qqbot import QQBotAdapter
        return QQBotAdapter(
            app_id=overrides.get("app_id") or get_secret("QQ_APP_ID", required=True),
            client_secret=overrides.get("client_secret") or get_secret("QQ_CLIENT_SECRET", required=True),
        )
    if platform == "slack":
        from .slack import SlackAdapter
        return SlackAdapter(
            bot_token=overrides.get("bot_token") or get_secret("SLACK_BOT_TOKEN", required=True),
        )
    if platform == "telegram":
        from .telegram import TelegramAdapter
        return TelegramAdapter(
            bot_token=overrides.get("bot_token") or get_secret("TELEGRAM_BOT_TOKEN", required=True),
        )
    if platform == "wecom":
        from .wecom import WeComAdapter
        return WeComAdapter(
            bot_id=overrides.get("bot_id") or get_secret("WECOM_BOT_ID", required=True),
            secret=overrides.get("secret") or get_secret("WECOM_SECRET", required=True),
        )
    if platform == "weixin":
        from .weixin import WeixinAdapter
        return WeixinAdapter(
            account_id=overrides.get("account_id") or get_secret("WEIXIN_ACCOUNT_ID", required=True),
            token=overrides.get("token") or get_secret("WEIXIN_TOKEN", required=True),
            base_url=overrides.get("base_url", "https://ilinkai.weixin.qq.com"),
        )
    if platform == "yuanbao":
        from .yuanbao import YuanbaoAdapter
        return YuanbaoAdapter(
            app_id=overrides.get("app_id") or get_secret("YUANBAO_APP_ID", required=True),
            app_secret=overrides.get("app_secret") or get_secret("YUANBAO_APP_SECRET", required=True),
            bot_id=overrides.get("bot_id") or get_secret("YUANBAO_BOT_ID") or "",
        )
    if platform == "dingtalk":
        from .dingtalk import DingTalkAdapter
        return DingTalkAdapter(
            webhook_url=overrides.get("webhook_url") or get_secret("DINGTALK_WEBHOOK_URL", required=True),
        )
    raise NotImplementedError(f"未知平台适配器：{platform!r}")


__all__ = ["build_adapter"]

