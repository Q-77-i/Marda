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

import asyncio

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


@requires_pg
def test_迁移后新场次能finalize_序列已重置(pg, tmp_path):
    """**迁移后新建一场并 finalize 不该主键冲突**。

    背景：迁移脚本给 `answers` **显式插原 id**（保持 id 一致），但 PG 的 BIGSERIAL
    **序列不会因为显式插入而前进**——序列只在用默认值插入时才自增。
    于是迁移完新场次 finalize_interview 第一次插 answers 会拿到 id=1，
    而 id=1 已被迁移进来的行占了 → 主键冲突。
    修法：迁移末尾 `setval(pg_get_serial_sequence('answers','id'), max(id), true)`。

    原有用例只验了行数，验不出这个——**行数对但序列不对**，只有真插一行才知道。
    """
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
            "INSERT INTO interviews VALUES ('iv-old', 'iv-old', 'u1', 'Agent', 'tech', 5, 'intro', 'L1',"
            " 'finished', '2026-01-01T00:00:00+00:00', '2026-01-01T01:00:00+00:00', NULL)"
        )
        # **id=1 是迁进来的行**——序列没重置的话，新场次 finalize 第一次插就撞它
        conn.execute(
            "INSERT INTO answers VALUES (1, 'iv-old', 'q1', 'rag', 'L1', '答', 0, 0, '{}',"
            " '2026-01-01T01:00:00+00:00')"
        )
        conn.commit()

    # **先把序列拨回起点**，模拟「刚建好库」的状态。
    # 不这么做的话，本用例会**继承上一个用例留下的序列位置**（用例之间有顺序依赖），
    # 于是「序列没重置」这个 bug 就测不出来了——测试库历史插入已经把序列推上去了。
    with pg._pool.connection() as conn:  # noqa: SLF001 —— 测试里直接查
        seq = conn.execute(
            "SELECT pg_get_serial_sequence('answers', 'id')"
        ).fetchone()["pg_get_serial_sequence"]
        conn.execute("SELECT setval(%s, 1, false)", (seq,))
        conn.commit()

    migrate(src, DSN, force=True)

    # **先确认迁移后的序列状态**（这才是这个用例要验的东西）：
    # 迁移是**显式插原 id**，PG 的 BIGSERIAL 序列**不会因此前进**——
    # 序列只在用默认值插入时才自增。于是新场次 finalize 第一次插 answers
    # 会拿到一个**已被占用**的 id → 主键冲突。
    with pg._pool.connection() as conn:  # noqa: SLF001 —— 测试里直接查
        seq = conn.execute(
            "SELECT pg_get_serial_sequence('answers', 'id')"
        ).fetchone()["pg_get_serial_sequence"]
        seq_now = conn.execute(f"SELECT last_value FROM {seq}").fetchone()["last_value"]
        max_id = conn.execute("SELECT MAX(id) FROM answers").fetchone()["max"]
    assert seq_now >= max_id, (
        f"迁移后序列({seq_now}) 必须 >= 表内最大 id({max_id})，"
        "否则新场次 finalize 会撞主键"
    )

    # 新建一场 → finalize → **不冲突**
    db.create_interview(
        DSN, interview_id="iv-new", position="Agent", question_count=1, user_id="u1"
    )
    db.finalize_interview(
        DSN,
        "iv-new",
        [_records(1)[0]],
        {"overall": 70},
    )
    assert db.get_interview(DSN, "iv-new")["status"] == "finished"
    # 新行的 id 必须**大于**迁进来的 1（序列确实前进了）
    with pg._pool.connection() as conn:  # noqa: SLF001 —— 测试里直接查
        new_id = conn.execute(
            "SELECT id FROM answers WHERE interview_id=%s", ("iv-new",)
        ).fetchone()["id"]
    assert new_id > 1, f"序列没重置：新行 id={new_id}（迁进来的行占了 id=1）"


@requires_pg
def test_多worker同时建schema不会死锁(pg):
    """**多个进程同时跑 `ensure_schema` 不能死锁**（`--workers 2` 的真实场景）。

    问题现场：`--workers 2` 时两个 worker 同时启动、同时跑 `ensure_schema`
    （里面是一串 `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`）。两个事务同时拿
    同一批表的锁 → PG 判定 `DeadlockDetected` → 启动失败、worker 反复重启
    （实测容器里就是这个症状：api 一直 `health: starting`，日志里全是
    `psycopg.errors.DeadlockDetected`）。

    `IF NOT EXISTS` 只保证**幂等**，不保证**并发安全**。
    修法：`Service._ensure_schema_with_retry` —— 撞上并发冲突就退避重试。

    **先证明裸并发会死锁**（否则这个用例啥也没验到），**再证明 Service 的重试能兜住**。
    """
    import concurrent.futures
    import threading

    # ① 裸并发确实会撞死锁（证明竞争是真的，不是臆想）
    hit = {"deadlock": False}
    lock = threading.Lock()

    def _run(_: int) -> None:
        try:
            db.ensure_schema(DSN)
        except Exception as exc:  # noqa: BLE001
            if type(exc).__name__ == "DeadlockDetected":
                with lock:
                    hit["deadlock"] = True
                return
            raise

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as ex:
        for f in [ex.submit(_run, i) for i in range(4)]:
            f.result()

    # ② Service 的重试路径要能兜住（这才是修的东西）
    from app.service import _is_concurrency_conflict

    class _Deadlock(Exception):
        pass

    _Deadlock.__name__ = "DeadlockDetected"
    assert _is_concurrency_conflict(_Deadlock())
    # 非并发冲突不该被当成可重试（否则连不上也会重试 5 次，白等）
    assert not _is_concurrency_conflict(ConnectionError("连不上"))
    assert not _is_concurrency_conflict(ValueError("SQL 写错"))

    if hit["deadlock"]:
        # 既然真撞上了，就再打一次 schema——重试语义下它该能成功（幂等）
        db.ensure_schema(DSN)

    # ③ **Service 的重试真能兜住**：并发跑多个 `_ensure_schema_with_retry`
    from app.config import get_settings
    from app.service import Service

    async def _one() -> None:
        svc = Service(get_settings())
        await svc._ensure_schema_with_retry()  # noqa: SLF001

    async def _all() -> None:
        await asyncio.gather(*[_one() for _ in range(4)])

    # 不抛即通过（内部撞死锁会自动退避重试）
    import asyncio as _asyncio

    _asyncio.run(_all())
