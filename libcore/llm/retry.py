"""重试机制 + 错误分类。

翻译自 Hermes Agent `agent/retry_utils.py`，Python + asyncio + httpx 语义下实现。

核心能力：
  1. ErrorClassifier：按错误类型分类（5 类），决定是否重试、重试 TTL。
  2. retry_context：异步迭代器（async for attempt in ...），负责：
       - 循环 attempts（max_attempts）
       - 捕获 HTTP 错误 → 分类 → 指数退避 sleep → 下一轮
       - billing / auth fatal → 提前 break，抛出 RetryFatal 供上层处理
  3. 指数退避：采用 transient_ladder。

错误分类说明（5 类）：
  ErrorCategory.TRANSIENT        5xx / ConnectTimeout / 网络抖动   可重试，指数退避
  ErrorCategory.RATE_LIMIT       429                              可重试，TTL_RATE_LIMIT
  ErrorCategory.AUTH_TEMP        401（非 Terminal）              可重试，TTL_SHORT_AUTH
  ErrorCategory.BILLING          402                              不可重试，fallback
  ErrorCategory.AUTH_FATAL       401 + Terminal Auth Reason      不可重试，DEAD + fallback

典型用法（openai adapter 内）：
    async with retry_context(
        default_credentials=None,
        max_attempts=options.get("max_retries", 2),
        error_classifier=lambda err: classify_httpx_error(err, "openai-completions"),
    ) as ctx:
        async for attempt in ctx:
            try:
                resp = await client.post("/chat/completions", json=payload)
                resp.raise_for_status()
                await attempt.success()
                return self._parse_chat_response(resp.json(), model)
            except Exception as exc:
                await attempt.fail(exc)
    # 全部重试失败 → 抛出 RetryExhausted
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Awaitable, Callable, Optional

import httpx

TTL_TRANSIENT_LADDER = [1, 3, 10, 30]  # seconds
TTL_RATE_LIMIT = 60                    # 429
TTL_SHORT_AUTH = 5 * 60                # 401 临时
TTL_BILLING = 60 * 60                  # 402

TERMINAL_AUTH_REASONS = (
    "token_invalidated", "token_revoked", "invalid_token",
    "invalid_grant", "expired_token", "account_closed", "account_disabled",
)


# ============================================================
# 1. 错误分类
# ============================================================

class ErrorCategory(str, Enum):
    TRANSIENT = "transient"
    RATE_LIMIT = "rate_limit"
    AUTH_TEMP = "auth_temp"
    BILLING = "billing"
    AUTH_FATAL = "auth_fatal"
    CLIENT = "client"  # 4xx 其他（参数错误等，不重试）

    @property
    def retryable(self) -> bool:
        return self in (
            ErrorCategory.TRANSIENT,
            ErrorCategory.RATE_LIMIT,
            ErrorCategory.AUTH_TEMP,
        )

    @property
    def triggers_fallback(self) -> bool:
        """是否需要触发 fallback 链（billing / auth_fatal）。"""
        return self in (ErrorCategory.BILLING, ErrorCategory.AUTH_FATAL)


@dataclass
class ErrorInfo:
    category: ErrorCategory
    http_status: Optional[int]
    error_reason: Optional[str]      # 如 "invalid_token"
    error_message: Optional[str]
    raw: Optional[Exception]         # 原始异常（供调试，不进日志 str 化）

    def __str__(self) -> str:
        # 不泄露 credentials / body，仅展示分类 / status / reason
        return f"ErrorInfo(category={self.category.value}, http={self.http_status}, reason={self.error_reason})"


# ============================================================
# 2. HTTP / httpx 错误分类器
# ============================================================

def classify_httpx_error(err: Exception, api_family_tag: str = "unknown") -> ErrorInfo:
    """分类 httpx 异常 → ErrorInfo。

    规则：
      - HTTPStatusError → 按 status_code
        - 401: 从 error.message 里匹配 TERMINAL_AUTH_REASONS 判 AUTH_FATAL，否则 AUTH_TEMP
        - 402: BILLING
        - 429: RATE_LIMIT
        - 5xx: TRANSIENT
        - 其他 4xx: CLIENT（不重试）
      - TimeoutException / ConnectError / ConnectTimeout: TRANSIENT
      - 其他: CLIENT
    """
    if isinstance(err, httpx.HTTPStatusError):
        status = err.response.status_code
        try:
            body = err.response.json()
        except Exception:
            body = None
        msg = None
        reason = None
        if isinstance(body, dict):
            err_obj = body.get("error") if isinstance(body.get("error"), dict) else None
            if err_obj:
                msg = err_obj.get("message")
                reason = err_obj.get("code") or err_obj.get("type") or err_obj.get("reason")
        if status == 401:
            lowered = ""
            if reason:
                lowered = str(reason).lower()
            elif msg:
                lowered = str(msg).lower()
            is_terminal = any(t in lowered for t in TERMINAL_AUTH_REASONS)
            return ErrorInfo(
                category=ErrorCategory.AUTH_FATAL if is_terminal else ErrorCategory.AUTH_TEMP,
                http_status=status,
                error_reason=reason,
                error_message=msg,
                raw=err,
            )
        if status == 402:
            return ErrorInfo(category=ErrorCategory.BILLING, http_status=status,
                             error_reason="billing_exhausted", error_message=msg, raw=err)
        if status == 429:
            return ErrorInfo(category=ErrorCategory.RATE_LIMIT, http_status=status,
                             error_reason="rate_limit", error_message=msg, raw=err)
        if status >= 500:
            return ErrorInfo(category=ErrorCategory.TRANSIENT, http_status=status,
                             error_reason=f"http_{status}", error_message=msg, raw=err)
        return ErrorInfo(category=ErrorCategory.CLIENT, http_status=status,
                         error_reason=reason or f"http_{status}", error_message=msg, raw=err)

    if isinstance(err, (httpx.TimeoutException, httpx.ConnectError, httpx.ConnectTimeout,
                        httpx.RemoteProtocolError, httpx.NetworkError)):
        return ErrorInfo(category=ErrorCategory.TRANSIENT, http_status=None,
                         error_reason=type(err).__name__, error_message=str(err)[:200], raw=err)

    return ErrorInfo(category=ErrorCategory.CLIENT, http_status=None,
                     error_reason=type(err).__name__, error_message=str(err)[:200], raw=err)


# ============================================================
# 3. 自定义异常
# ============================================================

class RetryFatal(Exception):
    """重试期间遇到不可恢复错误（BILLING / AUTH_FATAL）。"""

    def __init__(self, error_info: ErrorInfo, last_attempt_index: int) -> None:
        super().__init__(f"RetryFatal({error_info}) after {last_attempt_index+1} attempts")
        self.error_info = error_info
        self.last_attempt_index = last_attempt_index


class RetryExhausted(Exception):
    """可重试错误全部用完 max_attempts。"""

    def __init__(self, last_error_info: ErrorInfo, total_attempts: int) -> None:
        super().__init__(f"RetryExhausted({last_error_info}) after {total_attempts} attempts")
        self.last_error_info = last_error_info
        self.total_attempts = total_attempts


# ============================================================
# 4. RetryAttempt：每次尝试的句柄
# ============================================================

@dataclass
class RetryAttempt:
    """单次重试句柄（在 async for 循环里 yield）。

    用法：
        async for attempt in ctx:
            try:
                result = ... do call ...
                await attempt.success()
                # 此时 ctx 会 break 循环
            except Exception as e:
                await attempt.fail(e)
                # 此时 ctx 会 sleep 退避 + 下一轮（或抛出 RetryFatal / Exhausted）
    """
    attempt_index: int                # 0-based
    # 内部回调（由 RetryContext 设置）
    _on_success: Optional[Callable[[], Awaitable[None]]] = field(default=None, repr=False)
    _on_fail: Optional[Callable[[ErrorInfo], Awaitable["RetryDecision"]]] = field(default=None, repr=False)

    async def success(self) -> None:
        """调用成功回调：标记 ctx 完成。"""
        if self._on_success is not None:
            await self._on_success()

    async def fail(self, err: Exception) -> None:
        """调用失败回调：分类 + 退避 + 决定下一轮。"""
        if self._on_fail is None:
            raise err
        info = classify_httpx_error(err)
        await self._on_fail(info)


# ============================================================
# 5. RetryDecision & RetryContext
# ============================================================

class RetryDecision(str, Enum):
    RETRY_NEXT_CREDENTIAL = "retry_next"     # 切换 key，退避后重试
    RETRY_SAME_CREDENTIAL = "retry_same"     # 同 key，退避后重试（5xx transient 无池）
    FATAL_FALLBACK = "fatal"                 # 致命错误，抛 RetryFatal
    EXHAUSTED = "exhausted"                  # 重试耗尽，抛 RetryExhausted


class RetryContext:
    """retry_context 上下文管理器（async with + async for 双重协议）。

    内部状态机：
      1. 每轮 yield RetryAttempt
      2. attempt.success() → done
      3. attempt.fail(exc) → classify → 退避 sleep → 进入下一轮 / 抛异常
    """

    def __init__(
        self,
        *,
        max_attempts: int,
        error_classifier: Optional[Callable[[Exception], ErrorInfo]] = None,
        backoff_base_ms: int = 1000,
    ) -> None:
        self.max_attempts = max(1, max_attempts)
        self.classifier = error_classifier or classify_httpx_error
        self.backoff_base_ms = backoff_base_ms
        self._attempt_index: int = 0
        self._done: bool = False
        self._last_error: Optional[ErrorInfo] = None

    # ------------------------------------------------------------
    # async with 协议
    # ------------------------------------------------------------

    async def __aenter__(self) -> "RetryContext":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        return False

    # ------------------------------------------------------------
    # async for 协议（产生 RetryAttempt）
    # ------------------------------------------------------------

    def __aiter__(self):
        return self

    async def __anext__(self) -> RetryAttempt:
        if self._done:
            raise StopAsyncIteration
        if self._attempt_index >= self.max_attempts:
            last = self._last_error or ErrorInfo(
                category=ErrorCategory.TRANSIENT, http_status=None,
                error_reason="unknown", error_message="max_attempts exceeded", raw=None,
            )
            raise RetryExhausted(last, self.max_attempts)

        return RetryAttempt(
            attempt_index=self._attempt_index,
            _on_success=self._handle_success,
            _on_fail=self._handle_fail,
        )

    # ------------------------------------------------------------
    # 回调：success / fail
    # ------------------------------------------------------------

    async def _handle_success(self) -> None:
        self._done = True
        self._last_error = None

    async def _handle_fail(self, info: ErrorInfo) -> None:
        self._last_error = info
        idx = self._attempt_index
        self._attempt_index += 1

        # Fatal / Billing → 立即抛 RetryFatal
        if info.category.triggers_fallback:
            raise RetryFatal(info, idx)

        # Client（非重试类）→ 直接抛 RetryExhausted（不再尝试）
        if info.category == ErrorCategory.CLIENT:
            raise RetryExhausted(info, idx + 1)

        # 可重试：TRANSIENT / RATE_LIMIT / AUTH_TEMP → 退避 sleep
        sleep_s = self._sleep_for_category(info.category, idx)
        if sleep_s > 0:
            await asyncio.sleep(sleep_s)

        if self._attempt_index >= self.max_attempts:
            raise RetryExhausted(info, self.max_attempts)

    def _sleep_for_category(self, cat: ErrorCategory, attempt_index: int) -> float:
        if cat == ErrorCategory.TRANSIENT:
            i = min(attempt_index, len(TTL_TRANSIENT_LADDER) - 1)
            return float(TTL_TRANSIENT_LADDER[i])
        if cat == ErrorCategory.RATE_LIMIT:
            return TTL_RATE_LIMIT / 4.0
        if cat == ErrorCategory.AUTH_TEMP:
            return 2.0
        return 0.0


# ============================================================
# 6. 工厂：retry_context()（便于 import）
# ============================================================

def retry_context(
    *,
    max_attempts: int = 2,
    error_classifier: Optional[Callable[[Exception], ErrorInfo]] = None,
    backoff_base_ms: int = 1000,
) -> RetryContext:
    """创建 RetryContext（用法见模块 docstring）。"""
    return RetryContext(
        max_attempts=max_attempts,
        error_classifier=error_classifier,
        backoff_base_ms=backoff_base_ms,
    )
