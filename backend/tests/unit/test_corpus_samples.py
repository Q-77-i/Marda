"""评分 golden 的真库取样（P1-M12 会话 2）：只读、按类型过滤、关键点来源裁决。

取样逻辑错了不会报错——只会让 golden 悄悄少几条或关键点错位，指标跟着失真。
故把「关键点优先题库原表、生成题退回评分并集」「行为面/技术面互不串场」
「空回答不进样本」都钉住。
"""

from __future__ import annotations

import json
import sqlite3

import bank_fixture

from app import db
from app.domain import INTERVIEW_TECH

from evals import corpus


def _seed(tmp_path, *, interview_type: str, question_id: str | None, covered, missed):
    """造一份最小可读的库：一场面试 + 一份报告 payload（形状同 build_per_question_comments）。"""
    path = tmp_path / "test.sqlite3"
    db.ensure_schema(path)
    db.create_interview(
        path, interview_id="iv-1", position="Agent/AI 工程师", question_count=5,
        interview_type=interview_type,
    )
    payload = {
        "interview_type": interview_type,
        "per_question_comments": [{
            "index": 1, "number": 1, "question_id": question_id or "",
            "question_type": "tech", "domain": "rag", "text": "题干",
            "comment": "", "candidate_answer": "我的回答",
            "score": {"technical_depth": 3, "fundamentals": 3, "project_experience": 3,
                      "communication": 3, "problem_solving": 3},
            "covered_key_points": covered, "missed_key_points": missed,
            "reference_answer": None,
        }],
    }
    with sqlite3.connect(path) as conn:
        conn.execute(
            "INSERT INTO reports (interview_id, payload, created_at) VALUES (?, ?, ?)",
            ("iv-1", json.dumps(payload, ensure_ascii=False), "2026-10-01T00:00:00"),
        )
    return path


def test_题库题的关键点取原表(tmp_path):
    """题库题即便评分官漏判过某条，关键点也以原表为准（canonical 完整）。"""
    path = _seed(tmp_path, interview_type=INTERVIEW_TECH, question_id="q_bank",
                 covered=["k1"], missed=["k2"])
    bank_fixture.create_tables(path)
    row = bank_fixture.question_row("q_bank")
    row["key_points"] = json.dumps(["k1", "k2", "k3"], ensure_ascii=False)  # 原表三条
    bank_fixture.insert_questions(path, [row])
    rows = corpus.answer_samples(path, interview_type=INTERVIEW_TECH)
    assert len(rows) == 1
    assert rows[0]["key_points"] == ["k1", "k2", "k3"]  # 含评分官判定里没出现的那条
    assert rows[0]["key_points_from"] == "bank"
    assert rows[0]["overall"] == 3.0


def test_生成题退回评分并集(tmp_path):
    """生成题不在题库表里——关键点只能取评分官判定过的覆盖+漏点并集。"""
    path = _seed(tmp_path, interview_type=INTERVIEW_TECH, question_id=None,
                 covered=["k1"], missed=["k2"])
    rows = corpus.answer_samples(path, interview_type=INTERVIEW_TECH)
    assert rows[0]["key_points"] == ["k1", "k2"]
    assert rows[0]["key_points_from"] == "score"
    assert rows[0]["question_id"] is None


def test_按会话类型过滤不串场(tmp_path):
    path = _seed(tmp_path, interview_type="behavioral", question_id=None, covered=["k1"], missed=[])
    assert corpus.answer_samples(path, interview_type=INTERVIEW_TECH) == []
    assert len(corpus.answer_samples(path, interview_type="behavioral")) == 1


def test_空回答不进样本(tmp_path):
    path = _seed(tmp_path, interview_type=INTERVIEW_TECH, question_id=None, covered=[], missed=[])
    con = sqlite3.connect(path)
    payload = json.loads(con.execute("SELECT payload FROM reports").fetchone()[0])
    payload["per_question_comments"][0]["candidate_answer"] = "   "
    con.execute("UPDATE reports SET payload=?", (json.dumps(payload, ensure_ascii=False),))
    con.commit()
    con.close()
    assert corpus.answer_samples(path, interview_type=INTERVIEW_TECH) == []


def test_老场次无类型字段视为技术面(tmp_path):
    """M11 之前落库的报告没有 interview_type 字段（NULL ≡ tech 口径）。"""
    path = _seed(tmp_path, interview_type=INTERVIEW_TECH, question_id=None, covered=["k1"], missed=[])
    con = sqlite3.connect(path)
    payload = json.loads(con.execute("SELECT payload FROM reports").fetchone()[0])
    del payload["interview_type"]
    con.execute("UPDATE reports SET payload=?", (json.dumps(payload, ensure_ascii=False),))
    con.commit()
    con.close()
    assert len(corpus.answer_samples(path, interview_type=INTERVIEW_TECH)) == 1
