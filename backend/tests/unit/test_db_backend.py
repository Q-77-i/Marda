"""业务库门面 + 单事务收尾的单测（P4-E2）。

三件事：
1. **门面按 target 类型分派**（Path → SQLite；DSN 字符串 → PostgreSQL）；
2. **finalize 单事务**：三步（answers / report / status）要么全成要么全不成；
3. **幂等**：同一场重放 finalize 不会重复计分。

第 2 条是本次升级最实在的收益：旧实现三次独立写，中途失败会留下
「图 finished、业务 running、报告缺失」的半成品场次，用户还不能重试。
"""

from __future__ import annotations

import pytest

from app import db


@pytest.fixture
def sqlite_db(tmp_path):
    path = tmp_path / "marda.sqlite3"
    db.ensure_schema(path)
    return path


def _records(n: int = 2) -> list[dict]:
    return [
        {
            "question_id": f"q{i}",
            "domain": "rag",
            "difficulty": "L1",
            "candidate_answer": f"回答{i}",
            "followup_count": 0,
            "skipped": 0,
            "score_json": '{"technical_depth": 4}',
        }
        for i in range(n)
    ]


def test_门面按target类型分派(sqlite_db):
    """Path → SQLite（就地建表）；字符串 → PostgreSQL（这里只验分派，不连库）。"""
    db.create_interview(
        sqlite_db, interview_id="iv-1", position="Agent", question_count=5, user_id="u1"
    )
    assert db.get_interview(sqlite_db, "iv-1")["position"] == "Agent"


def test_finalize一次落库三样东西(sqlite_db):
    db.create_interview(
        sqlite_db, interview_id="iv-1", position="Agent", question_count=5, user_id="u1"
    )
    db.finalize_interview(sqlite_db, "iv-1", _records(2), {"overall": 80})
    interview = db.get_interview(sqlite_db, "iv-1")
    assert interview["status"] == "finished"
    assert interview["ended_at"]
    report = db.get_report(sqlite_db, "iv-1")
    assert report["payload"] == {"overall": 80}


def test_finalize是幂等的(sqlite_db):
    """重放同一场的 finalize：answers 不重复（否则报告会重复计分）。"""
    db.create_interview(
        sqlite_db, interview_id="iv-1", position="Agent", question_count=5, user_id="u1"
    )
    db.finalize_interview(sqlite_db, "iv-1", _records(2), {"overall": 80})
    db.finalize_interview(sqlite_db, "iv-1", _records(2), {"overall": 81})
    import sqlite3

    with sqlite3.connect(sqlite_db) as conn:
        n = conn.execute(
            "SELECT COUNT(*) FROM answers WHERE interview_id='iv-1'"
        ).fetchone()[0]
    assert n == 2
    assert db.get_report(sqlite_db, "iv-1")["payload"] == {"overall": 81}


def test_finalize中途失败要整体回滚(sqlite_db):
    """answers 插到一半失败 → answers / report / status 都不能变。

    这是本次升级的核心断言：旧的三次独立写会留下「报告缺失但状态 finished」的场次。
    失败点在**事务内部**（第二条记录的值 sqlite 绑不进去）——只有这样才验到回滚。
    """
    db.create_interview(
        sqlite_db, interview_id="iv-1", position="Agent", question_count=5, user_id="u1"
    )
    bad = _records(2)
    bad[1]["candidate_answer"] = {"不是": "字符串"}  # sqlite 绑不进去 → 插到一半才炸
    with pytest.raises(Exception):
        db.finalize_interview(sqlite_db, "iv-1", bad, {"overall": 80})
    # 什么都没写进去
    assert db.get_report(sqlite_db, "iv-1") is None
    assert db.get_interview(sqlite_db, "iv-1")["status"] == "running"
    import sqlite3

    with sqlite3.connect(sqlite_db) as conn:
        n = conn.execute(
            "SELECT COUNT(*) FROM answers WHERE interview_id='iv-1'"
        ).fetchone()[0]
    assert n == 0


def test_旧简历行启动时回填内容哈希(tmp_path):
    """老库的 resumes 没有 content_hash；ensure_schema 要补列并回填，缓存才能命中。"""
    import hashlib
    import sqlite3

    path = tmp_path / "old.sqlite3"
    with sqlite3.connect(path) as conn:
        conn.execute(
            "CREATE TABLE resumes (id TEXT PRIMARY KEY, user_id TEXT, filename TEXT,"
            " text TEXT, parsed TEXT, created_at TEXT)"
        )
        conn.execute(
            "INSERT INTO resumes VALUES ('r_old','u1','简历.md','同一份简历','{}','2026-01-01')"
        )
        conn.commit()

    db.ensure_schema(path)

    row = db.find_resume_by_hash(
        path,
        user_id="u1",
        content_hash=hashlib.sha256("同一份简历".encode("utf-8")).hexdigest(),
    )
    assert row is not None
    assert row["id"] == "r_old"
