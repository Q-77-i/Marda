"""逐题点评组装单测（SPEC §4.6）：元信息以后端真实作答记录为准，条数恒等于作答数。"""

from __future__ import annotations

from app.graph.rules.aggregate import build_per_question_comments
from app.graph.state import QuestionRecord, ScoreItem


def _record(
    domain: str, text: str = "题", question_id: str | None = None, comment: str = "评分官点评"
) -> QuestionRecord:
    return QuestionRecord(
        question_id=question_id,
        text=text,
        domain=domain,
        topic="t",
        difficulty="L1",
        score=ScoreItem(
            technical_depth=4,
            fundamentals=4,
            project_experience=4,
            communication=4,
            problem_solving=4,
            comment=comment,
        ),
    )


def test_按作答顺序带出真实元信息():
    questions = [
        _record("rag", "什么是混合检索？", question_id="q_aaa"),
        _record("memory", "记忆怎么分层？", question_id="q_bbb"),
    ]

    result = build_per_question_comments(questions, ["点评一", "点评二"])

    assert [r["index"] for r in result] == [1, 2]
    assert [r["question_id"] for r in result] == ["q_aaa", "q_bbb"]
    assert [r["domain"] for r in result] == ["rag", "memory"]
    assert [r["text"] for r in result] == ["什么是混合检索？", "记忆怎么分层？"]
    assert [r["comment"] for r in result] == ["点评一", "点评二"]


def test_场景题实录_project_域且无题库_id():
    questions = [
        _record("rag", "技术题", question_id="q_aaa"),
        _record("project", "请设计一个 Agent 系统", question_id=None),
    ]

    result = build_per_question_comments(questions, ["点评一", "点评二"])

    scenario = result[-1]
    assert scenario["domain"] == "project"
    assert scenario["question_id"] is None
    assert scenario["text"] == "请设计一个 Agent 系统"
    assert scenario["index"] == 2


def test_llm_少给点评时用评分官点评补齐_条数恒等于作答数():
    questions = [
        _record("rag", question_id="q_aaa", comment="评分官说这道答得好"),
        _record("memory", question_id="q_bbb", comment="评分官说这道要补基础"),
    ]

    result = build_per_question_comments(questions, ["只有一条点评"])

    assert len(result) == 2
    assert result[0]["comment"] == "只有一条点评"
    assert result[1]["comment"] == "评分官说这道要补基础"


def test_llm_多给点评时忽略多余条目():
    questions = [_record("rag", question_id="q_aaa")]

    result = build_per_question_comments(questions, ["点评一", "多出来的点评"])

    assert len(result) == 1
    assert result[0]["comment"] == "点评一"


def test_无作答记录时返回空():
    assert build_per_question_comments([], ["点评一"]) == []
