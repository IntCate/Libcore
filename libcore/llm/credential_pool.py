"""多凭据池（CredentialPool）。

翻译自旧架构 ``llm_node_vendors/credential_pool.py``，原生重写（去 app 依赖）。

核心能力：
  1. 3 种挑选策略：fill_first / round_robin / least_used
  2. 4 类 HTTP code 差异化冷却 TTL：
     - 401 (Auth)         → TTL_SHORT (5min)；Terminal Auth Reason 列表直接标 DEAD
     - 429 (Rate Limit)   → TTL_RATE (60s/单 key，可叠加全局配额窗口)
     - 402 (Billing Exh.) → TTL_BILLING (1h)；会触发 fallback 链
     - 5xx (Server)       → TTL_TRANSIENT (指数退避：1s / 3s / 10s / 30s 递增)
  3. Terminal Auth Reason 列表：匹配 token_invalidated / token_revoked / invalid_token /
     invalid_grant / expired_token 等错误 → DEAD 永不恢复直到写侧刷新
  4. billing exhausted（402）会触发 fallback 链标记：pool.next_fallback_hint()
  5. 按 provider_slug 分组；同一 provider 可多 key 池化
  6. 线程/协程安全：所有状态操作加 asyncio.Lock

典型用法：
    pool = CredentialPool(
        provider_slug="OpenAI",
        strategy="round_robin",
        entries=[
            CredentialEntry(id="key-1", credentials=ProviderCredentials(api_key="sk-xxx")),
            CredentialEntry(id="key-2", credentials=ProviderCredentials(api_key="sk-yyy")),
        ],
    )

    entry = await pool.pick()
    if entry is None:
        hint = pool.next_fallback_hint()
        ...

    await pool.report_result(entry.id, http_status=429, error_message="Rate limit")
    # 或
    await pool.report_result(entry.id, http_status=401, error_reason="invalid_token")  # DEAD

不变量守护：
    仅 import 标准库（asyncio / time / dataclasses / enum），零 app 依赖，零 pydantic。
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

from .credential_pool_store import CredentialPoolStore


# ============================================================
# 0. 极简凭据（替代旧 spi.ProviderCredentials，去 pydantic）
# ============================================================

@dataclass
class ProviderCredentials:
    """供应商凭据（极简 dataclass，替代旧 pydantic 模型）。

    LLM-7 不变量：密钥不进 repr / str（避免日志 / 异常栈泄露）。
    """
    api_key: Optional[str] = None
    oauth_access_token: Optional[str] = None
    # AWS Bedrock
    aws_access_key_id: Optional[str] = None
    aws_secret_access_key: Optional[str] = None
    aws_region: Optional[str] = None
    # Google Vertex / Generative AI
    gcp_service_account_json: Optional[str] = None
    gcp_project_id: Optional[str] = None
    gcp_location: Optional[str] = None
    # Ollama 等无 key 场景
    no_auth: bool = False

    def __repr__(self) -> str:
        """LLM-7：密钥不进 repr（避免日志 / 异常栈泄露）。"""
        return f"ProviderCredentials(api_key={'***' if self.api_key else None}, no_auth={self.no_auth})"

    def __str__(self) -> str:
        return self.__repr__()


# ============================================================
# 1. 枚举与数据结构
# ============================================================

class CredentialStatus(str, Enum):
    """凭据状态。"""
    AVAILABLE = "available"
    COOLING = "cooling"          # 临时冷却，cooldown_until 后恢复
    DEAD = "dead"                # 永久失效（Terminal Auth / 402 billing exhausted），需写侧刷新


class PoolStrategy(str, Enum):
    """挑选策略。"""
    FILL_FIRST = "fill_first"    # 按 entries 顺序，返回第一个可用；429 后再切下一个
    ROUND_ROBIN = "round_robin"  # 轮询，rr_cursor 取模，跳过不可用
    LEAST_USED = "least_used"    # 选 success_count - failure_count 最大者（最健康），跳过不可用


# 4 类 HTTP code → 默认冷却 TTL（秒）
TTL_SHORT_AUTH = 5 * 60           # 401: 5 分钟
TTL_RATE_LIMIT = 60               # 429: 60 秒
TTL_BILLING_EXHAUSTED = 60 * 60   # 402: 1 小时（billing exhausted）
# 5xx 指数退避阶梯（第 n 次失败对应秒数，超过则用最后一级）
TTL_TRANSIENT_LADDER = [1, 3, 10, 30]

# Terminal Auth Reason（命中即 DEAD）
TERMINAL_AUTH_REASONS = (
    "token_invalidated",
    "token_revoked",
    "invalid_token",
    "invalid_grant",
    "expired_token",
    "account_closed",
    "account_disabled",
)


@dataclass
class CredentialEntry:
    """池中单条凭据。"""
    id: str
    credentials: ProviderCredentials
    status: CredentialStatus = CredentialStatus.AVAILABLE
    cooldown_until: float = 0.0           # 冷却到期时间（epoch seconds）
    # 统计（least_used 用；可被外部监控上报）
    success_count: int = 0
    failure_count: int = 0
    consecutive_5xx_count: int = 0        # 用于 5xx 指数退避阶梯
    # 标签（可选）
    labels: Dict[str, Any] = field(default_factory=dict)

    def is_available(self, now: Optional[float] = None) -> bool:
        """当前是否可用（AVAILABLE 且冷却到期）。"""
        if self.status == CredentialStatus.DEAD:
            return False
        if self.status == CredentialStatus.COOLING:
            t = now if now is not None else time.time()
            if t < self.cooldown_until:
                return False
            # 冷却到期 → 自动恢复 AVAILABLE
            self.status = CredentialStatus.AVAILABLE
        return self.status == CredentialStatus.AVAILABLE

    def health_score(self) -> int:
        """健康分数（least_used 用）：success - failure，越大越好。"""
        return self.success_count - self.failure_count

    def next_transient_ttl(self) -> int:
        """下一级 5xx TTL（阶梯递增）。"""
        idx = min(self.consecutive_5xx_count, len(TTL_TRANSIENT_LADDER) - 1)
        return TTL_TRANSIENT_LADDER[idx]

    def mark_success(self) -> None:
        """成功回调：清零连续 5xx 计数，恢复 AVAILABLE。"""
        self.success_count += 1
        self.consecutive_5xx_count = 0
        self.status = CredentialStatus.AVAILABLE

    def mark_cooling(self, ttl_seconds: int) -> None:
        """临时冷却。"""
        self.status = CredentialStatus.COOLING
        self.cooldown_until = time.time() + ttl_seconds

    def mark_dead(self, reason: str = "") -> None:
        """永久失效（Terminal Auth 或 Billing Exhausted）。

        DEAD 需要重启进程或调用 CredentialPool.revive_dead(entry_id, new_creds)。
        """
        self.status = CredentialStatus.DEAD
        self.labels["dead_reason"] = reason


# ============================================================
# 2. CredentialPool 主体
# ============================================================

class CredentialPool:
    """多凭据池（按 provider_slug 隔离）。"""

    def __init__(
        self,
        *,
        provider_slug: str,
        strategy: PoolStrategy | str = PoolStrategy.ROUND_ROBIN,
        entries: Optional[List[CredentialEntry]] = None,
        store: Optional[CredentialPoolStore] = None,
    ) -> None:
        self.provider_slug = provider_slug
        if isinstance(strategy, str):
            strategy = PoolStrategy(strategy)
        self.strategy = strategy
        # 状态持久化（仅状态/计数，不落盘密钥）
        # 必须先赋值 _store：add_entry → _persist_entry 依赖它
        self._store = store
        # id → entry 映射 + entries 列表（保持原始顺序）
        self._entries: Dict[str, CredentialEntry] = {}
        self._entries_list: List[CredentialEntry] = []
        for e in (entries or []):
            self._add_entry_internal(e)
        # 轮询游标
        self._rr_cursor: int = 0
        # 上次 billing exhausted 的时间（用于 fallback hint）
        self._billing_fail_timestamp: float = 0.0
        # 协程安全锁
        self._lock = asyncio.Lock()
        if store is not None:
            self._restore_state()
            # 恢复后统一写回（add_entry 不 persist，避免覆盖 DB 里的 DEAD/COOLING）
            for e in self._entries_list:
                self._persist_entry(e)

    # ------------------------------------------------------------
    # 持久化（store 提供时生效）
    # ------------------------------------------------------------

    def _restore_state(self) -> None:
        """从 store 恢复条目状态（重启保留 COOLING/DEAD/计数）。

        只恢复**已存在**条目的状态；过期冷却按 AVAILABLE 处理（不写回，
        pick 时 is_available 会自动恢复）。
        """
        try:
            persisted = self._store.load_entries(self.provider_slug)
        except Exception:  # noqa: BLE001 — 持久化不可用降级为纯内存
            return
        now = time.time()
        for row in persisted:
            entry = self._entries.get(row["entry_id"])
            if entry is None:
                continue
            if row["status"] == "dead":
                entry.status = CredentialStatus.DEAD
                entry.labels["dead_reason"] = (
                    row.get("dead_reason") or "persisted_dead"
                )
            elif row["status"] == "cooling":
                if float(row.get("cooldown_until") or 0) > now:
                    entry.status = CredentialStatus.COOLING
                    entry.cooldown_until = float(row["cooldown_until"])
            entry.success_count = int(row.get("success_count") or 0)
            entry.failure_count = int(row.get("failure_count") or 0)
            entry.consecutive_5xx_count = int(row.get("consecutive_5xx_count") or 0)

    def _persist_entry(self, entry: CredentialEntry) -> None:
        """持久化单条 entry 状态（失败不影响主链路）。"""
        if self._store is None:
            return
        try:
            self._store.save_entry(self.provider_slug, entry)
        except Exception:  # noqa: BLE001 — 持久化失败不影响主链路
            pass

    # ------------------------------------------------------------
    # 管理 API（add / remove / revive）
    # ------------------------------------------------------------

    def add_entry(self, entry: CredentialEntry) -> None:
        """添加凭据条目（同 id 覆盖），并持久化。"""
        self._add_entry_internal(entry)
        self._persist_entry(entry)

    def _add_entry_internal(self, entry: CredentialEntry) -> None:
        """仅加入内存（不持久化）。__init__ 恢复状态前使用。"""
        if entry.id in self._entries:
            # 覆盖：把旧的位置保留（保持 FILL_FIRST 顺序稳定）
            idx = self._entries_list.index(self._entries[entry.id])
            self._entries_list[idx] = entry
        else:
            self._entries_list.append(entry)
        self._entries[entry.id] = entry

    def remove_entry(self, entry_id: str) -> bool:
        """移除凭据条目。返回是否找到。"""
        if entry_id not in self._entries:
            return False
        entry = self._entries.pop(entry_id)
        self._entries_list.remove(entry)
        if self._store is not None:
            try:
                self._store.delete_entry(self.provider_slug, entry_id)
            except Exception:  # noqa: BLE001
                pass
        return True

    def revive_dead(self, entry_id: str, new_credentials: Optional[ProviderCredentials] = None) -> bool:
        """写侧刷新：将 DEAD 条目复活（可选替换 credentials）。

        通常由 SettingService 的"更新密钥"事件触发。
        """
        entry = self._entries.get(entry_id)
        if entry is None:
            return False
        if new_credentials is not None:
            entry.credentials = new_credentials
        entry.status = CredentialStatus.AVAILABLE
        entry.cooldown_until = 0.0
        entry.consecutive_5xx_count = 0
        entry.labels.pop("dead_reason", None)
        self._persist_entry(entry)
        return True

    # ------------------------------------------------------------
    # 挑选 API
    # ------------------------------------------------------------

    async def pick(self) -> Optional[CredentialEntry]:
        """挑选一条可用凭据（协程安全）。

        Returns:
            可用 CredentialEntry，若全部不可用返回 None。
        """
        async with self._lock:
            return self._pick_unsafe()

    def pick_sync(self) -> Optional[CredentialEntry]:
        """同步版本（非协程上下文使用）。"""
        return self._pick_unsafe()

    def _pick_unsafe(self) -> Optional[CredentialEntry]:
        """无锁挑选（调用方保证互斥）。"""
        now = time.time()
        if self.strategy == PoolStrategy.FILL_FIRST:
            return self._pick_fill_first(now)
        if self.strategy == PoolStrategy.ROUND_ROBIN:
            return self._pick_round_robin(now)
        if self.strategy == PoolStrategy.LEAST_USED:
            return self._pick_least_used(now)
        # 默认 round_robin
        return self._pick_round_robin(now)

    def _pick_fill_first(self, now: float) -> Optional[CredentialEntry]:
        """FILL_FIRST：按 entries 原始顺序返回第一个可用。"""
        for e in self._entries_list:
            if e.is_available(now):
                return e
        return None

    def _pick_round_robin(self, now: float) -> Optional[CredentialEntry]:
        """ROUND_ROBIN：从 _rr_cursor 开始遍历，跳过不可用，取下一个可用。

        找不到时保留 cursor 不变（不破坏下一轮公平性）。
        """
        if not self._entries_list:
            return None
        n = len(self._entries_list)
        start = self._rr_cursor
        for i in range(n):
            idx = (start + i) % n
            e = self._entries_list[idx]
            if e.is_available(now):
                self._rr_cursor = (idx + 1) % n
                return e
        return None

    def _pick_least_used(self, now: float) -> Optional[CredentialEntry]:
        """LEAST_USED：选健康分数最高的可用条目。

        健康分数相同时，按顺序取第一个（FILL_FIRST 作为 tie-break）。
        """
        best: Optional[CredentialEntry] = None
        best_score = None
        for e in self._entries_list:
            if not e.is_available(now):
                continue
            score = e.health_score()
            if best is None or score > best_score:
                best = e
                best_score = score
        return best

    # ------------------------------------------------------------
    # 结果上报 API
    # ------------------------------------------------------------

    async def report_result(
        self,
        entry_id: str,
        *,
        success: Optional[bool] = None,
        http_status: Optional[int] = None,
        error_message: Optional[str] = None,
        error_reason: Optional[str] = None,
    ) -> None:
        """上报调用结果（协程安全）。

        参数优先级（推断 success / 冷却策略）：
          1. success=True → mark_success()
          2. error_reason in TERMINAL_AUTH_REASONS → DEAD
          3. http_status → 按 4 类分级
          4. success=False → 短暂冷却（TTL_TRANSIENT_LADDER[0]）
        """
        async with self._lock:
            entry = self._entries.get(entry_id)
            if entry is None:
                return
            # 显式成功
            if success is True:
                entry.mark_success()
                self._persist_entry(entry)
                return
            # Terminal Auth → DEAD
            if error_reason and error_reason.lower() in TERMINAL_AUTH_REASONS:
                entry.mark_dead(reason=f"terminal_auth:{error_reason}")
                entry.failure_count += 1
                self._persist_entry(entry)
                return
            # 按 HTTP status 分类
            if http_status is not None:
                self._apply_http_status(entry, http_status)
                entry.failure_count += 1
                self._persist_entry(entry)
                return
            # 显式失败 → 短暂冷却
            if success is False:
                entry.consecutive_5xx_count += 1
                entry.mark_cooling(TTL_TRANSIENT_LADDER[0])
                entry.failure_count += 1
                self._persist_entry(entry)
                return
            # 无信息 → 不处理

    def _apply_http_status(self, entry: CredentialEntry, status: int) -> None:
        """按 HTTP 状态码应用冷却策略。"""
        if 200 <= status < 300:
            entry.mark_success()
            return
        if status == 401:
            entry.mark_cooling(TTL_SHORT_AUTH)
            return
        if status == 429:
            entry.mark_cooling(TTL_RATE_LIMIT)
            return
        if status == 402:
            # billing exhausted → 1h 冷却 + 触发 fallback 标记
            entry.mark_cooling(TTL_BILLING_EXHAUSTED)
            self._billing_fail_timestamp = time.time()
            return
        if status == 403:
            # 授权错误通常较严重 → 长时间冷却
            entry.mark_cooling(TTL_SHORT_AUTH * 12)
            return
        if status >= 500:
            entry.consecutive_5xx_count += 1
            entry.mark_cooling(entry.next_transient_ttl())
            return
        # 其他 4xx（400/404/405/406/407/408/409 等）→ 短暂冷却
        entry.mark_cooling(TTL_RATE_LIMIT)

    # ------------------------------------------------------------
    # Fallback 提示
    # ------------------------------------------------------------

    def all_unavailable(self) -> bool:
        """是否全部不可用（DEAD / COOLING 未到期）。"""
        now = time.time()
        return not any(e.is_available(now) for e in self._entries_list)

    def next_fallback_hint(self) -> Dict[str, Any]:
        """返回 fallback 提示（供 auxiliary_fallback_router 使用）。

        包含：
            - trigger: "all_unavailable" | "billing_exhausted" | "none"
            - provider_slug
            - billing_fail_ts
            - dead_count / cooling_count / total_count
        """
        now = time.time()
        total = len(self._entries_list)
        dead = sum(1 for e in self._entries_list if e.status == CredentialStatus.DEAD)
        cooling = sum(1 for e in self._entries_list if e.is_available(now) is False and e.status != CredentialStatus.DEAD)
        billing_recent = (time.time() - self._billing_fail_timestamp) < TTL_BILLING_EXHAUSTED
        if billing_recent and dead == total:
            trigger = "billing_exhausted"
        elif not self.all_unavailable():
            trigger = "none"
        else:
            trigger = "billing_exhausted" if billing_recent else "all_unavailable"
        return {
            "trigger": trigger,
            "provider_slug": self.provider_slug,
            "billing_fail_timestamp": self._billing_fail_timestamp,
            "dead_count": dead,
            "cooling_count": cooling,
            "total_count": total,
        }

    # ------------------------------------------------------------
    # 调试 / 监控
    # ------------------------------------------------------------

    def snapshot(self) -> Dict[str, Any]:
        """返回池状态快照（不泄露密钥）。"""
        now = time.time()
        entries = []
        for e in self._entries_list:
            entries.append({
                "id": e.id,
                "status": e.status.value,
                "cooldown_remaining_s": max(0, int(e.cooldown_until - now)),
                "success": e.success_count,
                "failure": e.failure_count,
                "consecutive_5xx": e.consecutive_5xx_count,
                "health_score": e.health_score(),
            })
        return {
            "provider_slug": self.provider_slug,
            "strategy": self.strategy.value,
            "entries": entries,
            "all_unavailable": self.all_unavailable(),
            "fallback_hint": self.next_fallback_hint(),
        }


# ============================================================
# 3. 全局 PoolManager（按 provider_slug 注册 / 查询）
# ============================================================

# 全局单例
_pool_manager: Optional["PoolManager"] = None


class PoolManager:
    """全局凭据池管理器（按 provider_slug 分发）。

    TD-4：共享单一 CredentialPoolStore（sqlite），冷却/DEAD 状态跨调用
    与跨进程重启保留；store 初始化失败时降级为纯内存。
    """

    def __init__(self, store: Optional[CredentialPoolStore] = None) -> None:
        self._pools: Dict[str, CredentialPool] = {}
        self._store: Optional[CredentialPoolStore] = store
        self._store_attempted = store is not None

    def _get_store(self) -> Optional[CredentialPoolStore]:
        """惰性创建共享 store（失败降级为 None = 纯内存）。"""
        if self._store_attempted:
            return self._store
        self._store_attempted = True
        try:
            self._store = CredentialPoolStore()
        except Exception:  # noqa: BLE001 — 持久化不可用降级为纯内存
            self._store = None
        return self._store

    def get_or_create(
        self,
        *,
        provider_slug: str,
        strategy: PoolStrategy | str = PoolStrategy.ROUND_ROBIN,
        entries: Optional[List[CredentialEntry]] = None,
    ) -> CredentialPool:
        """获取或创建指定 provider_slug 的 pool。

        - 已存在：直接返回（不覆盖 strategy/entries，要改动需显式 update()）
        - 不存在：创建并注册（TD-4：创建时挂共享 store，恢复持久化状态）
        """
        existing = self._pools.get(provider_slug)
        if existing is not None:
            return existing
        pool = CredentialPool(
            provider_slug=provider_slug,
            strategy=strategy,
            entries=entries,
            store=self._get_store(),
        )
        self._pools[provider_slug] = pool
        return pool

    def get(self, provider_slug: str) -> Optional[CredentialPool]:
        """查询 pool（不存在返回 None）。"""
        return self._pools.get(provider_slug)

    def unregister(self, provider_slug: str) -> bool:
        """注销 pool（例如：删除 provider 配置时），并清除持久化状态。"""
        if provider_slug in self._pools:
            self._pools.pop(provider_slug)
            store = self._get_store()
            if store is not None:
                try:
                    store.delete_pool(provider_slug)
                except Exception:  # noqa: BLE001
                    pass
            return True
        return False

    def snapshot_all(self) -> Dict[str, Dict[str, Any]]:
        """所有 pool 的快照（监控用）。"""
        return {slug: p.snapshot() for slug, p in self._pools.items()}


def get_global_pool_manager() -> PoolManager:
    """获取全局 PoolManager 单例。"""
    global _pool_manager
    if _pool_manager is None:
        _pool_manager = PoolManager()
    return _pool_manager


def reset_global_pool_manager() -> None:
    """重置全局单例（测试用）。"""
    global _pool_manager
    _pool_manager = None
