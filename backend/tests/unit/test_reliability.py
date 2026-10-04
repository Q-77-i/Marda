"""上游可靠性原语单测（P2-M9）：并发闸门 + 断路器，全部离线、不真睡。

断路器的时间全走注入 clock（`FakeClock`）——冷却期用真 sleep 会让用例慢且不稳。
"""

from __future__ import annotations

import asyncio

import pytest

from app.reliability import ConcurrencyGate, CircuitBreaker, BreakerState, UpstreamBusy


class FakeClock:
    """手动推进的单调钟。"""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


# ---------- 并发闸门 ----------


async def test_闸门并发上限_峰值不超过限值():
    gate = ConcurrencyGate(2, timeout=5.0)
    peak = 0
    inflight = 0

    async def worker():
        nonlocal peak, inflight
        async with gate.slot():
            inflight += 1
            peak = max(peak, inflight)
            await asyncio.sleep(0.01)  # 让出控制权，制造真实并发窗口
            inflight -= 1

    await asyncio.gather(*(worker() for _ in range(6)))

    assert peak == 2  # 上限真实生效（不是「恰好没撞上」）
    assert gate.in_flight == 0  # 全部释放


async def test_闸门等待超时抛UpstreamBusy():
    gate = ConcurrencyGate(1, timeout=0.05)

    async with gate.slot():
        with pytest.raises(UpstreamBusy):
            async with gate.slot():
                pytest.fail("不该拿到槽位")

    # 超时不影响占用方，槽位释放后可正常再入
    async with gate.slot():
        assert gate.in_flight == 1


async def test_闸门异常路径也释放槽位():
    gate = ConcurrencyGate(1, timeout=0.05)

    with pytest.raises(RuntimeError):
        async with gate.slot():
            raise RuntimeError("业务炸了")

    assert gate.in_flight == 0
    async with gate.slot():  # 槽位没被泄漏
        pass


# ---------- 断路器 ----------


def test_断路器_连续失败达阈值开路():
    breaker = CircuitBreaker(threshold=3, cooldown=30.0, clock=FakeClock())

    assert breaker.state is BreakerState.CLOSED
    for _ in range(2):
        assert breaker.allow() is True
        breaker.record_failure()
    assert breaker.state is BreakerState.CLOSED  # 未达阈值不打开
    assert breaker.allow() is True
    breaker.record_failure()
    assert breaker.state is BreakerState.OPEN  # 第 3 次失败打开
    assert breaker.allow() is False  # 开路期间快速失败


def test_断路器_成功清零连续计数():
    breaker = CircuitBreaker(threshold=3, cooldown=30.0, clock=FakeClock())

    breaker.record_failure()
    breaker.record_failure()
    breaker.record_success()  # 半途成功 → 计数清零
    breaker.record_failure()
    breaker.record_failure()

    assert breaker.state is BreakerState.CLOSED  # 只有连续失败才开路


def test_断路器_冷却后半开只放一个探针():
    clock = FakeClock()
    breaker = CircuitBreaker(threshold=1, cooldown=30.0, clock=clock)

    breaker.record_failure()
    assert breaker.state is BreakerState.OPEN
    assert breaker.allow() is False  # 冷却期内不放行

    clock.advance(30.0)
    assert breaker.allow() is True  # 冷却结束 → 放一个探针
    assert breaker.state is BreakerState.HALF_OPEN
    assert breaker.allow() is False  # 探针在飞，第二个请求仍快速失败


def test_断路器_半开探针成功则闭合():
    clock = FakeClock()
    breaker = CircuitBreaker(threshold=1, cooldown=10.0, clock=clock)

    breaker.record_failure()
    clock.advance(10.0)
    assert breaker.allow() is True
    breaker.record_success()

    assert breaker.state is BreakerState.CLOSED
    assert breaker.allow() is True  # 恢复放行


def test_断路器_半开探针失败则重新开路并重新计时():
    clock = FakeClock()
    breaker = CircuitBreaker(threshold=1, cooldown=10.0, clock=clock)

    breaker.record_failure()
    clock.advance(10.0)
    assert breaker.allow() is True
    breaker.record_failure()  # 探针也失败 → 重新开路

    assert breaker.state is BreakerState.OPEN
    clock.advance(9.9)
    assert breaker.allow() is False  # 冷却从重开时刻重新计
    clock.advance(0.1)
    assert breaker.allow() is True


# ---------- UpstreamGuard ----------


async def test_guard_成功路径记账并释放():
    from app.reliability import UpstreamGuard

    guard = UpstreamGuard(
        "flash", limit=1, acquire_timeout=1.0, threshold=2, cooldown=10.0,
        counts_as_failure=lambda exc: True,
    )
    calls = []

    async def call():
        calls.append(1)
        return "ok"

    assert await guard.run(call) == "ok"
    assert calls == [1]
    assert guard.gate.in_flight == 0
    assert guard.breaker.state is BreakerState.CLOSED


async def test_guard_非上游类异常不计入熔断():
    """内容类失败（如 JSON 校验）是上游活着时出的错——计数会误开熔断。"""
    from app.reliability import UpstreamGuard

    guard = UpstreamGuard(
        "flash", limit=1, acquire_timeout=1.0, threshold=2, cooldown=10.0,
        counts_as_failure=lambda exc: isinstance(exc, TimeoutError),
        )

    async def boom():
        raise ValueError("结构化输出不合法")

    for _ in range(5):
        with pytest.raises(ValueError):
            await guard.run(boom)

    assert guard.breaker.state is BreakerState.CLOSED


async def test_guard_熔断开路时不发起调用():
    from app.reliability import UpstreamGuard

    clock = FakeClock()
    guard = UpstreamGuard(
        "flash", limit=1, acquire_timeout=1.0, threshold=1, cooldown=60.0,
        counts_as_failure=lambda exc: True, clock=clock,
    )
    calls = []

    async def boom():
        calls.append(1)
        raise TimeoutError("上游超时")

    with pytest.raises(TimeoutError):
        await guard.run(boom)
    assert guard.breaker.state is BreakerState.OPEN

    with pytest.raises(UpstreamBusy):
        await guard.run(boom)
    assert calls == [1]  # 开路期间一次上游调用都没发


async def test_guard_闸门超时抛UpstreamBusy且不记账():
    from app.reliability import UpstreamGuard

    guard = UpstreamGuard(
        "flash", limit=1, acquire_timeout=0.05, threshold=1, cooldown=60.0,
        counts_as_failure=lambda exc: True,
    )

    async def slow():
        await asyncio.sleep(0.2)
        return "ok"

    blocker = asyncio.create_task(guard.run(slow))
    await asyncio.sleep(0.01)  # 让 blocker 先占住槽位

    with pytest.raises(UpstreamBusy):
        await guard.run(slow)

    await blocker
    assert guard.breaker.state is BreakerState.CLOSED  # 排队超时不是上游故障
