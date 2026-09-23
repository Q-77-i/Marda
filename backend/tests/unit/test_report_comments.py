"""逐题点评组装单测（SPEC §4.6）：元信息以后端真实作答记录为准，条数恒等于作答数。"""

from __future__ import annotations

import json

from app.graph.rules.aggregate import build_per_question_comments
from app.graph.state import QuestionRecord, ScoreItem


def _record(
    domain: str,
    text: str = "题",
    question_id: str | None = None,
    comment: str = "评分官点评",
    question_type: str = "tech",
    answer: str | None = None,
    covered: list[str] | None = None,
    missed: list[str] | None = None,
) -> QuestionRecord:
    return QuestionRecord(
        question_id=question_id,
        text=text,
        domain=domain,
        topic="t",
        difficulty="L1",
        question_type=question_type,
        answer=answer,
        score=ScoreItem(
            technical_depth=4,
            fundamentals=4,
            project_experience=4,
            communication=4,
            problem_solving=4,
            covered_key_points=covered if covered is not None else [],
            missed_key_points=missed if missed is not None else [],
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
        _record("project", "请设计一个 Agent 系统", question_id=None, question_type="scenario"),
    ]

    result = build_per_question_comments(questions, ["点评一", "点评二"])

    scenario = result[-1]
    assert scenario["domain"] == "project"
    assert scenario["question_id"] is None
    assert scenario["text"] == "请设计一个 Agent 系统"
    assert scenario["index"] == 2
    assert scenario["question_type"] == "scenario"
    assert scenario["number"] == 2  # 计入问答轮次 → 按序编号


def test_题型语义带出_全部按轮次编号():
    questions = [
        _record("rag", "技术题一", question_id="q_aaa"),
        _record("memory", "技术题二", question_id="q_bbb"),
        _record("project", "请设计一个 Agent 系统", question_type="scenario"),
    ]

    result = build_per_question_comments(questions, ["一", "二", "三"])

    assert [r["question_type"] for r in result] == ["tech", "tech", "scenario"]
    assert [r["number"] for r in result] == [1, 2, 3]


def test_默认题型_tech_存量_checkpoint_兼容():
    """旧 checkpoint 的 QuestionRecord 无 question_type 字段 → 默认 tech 参与编号。"""
    questions = [_record("rag", question_id="q_aaa")]

    result = build_per_question_comments(questions, ["一"])

    assert result[0]["question_type"] == "tech"
    assert result[0]["number"] == 1


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


def test_复盘字段_回答五维与关键点对比():
    """FR-25 复盘扩展（SPEC §4.6）：回答、五维、覆盖/遗漏关键点逐题带出。"""
    questions = [
        _record("rag", question_id="q_aaa", answer="首答\n\n【追问补充】补充", covered=["k1"], missed=["k2"])
    ]

    row = build_per_question_comments(questions, ["点评一"])[0]

    assert row["candidate_answer"] == "首答\n\n【追问补充】补充"
    assert row["score"] == {
        "technical_depth": 4,
        "fundamentals": 4,
        "project_experience": 4,
        "communication": 4,
        "problem_solving": 4,
    }
    assert row["covered_key_points"] == ["k1"]
    assert row["missed_key_points"] == ["k2"]


def test_复盘字段全部可_json_序列化():
    """score 必须是标量 dict：Pydantic 对象直接进 payload 会让报告接口序列化炸。"""
    questions = [_record("rag", question_id="q_aaa", answer="回答", covered=["k1"], missed=["k2"])]

    rows = build_per_question_comments(questions, ["点评一"], {"q_aaa": "参考答案"})

    json.dumps(rows, ensure_ascii=False)  # 不抛异常即通过


def test_题库题带出参考答案_场景题为_null():
    """场景题 question_id 为空 → 无权威答案，reference_answer 为 null（前端不渲染）。"""
    questions = [
        _record("rag", question_id="q_aaa"),
        _record("project", text="设计一个 Agent 系统", question_type="scenario"),
    ]

    rows = build_per_question_comments(questions, ["一", "二"], {"q_aaa": "参考答案全文"})

    assert rows[0]["reference_answer"] == "参考答案全文"
    assert rows[1]["reference_answer"] is None
    assert rows[1]["question_id"] is None


def test_参考答案映射缺省或查不到时为_null():
    questions = [_record("rag", question_id="q_aaa"), _record("memory", question_id="q_bbb")]

    rows = build_per_question_comments(questions, ["一", "二"])  # 未传映射
    assert [r["reference_answer"] for r in rows] == [None, None]

    rows = build_per_question_comments(questions, ["一", "二"], {"q_other": "别的题"})  # 查不到
    assert [r["reference_answer"] for r in rows] == [None, None]


def test_未评分题目的复盘字段为空值不崩():
    """主动结束/异常路径下 score 可能缺失：字段仍在，值为空。"""
    question = QuestionRecord(question_id="q_x", text="题", domain="rag", topic="t", difficulty="L1")

    row = build_per_question_comments([question], [])[0]

    assert row["candidate_answer"] is None
    assert row["score"] is None
    assert row["covered_key_points"] == []
    assert row["missed_key_points"] == []
