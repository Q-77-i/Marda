"""SSE 事件映射单测（SPEC §7 事件表）：astream updates chunk → 事件列表。

map_updates 是纯函数：chunk（{node: updates}）+ 累计 snapshot → (事件列表, 新 snapshot)。
事件按 SPEC §7：delta / question / meta（done/error 由 service 层在流结束后产出）。
事件 data 为 JSON 字符串（sse-starlette 编码约定），断言时 json.loads 解析。
"""

from __future__ import annotations

import json

from app.graph.state import Phase, QuestionRecord
from app.service import map_updates


def _data(event: dict) -> dict:
    return json.loads(event["data"])


def test_开场文本映射为delta并累计快照():
    events, snapshot = map_updates(
        {"intro": {"phase": Phase.WARMUP, "chat_history": [
            {"role": "assistant", "content": "欢迎参加面试"}]}},
        {},
    )
    deltas = [e for e in events if e["event"] == "delta"]
    assert [_data(d)["text"] for d in deltas] == ["欢迎参加面试"]
    assert snapshot["chat_history"][-1]["content"] == "欢迎参加面试"
    assert snapshot["phase"] == "warmup"  # 枚举已归一化


def test_用户消息不产生delta():
    events, _ = map_updates(
        {"judge": {"chat_history": [{"role": "user", "content": "我的回答"}]}},
        {},
    )
    assert not [e for e in events if e["event"] == "delta"]


def test_多轮只发增量():
    events, snapshot = map_updates(
        {"ask": {"chat_history": [{"role": "assistant", "content": "第一题"}]}}, {})
    assert len([e for e in events if e["event"] == "delta"]) == 1
    events2, _ = map_updates(
        {"ask": {"chat_history": [
            {"role": "assistant", "content": "第一题"},
            {"role": "assistant", "content": "追问"}]}},
        snapshot,
    )
    deltas = [e for e in events2 if e["event"] == "delta"]
    assert [_data(d)["text"] for d in deltas] == ["追问"]  # 只发新增


def test_出题事件带序号与元数据():
    events, _ = map_updates(
        {"ask": {"current_question": QuestionRecord(
            question_id="q_rag", text="题目", domain="rag", topic="RAG 检索",
            difficulty="L2")}},
        {"answered_count": 2},
    )
    q = [e for e in events if e["event"] == "question"]
    assert len(q) == 1
    assert _data(q[0]) == {
        "index": 3,  # answered_count + 1
        "question_id": "q_rag",
        "domain": "rag",
        "difficulty": "L2",
    }


def test_生成题无question事件():
    events, _ = map_updates(
        {"ask": {"current_question": QuestionRecord(
            text="场景题", domain="project", topic="t", difficulty="L3")}},
        {"answered_count": 1},
    )
    assert not [e for e in events if e["event"] == "question"]


def test_同题重传不重复发question事件():
    # 追问/评分节点会重传 current_question（仅新增日志/分数），不视为新题
    events, _ = map_updates(
        {"followup": {"current_question": QuestionRecord(
            question_id="q_rag", text="题目", domain="rag", topic="t",
            difficulty="L1", follow_up_count=1)}},
        {"answered_count": 1, "current_question": {
            "question_id": "q_rag", "text": "题目", "domain": "rag",
            "topic": "t", "difficulty": "L1"}},
    )
    assert not [e for e in events if e["event"] == "question"]


def test_阶段变化发meta():
    events, _ = map_updates(
        {"advance": {"phase": Phase.CLOSING}},
        {"phase": "tech_base", "answered_count": 2, "question_count": 10},
    )
    metas = [e for e in events if e["event"] == "meta"]
    assert len(metas) == 1
    assert _data(metas[0]) == {"phase": "closing", "answered_count": 2, "question_count": 10}


def test_无meta字段不发meta():
    events, _ = map_updates({"judge": {"user_input": "x"}}, {"answered_count": 1})
    assert not [e for e in events if e["event"] == "meta"]


def test_快照逐chunk累计():
    _, snapshot = map_updates({"a": {"x": 1}}, {})
    _, snapshot = map_updates({"b": {"y": 2}}, snapshot)
    assert snapshot == {"x": 1, "y": 2}
