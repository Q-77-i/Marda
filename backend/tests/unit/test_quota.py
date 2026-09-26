"""quota 知识域配额单测：largest remainder 分配 + 同域成块选域（SPEC §4.3）。"""

from __future__ import annotations

from collections import Counter

from app.domain import DOMAIN_WEIGHTS, project_count
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


def test_技术配额等于轮次减项目题():
    # 轮次语义：question_count 为全场问答轮次，项目题 project_count 道不占域配额
    assert sum(remaining_quota(_state(10)).values()) == 7  # 10 − project_count(10)=3
    assert sum(remaining_quota(_state(5)).values()) == 3   # 5 − project_count(5)=2


def test_剩余配额扣减已答题与当前题():
    state = _state()
    state.answered_questions = [_record("agent-architecture")]
    state.current_question = _record("agent-architecture")  # 正在答的题也占用配额

    assert remaining_quota(state)["agent-architecture"] == 0  # 配额 2 − 已答 1 − 当前 1


def test_选域取剩余配额最大的域():
    assert pick_domain(_state()) == "agent-architecture"  # 2 > 1，权重表第一个 2 配额域


def test_配额耗尽兜底取权重表首域():
    state = _state()
    state.answered_questions = [
        _record(domain) for domain, n in allocate_quota(7, DOMAIN_WEIGHTS).items() for _ in range(n)
    ]

    assert pick_domain(state) == "agent-architecture"


# ---- 同域成块（P1-M4.7-D2）----


def _tech_segment(question_count: int) -> list[str]:
    """模拟技术段出题：每步按出题前的 state 形态调 pick_domain，再记为已答/当前题。"""
    state = _state(question_count)
    total = sum(allocate_quota(question_count - project_count(question_count), DOMAIN_WEIGHTS).values())
    sequence = []
    for _ in range(total):
        domain = pick_domain(state)
        sequence.append(domain)
        record = _record(domain)
        state.answered_questions = [*state.answered_questions, record]
        state.current_question = record
    return sequence


def test_技术段同域成块_十题场():
    # 轮次 10 = 3 项目 + 7 技术，配额 {aa:2, rag:1, pr:1, tu:1, mem:1, eo:1} → 块序固定
    assert _tech_segment(10) == [
        "agent-architecture", "agent-architecture", "rag",
        "planning-reasoning", "tool-use", "memory", "engineering-observability",
    ]


def test_技术段同域成块_十五题场():
    # 轮次 15 = 3 项目 + 12 技术（六域各 2 道）→ 六块，块内连问
    assert _tech_segment(15) == [domain for domain in DOMAIN_WEIGHTS for _ in range(2)]


def test_同域成块不改变域分布():
    """跨场次可比性（P1-M4.7 拍板）：成块只改题序，每域题数与配额分配逐位一致。"""
    for question_count in (5, 7, 10, 15, 20):
        expected = allocate_quota(question_count - project_count(question_count), DOMAIN_WEIGHTS)
        assert Counter(_tech_segment(question_count)) == Counter(
            {d: n for d, n in expected.items() if n}
        )


def test_当前域配额未尽则继续同域():
    state = _state()
    record = _record("agent-architecture")
    state.answered_questions = [record]
    state.current_question = record

    assert pick_domain(state) == "agent-architecture"  # 配额 2、已答 1 → 继续成块


def test_当前域配额用尽则切下一域():
    state = _state()
    first, second = _record("agent-architecture"), _record("agent-architecture")
    state.answered_questions = [first, second]
    state.current_question = second

    assert pick_domain(state) == "rag"  # aa 配额用尽 → 剩余最多的下一域


def test_项目深挖题不参与同域粘性():
    state = _state()
    state.current_question = _record("project")  # 上一题是项目深挖题（domain 不占配额表）

    assert pick_domain(state) == "agent-architecture"
