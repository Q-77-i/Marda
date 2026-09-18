"""业务库持久化（SPEC §8）：interviews / answers / reports 三表。

分工口径（SPEC §8）：面试过程以 checkpointer state 为权威，本模块只承担
「历史列表 + 落库产物」；answers/reports 在面试结束后一次写入。

全部为同步函数（sqlite3），异步调用方经 asyncio.to_thread 包裹
（与 tools/question_search._fetch_by_ids 同模式）。
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS interviews (
    id TEXT PRIMARY KEY,
    thread_id TEXT UNIQUE,
    position TEXT,
    question_count INT,
    phase TEXT,
    difficulty TEXT,
    status TEXT,
    started_at TEXT,
    ended_at TEXT
);
CREATE TABLE IF NOT EXISTS answers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    interview_id TEXT,
    question_id TEXT,
    domain TEXT,
    difficulty TEXT,
    candidate_answer TEXT,
    followup_count INT,
    skipped INT DEFAULT 0,
    score_json TEXT,
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS reports (
    id TEXT PRIMARY KEY,
    interview_id TEXT,
    payload JSON,
    created_at TEXT
);
"""


def _now() -> str:
    """UTC ISO 时间戳（微秒精度，同秒多场次排序不丢序）。"""
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def ensure_schema(db_path: Path) -> None:
    with _connect(db_path) as conn:
        conn.executescript(SCHEMA)


def create_interview(
    db_path: Path,
    *,
    interview_id: str,
    position: str,
    question_count: int,
    difficulty: str = "L1",
) -> None:
    with _connect(db_path) as conn:
        conn.execute(
            "INSERT INTO interviews (id, thread_id, position, question_count, phase,"
            " difficulty, status, started_at) VALUES (?, ?, ?, ?, ?, ?, 'running', ?)",
            (interview_id, interview_id, position, question_count, "intro", difficulty, _now()),
        )


def finish_interview(db_path: Path, interview_id: str) -> None:
    with _connect(db_path) as conn:
        conn.execute(
            "UPDATE interviews SET status='finished', ended_at=? WHERE id=?",
            (_now(), interview_id),
        )


def save_answers(db_path: Path, interview_id: str, records: list[dict[str, Any]]) -> None:
    """records 字段：question_id/domain/difficulty/candidate_answer/followup_count/
    skipped/score_json（question_id 为空 = 生成题，SPEC §8）。"""
    with _connect(db_path) as conn:
        conn.executemany(
            "INSERT INTO answers (interview_id, question_id, domain, difficulty,"
            " candidate_answer, followup_count, skipped, score_json, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    interview_id,
                    r["question_id"],
                    r["domain"],
                    r["difficulty"],
                    r["candidate_answer"],
                    r["followup_count"],
                    r["skipped"],
                    r["score_json"],
                    _now(),
                )
                for r in records
            ],
        )


def save_report(db_path: Path, interview_id: str, payload: dict[str, Any]) -> None:
    """id = interview_id（1:1），二次保存自然覆盖（幂等，SPEC §8 落库产物）。"""
    with _connect(db_path) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO reports (id, interview_id, payload, created_at)"
            " VALUES (?, ?, ?, ?)",
            (interview_id, interview_id, json.dumps(payload, ensure_ascii=False), _now()),
        )


def get_interview(db_path: Path, interview_id: str) -> dict | None:
    with _connect(db_path) as conn:
        row = conn.execute("SELECT * FROM interviews WHERE id=?", (interview_id,)).fetchone()
    return dict(row) if row else None


def list_interviews(db_path: Path, limit: int = 50) -> list[dict]:
    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM interviews ORDER BY started_at DESC, id ASC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(row) for row in rows]


def get_report(db_path: Path, interview_id: str) -> dict | None:
    with _connect(db_path) as conn:
        row = conn.execute("SELECT * FROM reports WHERE interview_id=?", (interview_id,)).fetchone()
    if row is None:
        return None
    return {"payload": json.loads(row["payload"]), "created_at": row["created_at"]}
