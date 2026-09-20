"""quota 知识域配额单测：largest remainder 分配（SPEC §4.3）。"""

from __future__ import annotations

from app.domain import DOMAIN_WEIGHTS
from app.graph.rules.quota import allocate_quota, pick_domain, remaining_quota
from app.graph.state import InterviewState, QuestionRecord


def test_十题六域配额符合SPEC示例():
    got = allocate_quota(10, DOMAIN_WEIGHTS)
    assert got == {
        "agent-architecture": 2,
        "rag": 2,
        "planning-reasoning": 2,
        "tool-use": 2,
        "memory": 1,
        "engineering-observability": 1,
    }


def test_配额总和恒等于题量():
    for total in (5, 7, 10, 15, 20):
        assert sum(allocate_quota(total, DOMAIN_WEIGHTS).values()) == total


def test_零题全零():
    assert allocate_quota(0, DOMAIN_WEIGHTS) == {k: 0 for k in DOMAIN_WEIGHTS}


def test_单域权重一全占():
    assert allocate_quota(5, {"only": 1.0}) == {"only": 5}


def test_余数并列按权重表出现顺序断平局():
    assert allocate_quota(2, {"a": 0.4, "b": 0.4, "c": 0.2}) == {"a": 1, "b": 1, "c": 0}


def test_十五题六域配额与SPEC示例同构():
    got = allocate_quota(15, DOMAIN_WEIGHTS)
    assert got["agent-architecture"] == 3
    assert got["rag"] == 3
    assert sum(got.values()) == 15


def _state(question_count: int = 10) -> InterviewState:
    return InterviewState(interview_id="t", position="Agent/AI 工程师", question_count=question_count)


def _record(domain: str) -> QuestionRecord:
    return QuestionRecord(text="题", domain=domain, topic="t", difficulty="L1")


def test_技术配额等于轮次减场景题():
    # 轮次语义：question_count 为全场问答轮次，场景题固定 1 道不占域配额
    assert sum(remaining_quota(_state(10)).values()) == 9
    assert sum(remaining_quota(_state(5)).values()) == 4


def test_剩余配额扣减已答题与当前题():
    state = _state()
    state.answered_questions = [_record("rag")]
    state.current_question = _record("rag")  # 正在答的题也占用配额

    assert remaining_quota(state)["rag"] == 0  # 配额 2 − 已答 1 − 当前 1


def test_选域取剩余配额最大的域():
    assert pick_domain(_state()) == "agent-architecture"  # 2 > 1，权重表第一个 2 配额域


def test_配额耗尽兜底取权重表首域():
    state = _state()
    state.answered_questions = [
        _record(domain) for domain, n in allocate_quota(9, DOMAIN_WEIGHTS).items() for _ in range(n)
    ]

    assert pick_domain(state) == "agent-architecture"
