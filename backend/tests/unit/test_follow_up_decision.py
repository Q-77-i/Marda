"""follow_up 追问决策单测：PRD §4.2 规则表全部状态转移与计数上限（验收 3 核心）。

覆盖口径（2026-09-16 拍板）：覆盖率 < 70% 才追问遗漏（PRD §4.2 阈值），
SPEC §4.3 伪代码「有遗漏即追问」已同步修订为阈值口径。
密度口径（P1-M4.5-R1 拍板）：优先级 澄清（不占池）→ 深挖（达标，不占池）→
遗漏（占池，同一 key_point 只追问一次）→ 换题；全场补救池 max(3, ceil(N×0.7))。
"""

from __future__ import annotations

from app.graph.rules.follow_up import (
    Decision,
    FollowUpRules,
    Reason,
    decide_follow_up,
    explain_decision,
    remedy_budget,
    unasked_missed,
)
from app.graph.state import ScoreItem


def _score(
    *,
    error: bool = False,
    covered: tuple[str, ...] = (),
    missed: tuple[str, ...] = (),
) -> ScoreItem:
    """构造五维 3 分的 ScoreItem，只关心追问决策依赖的字段。"""
    return ScoreItem(
        technical_depth=3,
        fundamentals=3,
        project_experience=3,
        communication=3,
        problem_solving=3,
        covered_key_points=list(covered),
        missed_key_points=list(missed),
        error_flag=error,
        comment="",
    )


def _decide(score: ScoreItem, **counts) -> Decision:
    return decide_follow_up(
        score,
        question_count=counts.get("question_count", 5),  # budget(5)=4
        clarify_used=counts.get("clarify_used", 0),
        missing_used=counts.get("missing_used", 0),
        deepen_used=counts.get("deepen_used", 0),
        remedy_used=counts.get("remedy_used", 0),
        asked_key_points=list(counts.get("asked", ())),
    )


def _explain(score: ScoreItem, **counts) -> tuple[Decision, Reason]:
    return explain_decision(
        score,
        question_count=counts.get("question_count", 5),
        clarify_used=counts.get("clarify_used", 0),
        missing_used=counts.get("missing_used", 0),
        deepen_used=counts.get("deepen_used", 0),
        remedy_used=counts.get("remedy_used", 0),
        asked_key_points=list(counts.get("asked", ())),
    )


# ---- 决策：澄清（不占池）----


def test_有明确错误触发澄清追问():
    assert _decide(_score(error=True)) is Decision.CLARIFY


def test_澄清追问每题仅一次():
    score = _score(error=True)
    assert _decide(score, clarify_used=1) is Decision.NEXT


def test_澄清用尽错误仍在换题_即使有遗漏():
    """错误 + 澄清已用完 → 换题（不深挖、不转遗漏——有悬而未决的错误，追问漏点无意义）。"""
    score = _score(error=True, missed=("k1", "k2"))
    assert _decide(score, clarify_used=1) is Decision.NEXT


def test_错误优先于遗漏():
    score = _score(error=True, missed=("k1",))
    assert _decide(score) is Decision.CLARIFY


def test_错误优先于深挖():
    score = _score(error=True, covered=("k1", "k2"))
    assert _decide(score) is Decision.CLARIFY
    assert _decide(score, clarify_used=1) is Decision.NEXT  # 用尽后仍不深挖


# ---- 决策：遗漏（占池，漏点只问一次）----


def test_遗漏且覆盖率低于70触发追问遗漏():
    score = _score(covered=("k1", "k2"), missed=("k3", "k4"))  # 2/4 = 50%
    assert _decide(score) is Decision.MISSING


def test_覆盖率恰好70不追问遗漏_转深挖():
    score = _score(covered=("a", "b", "c", "d", "e", "f", "g"), missed=("h", "i", "j"))  # 7/10
    assert _decide(score) is Decision.DEEPEN


def test_覆盖率略低于70仍追问遗漏():
    score = _score(covered=tuple(f"c{i}" for i in range(20)), missed=tuple(f"m{i}" for i in range(9)))  # 20/29 ≈ 69%
    assert _decide(score) is Decision.MISSING


def test_遗漏追问每题上限两次():
    score = _score(covered=("k1",), missed=("k2", "k3"))
    assert _decide(score, missing_used=2) is Decision.NEXT


def test_有遗漏无覆盖触发追问遗漏():
    assert _decide(_score(missed=("k1",))) is Decision.MISSING


def test_遗漏点均已问过不再追问():
    """覆盖率跳回 <70% 但漏点已全部追问过 → 换题（flicker 不再触发重复追问）。"""
    score = _score(covered=("k1",), missed=("k2", "k3"))
    assert _decide(score, asked=("k2", "k3")) is Decision.NEXT


def test_部分遗漏点已问过_仍追问未问部分():
    score = _score(covered=("k1",), missed=("k2", "k3"))
    assert _decide(score, asked=("k2",)) is Decision.MISSING


def test_遗漏追问后补充达标转深挖():
    """先追问遗漏、补充后重评达标 → 再深挖（深挖独立额度，不占补救池）。"""
    score = _score(covered=("k1", "k2", "k3"))
    assert _decide(score, missing_used=1, remedy_used=1) is Decision.DEEPEN


# ---- 决策：深挖（不占池）----


def test_无错误覆盖达标深挖():
    assert _decide(_score(covered=("k1", "k2", "k3"))) is Decision.DEEPEN


def test_深挖追问每题仅一次():
    score = _score(covered=("k1", "k2", "k3"))
    assert _decide(score, deepen_used=1) is Decision.NEXT


