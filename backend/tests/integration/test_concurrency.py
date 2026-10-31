"""并发验证（P4-E4 验收项）：同一场的并发请求只有一条能进。

为什么要有这个用例：多 worker 形态下「同一场两个请求同时 resume」会各自推进状态——
轻则重复计分，重则状态互相覆盖。会话锁是唯一的闸门，所以**它必须有并发证据**，
不能只测「拿不到锁就抛异常」这种单点行为。

断言（三个，逐层加严）：
1. 并发 N 个请求 → **恰好 1 个成功**，其余全是 409（不是 500、不是静默排队）；
2. 状态里 `answered_count == 1`——**只有那一条回答被记入**（不重复计分）；
3. 409 的响应带中文业务文案（前端直接透传显示，不是裸状态码）。
"""

from __future__ import annotations

import asyncio
import json

from fake_llm import FakeLLMClient


def _interview_id(sse_text: str) -> str:
    """从 SSE 文本里取 interview_id（首条 meta 事件的 data）。"""
    for line in sse_text.splitlines():
        if line.startswith("data: "):
            payload = json.loads(line[len("data: "):])
            if "interview_id" in payload:
                return payload["interview_id"]
    raise AssertionError(f"SSE 里没有 interview_id：{sse_text[:200]}")


class _SlowFakeLLM(FakeLLMClient):
    """带延迟的 fake：让「持有锁的那条请求」有足够时间把其余请求挡在门外。

    没有这个延迟，并发请求可能排在赢家后面**依次**拿到锁（那是串行，不是并发拒绝），
    断言就会变脆。延迟把并发窗口固定住——测的是**并发**语义，不是时序运气。
    """

    async def _stream(self, **kwargs):
        await asyncio.sleep(0.05)
        async for chunk in super()._stream(**kwargs):
            yield chunk


async def test_同一场并发只有一个能进(client, monkeypatch):
    """8 路并发打同一场 → 1 个 200 + 7 个 409。"""
    monkeypatch.setattr(
        "app.llm._get_client", lambda: _SlowFakeLLM(), raising=True
    )
    # 先开场（拿到 interview_id；流结束即释放锁）
    created = await client.post(
        "/api/interviews", json={"position": "Agent", "question_count": 5}
    )
    interview_id = _interview_id(created.text)

    responses = await asyncio.gather(
        *[
            client.post(
                f"/api/interviews/{interview_id}/messages",
                json={"content": f"第 {i} 条并发回答"},
            )
            for i in range(8)
        ]
    )
    codes = [r.status_code for r in responses]
    assert codes.count(200) == 1, f"应当只有一条请求能进，实际：{codes}"
    assert codes.count(409) == 7, f"其余应当全是 409，实际：{codes}"
    # 409 带中文业务文案（前端直接透传显示）
    conflict = next(r for r in responses if r.status_code == 409)
    assert "处理中" in conflict.json()["detail"]


async def test_并发不会重复计分(client, monkeypatch):
    """并发被拒的那几条**不能**进 state——只有一条回答被记入。

    判据取 `chat_history` 里 role=user 的条数：**只有赢家的那条回答进了对话历史**。
    （`answered_count` 此时还是 0——第一条消息是自我介绍，还没进计分题；
    真正计分要等后续轮次，这里要验的是「并发没有把同一条回答记两遍」。）
    """
    monkeypatch.setattr(
        "app.llm._get_client", lambda: _SlowFakeLLM(), raising=True
    )
    created = await client.post(
        "/api/interviews", json={"position": "Agent", "question_count": 5}
    )
    interview_id = _interview_id(created.text)

    await asyncio.gather(
        *[
            client.post(
                f"/api/interviews/{interview_id}/messages",
                json={"content": f"第 {i} 条并发回答"},
            )
            for i in range(5)
        ]
    )

    session = await client.get(f"/api/interviews/{interview_id}")
    assert session.status_code == 200
    body = session.json()
    user_messages = [m for m in body["chat_history"] if m["role"] == "user"]
    assert len(user_messages) == 1, f"只有一条回答应被记入：{user_messages}"


async def test_不同场次互不阻塞(client, monkeypatch):
    """锁是**按场次**的：A 场在跑不该挡住 B 场。"""
    monkeypatch.setattr(
        "app.llm._get_client", lambda: _SlowFakeLLM(), raising=True
    )
    first = await client.post(
        "/api/interviews", json={"position": "Agent", "question_count": 5}
    )
    second = await client.post(
        "/api/interviews", json={"position": "RAG", "question_count": 5}
    )
    id_a = _interview_id(first.text)
    id_b = _interview_id(second.text)

    responses = await asyncio.gather(
        client.post(f"/api/interviews/{id_a}/messages", json={"content": "A 的回答"}),
        client.post(f"/api/interviews/{id_b}/messages", json={"content": "B 的回答"}),
    )
    assert [r.status_code for r in responses] == [200, 200]
