"""上游可靠性原语（P2-M9，SPEC §3）：并发闸门 + 断路器。

为什么是这两个：

- **并发闸门**——DeepSeek 的限流维度是**并发数**不是 QPS（CLAUDE.md 坑位 3），
  所以保护形态是计数信号量 + 有界等待，不是令牌桶速率。**持有期 = 整个上游调用的
  生命周期**：流式响应体是长连接，只包住建连段等于没限流；
- **断路器**——每模型一个：连续失败到阈值 → 开路（冷却期内快速失败、不碰上游），
  冷却结束 → 半开只放 **1 个**探针，探针成功闭合、失败重新开路。
- **只统计「上游不可用」类失败**（由调用方注入 `counts_as_failure` 判据）：内容类失败
  （JSON 校验不合法）是上游活着时出的错，计入会误开熔断——本项目已知 DeepSeek 偶发
  非法 JSON，这条不写对，正常场次会被已知抖动打成降级。

本模块是纯逻辑（注入 clock、不 import openai / llm），单测覆盖全部分支；
LLM 侧的接线与错误映射在 `llm.py`，降级语义（换模型 / 换兜底文案）在节点层。
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from contextlib import asynccontextmanager
from enum import Enum
from typing import Any, AsyncIterator, Awaitable


class UpstreamBusy(Exception):
    """上游暂时不可用的**自我保护**信号：排队超时或断路器开路。

    与「上游真的报错」区分：它不代表上游已经坏（可能就是本地闸门满了），
    调用方通常按「可重试」处置。
    """


class BreakerState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class ConcurrencyGate:
    """计数信号量 + 有界等待；超时抛 UpstreamBusy（不静默丢请求、也不无限排队）。"""

    def __init__(self, limit: int, *, timeout: float) -> None:
        if limit < 1:
            raise ValueError("limit 必须 ≥ 1")
        self.limit = limit
        self.timeout = timeout
        self._sem = asyncio.Semaphore(limit)
        self.in_flight = 0

    @asynccontextmanager
    async def slot(self) -> AsyncIterator[None]:
        try:
            await asyncio.wait_for(self._sem.acquire(), timeout=self.timeout)
        except (TimeoutError, asyncio.TimeoutError) as exc:
            raise UpstreamBusy(f"上游并发已满，排队 {self.timeout:g}s 未能获得槽位") from exc
        self.in_flight += 1
        try:
            yield
        finally:
            self.in_flight -= 1
            self._sem.release()


class CircuitBreaker:
    """连续失败型断路器（状态迁移全同步，无 await 点 → 不需要锁）。"""

    def __init__(
        self,
        *,
        threshold: int,
        cooldown: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if threshold < 1:
            raise ValueError("threshold 必须 ≥ 1")
        self.threshold = threshold
        self.cooldown = cooldown
        self._clock = clock
        self.state = BreakerState.CLOSED
        self._failures = 0
        self._opened_at = 0.0
        self._probe_in_flight = False

    def allow(self) -> bool:
        """是否放行这次调用；开路期（含半开已有探针在飞）返回 False。"""
        if self.state is BreakerState.CLOSED:
            return True
        if self.state is BreakerState.OPEN:
            if self._clock() - self._opened_at < self.cooldown:
                return False
            self.state = BreakerState.HALF_OPEN
            self._probe_in_flight = True
            return True
        # HALF_OPEN：只放一个探针
        if self._probe_in_flight:
            return False
        self._probe_in_flight = True
        return True

    def record_success(self) -> None:
        self._failures = 0
        self._probe_in_flight = False
        self.state = BreakerState.CLOSED

    def record_failure(self) -> None:
        self._probe_in_flight = False
        if self.state is BreakerState.HALF_OPEN:
            self._open()  # 探针也失败 → 重新开路、冷却重新计时
            return
        self._failures += 1
        if self._failures >= self.threshold:
            self._open()

    def _open(self) -> None:
        self.state = BreakerState.OPEN
        self._opened_at = self._clock()
        self._failures = 0


class UpstreamGuard:
    """一个上游（模型名）的一组保护：断路器放行 → 闸门占位 → 记账。

    用法两种，记账责任一致：`run(call)` 包一次完整调用（非流式）；
    流式要在整条流的生命周期里持槽，用 `acquire()` 手动包、在 try/except 里
    调 `record_success/record_failure`。
    """

    def __init__(
        self,
        name: str,
        *,
        limit: int,
        acquire_timeout: float,
        threshold: int,
        cooldown: float,
        counts_as_failure: Callable[[BaseException], bool],
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.name = name
        self.gate = ConcurrencyGate(limit, timeout=acquire_timeout)
        self.breaker = CircuitBreaker(threshold=threshold, cooldown=cooldown, clock=clock)
        self._counts_as_failure = counts_as_failure

    @asynccontextmanager
    async def acquire(self) -> AsyncIterator[None]:
        """断路器放行 + 闸门占位；两者不过则抛 UpstreamBusy（不发起上游调用）。"""
        if not self.breaker.allow():
            raise UpstreamBusy(f"{self.name} 上游已熔断，冷却中")
        async with self.gate.slot():
            yield

    def record_success(self) -> None:
        self.breaker.record_success()

    def record_failure(self, exc: BaseException) -> None:
        """按注入判据过滤：内容类失败不计入（见模块 docstring）。"""
        if self._counts_as_failure(exc):
            self.breaker.record_failure()

    async def run(self, call: Callable[[], Awaitable[Any]]) -> Any:
        async with self.acquire():
            try:
                result = await call()
            except BaseException as exc:
                self.record_failure(exc)
                raise
            self.record_success()
            return result
