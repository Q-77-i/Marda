"""会话锁与限流的 API 层测试（P4-E4）：409 / 429 / 503 的语义。

三个状态码各有明确含义，别混：
- **409**：同一场已有请求在跑（会话锁）——多 worker 下防重复计分的关键闸门；
- **429**：单用户请求超频（限流）——保护，不是错误；
- **503**：协调后端不可用（Redis 挂了）——**fail closed**，宁可拒绝也不能两 worker 同跑一场。
"""

from __future__ import annotations

import json

from app import coordination
from app.main import app


def _interview_id(sse_text: str) -> str:
    """从 SSE 文本里取 interview_id（首条 meta 事件的 data）。"""
    for line in sse_text.splitlines():
        if line.startswith("data: "):
            payload = json.loads(line[len("data: "):])
            if "interview_id" in payload:
                return payload["interview_id"]
    raise AssertionError(f"SSE 里没有 interview_id：{sse_text[:200]}")


async def test_同一场并发请求第二个拿409(client):
    """先手工占住本场的锁，再发消息 → 409（不是 500，也不是静默排队）。"""
    service = app.state.service
    created = await client.post(
        "/api/interviews", json={"position": "Agent", "question_count": 2}
    )
    interview_id = _interview_id(created.text)
    token = await service.acquire_session(interview_id)
    try:
        r = await client.post(
            f"/api/interviews/{interview_id}/messages", json={"content": "你好"}
        )
        assert r.status_code == 409
    finally:
        await service.release_session(interview_id, token)


async def test_限流超限返回429(client, monkeypatch):
    from app.config import get_settings

    monkeypatch.setenv("USER_RATE_LIMIT_PER_MIN", "1")
    get_settings.cache_clear()
    body = {"position": "Agent", "question_count": 2}
    first = await client.post("/api/interviews", json=body)
    assert first.status_code == 200
    second = await client.post("/api/interviews", json=body)
    assert second.status_code == 429


async def test_协调后端不可用时返回503(client, monkeypatch):
    """Redis 挂了 → 会话锁 fail closed（503），不能放行。

    开场也要拿锁，所以**创建场次**这一步就已经 503 了——宁可拒绝，也不能开出一场
    两个 worker 同时推进的面试。
    """

    class _Broken:
        async def acquire_session(self, interview_id):
            raise coordination.CoordinationUnavailable("redis down")

        async def allow_request(self, user_id, *, limit, window_s=60):
            return True

        async def close(self):
            return None

    monkeypatch.setattr(app.state.service, "_coordinator", _Broken())
    created = await client.post(
        "/api/interviews", json={"position": "Agent", "question_count": 2}
    )
    assert created.status_code == 503


async def test_正常请求不受影响(client):
    """锁在流结束后释放——不能把场次永久卡住。"""
    r = await client.post(
        "/api/interviews", json={"position": "Agent", "question_count": 2}
    )
    assert r.status_code == 200
    interview_id = _interview_id(r.text)
    # 流已结束 → 锁应已释放；再发一条不该 409
    r2 = await client.post(
        f"/api/interviews/{interview_id}/messages", json={"content": "我是应届生"}
    )
    assert r2.status_code == 200
