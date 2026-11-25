"""分布式缓存（P5-J4）：cache-aside + TTL 抖动 + 空值缓存 + 单飞 + 故障回源。

**失败语义与 coordination 不同**：缓存是加速，不是正确性来源。Redis 挂掉时
所有缓存操作都吞异常并回源 DB（`CacheOutcome.BYPASS`），绝不把异常抛给业务；
会话锁仍然是 fail closed（见 `app/coordination.py`）。

两个实现（memory / redis）共用同一套 `BaseCache` 语义：
- memory：本地开发 / 单测，进程内有效（多 worker 不共享）；
- redis：多 worker / 多实例共享，生产形态。

值格式是版本信封 `{"v": 1, "data": ...}` / `{"v": 1, "null": true}`；
版本不匹配或 JSON 损坏按未命中处理——升级不炸老缓存。
"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import logging
import random
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import Enum
from typing import Any, Generic, Protocol, TypeVar

from app.config import Settings

logger = logging.getLogger(__name__)

T = TypeVar("T")

DEFAULT_PREFIX = "marda:cache:"
CACHE_VERSION = 1


class CacheOutcome(str, Enum):
    """一次缓存读取的结果（也用于响应的 X-Cache 头）。"""

    HIT = "HIT"
    MISS = "MISS"
    BYPASS = "BYPASS"  # 缓存不可用 / 被禁用：本次直接回源


@dataclass(frozen=True)
class CacheLookup(Generic[T]):
    value: T | None
    outcome: CacheOutcome


class Cache(Protocol):
    async def get(self, key: str) -> CacheLookup[Any]: ...

    async def set(self, key: str, value: Any, *, ttl_s: float) -> None: ...

    async def delete(self, key: str) -> None: ...

    async def get_or_load(
        self,
        key: str,
        loader: Callable[[], Awaitable[T]],
        *,
        ttl_s: float,
        null_ttl_s: float = 30.0,
        cache_null: bool = False,
    ) -> CacheLookup[T]: ...

    async def close(self) -> None: ...


def _default_jitter(ttl_s: float) -> float:
    """±10% 抖动：避免同一批 key 同时过期（雪崩）。"""
    return max(0.001, ttl_s * random.uniform(0.9, 1.1))


class BaseCache:
    """cache-aside 的共享流程；子类只实现 get / set / delete / 锁原语。"""

    def __init__(
        self,
        *,
        prefix: str = DEFAULT_PREFIX,
        lock_ttl_s: float = 3.0,
        lock_wait_s: float = 0.2,
        poll_s: float = 0.05,
        jitter: Callable[[float], float] | None = None,
    ) -> None:
        self._prefix = prefix
        self._lock_ttl_s = lock_ttl_s
        self._lock_wait_s = lock_wait_s
        self._poll_s = poll_s
        self._jitter = jitter or _default_jitter

    @property
    def enabled(self) -> bool:
        return True

    def _key(self, key: str) -> str:
        return f"{self._prefix}{key}"

    def _lock_key(self, key: str) -> str:
        return f"{self._prefix}lock:{key}"

    def _ttl(self, ttl_s: float) -> float:
        return max(0.001, self._jitter(ttl_s))

    async def get_or_load(
        self,
        key: str,
        loader: Callable[[], Awaitable[T]],
        *,
        ttl_s: float,
        null_ttl_s: float = 30.0,
        cache_null: bool = False,
    ) -> CacheLookup[T]:
        lookup = await self.get(key)
        if lookup.outcome is CacheOutcome.HIT:
            return CacheLookup(lookup.value, CacheOutcome.HIT)
        if lookup.outcome is CacheOutcome.BYPASS:
            return CacheLookup(await loader(), CacheOutcome.BYPASS)

        token = uuid.uuid4().hex
        acquired = await self._try_lock(key, token)
        if acquired is None:
            # 锁原语本身不可用（Redis 故障）：本次回源，不阻塞业务。
            return CacheLookup(await loader(), CacheOutcome.BYPASS)
        if acquired:
            try:
                # 双检：等锁期间别的请求可能已经回填。
                lookup = await self.get(key)
                if lookup.outcome is CacheOutcome.HIT:
                    return CacheLookup(lookup.value, CacheOutcome.HIT)
                value = await loader()
                if value is not None or cache_null:
                    await self.set(
                        key,
                        value,
                        ttl_s=null_ttl_s if value is None else ttl_s,
                    )
                return CacheLookup(value, CacheOutcome.MISS)
            finally:
                await self._unlock(key, token)

        # 没抢到锁：等赢家回填；等不到就回源，但不回填（避免失败者一起写）。
        deadline = time.monotonic() + self._lock_wait_s
        while time.monotonic() < deadline:
            await asyncio.sleep(self._poll_s)
            lookup = await self.get(key)
            if lookup.outcome is CacheOutcome.HIT:
                return CacheLookup(lookup.value, CacheOutcome.HIT)
            if lookup.outcome is CacheOutcome.BYPASS:
                break
        return CacheLookup(await loader(), CacheOutcome.MISS)

    async def _try_lock(self, key: str, token: str) -> bool | None:
        raise NotImplementedError

    async def _unlock(self, key: str, token: str) -> None:
        raise NotImplementedError


@dataclass
class _MemoryEntry:
    value: Any
    expires_at: float
    null: bool


class MemoryCache(BaseCache):
    """进程内缓存：单测 / 本地单进程开发用；多 worker 不共享（用 Redis）。"""

    def __init__(self, *, clock: Callable[[], float] | None = None, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._clock = clock or time.monotonic
        self._entries: dict[str, _MemoryEntry] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    async def get(self, key: str) -> CacheLookup[Any]:
        entry = self._entries.get(key)
        if entry is None:
            return CacheLookup(None, CacheOutcome.MISS)
        if entry.expires_at <= self._clock():
            self._entries.pop(key, None)
            return CacheLookup(None, CacheOutcome.MISS)
        if entry.null:
            return CacheLookup(None, CacheOutcome.HIT)
        return CacheLookup(copy.deepcopy(entry.value), CacheOutcome.HIT)

    async def set(self, key: str, value: Any, *, ttl_s: float) -> None:
        # 本地缓存没有 Redis 的被动淘汰：key 多起来时顺手清过期项，避免长跑进程只增不减。
        if len(self._entries) >= 1024:
            now = self._clock()
            for existing_key, entry in list(self._entries.items()):
                if entry.expires_at <= now:
                    self._entries.pop(existing_key, None)
        self._entries[key] = _MemoryEntry(
            value=copy.deepcopy(value),
            expires_at=self._clock() + self._ttl(ttl_s),
            null=value is None,
        )

    async def delete(self, key: str) -> None:
        self._entries.pop(key, None)

    async def _try_lock(self, key: str, token: str) -> bool | None:
        lock = self._locks.setdefault(key, asyncio.Lock())
        if lock.locked():
            return False
        await lock.acquire()
        return True

    async def _unlock(self, key: str, token: str) -> None:
        lock = self._locks.get(key)
        if lock is not None and lock.locked():
            lock.release()

    async def close(self) -> None:
        self._entries.clear()
        self._locks.clear()


_UNLOCK_LUA = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('del', KEYS[1])
end
return 0
"""


