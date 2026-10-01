"""评分消息构造的单一来源（P1-M12 会话 2）。

**这条测试是整个评分门禁的地基**：评测 harness 用 `judge_messages` 建消息、
用 `score_schema_for` 取 schema，必须与 `judge_node` 实际发出去的东西逐字一致——
否则模板一改，评测就悄悄量起了旧口径，门禁形同虚设。
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from app import llm
from app.domain import INTERVIEW_BEHAVIORAL, INTERVIEW_TECH
from app.graph.nodes import judge as judge_mod
from app.graph.state import InterviewState, QuestionRecord

TECH_SCORE = {
    "technical_depth": 4, "fundamentals": 3, "project_experience": 3,
    "communication": 4, "problem_solving": 3,
    "covered_key_points": ["k1"], "missed_key_points": ["k2"],
    "error_flag": False, "comment": "还行",
}
BEHAVIORAL_SCORE = {
    "communication": 4, "logic_structure": 3, "project_experience": 3,
    "values_motivation": 4, "career_stability": 3,
    "covered_key_points": ["k1"], "missed_key_points": [], "error_flag": False, "comment": "不错",
}


class _CapturingClient:
    """记录完整请求 kwargs 的 fake（比共享 FakeLLM 多存 temperature）。"""

    def __init__(self, payload: dict):
        self.payload = payload
        self.calls: list[dict] = []

    @property
    def chat(self):
        outer = self

        class _Completions:
            async def create(self, **kwargs):
                outer.calls.append(kwargs)
                return SimpleNamespace(
                    choices=[SimpleNamespace(message=SimpleNamespace(
                        content=json.dumps(outer.payload, ensure_ascii=False)))]
                )

        return SimpleNamespace(completions=_Completions())


def _state(interview_type: str) -> InterviewState:
    question = QuestionRecord(
        question_id="q_1", text="什么是 ReAct？", domain="planning-reasoning", topic="规划",
        difficulty="L2", key_points=["k1", "k2"], followup_log=[], answer=None,
    )
    state = InterviewState(interview_id="iv1", position="Agent/AI 工程师", interview_type=interview_type)
    state.current_question = question
    state.user_input = "我的回答"
    return state


@pytest.mark.asyncio
async def test_评分节点发的消息与评测构造的逐字一致(monkeypatch):
    client = _CapturingClient(TECH_SCORE)
    monkeypatch.setattr(llm, "_get_client", lambda: client)

    state = _state(INTERVIEW_TECH)
    await judge_mod.judge_node(state)

    sent = client.calls[0]
    expected = judge_mod.judge_messages(
        question="什么是 ReAct？",
        key_points=["k1", "k2"],
        answer="我的回答",  # 首答无追问，合并后即原样
        followup_log=(),
        interview_type=INTERVIEW_TECH,
    )
    # chat_json 会在 system 末尾追加 schema 提示（llm 层行为）——比较时必须含它，
    # 因为评测走的是同一条 chat_json 路径，吃到的是同一份完整消息
    assert sent["messages"][0]["role"] == "system"
    assert sent["messages"][0]["content"].startswith(expected[0]["content"])
    assert "ScoreItem" in sent["messages"][0]["content"]  # schema 提示确实附上了


@pytest.mark.asyncio
async def test_评分节点用生产温度(monkeypatch):
    client = _CapturingClient(TECH_SCORE)
    monkeypatch.setattr(llm, "_get_client", lambda: client)
    await judge_mod.judge_node(_state(INTERVIEW_TECH))
    assert client.calls[0]["temperature"] == judge_mod.JUDGE_TEMPERATURE


def test_行为面走行为面模板():
    tech = judge_mod.judge_messages(
        question="题", key_points=["a"], answer="答", interview_type=INTERVIEW_TECH)
    behavioral = judge_mod.judge_messages(
        question="题", key_points=["a"], answer="答", interview_type=INTERVIEW_BEHAVIORAL)
    assert "行为面" not in tech[0]["content"]
    assert "行为面" in behavioral[0]["content"]
    # 维度表也必须换：技术面的「技术深度」不该出现在行为面 prompt 里
    assert "technical_depth" in tech[0]["content"]
    assert "technical_depth" not in behavioral[0]["content"]
    assert "logic_structure" in behavioral[0]["content"]


def test_关键点逐条成行_无追问记录填无():
    content = judge_mod.judge_messages(
        question="题", key_points=["要点一", "要点二"], answer="答",
        interview_type=INTERVIEW_TECH)[0]["content"]
    assert "- 要点一" in content and "- 要点二" in content
    assert "【追问记录】\n无" in content


def test_追问记录逐条列出():
    content = judge_mod.judge_messages(
        question="题", key_points=["a"], answer="答", followup_log=["你刚才说 X，为什么？"],
        interview_type=INTERVIEW_TECH)[0]["content"]
    assert "- 你刚才说 X，为什么？" in content


@pytest.mark.asyncio
async def test_行为面评分节点走行为面_schema(monkeypatch):
    client = _CapturingClient(BEHAVIORAL_SCORE)
    monkeypatch.setattr(llm, "_get_client", lambda: client)
    state = _state(INTERVIEW_BEHAVIORAL)
    await judge_mod.judge_node(state)
    assert state.current_question.score.logic_structure == 3
