"""Redis 缓存集成测试（P5-J4）：两个缓存实例 = 两个 worker / 两个业务实例。

**需要真 Redis**，默认跳过：

    export MARDA_TEST_REDIS_URL=redis://localhost:6379/1
    uv run pytest -m redis

单元测试用 MemoryCache 钉语义；这里钉的是「共享」——A 写、B 读，A 删、B 未命中。
"""

from __future__ import annotations

import asyncio
import os
import uuid

import pytest
from redis.asyncio import Redis

from app.cache import CacheOutcome, RedisCache

pytestmark = pytest.mark.redis

REDIS_URL = os.environ.get("MARDA_TEST_REDIS_URL")
requires_redis = pytest.mark.skipif(not REDIS_URL, reason="未配置 MARDA_TEST_REDIS_URL")


@pytest.fixture
async def caches():
    """两个独立连接（模拟两个 worker），共用一个测试前缀。"""
    prefix = f"marda:cache:test:{uuid.uuid4().hex}:"
    clients = [Redis.from_url(REDIS_URL), Redis.from_url(REDIS_URL)]
    a = RedisCache(clients[0], prefix=prefix, jitter=lambda ttl: ttl)
    b = RedisCache(clients[1], prefix=prefix, jitter=lambda ttl: ttl)
    yield a, b
    for client in clients:
        async for key in client.scan_iter(match=f"{prefix}*"):
            await client.delete(key)
        await client.aclose()


@requires_redis
async def test_跨实例共享缓存(caches):
    a, b = caches
    await a.set("k", {"v": 1}, ttl_s=60)
    lookup = await b.get("k")
    assert lookup.outcome is CacheOutcome.HIT
    assert lookup.value == {"v": 1}


@requires_redis
async def test_删除后两个实例都未命中(caches):
    a, b = caches
    await a.set("k", "v", ttl_s=60)
    await a.delete("k")
    assert (await b.get("k")).outcome is CacheOutcome.MISS


@requires_redis
async def test_TTL自然过期(caches):
    a, b = caches
    await a.set("k", "v", ttl_s=1)
    await asyncio.sleep(1.2)
    assert (await b.get("k")).outcome is CacheOutcome.MISS


@requires_redis
async def test_跨实例并发未命中只回源一次(caches):
    """击穿保护必须跨进程生效：单飞锁也在 Redis 里。"""
    a, b = caches
    calls: list[int] = []

    async def load() -> dict:
        calls.append(1)
        await asyncio.sleep(0.05)
        return {"v": 1}

    results = await asyncio.gather(
        *[
            (a if index % 2 == 0 else b).get_or_load("k", load, ttl_s=60)
            for index in range(10)
        ]
    )
    assert len(calls) == 1
    assert all(r.value == {"v": 1} for r in results)
