"""P5-J5 T2：SQLite 侧 outbox 与 finalize 同事务（单机形态也要保持一致语义）。"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import datetime, timezone

import pytest

from app import db
from app.db_sqlite import SqliteStore
from app.outbox.events import build_interview_finalized

FINISHED_AT = datetime(2026, 11, 20, 12, 0, 0, tzinfo=timezone.utc)


def _report() -> dict:
    return {
        "interview_id": "iv-1",
        "position": "Agent",
        "interview_type": "tech",
        "scores": {"technical_depth": 3.0},
        "overall": 3.0,
        "domain_scores": {"rag": 2.5},
        "weaknesses": ["rag"],
        "study_advice": [{"domain": "rag", "advice": "补混合检索"}],
    }


def _event() -> dict:
    event = build_interview_finalized(
        interview_id="iv-1",
        user_id="u1",
        report=_report(),
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


def _prepare(tmp_path):
    target = tmp_path / "marda.sqlite3"
    db.ensure_schema(target)
    db.create_interview(
        target, interview_id="iv-1", position="Agent", question_count=3, user_id="u1"
    )
    return target


def _rows(target, sql: str) -> list[sqlite3.Row]:
    with closing(sqlite3.connect(target)) as conn:
        conn.row_factory = sqlite3.Row
        return conn.execute(sql).fetchall()


def test_finalize_与outbox同事务写入(tmp_path):
    target = _prepare(tmp_path)

    db.finalize_interview(target, "iv-1", _records(), _report(), event=_event())

    rows = _rows(target, "SELECT * FROM agent_outbox")
    assert len(rows) == 1
    assert rows[0]["id"] == _event()["event_id"]
    assert rows[0]["event_type"] == "interview.finalized"
    assert rows[0]["aggregate_id"] == "iv-1"
    assert rows[0]["published_at"] is None


def test_不传事件不写outbox(tmp_path):
    target = _prepare(tmp_path)

    db.finalize_interview(target, "iv-1", _records(), _report())

    assert _rows(target, "SELECT * FROM agent_outbox") == []


def test_重复finalize只有一条事件(tmp_path):
    target = _prepare(tmp_path)

    db.finalize_interview(target, "iv-1", _records(), _report(), event=_event())
    db.finalize_interview(target, "iv-1", _records(), _report(), event=_event())

    assert len(_rows(target, "SELECT * FROM agent_outbox")) == 1


def test_单独入队幂等(tmp_path):
    target = _prepare(tmp_path)

    assert db.enqueue_outbox(target, _event()) is True
    assert db.enqueue_outbox(target, _event()) is False

    assert len(_rows(target, "SELECT * FROM agent_outbox")) == 1


def test_outbox写失败时finalize整体回滚(tmp_path, monkeypatch):
    target = _prepare(tmp_path)

    def boom(conn, event, now):
        raise RuntimeError("outbox insert failed")

    monkeypatch.setattr(SqliteStore, "_insert_outbox", staticmethod(boom))

    with pytest.raises(RuntimeError):
        db.finalize_interview(target, "iv-1", _records(), _report(), event=_event())

    assert db.get_interview(target, "iv-1")["status"] == "running"
    assert db.get_report(target, "iv-1") is None
    assert _rows(target, "SELECT * FROM answers") == []
