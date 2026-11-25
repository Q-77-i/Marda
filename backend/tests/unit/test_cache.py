"""分布式缓存单测（P5-J4）：Cache 的公开行为。

这里只钉**缓存语义**（命中/回源/空值/TTL/单飞/故障回源），不碰 Redis 协议细节；
真 Redis 的跨实例共享由 `tests/integration/test_cache_redis.py` 覆盖。
"""

from __future__ import annotations

import asyncio

from app.cache import CacheOutcome, MemoryCache, NoopCache, RedisCache


async def test_未命中时加载并回填():
    cache = MemoryCache(prefix="test:")
    calls: list[int] = []

    async def load() -> dict:
        calls.append(1)
        return {"a": 1}

    first = await cache.get_or_load("k", load, ttl_s=60)
    assert first.value == {"a": 1}
    assert first.outcome is CacheOutcome.MISS

    second = await cache.get_or_load("k", load, ttl_s=60)
    assert second.value == {"a": 1}
    assert second.outcome is CacheOutcome.HIT
    assert len(calls) == 1, "第二次必须命中缓存，不能再回源"


async def test_删除后重新加载():
    cache = MemoryCache(prefix="test:", jitter=lambda ttl: ttl)
    calls: list[int] = []

    async def load() -> str:
        calls.append(1)
        return "v"

    await cache.get_or_load("k", load, ttl_s=60)
    await cache.delete("k")
    assert (await cache.get_or_load("k", load, ttl_s=60)).outcome is CacheOutcome.MISS
    assert len(calls) == 2


async def test_TTL过期后重新加载():
    now = [0.0]
    cache = MemoryCache(
        prefix="test:", clock=lambda: now[0], jitter=lambda ttl: ttl
    )
    calls: list[int] = []

    async def load() -> str:
        calls.append(1)
        return "v"

    await cache.get_or_load("k", load, ttl_s=10)
    now[0] = 9.9
    assert (await cache.get_or_load("k", load, ttl_s=10)).outcome is CacheOutcome.HIT

    now[0] = 10.1
    assert (await cache.get_or_load("k", load, ttl_s=10)).outcome is CacheOutcome.MISS
    assert len(calls) == 2


async def test_空值缓存避免穿透():
    cache = MemoryCache(prefix="test:", jitter=lambda ttl: ttl)
    calls: list[int] = []

    async def load() -> None:
        calls.append(1)
        return None

    first = await cache.get_or_load(
        "k", load, ttl_s=60, null_ttl_s=30, cache_null=True
    )
    assert first.value is None and first.outcome is CacheOutcome.MISS

    second = await cache.get_or_load(
        "k", load, ttl_s=60, null_ttl_s=30, cache_null=True
    )
    assert second.value is None and second.outcome is CacheOutcome.HIT
    assert len(calls) == 1

    # 未开启空值缓存时，空结果不落缓存
    third = await cache.get_or_load("k2", load, ttl_s=60)
    fourth = await cache.get_or_load("k2", load, ttl_s=60)
    assert third.outcome is CacheOutcome.MISS
    assert fourth.outcome is CacheOutcome.MISS
    assert len(calls) == 3


async def test_并发未命中只回源一次():
    """击穿保护：20 个并发请求，只有一个真正查 DB。"""
    cache = MemoryCache(
        prefix="test:", lock_wait_s=1.0, poll_s=0.01, jitter=lambda ttl: ttl
    )
    calls: list[int] = []

    async def load() -> dict:
        calls.append(1)
        await asyncio.sleep(0.05)
        return {"v": 1}

    results = await asyncio.gather(
        *[cache.get_or_load("k", load, ttl_s=60) for _ in range(20)]
    )
    assert len(calls) == 1
    assert all(r.value == {"v": 1} for r in results)


async def test_key按前缀隔离():
    a = MemoryCache(prefix="a:", jitter=lambda ttl: ttl)
    b = MemoryCache(prefix="b:", jitter=lambda ttl: ttl)
    await a.set("k", 1, ttl_s=60)
    assert (await a.get("k")).value == 1
    assert (await b.get("k")).outcome is CacheOutcome.MISS


class _BrokenRedis:
    """所有命令都失败：模拟 Redis 挂掉。"""

    async def get(self, *args, **kwargs):
        raise ConnectionError("redis down")

    async def set(self, *args, **kwargs):
        raise ConnectionError("redis down")

    async def delete(self, *args, **kwargs):
        raise ConnectionError("redis down")

    async def eval(self, *args, **kwargs):
        raise ConnectionError("redis down")

    async def aclose(self):
        return None


async def test_Redis故障时回源且不抛():
    cache = RedisCache(_BrokenRedis(), warn_interval_s=60)
    calls: list[int] = []

    async def load() -> str:
        calls.append(1)
        return "from-db"

    lookup = await cache.get_or_load("k", load, ttl_s=60)
    assert lookup.value == "from-db"
    assert lookup.outcome is CacheOutcome.BYPASS

    await cache.set("k", "v", ttl_s=60)
    await cache.delete("k")
    await cache.close()


async def test_损坏值与版本不匹配按未命中():
    class _StaticRedis:
        def __init__(self, value):
            self.value = value

        async def get(self, key):
            return self.value

    broken = RedisCache(_StaticRedis(b"{not-json"), warn_interval_s=60)
    assert (await broken.get("k")).outcome is CacheOutcome.MISS

    old = RedisCache(
        _StaticRedis(b'{"v":99,"data":"old"}'), warn_interval_s=60
    )
    assert (await old.get("k")).outcome is CacheOutcome.MISS

    missing = RedisCache(_StaticRedis(b'{"v":1}'), warn_interval_s=60)
    assert (await missing.get("k")).outcome is CacheOutcome.MISS


async def test_缓存禁用时总是回源():
    cache = NoopCache()
    calls: list[int] = []

    async def load() -> str:
        calls.append(1)
        return "db"

    first = await cache.get_or_load("k", load, ttl_s=60)
    second = await cache.get_or_load("k", load, ttl_s=60)
    assert first.outcome is CacheOutcome.BYPASS
    assert second.outcome is CacheOutcome.BYPASS
    assert len(calls) == 2
