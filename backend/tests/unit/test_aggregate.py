"""aggregate 报告聚合单测：五维均值/域均分/短板（SPEC §4.6）。"""

from __future__ import annotations

from app.graph.rules.aggregate import aggregate_scores, overall_score
from app.graph.state import QuestionRecord, ScoreItem


def _record(domain: str, *dims: int) -> QuestionRecord:
    dims = dims or (3, 3, 3, 3, 3)
    return QuestionRecord(
        text="题",
        domain=domain,
        topic="t",
        difficulty="L1",
        score=ScoreItem(
            technical_depth=dims[0],
            fundamentals=dims[1],
            project_experience=dims[2],
            communication=dims[3],
            problem_solving=dims[4],
            comment="",
        ),
    )


def test_五维均值跨题平均():
    agg = aggregate_scores([_record("rag", 4, 4, 4, 4, 4), _record("rag", 2, 2, 2, 2, 2)])

    assert agg["scores"]["technical_depth"] == 3.0
    assert agg["scores"]["problem_solving"] == 3.0


def test_域均分只算本域题目():
    agg = aggregate_scores([_record("rag", 3, 3, 3, 3, 3), _record("memory", 5, 5, 5, 5, 5)])

    assert agg["domain_scores"] == {"memory": 5.0, "rag": 3.0}


def test_场景题不参与域统计():
    agg = aggregate_scores([_record("rag", 4, 4, 4, 4, 4), _record("project", 1, 1, 1, 1, 1)])

    assert "project" not in agg["domain_scores"]
    assert agg["domain_scores"]["rag"] == 4.0


def test_短板取均分最低两个域():
    agg = aggregate_scores(
        [_record("rag", 5, 5, 5, 5, 5), _record("memory", 2, 2, 2, 2, 2), _record("tool-use", 3, 3, 3, 3, 3)]
    )

    assert agg["weaknesses"] == ["memory", "tool-use"]


def test_短板第三名同分一并带上():
    agg = aggregate_scores(
        [
            _record("rag", 5, 5, 5, 5, 5),
            _record("memory", 2, 2, 2, 2, 2),
            _record("tool-use", 3, 3, 3, 3, 3),
            _record("planning-reasoning", 3, 3, 3, 3, 3),
        ]
    )

    assert agg["weaknesses"] == ["memory", "planning-reasoning", "tool-use"]


def test_无评分题全空():
    agg = aggregate_scores([])

    assert agg["scores"] == {d: 0.0 for d in ("technical_depth", "fundamentals", "project_experience", "communication", "problem_solving")}
    assert agg["domain_scores"] == {}
    assert agg["weaknesses"] == []


def test_总分是五维等权均值():
    """总分口径（P1-M10 D1）：等权，不是加权；报告页/PDF/能力档案共用同一个数。"""
    agg = aggregate_scores([_record("rag", 5, 4, 4, 3, 4), _record("rag", 3, 2, 2, 1, 2)])

    # 五维均值 = (4 + 3 + 3 + 2 + 3) / 5
    assert agg["scores"] == {
        "technical_depth": 4.0, "fundamentals": 3.0, "project_experience": 3.0,
        "communication": 2.0, "problem_solving": 3.0,
    }
    assert agg["overall"] == 3.0


def test_无评分题总分零():
    assert aggregate_scores([])["overall"] == 0.0


def test_总分缺维按零计():
    """防御历史 payload（五维不全）：缺的维按 0 摊进分母，不静默按剩余维平均。"""
    assert overall_score({"technical_depth": 5}) == 1.0
    assert overall_score({}) == 0.0