def test_关键点列表都空按达标深挖_防御空输出():
    # 无关键点数据时 coverage 视为 1.0（ScoreItem 口径）→ 达标 → 深挖；
    # 防御点仍在 MISSING 分支：空列表绝不凭空追问遗漏
    assert _decide(_score()) is Decision.DEEPEN


# ---- 决策：补救池（max(3, ceil(N×0.7))）----


def test_remedy_budget_公式():
    assert remedy_budget(5) == 4   # ceil(3.5)
    assert remedy_budget(10) == 7  # ceil(7)
    assert remedy_budget(15) == 11  # ceil(10.5)
    assert remedy_budget(2) == 3  # 地板：短场保底 3
    assert remedy_budget(1) == 3


def test_池用尽不再追问遗漏():
    score = _score(covered=("k1",), missed=("k2", "k3"))
    assert _decide(score, remedy_used=4) is Decision.NEXT  # budget(5)=4 已用尽


def test_池不挡澄清():
    """澄清豁免补救池（error 纠错是高价值追问，单题上限约束即可）。"""
    assert _decide(_score(error=True), remedy_used=4) is Decision.CLARIFY


def test_池不挡深挖():
    """深挖独立额度，补救池用尽不影响好答案的深挖。"""
    assert _decide(_score(covered=("k1", "k2", "k3")), remedy_used=4) is Decision.DEEPEN


def test_unasked_missed_过滤已问点():
    score = _score(covered=("k1",), missed=("k2", "k3", "k4"))
    assert unasked_missed(score, ["k2", "k3"]) == ["k4"]
    assert unasked_missed(score, []) == ["k2", "k3", "k4"]


def test_自定义规则参数生效():
    rules = FollowUpRules(clarify_limit=2, coverage_threshold=0.5)
    score = _score(error=True)
    assert decide_follow_up(
        score, question_count=5, clarify_used=1, missing_used=0, deepen_used=0,
        remedy_used=0, asked_key_points=[], rules=rules,
    ) is Decision.CLARIFY


def test_自定义规则_deepen_limit可配置():
    score = _score(covered=("k1", "k2", "k3"))
    # 关闭深挖：达标直接换题
    rules = FollowUpRules(deepen_limit=0)
    assert decide_follow_up(
        score, question_count=5, clarify_used=0, missing_used=0, deepen_used=0,
        remedy_used=0, asked_key_points=[], rules=rules,
    ) is Decision.NEXT
    # 放宽到 2 次：第二次深挖仍可触发
    rules = FollowUpRules(deepen_limit=2)
    assert decide_follow_up(
        score, question_count=5, clarify_used=0, missing_used=0, deepen_used=1,
        remedy_used=0, asked_key_points=[], rules=rules,
    ) is Decision.DEEPEN


# ---- 原因（与决策同源，回放展示用）----


def test_原因_明确错误触发澄清():
    assert _explain(_score(error=True)) == (Decision.CLARIFY, Reason.ERROR_FLAG)


def test_原因_澄清用尽换题_即使有遗漏():
    score = _score(error=True, missed=("k1", "k2"))
    assert _explain(score, clarify_used=1) == (Decision.NEXT, Reason.CLARIFY_LIMIT)


def test_原因_覆盖率低触发遗漏追问():
    score = _score(covered=("k1", "k2"), missed=("k3", "k4"))  # 50%
    assert _explain(score) == (Decision.MISSING, Reason.COVERAGE_LOW)


def test_原因_覆盖达标深挖():
    assert _explain(_score(covered=("k1", "k2"))) == (Decision.DEEPEN, Reason.DEEPEN_OK)


def test_原因_深挖用尽换题仍报覆盖达标():
    """深挖额度用尽后换题，原因保留 COVERAGE_OK（旧值语义不漂移，回放旧事件不变味）。"""
    assert _explain(_score(covered=("k1", "k2")), deepen_used=1) == (Decision.NEXT, Reason.COVERAGE_OK)


def test_原因_关键点都空按达标深挖():
    assert _explain(_score()) == (Decision.DEEPEN, Reason.DEEPEN_OK)


def test_原因_池用尽():
    score = _score(covered=("k1",), missed=("k2", "k3"))
    assert _explain(score, remedy_used=4) == (Decision.NEXT, Reason.REMEDY_LIMIT)


def test_原因_遗漏追问用满():
    score = _score(covered=("k1",), missed=("k2", "k3"))  # 1/3 < 70%
    assert _explain(score, missing_used=2) == (Decision.NEXT, Reason.MISSING_LIMIT)


def test_原因_遗漏点均已追问过():
    score = _score(covered=("k1",), missed=("k2", "k3"))
    assert _explain(score, asked=("k2", "k3")) == (Decision.NEXT, Reason.MISSING_ASKED)


def test_原因与决策同源_遍历组合不漂移():
    """explain_decision 是 decide_follow_up 的唯一实现：任何组合下决策一致。"""
    scores = [
        _score(),
        _score(error=True),
        _score(missed=("k1",)),
        _score(covered=("k1",), missed=("k2", "k3")),
        _score(error=True, covered=("k1",), missed=("k2",)),
        _score(covered=("k1", "k2", "k3")),
    ]
    for score in scores:
        for clarify_used in (0, 1):
            for missing_used in (0, 2):
                for deepen_used in (0, 1):
                    for remedy_used in (0, 4):
                        for asked in ((), ("k1",), ("k2", "k3")):
                            counts = dict(
                                question_count=5,
                                clarify_used=clarify_used,
                                missing_used=missing_used,
                                deepen_used=deepen_used,
                                remedy_used=remedy_used,
                                asked_key_points=list(asked),
                            )
                            decision, reason = explain_decision(score, **counts)
                            assert decision is decide_follow_up(score, **counts), (score, counts)
                            assert isinstance(reason, Reason)
