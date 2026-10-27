"""PostgreSQL 集成测试（P4-E2/P4-E3）。

**需要真 PG**：默认跳过，靠环境变量打开：

    export MARDA_TEST_DATABASE_URL=postgresql://marda:marda@localhost:5432/marda_test
    export MARDA_TEST_CHECKPOINT_URL=postgresql://marda:marda@localhost:5432/marda_ckpt_test
    uv run pytest -m postgres

CI 里对应 `p4-integration` job（GitHub Actions 的 postgres service，假 key、不打真模型）。

验的是**只有 PG 才暴露的东西**：方言（%s 占位符 / ON CONFLICT / BIGSERIAL）、
单事务回滚、checkpointer 的跨实例 resume、保留策略的 PG 分支。
"""

from __future__ import annotations

import os

import pytest

from app import db

pytestmark = pytest.mark.postgres

DSN = os.environ.get("MARDA_TEST_DATABASE_URL")
CKPT_DSN = os.environ.get("MARDA_TEST_CHECKPOINT_URL")

requires_pg = pytest.mark.skipif(not DSN, reason="未配置 MARDA_TEST_DATABASE_URL")
requires_pg_ckpt = pytest.mark.skipif(
    not CKPT_DSN, reason="未配置 MARDA_TEST_CHECKPOINT_URL"
)


@pytest.fixture
def pg():
    """PG 业务库连接（每个用例前清空业务表——测试库专用，不要指向正式库）。"""
    from app.db_postgres import PostgresStore

    store = PostgresStore.connect(DSN, min_size=1, max_size=2)
    store.ensure_schema()
    with store._pool.connection() as conn:  # noqa: SLF001 —— 测试里直接清表
        for table in ("answers", "reports", "interviews", "resumes", "users"):
            conn.execute(f"DELETE FROM {table}")
        conn.commit()
    yield store
    store.close()


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


@requires_pg
def test_业务库在PG上跑通(pg):
    db.create_user(DSN, user_id="u1", username="alice", password_hash="h")
    assert db.get_user(DSN, "u1")["username"] == "alice"
    assert db.get_user_by_username(DSN, "alice") is not None
    # 用户名唯一（重复注册返回 False，不抛异常）
    assert db.create_user(DSN, user_id="u2", username="alice", password_hash="h") is False

    db.create_interview(
        DSN, interview_id="iv-1", position="Agent", question_count=5, user_id="u1"
    )
    db.finalize_interview(DSN, "iv-1", _records(2), {"overall": 80})
    interview = db.get_interview(DSN, "iv-1")
    assert interview["status"] == "finished"
    assert db.get_report(DSN, "iv-1")["payload"] == {"overall": 80}


@requires_pg
def test_finalize在PG上是单事务(pg):
    """中途失败 → answers / report / status 全部回滚。"""
    db.create_interview(
        DSN, interview_id="iv-1", position="Agent", question_count=5, user_id="u1"
    )
    bad = _records(2)
    bad[1]["candidate_answer"] = {"不是": "字符串"}  # psycopg 绑不进去 → 事务内失败
    with pytest.raises(Exception):
        db.finalize_interview(DSN, "iv-1", bad, {"overall": 80})
    assert db.get_report(DSN, "iv-1") is None
    assert db.get_interview(DSN, "iv-1")["status"] == "running"
    assert db.list_reports(DSN, user_id="u1") == []


@requires_pg
def test_finalize在PG上幂等(pg):
    db.create_interview(
        DSN, interview_id="iv-1", position="Agent", question_count=5, user_id="u1"
    )
    db.finalize_interview(DSN, "iv-1", _records(2), {"overall": 80})
    db.finalize_interview(DSN, "iv-1", _records(2), {"overall": 81})
    with pg._pool.connection() as conn:  # noqa: SLF001
        # dict_row：按列名取（下标是 tuple 的写法）
        n = conn.execute(
            "SELECT COUNT(*) AS n FROM answers WHERE interview_id='iv-1'"
        ).fetchone()["n"]
    assert n == 2
    assert db.get_report(DSN, "iv-1")["payload"] == {"overall": 81}


@requires_pg
def test_分页与过滤在PG上口径一致(pg):
    for i in range(5):
        db.create_interview(
            DSN, interview_id=f"iv-{i}", position="Agent", question_count=5, user_id="u1"
        )
    rows, total, counts = db.query_interviews(DSN, user_id="u1", page=1, page_size=2)
    assert len(rows) == 2
    assert total == 5
    assert counts == {"finished": 0, "running": 5}
    # 归属隔离
    assert db.query_interviews(DSN, user_id="u2")[1] == 0


@requires_pg
def test_迁移脚本行数一致(tmp_path, pg):
    """SQLite → PG 迁移：行数与抽样一致（迁移脚本的核心判据）。"""
    import sqlite3
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
    from migrate_sqlite_to_pg import migrate  # type: ignore

    src = tmp_path / "src.sqlite3"
    with sqlite3.connect(src) as conn:
        conn.executescript(
            "CREATE TABLE users (id TEXT PRIMARY KEY, username TEXT, password_hash TEXT, created_at TEXT);"
            "CREATE TABLE interviews (id TEXT PRIMARY KEY, thread_id TEXT, user_id TEXT, position TEXT,"
            " interview_type TEXT, question_count INT, phase TEXT, difficulty TEXT, status TEXT,"
            " started_at TEXT, ended_at TEXT, resume_id TEXT);"
            "CREATE TABLE answers (id INTEGER PRIMARY KEY AUTOINCREMENT, interview_id TEXT, question_id TEXT,"
            " domain TEXT, difficulty TEXT, candidate_answer TEXT, followup_count INT, skipped INT,"
            " score_json TEXT, created_at TEXT);"
            "CREATE TABLE reports (id TEXT PRIMARY KEY, interview_id TEXT, payload TEXT, created_at TEXT);"
            "CREATE TABLE resumes (id TEXT PRIMARY KEY, user_id TEXT, filename TEXT, text TEXT, parsed TEXT,"
            " created_at TEXT);"
        )
        conn.execute("INSERT INTO users VALUES ('u1', 'alice', 'h', '2026-01-01T00:00:00+00:00')")
        conn.execute(
            "INSERT INTO interviews VALUES ('iv-1', 'iv-1', 'u1', 'Agent', 'tech', 5, 'intro', 'L1',"
            " 'finished', '2026-01-01T00:00:00+00:00', '2026-01-01T01:00:00+00:00', NULL)"
        )
        conn.execute(
            "INSERT INTO answers VALUES (1, 'iv-1', 'q1', 'rag', 'L1', '答', 0, 0, '{}',"
            " '2026-01-01T01:00:00+00:00')"
        )
        conn.execute(
            "INSERT INTO reports VALUES ('iv-1', 'iv-1', '{\"overall\": 80}',"
            " '2026-01-01T01:00:00+00:00')"
        )
        conn.commit()

    counts = migrate(src, DSN, force=True)
    assert counts["users"] == 1
    assert counts["interviews"] == 1
    assert counts["answers"] == 1
    assert counts["reports"] == 1
    # 原 id 与时间戳保留
    assert db.get_interview(DSN, "iv-1")["started_at"] == "2026-01-01T00:00:00+00:00"
    assert db.get_report(DSN, "iv-1")["payload"] == {"overall": 80}
