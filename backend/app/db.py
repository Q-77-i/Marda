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

from app.domain import INTERVIEW_TECH

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
    interview_type TEXT,
    question_count INT,
    phase TEXT,
    difficulty TEXT,
    status TEXT,
    started_at TEXT,
    ended_at TEXT,
    resume_id TEXT
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
CREATE TABLE IF NOT EXISTS resumes (
    id TEXT PRIMARY KEY,
    user_id TEXT,
    filename TEXT,
    text TEXT,
    parsed JSON,
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
    """轻量迁移（阶段 2 仍 SQLite，PG 迁移推阶段 3）。

    - interviews.user_id：阶段 1 老库补归属列（FR-23）
    - interviews.interview_type：会话类型列（P1-M11 FR-22），老库补列后为 NULL——
      语义等同 "tech"（历史场次全是技术面），消费方按「非 behavioral 即技术面」处理，
      不做全表回填（回填会与 reports.payload.interview_type 形成两个可漂移的来源）。
    - questions.user_id：私有题库归属列（P1-M7 FR-13），NULL = 公共题。
    - interviews.resume_id：场次引用的简历（P2-M11 FR-28），老库补列后为 NULL = 没传简历。

    questions 表由语料管道建（不是本模块的 DDL），故先探测表是否存在——
    老库/未跑管道的库不该因为这个迁移而报错。管道侧 ingest._migrate 对同一列
    有同样的列探测补齐，两边谁先跑都成立。
    """
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(interviews)")}
    if "user_id" not in columns:
        conn.execute("ALTER TABLE interviews ADD COLUMN user_id TEXT")
    if "interview_type" not in columns:
        conn.execute("ALTER TABLE interviews ADD COLUMN interview_type TEXT")
    if "resume_id" not in columns:
        conn.execute("ALTER TABLE interviews ADD COLUMN resume_id TEXT")
    if _table_exists(conn, "questions"):
        question_columns = {row["name"] for row in conn.execute("PRAGMA table_info(questions)")}
        if "user_id" not in question_columns:
            conn.execute("ALTER TABLE questions ADD COLUMN user_id TEXT")


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return row is not None


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
    interview_type: str = INTERVIEW_TECH,
    resume_id: str | None = None,
) -> None:
    """`difficulty` 存**创建时选定的难度**（P1-M6 FR-14）："adaptive" 或 L1/L2/L3。

    注意与 state.difficulty 的区别：state 里存的是实际选题难度（自适应会随连击升降），
    本列是用户的选择、供列表展示；列表页不展示自适应过程中的中间难度。

    `interview_type`（P1-M11 FR-22）：tech / behavioral，与 position 正交。

    `resume_id`（P2-M11 FR-28）：本场使用的简历（NULL = 没传）。它同时是简历的
    **引用计数**——删除场次时，没人再引用才连简历一起删（见 delete_interview）。
    """
    with _connect(db_path) as conn:
        conn.execute(
            "INSERT INTO interviews (id, thread_id, user_id, position, interview_type,"
            " question_count, phase, difficulty, status, started_at, resume_id)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'running', ?, ?)",
            (
                interview_id,
                interview_id,
                user_id,
                position,
                interview_type,
                question_count,
                "intro",
                difficulty,
                _now(),
                resume_id,
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
    """物理删除场次（T7a-R1）：interviews/answers/reports 三表。返回是否存在过。

    简历（P2-M11）按**引用计数**清理：简历是用户级资产、同一份可跑多场，删场次只在该
    简历已无任何场次引用时才一并删除——不静默把简历原文永久留在库里，也不误删还要用的。
    """
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT resume_id FROM interviews WHERE id=?", (interview_id,)
        ).fetchone()
        resume_id = row["resume_id"] if row else None
        deleted = conn.execute("DELETE FROM interviews WHERE id=?", (interview_id,)).rowcount
        conn.execute("DELETE FROM answers WHERE interview_id=?", (interview_id,))
        conn.execute("DELETE FROM reports WHERE interview_id=?", (interview_id,))
        if resume_id:
            conn.execute(
                "DELETE FROM resumes WHERE id=?"
                " AND NOT EXISTS (SELECT 1 FROM interviews WHERE resume_id=?)",
                (resume_id, resume_id),
            )
    return deleted > 0


def list_interviews(db_path: Path, *, user_id: str, limit: int = 50) -> list[dict]:
    """按用户过滤（FR-23）：user_id 必传，避免漏过滤导致跨用户泄漏。"""
    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM interviews WHERE user_id=? ORDER BY started_at DESC, id ASC LIMIT ?",
            (user_id, limit),
        ).fetchall()
    return [dict(row) for row in rows]


