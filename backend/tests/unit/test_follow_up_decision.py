"""follow_up 追问决策单测：PRD §4.2 规则表全部状态转移与计数上限（验收 3 核心）。

覆盖口径（2026-09-16 拍板）：覆盖率 < 70% 才追问遗漏（PRD §4.2 阈值），
SPEC §4.3 伪代码「有遗漏即追问」已同步修订为阈值口径。
"""

from __future__ import annotations

from app.graph.rules.follow_up import Decision, FollowUpRules, decide_follow_up
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
        follow_up_count=counts.get("follow_up_count", 0),
        clarify_used=counts.get("clarify_used", 0),
        missing_used=counts.get("missing_used", 0),
    )


def test_总数达上限直接换题_即使仍有错误与遗漏():
    score = _score(error=True, missed=("k1", "k2"))
    assert _decide(score, follow_up_count=3) is Decision.NEXT


def test_有明确错误触发澄清追问():
    assert _decide(_score(error=True)) is Decision.CLARIFY


def test_澄清追问每题仅一次():
    score = _score(error=True)
    assert _decide(score, clarify_used=1) is Decision.NEXT  # 无遗漏，直接换题


def test_遗漏且覆盖率低于70触发追问遗漏():
    score = _score(covered=("k1", "k2"), missed=("k3", "k4"))  # 2/4 = 50%
    assert _decide(score) is Decision.MISSING


def test_覆盖率恰好70不追问遗漏():
    score = _score(covered=("a", "b", "c", "d", "e", "f", "g"), missed=("h", "i", "j"))  # 7/10
    assert _decide(score) is Decision.NEXT


def test_覆盖率略低于70仍追问遗漏():
    score = _score(covered=tuple(f"c{i}" for i in range(20)), missed=tuple(f"m{i}" for i in range(9)))  # 20/29 ≈ 69%
    assert _decide(score) is Decision.MISSING


def test_遗漏追问每题上限两次():
    score = _score(covered=("k1",), missed=("k2", "k3"))
    assert _decide(score, missing_used=2) is Decision.NEXT


def test_错误优先于遗漏():
    score = _score(error=True, missed=("k1",))
    assert _decide(score) is Decision.CLARIFY


def test_澄清已用满但遗漏存在转追问遗漏():
    score = _score(error=True, covered=("k1", "k2"), missed=("k3", "k4"))
    assert _decide(score, clarify_used=1) is Decision.MISSING


def test_无错误无遗漏换题():
    score = _score(covered=("k1", "k2", "k3"))
    assert _decide(score) is Decision.NEXT


def test_关键点列表都空不追问_防御空输出():
    assert _decide(_score()) is Decision.NEXT


def test_有遗漏无覆盖触发追问遗漏():
    assert _decide(_score(missed=("k1",))) is Decision.MISSING


def test_自定义规则参数生效():
    rules = FollowUpRules(clarify_limit=2, coverage_threshold=0.5)
    score = _score(error=True)
    assert decide_follow_up(score, follow_up_count=0, clarify_used=1, missing_used=0, rules=rules) is Decision.CLARIFY
