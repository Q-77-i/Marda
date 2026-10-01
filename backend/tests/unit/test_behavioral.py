"""行为面（P1-M11 FR-22）单测：域集合不变量 / 五维聚合 / deepen-only 追问 / 阶段推进。

验收两条的离线证据：
① 一场行为面跑通（新维度评分 + 聚合）——聚合与评分类型在这里钉死；
② **不混入技术面出题池**——`pick_domain` 只在 DOMAIN_WEIGHTS 分配配额，行为面不在其中
   （域名不变量 + pick_domain 反查两处都断言）。
"""

from __future__ import annotations

import pytest

from app.domain import (
    ASKABLE_DOMAINS,
    BEHAVIORAL_DOMAIN,
    BEHAVIORAL_MAX_QUESTIONS,
    DOMAIN_WEIGHTS,
    ENABLED_DOMAINS,
    INTERVIEW_BEHAVIORAL,
    INTERVIEW_TECH,
    QUESTION_TYPE_BEHAVIORAL,
    COUNTED_QUESTION_TYPES,
)
from app.graph.rules.advance import phase_after_answer
from app.graph.rules.aggregate import (
    BEHAVIORAL_DIMS,
    FIVE_DIMS,
    aggregate_scores,
    dims_for,
    dims_payload,
    overall_score,
)
from app.graph.rules.follow_up import Decision, Reason, explain_decision
from app.graph.rules.quota import pick_domain
from app.graph.rules.transition import TransitionKind, reconnect_line, transition_kind
from app.graph.state import (
    BehavioralScoreItem,
    InterviewState,
    Phase,
    QuestionRecord,
    ScoreItem,
    score_schema_for,
)


# ---- 域集合不变量（验收②：行为面不混入技术面出题池）----


def test_行为面可出题但不属于技术配额():
    assert BEHAVIORAL_DOMAIN in ASKABLE_DOMAINS
    assert BEHAVIORAL_DOMAIN not in DOMAIN_WEIGHTS, "行为面进了技术配额，技术面会抽到它"
    assert BEHAVIORAL_DOMAIN not in ENABLED_DOMAINS, "私有题库不开放行为面域（D7）"


def test_行为面题型计入问答轮次():
    """T7a 口径：新增题型只改后端组成常量，用户侧「N 轮问答」语义不变。"""
    assert QUESTION_TYPE_BEHAVIORAL in COUNTED_QUESTION_TYPES


def test_pick_domain_永不返回行为面():
    """验收②的机制证据：技术面选域只看 DOMAIN_WEIGHTS，跑满一场也不会落 behavioral。"""
    state = InterviewState(question_count=15)
    picked = set()
    for index in range(15):
        state.answered_questions.append(_record(f"q{index}", "rag"))  # 已答域只驱动配额
        picked.add(pick_domain(state))
    assert BEHAVIORAL_DOMAIN not in picked
    assert picked <= set(DOMAIN_WEIGHTS)


# ---- 评分类型与聚合 ----


def _behavioral_record(*dims: int, key_points: list[str] | None = None) -> QuestionRecord:
    dims = dims or (4, 4, 4, 3, 5)
    return QuestionRecord(
        text="讲讲你最有成就感的项目",
        domain=BEHAVIORAL_DOMAIN,
        topic="项目表达",
        difficulty="L2",  # 死数据，不参与出题与展示
        question_type=QUESTION_TYPE_BEHAVIORAL,
        key_points=key_points if key_points is not None else ["k1"],
        score=BehavioralScoreItem(
            communication=dims[0],
            logic_structure=dims[1],
            project_experience=dims[2],
            values_motivation=dims[3],
            career_stability=dims[4],
            comment="",
        ),
    )


def _record(question_id: str, domain: str) -> QuestionRecord:
    return QuestionRecord(
        question_id=question_id, text="题", domain=domain, topic="t", difficulty="L1",
        score=ScoreItem(
            technical_depth=3, fundamentals=3, project_experience=3,
            communication=3, problem_solving=3, comment="",
        ),
    )


def test_评分_schema_按会话类型分派():
    assert score_schema_for(INTERVIEW_TECH) is ScoreItem
    assert score_schema_for(INTERVIEW_BEHAVIORAL) is BehavioralScoreItem
    assert score_schema_for("未知类型") is ScoreItem  # 未知按技术面兜底，不炸


def test_行为面评分_union_可往返序列化():
    """checkpoint 里两种评分共存：字典能按字段集自动落到正确的模型上。"""
    record = _behavioral_record()
    dumped = record.model_dump()
    restored = QuestionRecord.model_validate(dumped)

    assert isinstance(restored.score, BehavioralScoreItem)
    tech = _record("q_1", "rag").model_dump()
    assert isinstance(QuestionRecord.model_validate(tech).score, ScoreItem)


def test_行为面聚合_域为空集短板改为维度():
    agg = aggregate_scores([_behavioral_record(4, 4, 4, 3, 5)], interview_type=INTERVIEW_BEHAVIORAL)

    assert agg["domain_scores"] == {}
    assert set(agg["scores"]) == set(BEHAVIORAL_DIMS)
    assert agg["overall"] == 4.0
    assert agg["weaknesses"][0] == "values_motivation"  # 3 分最低


