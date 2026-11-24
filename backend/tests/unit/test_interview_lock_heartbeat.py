"""会话锁心跳（J6-B）：SSE 存活期间续租，流结束 / 断开时释放。"""

from __future__ import annotations

import asyncio

import pytest

from app.api.interviews import _heartbeat_loop, _with_session_release


class _FakeService:
    def __init__(self, *, renew_result: bool = True) -> None:
        self.renewed = 0
        self.released: list[tuple[str, str]] = []
        self.renew_result = renew_result

    async def renew_session(self, interview_id: str, token: str) -> bool:
        self.renewed += 1
        return self.renew_result

    async def release_session(self, interview_id: str, token: str) -> None:
        self.released.append((interview_id, token))


async def _stream(*values: str, delay_s: float = 0.0):
    for value in values:
        if delay_s:
            await asyncio.sleep(delay_s)
        yield value


async def test_心跳按间隔续租直到流结束():
    service = _FakeService()
    task = asyncio.create_task(_heartbeat_loop(service, "iv-1", "tok", 0.01))
    await asyncio.sleep(0.045)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert service.renewed >= 2


async def test_token失效时心跳停止():
    """锁已被别人接手：停止续租，不覆盖别人的 TTL。"""
    service = _FakeService(renew_result=False)
    task = asyncio.create_task(_heartbeat_loop(service, "iv-1", "tok", 0.01))
    await asyncio.wait_for(task, timeout=0.5)
    assert service.renewed == 1


async def test_流正常结束后释放锁():
    service = _FakeService()
    events = [event async for event in _with_session_release(
        service, "iv-1", "tok", _stream("a", "b")
    )]
    assert events == ["a", "b"]
    assert service.released == [("iv-1", "tok")]


async def test_客户端断开时也释放锁():
    """SSE 生成器被取消（客户端断开 / 实例下线）→ finally 必须释放。"""
    service = _FakeService()

    async def consume():
        async for _ in _with_session_release(
            service, "iv-1", "tok", _stream("a", "b", delay_s=10)
        ):
            pass

    task = asyncio.create_task(consume())
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert service.released == [("iv-1", "tok")]
