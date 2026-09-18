"""业务库持久化单测（SPEC §8）：三表 schema + 落库/查询。

纯同步函数 + tmp 库，与 checkpointer（过程权威）无关。
"""

from __future__ import annotations

import json
import sqlite3

from app import db


def _rows(tmp_path, sql: str) -> list:
    with sqlite3.connect(tmp_path / "test.sqlite3") as conn:
        return conn.execute(sql).fetchall()


def test_ensure_schema幂等(tmp_path):
    path = tmp_path / "test.sqlite3"
    db.ensure_schema(path)
    db.ensure_schema(path)  # 二次调用不报错（IF NOT EXISTS）
    tables = {r[0] for r in _rows(tmp_path, "SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"interviews", "answers", "reports"} <= tables


def test_创建并查询面试(tmp_path):
    path = tmp_path / "test.sqlite3"
    db.ensure_schema(path)
    db.create_interview(path, interview_id="iv-1", position="Agent/AI 工程师", question_count=10)
    row = db.get_interview(path, "iv-1")
    assert row["id"] == "iv-1"
    assert row["thread_id"] == "iv-1"  # thread_id = interview_id（run_config 口径）
    assert row["position"] == "Agent/AI 工程师"
    assert row["status"] == "running"
    assert row["phase"] == "intro"
    assert row["started_at"]
    assert row["ended_at"] is None


def test_结束面试更新状态(tmp_path):
    path = tmp_path / "test.sqlite3"
    db.ensure_schema(path)
    db.create_interview(path, interview_id="iv-1", position="x", question_count=5)
    db.finish_interview(path, "iv-1")
    row = db.get_interview(path, "iv-1")
    assert row["status"] == "finished"
    assert row["ended_at"]


def test_答案落库_生成题question_id为空(tmp_path):
    path = tmp_path / "test.sqlite3"
    db.ensure_schema(path)
    db.save_answers(path, "iv-1", [
        {"question_id": "q_1", "domain": "rag", "difficulty": "L1",
         "candidate_answer": "回答", "followup_count": 1, "skipped": 0,
         "score_json": json.dumps({"technical_depth": 4})},
        {"question_id": None, "domain": "project", "difficulty": "L3",
         "candidate_answer": "场景题回答", "followup_count": 0, "skipped": 0,
         "score_json": None},
    ])
    rows = _rows(tmp_path, "SELECT * FROM answers WHERE interview_id='iv-1' ORDER BY id")
    assert len(rows) == 2
    assert rows[0][2] == "q_1"          # question_id
    assert rows[1][2] is None           # 生成题无 question_id
    assert rows[0][7] == 0              # skipped
    assert rows[0][9]                   # created_at 有值


def test_报告保存幂等覆盖(tmp_path):
    path = tmp_path / "test.sqlite3"
    db.ensure_schema(path)
    db.save_report(path, "iv-1", {"scores": {"technical_depth": 4.0}})
    db.save_report(path, "iv-1", {"scores": {"technical_depth": 5.0}})
    rows = _rows(tmp_path, "SELECT * FROM reports WHERE interview_id='iv-1'")
    assert len(rows) == 1  # id = interview_id，二次保存覆盖
    row = db.get_report(path, "iv-1")
    assert row["payload"]["scores"]["technical_depth"] == 5.0
    assert row["created_at"]


def test_历史列表倒序(monkeypatch, tmp_path):
    path = tmp_path / "test.sqlite3"
    db.ensure_schema(path)
    times = iter(["2026-09-19T10:00:00+00:00", "2026-09-19T11:00:00+00:00"])
    monkeypatch.setattr(db, "_now", lambda: next(times))
    db.create_interview(path, interview_id="iv-a", position="x", question_count=5)
    db.create_interview(path, interview_id="iv-b", position="x", question_count=5)
    rows = db.list_interviews(path)
    assert [r["id"] for r in rows] == ["iv-b", "iv-a"]


def test_查询不存在返回None(tmp_path):
    path = tmp_path / "test.sqlite3"
    db.ensure_schema(path)
    assert db.get_interview(path, "nope") is None
    assert db.get_report(path, "nope") is None
