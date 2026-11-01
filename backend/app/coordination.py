"""跨进程协调层（P4-E4）：会话锁 + per-user 限流 + 全局 LLM 并发槽 + TTS 票据。

为什么要有这一层：升级前的进程内状态（`Semaphore(16/4)`、会话进行中标记）在
**多 worker 下互不可见**——两个 worker 能同时 resume 同一场面试、各自算各自的闸门，
并发上限被放大 N 倍。这几件事必须搬到 worker 之间共享的地方，Redis 是最轻的选择。

四种用途与失败语义（升级方案 §4.3，**行为要能背出来**）：

| 用途 | Key | Redis 不可用时 |
| --- | --- | --- |
| 会话锁 | `interview:{id}:lock` | **fail closed** → 503（宁可拒绝，也不能两 worker 同时 resume） |
| per-user 限流 | `rl:user:{id}:{minute}` | **fail open** → 放行（限流是保护，不是正确性） |
| 全局 LLM 槽 | `llm:{tier}:slot:{i}` | 回退**进程内**闸门（上游保护降级但不失效） |
| TTS 票据 | `tts:ticket:{ticket}` | 回退**进程内**（播报是可选能力，不阻断面试） |

后端选择：`COORDINATION_BACKEND=memory|redis`，默认 memory（单进程 demo / 单测），
生产 profile 用 redis。两种实现**同一套接口**，调用方不感知。

状态权威仍然在 PG checkpointer——Redis 里**不存面试状态**，只存锁、计数与 60s 票据。
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass
from typing import Protocol

from app.config import Settings

logger = logging.getLogger(__name__)


class CoordinationUnavailable(Exception):
    """协调后端不可用（Redis 连不上等）。

    会话锁用它 **fail closed**（503）：拿不到锁就不能确认「这一场没人正在跑」。
    """


@dataclass(frozen=True)
class SlotLease:
    """一个已占用的 LLM 槽位凭证。release 必须带它（防止误释放别人的槽）。"""

    tier: str
    token: str
    slot: str


class Coordinator(Protocol):
    """协调层接口（memory / redis 两个实现同构）。"""

    async def acquire_session(self, interview_id: str) -> str | None:
        """取会话锁；拿不到返回 None（**不抛异常**：并发是正常情况）。"""
        ...

    async def release_session(self, interview_id: str, token: str) -> None:
        """释放会话锁（token 不匹配则不删——防误删别人的锁）。"""
        ...

    async def allow_request(self, user_id: str, *, limit: int, window_s: int = 60) -> bool:
        """per-user 限流：超限返回 False。"""
        ...

    async def acquire_slot(self, tier: str, *, timeout: float) -> SlotLease | None:
        """取一个全局 LLM 槽；取不到（超时）返回 None。"""
        ...

    async def release_slot(self, lease: SlotLease) -> None:
        """释放 LLM 槽。"""
        ...

    async def put_tts_ticket(self, ticket: str, text: str, *, ttl_s: float) -> None:
        """存 TTS 播报票据（多 worker 共享；只存待播文本，不含音频）。"""
        ...

    async def get_tts_ticket(self, ticket: str) -> str | None:
        """取 TTS 播报票据；不存在 / 过期返回 None。"""
        ...

    async def close(self) -> None:
        """收尾（关连接）。"""
        ...


class MemoryCoordinator:
    """进程内协调（默认）：单进程形态成立，多 worker 下**各管各的**。

    刻意保留它：单测、demo、CI 都不该依赖 Redis；Redis 挂掉时它也是 LLM 槽的兜底。
    """

    def __init__(self, slots: dict[str, int] | None = None) -> None:
        self._sessions: dict[str, tuple[str, float]] = {}  # interview_id → (token, 过期时间)
        self._rate: dict[str, int] = {}  # "user:window" → 计数
        self._tts_tickets: dict[str, tuple[str, float]] = {}  # ticket → (text, 过期时间)
        # tier → 槽位 token 列表（None = 空闲）。**容量在这里定死**，
        # 与 Redis 侧的槽位数保持同一口径（否则两个后端行为不一致）。
        self._slots: dict[str, list[str | None]] = {
            tier: [None] * count for tier, count in (slots or {"flash": 16, "pro": 4}).items()
        }
        self._lock = asyncio.Lock()

    async def acquire_session(self, interview_id: str) -> str | None:
        now = time.monotonic()
        async with self._lock:
            entry = self._sessions.get(interview_id)
            if entry and entry[1] > now:
                return None
            token = uuid.uuid4().hex
            self._sessions[interview_id] = (token, now + 600)
            return token

    async def release_session(self, interview_id: str, token: str) -> None:
        async with self._lock:
            entry = self._sessions.get(interview_id)
            if entry and entry[0] == token:
                self._sessions.pop(interview_id, None)

    async def allow_request(self, user_id: str, *, limit: int, window_s: int = 60) -> bool:
        window = int(time.time() // window_s)
        key = f"{user_id}:{window}"
        async with self._lock:
            count = self._rate.get(key, 0) + 1
            self._rate[key] = count
            if len(self._rate) > 4096:  # 简易防脏：窗口键无限增长会漏内存
                for stale in list(self._rate)[:1024]:
                    if stale != key:
                        self._rate.pop(stale, None)
            return count <= limit

    async def acquire_slot(self, tier: str, *, timeout: float) -> SlotLease | None:
        deadline = time.monotonic() + timeout
        async with self._lock:
            slots = self._slots.setdefault(tier, [])
            for index, value in enumerate(slots):
                if value is None:
                    token = uuid.uuid4().hex
                    slots[index] = token
                    return SlotLease(tier=tier, token=token, slot=str(index))
        # 全满：有界等待（和进程内闸门同语义），不无限排队
        while time.monotonic() < deadline:
            await asyncio.sleep(0.05)
            async with self._lock:
                slots = self._slots.setdefault(tier, [])
                for index, value in enumerate(slots):
                    if value is None:
                        token = uuid.uuid4().hex
                        slots[index] = token
                        return SlotLease(tier=tier, token=token, slot=str(index))
        return None

    async def release_slot(self, lease: SlotLease) -> None:
        async with self._lock:
            slots = self._slots.get(lease.tier)
            if not slots:
                return
            try:
                index = int(lease.slot)
            except ValueError:
                return
            if 0 <= index < len(slots) and slots[index] == lease.token:
                slots[index] = None

    async def put_tts_ticket(self, ticket: str, text: str, *, ttl_s: float) -> None:
        now = time.monotonic()
        async with self._lock:
            self._tts_tickets[ticket] = (text, now + ttl_s)
            if len(self._tts_tickets) > 1024:
                for key, (_, expires) in list(self._tts_tickets.items()):
                    if expires <= now:
                        self._tts_tickets.pop(key, None)

    async def get_tts_ticket(self, ticket: str) -> str | None:
        now = time.monotonic()
        async with self._lock:
            entry = self._tts_tickets.get(ticket)
            if entry is None or entry[1] <= now:
                self._tts_tickets.pop(ticket, None)
                return None
            return entry[0]

    async def close(self) -> None:
        return None


# 释放锁的 Lua：比对 token 再删——防止「A 的锁过期后 B 拿到锁，A 来释放把 B 的锁删了」
_RELEASE_LUA = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('del', KEYS[1])
end
return 0
"""


