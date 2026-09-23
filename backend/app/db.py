"""业务库持久化（SPEC §8）：users / interviews / answers / reports 四表。

分工口径（SPEC §8）：面试过程以 checkpointer state 为权威，本模块只承担
「历史列表 + 落库产物」；answers/reports 在面试结束后一次写入。

用户隔离（FR-23）：interviews.user_id 为归属列，列表查询按用户过滤；
checkpointer 不需要隔离——thread_id = 全局唯一 uuid，本模块才是归属权威。

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
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    username TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password_hash TEXT NOT NULL,
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS interviews (
    id TEXT PRIMARY KEY,
    thread_id TEXT UNIQUE,
    user_id TEXT,
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
        _migrate(conn)


def _migrate(conn: sqlite3.Connection) -> None:
    """轻量迁移（阶段 2 仍 SQLite，PG 迁移推阶段 3）：阶段 1 老库补 interviews.user_id。"""
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(interviews)")}
    if "user_id" not in columns:
        conn.execute("ALTER TABLE interviews ADD COLUMN user_id TEXT")


def create_user(db_path: Path, *, user_id: str, username: str, password_hash: str) -> bool:
    """建账号；用户名已存在（UNIQUE 冲突）返回 False。username 由调用方保证已小写化。"""
    try:
        with _connect(db_path) as conn:
            conn.execute(
                "INSERT INTO users (id, username, password_hash, created_at) VALUES (?, ?, ?, ?)",
                (user_id, username, password_hash, _now()),
            )
    except sqlite3.IntegrityError:
        return False
    return True


def get_user(db_path: Path, user_id: str) -> dict | None:
    with _connect(db_path) as conn:
        row = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    return dict(row) if row else None


def get_user_by_username(db_path: Path, username: str) -> dict | None:
    with _connect(db_path) as conn:
        row = conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
    return dict(row) if row else None


def claim_orphan_interviews(db_path: Path, user_id: str) -> int:
    """无归属场次（user_id IS NULL）归入该用户，返回认领数。

    只在注册时调用：首个注册账号认领阶段 1 的历史数据；后续账号注册时已无 NULL 行，
    自然认领 0 条（不依赖「用户数 == 0」判断，免并发竞态）。
    """
    with _connect(db_path) as conn:
        return conn.execute(
            "UPDATE interviews SET user_id=? WHERE user_id IS NULL", (user_id,)
        ).rowcount


def create_interview(
    db_path: Path,
    *,
    interview_id: str,
    position: str,
    question_count: int,
    difficulty: str = "L1",
    user_id: str | None = None,
) -> None:
    with _connect(db_path) as conn:
        conn.execute(
            "INSERT INTO interviews (id, thread_id, user_id, position, question_count, phase,"
            " difficulty, status, started_at) VALUES (?, ?, ?, ?, ?, ?, ?, 'running', ?)",
            (
                interview_id,
                interview_id,
                user_id,
                position,
                question_count,
                "intro",
                difficulty,
                _now(),
            ),
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


def delete_interview(db_path: Path, interview_id: str) -> bool:
    """物理删除场次（T7a-R1）：interviews/answers/reports 三表。返回是否存在过。"""
    with _connect(db_path) as conn:
        deleted = conn.execute("DELETE FROM interviews WHERE id=?", (interview_id,)).rowcount
        conn.execute("DELETE FROM answers WHERE interview_id=?", (interview_id,))
        conn.execute("DELETE FROM reports WHERE interview_id=?", (interview_id,))
    return deleted > 0


def list_interviews(db_path: Path, *, user_id: str, limit: int = 50) -> list[dict]:
    """按用户过滤（FR-23）：user_id 必传，避免漏过滤导致跨用户泄漏。"""
    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM interviews WHERE user_id=? ORDER BY started_at DESC, id ASC LIMIT ?",
            (user_id, limit),
        ).fetchall()
    return [dict(row) for row in rows]


def get_report(db_path: Path, interview_id: str) -> dict | None:
    with _connect(db_path) as conn:
        row = conn.execute("SELECT * FROM reports WHERE interview_id=?", (interview_id,)).fetchone()
    if row is None:
        return None
    return {"payload": json.loads(row["payload"]), "created_at": row["created_at"]}
