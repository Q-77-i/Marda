"""**落库失败**的语义与半成品场次的修复路径（P4 收尾）。

背景：`_persist` 重试 3 次仍失败时抛 `RuntimeError`，而 `_run` 原本只 catch
`LLMError` / `GraphRecursionError` → **异常逃出 SSE 流**，客户端拿不到任何 error 事件，
只能看着一个「永远转圈」的界面（文档写的「仍失败回 SSE error」当时并不成立）。

而且落库失败会留下一场**半成品场次**：图跑完了、checkpoint 里状态是 finished，
但 `answers` / `reports` 没落库、`interviews.status` 还停在 running。
`resume` 又会被「图已 finished」拦掉（409）→ **用户自己修不了**。

这两条都要钉住：
1. 落库失败 → **发 SSE error 事件**（流正常结束，不是断连）；
2. 半成品场次 → 能通过 `POST /{id}/retry-persist` 修好（**不重跑图**）。
"""

from __future__ import annotations

import json

import httpx
import pytest

from app import db
from app.main import app

TURNS = [
    "我是应届生，做过 RAG 问答与 Agent 工具调用项目。",
    "我用 LangGraph 做状态机，支持中断恢复；检索用 BGE-M3 加 Qdrant 混合召回。",
    "第一题深挖补充……",
    "第二题回答……",
    "第二题深挖补充……",
    "请问团队技术栈？",
    "晋升路径？",
]


async def _events(response) -> list[dict]:
    """逐行解析 SSE 流为事件列表（与 tests/integration/test_api.py 的 `_events` 同一套形状）。"""
    out: list[dict] = []
    current: dict = {}
    async for line in response.aiter_lines():
        line = line.rstrip("\r")
        if not line:
            if current:
                out.append(current)
                current = {}
            continue
        if line.startswith("event: "):
            current["event"] = line[len("event: "):]
        elif line.startswith("data: "):
            current["data"] = json.loads(line[len("data: "):])
    if current:
        out.append(current)
    return out


async def _create(client: httpx.AsyncClient) -> str:
    """开一场（**开场面也是 SSE 流**，不是 JSON 响应）。"""
    async with client.stream(
        "POST", "/api/interviews", json={"position": "Agent", "question_count": 2}
    ) as resp:
        assert resp.status_code == 200, resp.status_code
        events = await _events(resp)
    return events[0]["data"]["interview_id"]


async def _send(client: httpx.AsyncClient, interview_id: str, content: str) -> list[dict]:
    """发一条消息并收集 SSE 事件。

    SSE 的形状是 `event: <名>` + `data: <json>` 两行一组（与 tests/integration/test_api.py
    的 `_events` 同一套解析）——所以 `code` 在 **data 里**，不在事件对象上。
    """
    async with client.stream(
        "POST",
        f"/api/interviews/{interview_id}/messages",
        json={"content": content},
    ) as resp:
        assert resp.status_code == 200, resp.status_code
        return await _events(resp)


def _error_codes(events: list[dict]) -> list[str]:
    """挑出所有 error 事件的 code。"""
    return [
        e.get("data", {}).get("code")
        for e in events
        if e.get("event") == "error"
    ]


@pytest.fixture
def break_persist(monkeypatch):
    """让落库**彻底失败**（模拟 PG 连不上 / 磁盘满这类最终修不好的问题）。"""

    def _break():
        def _boom(*args, **kwargs):
            raise RuntimeError("模拟的落库失败")

        monkeypatch.setattr(db, "finalize_interview", _boom)

    return _break


async def test_落库失败发SSE_error而不是断连(client, break_persist):
    """落库失败 → 流里要有 error 事件（客户端拿得到失败原因）。"""
    break_persist()
    interview_id = await _create(client)

    # 一直发到面试**结束**（落库发生在结束那一刻）
    events: list[dict] = []
    for turn in TURNS:
        events = await _send(client, interview_id, turn)
        if _error_codes(events):
            break

    codes = _error_codes(events)
    assert "persist_error" in codes, f"没收到 persist_error，实际事件：{[e.get('event') for e in events]}"
    err = next(e for e in events if e.get("event") == "error")
    assert err["data"].get("retryable") is True


async def test_落库失败留下半成品场次且能修复(client, break_persist, monkeypatch):
    """半成品场次（图 finished、业务库没写）→ retry-persist 能修好。"""
    break_persist()
    interview_id = await _create(client)

    events: list[dict] = []
    for turn in TURNS:
        events = await _send(client, interview_id, turn)
        if _error_codes(events):
            break
    assert "persist_error" in _error_codes(events)

    # 半成品的特征：图跑完了（checkpoint status=finished），但业务库没有报告
    from app.graph.checkpointer import create_checkpointer  # noqa: F401 —— 仅为可读性
    from app.main import app as _app

    service = _app.state.service
    values = await service._current_values(interview_id)  # noqa: SLF001 —— 测试里直接看状态
    assert values.get("status") == "finished", "图应该已经跑完"
    assert db.get_report(db_path_of(), interview_id) is None, "业务库此时不该有报告"

    # **修**：不重跑图，只重新落库
    monkeypatch.undo()  # 撤掉 break_persist，让落库恢复正常
    r = await client.post(f"/api/interviews/{interview_id}/retry-persist")
    assert r.status_code == 200, r.text
    assert r.json()["repaired"] is True

    # 修好了：报告在库里了，场次状态也对了
    assert db.get_report(db_path_of(), interview_id) is not None
    row = db.get_interview(db_path_of(), interview_id)
    assert row["status"] == "finished"


async def test_已有报告时repair不重复写(client, break_persist):
    """报告已存在 → repaired=false（不重复写、不报错）。"""
    interview_id = await _create(client)

    # 先正常跑完一场（落库成功）
    for turn in TURNS:
        await _send(client, interview_id, turn)

    r = await client.post(f"/api/interviews/{interview_id}/retry-persist")
    assert r.status_code == 200, r.text
    assert r.json()["repaired"] is False


async def test_未结束的场次不能repair(client):
    """还没结束的场次 → 409（不是半成品，不该走这条路）。"""
    interview_id = await _create(client)
    r = await client.post(f"/api/interviews/{interview_id}/retry-persist")
    assert r.status_code == 409, r.text


def db_path_of() -> str:
    """当前用例的业务库路径（tmp 库，由 conftest 的 `_test_env` 注入）。"""
    from app.config import get_settings

    return get_settings().business_db
