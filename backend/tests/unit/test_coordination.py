"""协调层单测（P4-E4）：会话锁 / per-user 限流 / 全局 LLM 槽。

两个实现（memory / redis）跑**同一套断言**——它们语义必须一致，
否则「切后端 = 换行为」，多 worker 形态就不可信。

Redis 侧用假客户端（不连真 Redis）：这里验的是**协议用法**（SET NX PX / INCR+EXPIRE /
Lua 比对释放），真 Redis 的连通性由 tests/integration 覆盖。
"""

from __future__ import annotations

import pytest

from app.coordination import (
    CoordinationUnavailable,
    MemoryCoordinator,
    RedisCoordinator,
    SlotLease,
)


# ---------- 共享语义（两个实现都跑）----------


@pytest.fixture(params=["memory", "redis"])
async def coordinator(request):
    # 两个实现的槽位容量必须一致（否则“同一套断言”名不副实）
    slots = {"flash": 2, "pro": 1}
    if request.param == "memory":
        yield MemoryCoordinator(slots=slots)
    else:
        yield RedisCoordinator(_FakeRedis(), slots=slots)


async def test_同一场只能有一个持有者(coordinator):
    token = await coordinator.acquire_session("iv-1")
    assert token
    assert await coordinator.acquire_session("iv-1") is None  # 第二个拿不到


async def test_释放后能再拿到锁(coordinator):
    token = await coordinator.acquire_session("iv-1")
    await coordinator.release_session("iv-1", token)
    assert await coordinator.acquire_session("iv-1") is not None


async def test_释放时token不匹配就不删锁(coordinator):
    """A 的锁过期后 B 拿到锁，A 来释放**不能**把 B 的锁删掉。"""
    token_a = await coordinator.acquire_session("iv-1")
    await coordinator.release_session("iv-1", "别人的token")
    assert await coordinator.acquire_session("iv-1") is None  # 锁还在
    await coordinator.release_session("iv-1", token_a)
    assert await coordinator.acquire_session("iv-1") is not None


async def test_限流超限返回False(coordinator):
    assert await coordinator.allow_request("u1", limit=2) is True
    assert await coordinator.allow_request("u1", limit=2) is True
    assert await coordinator.allow_request("u1", limit=2) is False


async def test_限流按用户隔离(coordinator):
    assert await coordinator.allow_request("u1", limit=1) is True
    assert await coordinator.allow_request("u2", limit=1) is True


async def test_tts票据写入后可读(coordinator):
    await coordinator.put_tts_ticket("t-1", "你好", ttl_s=60)
    assert await coordinator.get_tts_ticket("t-1") == "你好"
    assert await coordinator.get_tts_ticket("不存在") is None


async def test_tts票据按实例隔离_memory_单进程语义():
    """memory 后端本来就不跨进程；跨 worker 共享由 Redis 后端负责。"""
    a = MemoryCoordinator(slots={"flash": 1, "pro": 1})
    b = MemoryCoordinator(slots={"flash": 1, "pro": 1})
    await a.put_tts_ticket("t-1", "你好", ttl_s=60)
    assert await b.get_tts_ticket("t-1") is None


async def test_槽位用满后取不到(coordinator):
    first = await coordinator.acquire_slot("flash", timeout=0.2)
    second = await coordinator.acquire_slot("flash", timeout=0.2)
    assert first and second
    assert await coordinator.acquire_slot("flash", timeout=0.2) is None


async def test_释放槽位后又能取到(coordinator):
    lease = await coordinator.acquire_slot("flash", timeout=0.2)
    assert lease is not None
    await coordinator.release_slot(lease)
    assert await coordinator.acquire_slot("flash", timeout=0.2) is not None


async def test_槽位按档位隔离(coordinator):
    flash = await coordinator.acquire_slot("flash", timeout=0.2)
    pro = await coordinator.acquire_slot("pro", timeout=0.2)
    assert flash is not None and pro is not None
    assert flash.tier == "flash" and pro.tier == "pro"


# ---------- Redis 特有语义 ----------


async def test_redis挂掉时会话锁fail_closed():
    """锁是正确性闸门：Redis 不可用必须拒绝（503），不能放行。"""
    broken = RedisCoordinator(_BrokenRedis(), slots={"flash": 1, "pro": 1})
    with pytest.raises(CoordinationUnavailable):
        await broken.acquire_session("iv-1")


async def test_redis挂掉时限流fail_open():
    """限流是保护不是正确性：Redis 不可用要放行。"""
    broken = RedisCoordinator(_BrokenRedis(), slots={"flash": 1, "pro": 1})
    assert await broken.allow_request("u1", limit=1) is True


async def test_redis挂掉时LLM槽回退进程内():
    """上游保护不能因为 Redis 挂了就没了——回退进程内槽位。"""
    broken = RedisCoordinator(_BrokenRedis(), slots={"flash": 1, "pro": 1})
    lease = await broken.acquire_slot("flash", timeout=0.2)
    assert isinstance(lease, SlotLease)


async def test_redis_tts票据跨worker共享():
    """两个协调器实例 = 两个 worker；票据必须在共享存储里，不能各存各的。"""
    fake = _FakeRedis()
    a = RedisCoordinator(fake, slots={"flash": 1, "pro": 1})
    b = RedisCoordinator(fake, slots={"flash": 1, "pro": 1})
    await a.put_tts_ticket("t-1", "你好", ttl_s=60)
    assert await b.get_tts_ticket("t-1") == "你好"


async def test_redis挂掉时tts票据回退进程内():
    """播报是可选能力：Redis 挂了也不能让签发票据直接 500。"""
    broken = RedisCoordinator(_BrokenRedis(), slots={"flash": 1, "pro": 1})
    await broken.put_tts_ticket("t-1", "你好", ttl_s=60)
    assert await broken.get_tts_ticket("t-1") == "你好"


# ---------- 假 Redis ----------


class _FakeRedis:
    """够用的假 Redis：SET NX PX / INCR / EXPIRE / EVAL（只实现比对删除这一支）。"""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.expiry: dict[str, float] = {}

    async def set(self, key, value, nx=False, ex=None):
        if nx and key in self.store:
            return None
        self.store[key] = value if isinstance(value, str) else value.decode()
        return True

    async def get(self, key):
        return self.store.get(key)

    async def incr(self, key):
        self.store[key] = str(int(self.store.get(key, "0")) + 1)
        return int(self.store[key])

    async def expire(self, key, seconds):
        return True

    async def eval(self, script, numkeys, key, token):
        if self.store.get(key) == token:
            del self.store[key]
            return 1
        return 0

    async def aclose(self):
        return None


class _BrokenRedis:
    async def set(self, *args, **kwargs):
        raise ConnectionError("redis down")

    async def get(self, *args, **kwargs):
        raise ConnectionError("redis down")

    async def incr(self, *args, **kwargs):
        raise ConnectionError("redis down")

    async def expire(self, *args, **kwargs):
        raise ConnectionError("redis down")

    async def eval(self, *args, **kwargs):
        raise ConnectionError("redis down")

    async def aclose(self):
        return None