def list_reports(db_path: Path, *, user_id: str, limit: int = 200) -> list[dict]:
    """用户全部已落库报告 + 场次元信息（FR-19 能力档案），按开始时间**升序**。

    升序是刻意的：档案曲线从左到右 = 时间从早到晚，排序在这里定死，上层不再重排。

    **以 reports 表为准，不按 interviews.status 过滤**（P1-M10 D5）：有报告才算数——
    status='finished' 但报告落库失败/缺失的边缘场次没有分数可画，混进来只会让曲线多一个空点。
    代价是这类场次在档案里不可见（用户会疑惑「我明明跑了那场」），列为已知待办。
    """
    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT i.id AS interview_id, r.payload, i.position, i.difficulty, i.started_at"
            " FROM reports r JOIN interviews i ON i.id = r.interview_id"
            " WHERE i.user_id=?"
            " ORDER BY i.started_at ASC, i.id ASC LIMIT ?",
            (user_id, limit),
        ).fetchall()
    out = []
    for row in rows:
        item = dict(row)
        item["payload"] = json.loads(item["payload"])
        out.append(item)
    return out


def count_finished_without_report(db_path: Path, *, user_id: str) -> int:
    """本人**已完成但没有报告**的场次数（P2-M3）。

    档案按 reports 表取数（M10 D5，口径不变）→ 这类场次不在曲线里。计数回给前端，
    让档案页能说明「我明明跑了那场」——不补零、不画进曲线（它们没有分数）。
    判据与列表页一致（`status='finished'`）：进行中的场次不算，用户也不会期待它出现在档案里。
    """
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM interviews i"
            " WHERE i.user_id=? AND i.status='finished'"
            " AND NOT EXISTS (SELECT 1 FROM reports r WHERE r.interview_id = i.id)",
            (user_id,),
        ).fetchone()
    return int(row[0])


def save_resume(
    db_path: Path,
    *,
    resume_id: str,
    user_id: str,
    filename: str,
    text: str,
    parsed: dict[str, Any],
) -> None:
    """落一份简历（P2-M11）：原文 + 结构化结果。parsed 存 JSON 文本，读取侧解析。"""
    with _connect(db_path) as conn:
        conn.execute(
            "INSERT INTO resumes (id, user_id, filename, text, parsed, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (resume_id, user_id, filename, text, json.dumps(parsed, ensure_ascii=False), _now()),
        )


def prune_unreferenced_resumes(db_path: Path, *, user_id: str) -> int:
    """清掉该用户**没有任何场次引用**的旧简历，返回删除行数（P2-M11）。

    简历是用户级资产：创建场次时带上 resume_id、删场次后再无引用才销毁
    （delete_interview 的引用计数）。但「解析成功却没创建场次」会留下孤儿——
    下一次解析时顺手清掉，不留静默垃圾、也不引后台任务。

    **已知边界**：多标签同时开着创建页时，一边解析会清掉另一边尚未使用的简历
    （那一边创建时报「简历不存在」，重新解析即可）——单机 demo 可接受，如实记录。
    """
    with _connect(db_path) as conn:
        return conn.execute(
            "DELETE FROM resumes WHERE user_id=?"
            " AND NOT EXISTS (SELECT 1 FROM interviews WHERE resume_id = resumes.id)",
            (user_id,),
        ).rowcount


def get_resume(db_path: Path, *, user_id: str, resume_id: str) -> dict | None:
    """取本人简历；非本人 / 不存在一律 None（调用方按「不存在」404，不泄露存在性）。

    user_id 必传（同 bank_private 的隔离口径）：漏传在调用处直接 TypeError，
    而不是静默查出别人的简历。
    """
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM resumes WHERE id=? AND user_id=?", (resume_id, user_id)
        ).fetchone()
    if row is None:
        return None
    item = dict(row)
    item["parsed"] = json.loads(item["parsed"]) if item["parsed"] else {}
    return item


def get_report(db_path: Path, interview_id: str) -> dict | None:
    with _connect(db_path) as conn:
        row = conn.execute("SELECT * FROM reports WHERE interview_id=?", (interview_id,)).fetchone()
    if row is None:
        return None
    return {"payload": json.loads(row["payload"]), "created_at": row["created_at"]}
