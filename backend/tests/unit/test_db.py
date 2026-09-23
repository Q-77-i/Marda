"""业务库持久化单测（SPEC §8）：四表 schema + 落库/查询 + 用户隔离与轻量迁移。

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
    assert {"interviews", "answers", "reports", "users"} <= tables


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
    db.create_interview(path, interview_id="iv-a", position="x", question_count=5, user_id="u1")
    db.create_interview(path, interview_id="iv-b", position="x", question_count=5, user_id="u1")
    rows = db.list_interviews(path, user_id="u1")
    assert [r["id"] for r in rows] == ["iv-b", "iv-a"]


def test_查询不存在返回None(tmp_path):
    path = tmp_path / "test.sqlite3"
    db.ensure_schema(path)
    assert db.get_interview(path, "nope") is None
    assert db.get_report(path, "nope") is None


def test_历史列表按用户隔离(tmp_path):
    path = tmp_path / "test.sqlite3"
    db.ensure_schema(path)
    db.create_interview(path, interview_id="iv-a", position="x", question_count=5, user_id="u1")
    db.create_interview(path, interview_id="iv-b", position="x", question_count=5, user_id="u2")
    db.create_interview(path, interview_id="iv-c", position="x", question_count=5, user_id=None)
    assert [r["id"] for r in db.list_interviews(path, user_id="u1")] == ["iv-a"]
    assert [r["id"] for r in db.list_interviews(path, user_id="u2")] == ["iv-b"]
    assert db.list_interviews(path, user_id="u3") == []  # 孤儿行不属于任何用户


def test_用户表唯一约束与查询(tmp_path):
    path = tmp_path / "test.sqlite3"
    db.ensure_schema(path)
    assert db.create_user(path, user_id="u1", username="alice", password_hash="h1") is True
    assert db.create_user(path, user_id="u2", username="alice", password_hash="h2") is False
    # COLLATE NOCASE：库层也拦大小写变体（应用层已 lower，双保险）
    assert db.create_user(path, user_id="u3", username="Alice", password_hash="h3") is False
    assert db.get_user(path, "u1")["username"] == "alice"
    assert db.get_user_by_username(path, "alice")["id"] == "u1"
    assert db.get_user(path, "nope") is None
    assert db.get_user_by_username(path, "nope") is None


def test_孤儿场次归属(tmp_path):
    path = tmp_path / "test.sqlite3"
    db.ensure_schema(path)
    db.create_interview(path, interview_id="iv-a", position="x", question_count=5, user_id="u1")
    db.create_interview(path, interview_id="iv-b", position="x", question_count=5)
    db.create_interview(path, interview_id="iv-c", position="x", question_count=5)
    assert db.claim_orphan_interviews(path, "u2") == 2  # 只认领 NULL 行，不动 u1 的
    assert db.get_interview(path, "iv-b")["user_id"] == "u2"
    assert db.get_interview(path, "iv-c")["user_id"] == "u2"
    assert db.get_interview(path, "iv-a")["user_id"] == "u1"
    assert db.claim_orphan_interviews(path, "u3") == 0  # 无孤儿行：后续账号不接管


def test_旧库自动补user_id列(tmp_path):
    """阶段 1 建的库（interviews 无 user_id）经 ensure_schema 轻量迁移后可用。

    老场次 user_id 为 NULL —— 首个注册账号认领（见 claim_orphan_interviews）。
    """
    path = tmp_path / "test.sqlite3"
    with sqlite3.connect(path) as conn:
        conn.execute(
            "CREATE TABLE interviews (id TEXT PRIMARY KEY, thread_id TEXT UNIQUE, position TEXT,"
            " question_count INT, phase TEXT, difficulty TEXT, status TEXT, started_at TEXT,"
            " ended_at TEXT)"
        )
        conn.execute("INSERT INTO interviews (id, position, status) VALUES ('old-1', 'x', 'finished')")
    db.ensure_schema(path)
    row = db.get_interview(path, "old-1")
    assert row["id"] == "old-1" and row["user_id"] is None
    assert db.list_interviews(path, user_id="u1") == []