def test_技术面聚合不受行为面改动影响():
    agg = aggregate_scores([_record("q_1", "rag")])

    assert set(agg["scores"]) == set(FIVE_DIMS)
    assert agg["domain_scores"] == {"rag": 3.0}
    assert agg["weaknesses"] == ["rag"]


def test_维度表与_payload_同源():
    """前端/PDF 都消费 payload 的 dims（标签单一来源在后端）。"""
    assert dims_for(INTERVIEW_BEHAVIORAL)["project_experience"] == "项目经验"  # 与技术面同名对齐
    assert [d["key"] for d in dims_payload(INTERVIEW_BEHAVIORAL)] == list(BEHAVIORAL_DIMS)
    assert overall_score({"communication": 5}, BEHAVIORAL_DIMS) == 1.0  # 缺维按 0 摊


def test_第3维与技术面同名():
    """第 3 维刻意同名（D2）：两类型雷达图跨类型对照时语义一致。"""
    assert "project_experience" in FIVE_DIMS and "project_experience" in BEHAVIORAL_DIMS


# ---- 追问：deepen-only（补充约束①）----


def _deepen_only_args(score: BehavioralScoreItem, **overrides):
    args = {
        "question_count": 10,
        "clarify_used": 0,
        "missing_used": 0,
        "deepen_used": 0,
        "remedy_used": 0,
        "asked_key_points": [],
        "deepen_only": True,
    }
    args.update(overrides)
    return args


def test_行为面只深挖一次然后换题():
    score = BehavioralScoreItem(
        communication=4, logic_structure=4, project_experience=4,
        values_motivation=4, career_stability=4, comment="",
    )
    decision, reason = explain_decision(score, **_deepen_only_args(score))
    assert (decision, reason) == (Decision.DEEPEN, Reason.DEEPEN_OK)

    decision, reason = explain_decision(score, **_deepen_only_args(score, deepen_used=1))
    assert (decision, reason) == (Decision.NEXT, Reason.DEEPEN_LIMIT)


def test_行为面_错误标记与覆盖率都不触发澄清或补漏():
    """关键点是讲述结构不是知识点：「补漏」语义不成立；矛盾点也不在 HR 面当场对质。"""
    score = BehavioralScoreItem(
        communication=2, logic_structure=2, project_experience=2,
        values_motivation=2, career_stability=2,
        covered_key_points=[], missed_key_points=["k1", "k2"], error_flag=True, comment="",
    )
    decision, _ = explain_decision(score, **_deepen_only_args(score))

    assert decision is Decision.DEEPEN  # 不是 CLARIFY、不是 MISSING


def test_技术面追问决策不受_deepen_only_参数影响():
    """默认 False：既有技术面路径逐字不变。"""
    score = ScoreItem(
        technical_depth=2, fundamentals=2, project_experience=2,
        communication=2, problem_solving=2, error_flag=True, comment="",
    )
    decision, reason = explain_decision(
        score,
        question_count=10, clarify_used=0, missing_used=0, deepen_used=0,
        remedy_used=0, asked_key_points=[],
    )
    assert (decision, reason) == (Decision.CLARIFY, Reason.ERROR_FLAG)


# ---- 阶段推进与衔接 ----


def test_行为面答满进反问():
    assert phase_after_answer(
        Phase.BEHAVIORAL, 5, 5, interview_type=INTERVIEW_BEHAVIORAL
    ) is Phase.CLOSING
    assert phase_after_answer(
        Phase.BEHAVIORAL, 4, 5, interview_type=INTERVIEW_BEHAVIORAL
    ) is Phase.BEHAVIORAL
    # 技术面口径不受影响（默认参数）
    assert phase_after_answer(Phase.BEHAVIORAL, 5, 5) is Phase.BEHAVIORAL


def test_行为面首题用行为面衔接语():
    state = InterviewState(interview_type=INTERVIEW_BEHAVIORAL)
    question = _behavioral_record()

    assert transition_kind(state, question) is TransitionKind.OPEN_BEHAVIORAL
    tech_state = InterviewState()
    assert transition_kind(tech_state, _record("q_1", "rag")) is TransitionKind.OPEN_PROJECT


def test_行为面重连问候带出当前题():
    state = InterviewState(
        interview_type=INTERVIEW_BEHAVIORAL, status="running", phase=Phase.BEHAVIORAL,
        current_question=_behavioral_record(),
    )

    line = reconnect_line(state)
    assert line and "行为与项目面" in line


@pytest.mark.parametrize("count", [2, 5, 10])
def test_行为面题量上限是常量而非魔法数(count):
    """上限只作对照：真正的校验在 API 层（CreateRequest），这里钉住常量语义。"""
    assert BEHAVIORAL_MAX_QUESTIONS == 10
    assert count <= BEHAVIORAL_MAX_QUESTIONS
