"""P5-J5 T2：PG outbox —— 同事务、确定性去重、SKIP LOCKED 领取、发布标记。

**需要真 PG**（默认跳过）：

    export MARDA_TEST_DATABASE_URL=postgresql://marda:marda@localhost:55432/marda_test
    uv run pytest -m postgres

CI 对应 `p4-integration` job。
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

import pytest

from app import db
from app.db_postgres import PostgresStore
from app.outbox.events import build_interview_finalized

pytestmark = pytest.mark.postgres

DSN = os.environ.get("MARDA_TEST_DATABASE_URL")
requires_pg = pytest.mark.skipif(not DSN, reason="未配置 MARDA_TEST_DATABASE_URL")

FINISHED_AT = datetime(2026, 11, 20, 12, 0, 0, tzinfo=timezone.utc)


def _report(interview_id: str = "iv-1") -> dict:
    return {
        "interview_id": interview_id,
        "position": "Agent",
        "interview_type": "tech",
        "scores": {"technical_depth": 3.0},
        "overall": 3.0,
        "domain_scores": {"rag": 2.5},
        "weaknesses": ["rag"],
        "study_advice": [{"domain": "rag", "advice": "补混合检索"}],
    }


def _event(interview_id: str = "iv-1") -> dict:
    event = build_interview_finalized(
        interview_id=interview_id,
        user_id="u1",
        report=_report(interview_id),
        finished_at=FINISHED_AT,
    )
    assert event is not None
    return event


def _records() -> list[dict]:
    return [
        {
            "question_id": "q1",
            "domain": "rag",
            "difficulty": "L1",
            "candidate_answer": "回答",
            "followup_count": 0,
            "skipped": 0,
            "score_json": '{"technical_depth": 3}',
        }
    ]


@pytest.fixture
def pg():
    store = PostgresStore.connect(DSN, min_size=1, max_size=3)
    store.ensure_schema()
    with store._pool.connection() as conn:  # noqa: SLF001 —— 测试清表
        for table in (
            "agent_outbox",
            "answers",
            "reports",
            "interviews",
            "resumes",
            "users",
        ):
            conn.execute(f"DELETE FROM {table}")
        conn.commit()
    yield store
    store.close()


def _create_interview(interview_id: str = "iv-1") -> None:
    db.create_interview(
        DSN,
        interview_id=interview_id,
        position="Agent",
        question_count=3,
        user_id="u1",
    )


@requires_pg
def test_pg_finalize_与outbox同事务写入(pg):
    _create_interview()

    db.finalize_interview(DSN, "iv-1", _records(), _report(), event=_event())

    rows = pg.claim_outbox_batch(limit=10)
    assert len(rows) == 1
    assert rows[0]["id"] == _event()["event_id"]
    assert rows[0]["event_type"] == "interview.finalized"
    assert rows[0]["aggregate_id"] == "iv-1"


@requires_pg
def test_pg_重复finalize只有一条事件(pg):
    _create_interview()

    db.finalize_interview(DSN, "iv-1", _records(), _report(), event=_event())
    db.finalize_interview(DSN, "iv-1", _records(), _report(), event=_event())

    assert pg.count_outbox_pending() == 1


@requires_pg
def test_pg_单独入队幂等(pg):
    assert db.enqueue_outbox(DSN, _event()) is True
    assert db.enqueue_outbox(DSN, _event()) is False

    assert pg.count_outbox_pending() == 1


@requires_pg
def test_pg_outbox写失败时finalize整体回滚(pg, monkeypatch):
    _create_interview()

    def boom(self, conn, event, now):
        raise RuntimeError("outbox insert failed")

    monkeypatch.setattr(PostgresStore, "_insert_outbox", boom)

    with pytest.raises(RuntimeError):
        db.finalize_interview(DSN, "iv-1", _records(), _report(), event=_event())

    assert db.get_interview(DSN, "iv-1")["status"] == "running"
    assert db.get_report(DSN, "iv-1") is None
    with pg._pool.connection() as conn:  # noqa: SLF001
        assert conn.execute("SELECT COUNT(*) AS n FROM answers").fetchone()["n"] == 0


@requires_pg
def test_pg_领取后租约内不会被再次领取(pg):
    db.enqueue_outbox(DSN, _event())
    db.enqueue_outbox(DSN, _event("iv-2"))

    first = pg.claim_outbox_batch(limit=10, lease_s=60)
    second = pg.claim_outbox_batch(limit=10, lease_s=60)

    assert len(first) == 2
    assert second == []


@requires_pg
def test_pg_发布标记后不再待发(pg):
    db.enqueue_outbox(DSN, _event())
    rows = pg.claim_outbox_batch(limit=10)

    assert pg.mark_outbox_published([row["id"] for row in rows]) == 1
    assert pg.count_outbox_pending() == 0


@requires_pg
def test_pg_失败退避到期后可再次领取(pg):
    db.enqueue_outbox(DSN, _event())
    [row] = pg.claim_outbox_batch(limit=10)

    pg.mark_outbox_failed(
        [row["id"]],
        error="kafka down",
        next_attempt_at="2099-01-01T00:00:00+00:00",
    )
    assert pg.claim_outbox_batch(limit=10) == []

    pg.mark_outbox_failed(
        [row["id"]], error="retry", next_attempt_at="2000-01-01T00:00:00+00:00"
    )
    [again] = pg.claim_outbox_batch(limit=10)
    assert again["id"] == row["id"]
    assert again["attempts"] == 2


@requires_pg
def test_pg_skip_locked_不会重复领取正在被处理的行(pg):
    db.enqueue_outbox(DSN, _event())
    other = PostgresStore.connect(DSN, min_size=1, max_size=1)
    try:
        with pg._pool.connection() as conn:  # noqa: SLF001
            with conn.transaction():
                conn.execute(
                    "SELECT id FROM agent_outbox FOR UPDATE SKIP LOCKED"
                ).fetchall()
                # 行被当前事务锁住：另一个 relay 即使租约未到期也应跳过（SKIP LOCKED）
                assert other.claim_outbox_batch(limit=10) == []
    finally:
        other.close()
