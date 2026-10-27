"""Redis 协调层集成测试（P4-E4）：会话锁 / 限流 / 全局 LLM 槽**跨 worker**生效。

**需要真 Redis**，默认跳过：

    export MARDA_TEST_REDIS_URL=redis://localhost:6379/1
    uv run pytest -m redis

这里的关键是「两个协调器实例 = 两个 worker」：进程内的锁在多 worker 下互不可见，
Redis 里的锁才共享。不带这个断言的测试等于没测多 worker。
"""

from __future__ import annotations

import os
import uuid

import pytest

from app.coordination import RedisCoordinator

pytestmark = pytest.mark.redis

REDIS_URL = os.environ.get("MARDA_TEST_REDIS_URL")
requires_redis = pytest.mark.skipif(not REDIS_URL, reason="未配置 MARDA_TEST_REDIS_URL")


@pytest.fixture
async def coordinators():
    """两个「worker」各自的协调器（各自一条 Redis 连接）。"""
    from redis.asyncio import Redis

    a = RedisCoordinator(Redis.from_url(REDIS_URL), slots={"flash": 2, "pro": 1})
    b = RedisCoordinator(Redis.from_url(REDIS_URL), slots={"flash": 2, "pro": 1})
    yield a, b
    for coordinator in (a, b):
        await coordinator.close()


@pytest.fixture
def unique_user():
    """每次用例一个**唯一用户 id**。

    限流 key 是 `rl:user:{id}:{minute}`——**分钟窗口**。用固定 id 的话，
    同一个分钟里跑第二遍用例时计数是从上次接着累的（Redis 里还有上次的 key），
    断言就会莫名其妙地红。这不是产品缺陷，是测试没隔离。
    """
    return f"u-{uuid.uuid4().hex[:8]}"


@requires_redis
async def test_会话锁跨worker互斥(coordinators):
    a, b = coordinators
    interview_id = f"iv-{uuid.uuid4().hex[:8]}"
    token = await a.acquire_session(interview_id)
    assert token
    assert await b.acquire_session(interview_id) is None, "另一个 worker 不该拿到同一场的锁"
    await a.release_session(interview_id, token)
    assert await b.acquire_session(interview_id) is not None


@requires_redis
async def test_限流跨worker共享计数(coordinators, unique_user):
    a, b = coordinators
    assert await a.allow_request(unique_user, limit=2) is True
    assert await b.allow_request(unique_user, limit=2) is True
    assert await a.allow_request(unique_user, limit=2) is False, "限流计数必须是全局的"


@requires_redis
async def test_LLM槽是全局的(coordinators):
    """两个 worker 各拿一个 → 全局上限 2 就满了，第三个拿不到。"""
    a, b = coordinators
    first = await a.acquire_slot("flash", timeout=0.5)
    second = await b.acquire_slot("flash", timeout=0.5)
    assert first and second
    assert await a.acquire_slot("flash", timeout=0.5) is None
    await a.release_slot(first)
    assert await b.acquire_slot("flash", timeout=0.5) is not None


@requires_redis
async def test_锁的TTL会自动过期(coordinators):
    """worker 崩了不能把场次永久卡死——锁靠 TTL 过期。"""
    import asyncio

    a, b = coordinators
    interview_id = f"iv-ttl-{uuid.uuid4().hex[:8]}"
    token = await a.acquire_session(interview_id)
    assert token
    # 手工把 TTL 改短（模拟 worker 崩了来不及释放）
    await a._client.expire(f"interview:{interview_id}:lock", 1)  # noqa: SLF001
    await asyncio.sleep(1.2)
    assert await b.acquire_session(interview_id) is not None