class RedisCache(BaseCache):
    """Redis 缓存：多 worker / 多实例共享；所有 Redis 异常都回源。"""

    def __init__(self, client: Any, *, warn_interval_s: float = 30.0, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._client = client
        self._warn_interval_s = warn_interval_s
        self._last_warn_at = 0.0

    @classmethod
    def from_settings(cls, settings: Settings) -> "RedisCache":
        from app.redis_client import make_redis_client

        client = make_redis_client(
            settings,
            url=settings.cache_redis_url,
            decode_responses=False,
            socket_connect_timeout=settings.cache_redis_timeout_s,
            socket_timeout=settings.cache_redis_timeout_s,
        )
        return cls(client)

    async def get(self, key: str) -> CacheLookup[Any]:
        try:
            raw = await self._client.get(self._key(key))
        except Exception as exc:  # noqa: BLE001 —— 缓存读失败必须回源
            self._warn("GET", key, exc)
            return CacheLookup(None, CacheOutcome.BYPASS)
        if raw is None:
            return CacheLookup(None, CacheOutcome.MISS)
        try:
            text = raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)
            payload = json.loads(text)
            if payload.get("v") != CACHE_VERSION:
                return CacheLookup(None, CacheOutcome.MISS)
            if payload.get("null") is True:
                return CacheLookup(None, CacheOutcome.HIT)
            data = payload.get("data")
            if data is None:  # 缺 data 的信封按损坏处理，不能当成空值命中
                return CacheLookup(None, CacheOutcome.MISS)
            return CacheLookup(data, CacheOutcome.HIT)
        except (ValueError, TypeError):
            # 损坏值按未命中处理；它会随 TTL 过期，不在读路径上做删除。
            return CacheLookup(None, CacheOutcome.MISS)

    async def set(self, key: str, value: Any, *, ttl_s: float) -> None:
        payload: dict[str, Any] = (
            {"v": CACHE_VERSION, "null": True}
            if value is None
            else {"v": CACHE_VERSION, "data": value}
        )
        try:
            text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
            await self._client.set(
                self._key(key), text, ex=max(1, int(round(self._ttl(ttl_s))))
            )
        except Exception as exc:  # noqa: BLE001 —— 写失败只损失一次缓存
            self._warn("SET", key, exc)

    async def delete(self, key: str) -> None:
        try:
            await self._client.delete(self._key(key))
        except Exception as exc:  # noqa: BLE001 —— 删失败靠 TTL 兜底
            self._warn("DEL", key, exc)

    async def _try_lock(self, key: str, token: str) -> bool | None:
        try:
            ok = await self._client.set(
                self._lock_key(key), token, nx=True, ex=max(1, int(self._lock_ttl_s))
            )
        except Exception as exc:  # noqa: BLE001 —— 锁不可用时直接回源
            self._warn("LOCK", key, exc)
            return None
        return bool(ok)

    async def _unlock(self, key: str, token: str) -> None:
        try:
            await self._client.eval(_UNLOCK_LUA, 1, self._lock_key(key), token)
        except Exception as exc:  # noqa: BLE001 —— 锁会 TTL 过期
            self._warn("UNLOCK", key, exc)

    async def close(self) -> None:
        try:
            await self._client.aclose()
        except Exception as exc:  # noqa: BLE001 —— 关连接失败不影响退出
            self._warn("CLOSE", "-", exc)

    def _warn(self, op: str, key: str, exc: Exception) -> None:
        """Redis 故障时限频打日志：缓存挂了不能把日志打爆。

        key 里可能带 user_id（profile / report），日志只留前缀 + 尾段哈希，
        既能定位缓存类型，又不把用户标识写进日志。
        """
        now = time.monotonic()
        if now - self._last_warn_at < self._warn_interval_s:
            return
        self._last_warn_at = now
        head, _, tail = key.partition(":")
        safe_key = f"{head}:{hashlib.sha256(tail.encode('utf-8')).hexdigest()[:8]}"
        logger.warning("缓存 %s 失败（回源 DB）：key=%s err=%s", op, safe_key, exc)


class NoopCache(BaseCache):
    """缓存被禁用：每次都回源，行为与 Redis 故障一致。"""

    @property
    def enabled(self) -> bool:
        return False

    async def get(self, key: str) -> CacheLookup[Any]:
        return CacheLookup(None, CacheOutcome.BYPASS)

    async def set(self, key: str, value: Any, *, ttl_s: float) -> None:
        return None

    async def delete(self, key: str) -> None:
        return None

    async def close(self) -> None:
        return None


def create_cache(settings: Settings) -> Cache:
    """按配置建缓存：默认 memory（本地/测试），生产用 redis。"""
    if not settings.cache_enabled:
        return NoopCache()
    if settings.cache_backend == "redis":
        return RedisCache.from_settings(settings)
    return MemoryCache(prefix=settings.cache_key_prefix)