class RedisCoordinator:
    """Redis 协调（多 worker 共享状态）。

    三种用途的失败语义**各不相同**（见模块 docstring），实现时不要图省事统一处理：
    - 会话锁：Redis 报错 → 抛 CoordinationUnavailable（调用方 503，fail closed）
    - 限流：Redis 报错 → 放行（fail open）
    - LLM 槽：Redis 报错 → 回退进程内闸门（上游保护不失效）
    """

    def __init__(
        self,
        client,
        *,
        session_ttl_s: int = 600,
        slot_lease_s: int = 120,
        slots: dict[str, int] | None = None,
        fallback: MemoryCoordinator | None = None,
    ) -> None:
        self._client = client
        self._session_ttl_s = session_ttl_s
        self._slot_lease_s = slot_lease_s
        self._slots = slots or {"flash": 16, "pro": 4}
        self._fallback = fallback or MemoryCoordinator()

    @classmethod
    def from_settings(cls, settings: Settings) -> "RedisCoordinator":
        from redis.asyncio import Redis

        client = Redis.from_url(settings.redis_url, decode_responses=False)
        return cls(
            client,
            session_ttl_s=settings.interview_lock_ttl_s,
            slot_lease_s=settings.llm_slot_lease_s,
            slots={
                "flash": settings.llm_slots_flash,
                "pro": settings.llm_slots_pro,
            },
        )

    # ---------- 会话锁 ----------

    async def acquire_session(self, interview_id: str) -> str | None:
        key = f"interview:{interview_id}:lock"
        token = uuid.uuid4().hex
        try:
            ok = await self._client.set(
                key, token, nx=True, ex=self._session_ttl_s
            )
        except Exception as exc:  # noqa: BLE001 —— 任何 Redis 故障都 fail closed
            raise CoordinationUnavailable(f"会话锁不可用：{exc}") from exc
        return token if ok else None

    async def release_session(self, interview_id: str, token: str) -> None:
        key = f"interview:{interview_id}:lock"
        try:
            await self._client.eval(_RELEASE_LUA, 1, key, token)
        except Exception as exc:  # noqa: BLE001 —— 释放失败不阻断业务（锁会 TTL 过期）
            logger.warning("释放会话锁失败（将等 TTL 过期）：%s", exc)

    # ---------- per-user 限流 ----------

    async def allow_request(self, user_id: str, *, limit: int, window_s: int = 60) -> bool:
        window = int(time.time() // window_s)
        key = f"rl:user:{user_id}:{window}"
        try:
            count = await self._client.incr(key)
            if count == 1:
                await self._client.expire(key, window_s + 1)
        except Exception as exc:  # noqa: BLE001 —— 限流 fail open
            logger.warning("限流计数不可用（放行）：%s", exc)
            return True
        return int(count) <= limit

    # ---------- 全局 LLM 槽 ----------

    async def acquire_slot(self, tier: str, *, timeout: float) -> SlotLease | None:
        total = self._slots.get(tier, 0)
        if total <= 0:
            return None
        deadline = time.monotonic() + timeout
        offset = int(time.monotonic() * 1000) % total
        while time.monotonic() < deadline:
            for step in range(total):
                index = (offset + step) % total
                key = f"llm:{tier}:slot:{index}"
                token = uuid.uuid4().hex
                try:
                    ok = await self._client.set(
                        key, token, nx=True, ex=self._slot_lease_s
                    )
                except Exception as exc:  # noqa: BLE001 —— 回退进程内闸门
                    logger.warning("LLM 槽不可用（回退进程内）：%s", exc)
                    return await self._fallback.acquire_slot(tier, timeout=timeout)
                if ok:
                    return SlotLease(tier=tier, token=token, slot=str(index))
            await asyncio.sleep(0.05)
        return None

    async def release_slot(self, lease: SlotLease) -> None:
        key = f"llm:{lease.tier}:slot:{lease.slot}"
        try:
            await self._client.eval(_RELEASE_LUA, 1, key, lease.token)
        except Exception as exc:  # noqa: BLE001 —— 释放失败靠 TTL 过期
            logger.warning("释放 LLM 槽失败（将等 TTL 过期）：%s", exc)

    # ---------- TTS 票据 ----------

    async def put_tts_ticket(self, ticket: str, text: str, *, ttl_s: float) -> None:
        key = f"tts:ticket:{ticket}"
        try:
            await self._client.set(key, text, ex=max(1, int(ttl_s)))
        except Exception as exc:  # noqa: BLE001 —— 播报是可选能力，回退进程内
            logger.warning("TTS 票据写入 Redis 失败（回退进程内）：%s", exc)
            await self._fallback.put_tts_ticket(ticket, text, ttl_s=ttl_s)

    async def get_tts_ticket(self, ticket: str) -> str | None:
        key = f"tts:ticket:{ticket}"
        try:
            value = await self._client.get(key)
        except Exception as exc:  # noqa: BLE001 —— 读失败同样回退进程内
            logger.warning("TTS 票据读取 Redis 失败（回退进程内）：%s", exc)
            return await self._fallback.get_tts_ticket(ticket)
        if value is None:
            # put 曾经回退到进程内时，后续 Redis 恢复后仍要能读到
            return await self._fallback.get_tts_ticket(ticket)
        return value.decode("utf-8") if isinstance(value, bytes) else str(value)

    async def close(self) -> None:
        try:
            await self._client.aclose()
        except Exception as exc:  # noqa: BLE001 —— 关连接失败不影响退出
            logger.warning("关闭 Redis 连接失败：%s", exc)


_COORDINATOR: Coordinator | None = None


def get_coordinator(settings: Settings | None = None) -> Coordinator:
    """取（并缓存）协调器。

    缓存的理由同 db 门面：多 worker 各建一个 Redis 连接没问题，但**同一进程内**
    不该反复建连接；测试可用 `reset_coordinator()` 清缓存。
    """
    global _COORDINATOR
    if _COORDINATOR is None:
        from app.config import get_settings

        settings = settings or get_settings()
        if settings.coordination_backend == "redis":
            _COORDINATOR = RedisCoordinator.from_settings(settings)
        else:
            _COORDINATOR = MemoryCoordinator(
                slots={
                    "flash": settings.llm_slots_flash,
                    "pro": settings.llm_slots_pro,
                }
            )
    return _COORDINATOR


def reset_coordinator() -> None:
    """清缓存（测试隔离用：单例状态跨用例会串味）。"""
    global _COORDINATOR
    _COORDINATOR = None
