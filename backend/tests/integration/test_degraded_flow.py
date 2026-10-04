"""降级链路集成测试（P2-M9）：**LLM 全线不可用，一场面试仍能走完**。

这是 PRD 验收「断 LLM 全链路降级仍可用」的离线证据（真链路证据见
`scripts/smoke_degraded.py`）：注入所有 LLM 调用抛 `LLMError(retryable=True)`
（= 上游不可用），断言一场完整面试：
① 全程零 SSE error（降级不是错误）；② 有 degraded 提示事件；
③ 题库题照出（题面直发）；④ 报告落库且如实标注未评分、不产 0 分；
⑤ 能力档案排除该场并给出说明。

内容类失败（retryable=False）走的是另一条路（error + 重试），由 test_api 的
「模拟抖动」用例钉着——别把两者混起来。
"""

from __future__ import annotations

import json
import sqlite3

from app import llm
from app.config import get_settings

ANSWERS = [
    "我是应届生，做过一个 RAG 问答项目",
    "第一题回答：用双路召回再融合",
    "第二题回答：用状态机管理多轮流程",
    "请问团队的技术栈是什么？",
    "那晋升路径是怎样的？",
    "我还有一个问题：平时怎么做代码评审？",
]


async def _events(response) -> list[dict]:
    out, current = [], {}
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


async def _create(client) -> tuple[str, list[dict]]:
    async with client.stream(
        "POST", "/api/interviews",
        json={"position": "Agent/AI 工程师", "question_count": 2},
    ) as r:
        assert r.status_code == 200
        events = await _events(r)
    return events[0]["data"]["interview_id"], events


async def _send(client, interview_id: str, content: str) -> list[dict]:
    async with client.stream(
        "POST", f"/api/interviews/{interview_id}/messages", json={"content": content},
    ) as r:
        assert r.status_code == 200
        return await _events(r)


async def test_断LLM一场面试仍能走完并如实标注(client, monkeypatch):
    async def _down(*args, **kwargs):
        raise llm.LLMError("上游不可用（连接失败）", retryable=True)

    monkeypatch.setattr(llm, "chat", _down)
    monkeypatch.setattr(llm, "chat_json", _down)

    interview_id, create_events = await _create(client)
    for event in create_events:
        assert event["event"] != "error", f"创建阶段不该有 error：{event}"

    all_events: list[dict] = []
    done = False
    for text in ANSWERS:
        events = await _send(client, interview_id, text)
        all_events.extend(events)
        if any(e["event"] == "done" for e in events):
            done = True
            break
    assert done, f"面试没走完：{[e['event'] for e in all_events]}"

    # ① 全程零 error（降级不是错误——这是与「重试」语义的分界）
    assert [e for e in all_events if e["event"] == "error"] == []
    # ② 有降级提示，且原因是「上游不可用」那一族
    reasons = [e["data"]["reason"] for e in all_events if e["event"] == "degraded"]
    assert reasons, "降级场次必须发 degraded 事件（不许静默）"
    assert any("不可用" in reason for reason in reasons)

    # ③ 题目照出：题库题的原题面直接发（fake 检索有题；具体域由配额纯代码决定）
    session = (await client.get(f"/api/interviews/{interview_id}")).json()
    history = " ".join(m["content"] for m in session["chat_history"])
    assert "方向的题目" in history  # 题库题面（题面本就不需要 LLM）
    # ③b 会话状态带降级原因（P2-M9）：SSE 事件同因只发一次，刷新/中途进入靠它补横幅
    assert session["degraded_reasons"], "会话接口必须给出降级原因（否则刷新后横幅消失 = 静默）"

    # ④ 报告落库且如实标注（端点形状：{interview_id, report: payload, created_at}）
    report = (await client.get(f"/api/interviews/{interview_id}/report")).json()["report"]
    assert report["degraded"] is True
    assert report["degraded_reasons"]
    assert report["answered_count"] == 2
    assert report["unscored_count"] == 2
    assert report["scores"] == {} and "overall" not in report  # 不产 0 分
    assert report["total_comment"] == ""
    assert len(report["per_question_comments"]) == 2  # 题干与作答照出

    with sqlite3.connect(get_settings().db_path) as conn:
        rows = conn.execute(
            "SELECT score_json FROM answers WHERE interview_id = ?", (interview_id,)
        ).fetchall()
        assert rows and all(row[0] is None for row in rows)  # 未评分：NULL 而不是 0
        status = conn.execute(
            "SELECT status FROM interviews WHERE id = ?", (interview_id,)
        ).fetchone()[0]
        assert status == "finished"

    # ⑤ 能力档案：排除该场并说明（0 分不进曲线）
    profile = (await client.get("/api/profile")).json()
    assert profile["sessions"] == []
    assert profile["excluded"]["degraded"] == 1


async def test_降级场次与正常场次并存_档案只画正常的(client, monkeypatch, install_search):
    """同一账号：先跑一场正常（有分），再跑一场降级——曲线只含正常那场。"""
    from bank_fixture import create_tables, insert_sources  # noqa: F401  (与 conftest 同款最小库)
    from test_api import TURNS, _create as _create_normal, _send as _send_normal

    # 正常场次（FakeLLM 在线）
    interview_id, _ = await _create_normal(client, question_count=2)
    for text in TURNS:
        events = await _send_normal(client, interview_id, text)
        if any(e["event"] == "done" for e in events):
            break

    # 降级场次：同一账号、LLM 全断
    async def _down(*args, **kwargs):
        raise llm.LLMError("上游不可用", retryable=True)

    monkeypatch.setattr(llm, "chat", _down)
    monkeypatch.setattr(llm, "chat_json", _down)
    degraded_id, _ = await _create(client)
    for text in ANSWERS:
        events = await _send(client, degraded_id, text)
        if any(e["event"] == "done" for e in events):
            break

    profile = (await client.get("/api/profile")).json()
    assert [s["interview_id"] for s in profile["sessions"]] == [interview_id]  # 只有正常那场
    assert profile["excluded"]["degraded"] == 1
