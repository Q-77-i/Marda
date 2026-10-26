"""业务库的 SQLite 实现（SPEC §8）：users / interviews / answers / reports / resumes。

分工口径（SPEC §8）：面试过程以 checkpointer state 为权威，本模块只承担
「历史列表 + 落库产物」；answers/reports 在面试结束后一次写入。

用户隔离（FR-23）：interviews.user_id 为归属列，列表查询按用户过滤；
checkpointer 不需要隔离——thread_id = 全局唯一 uuid，本模块才是归属权威。

全部为同步函数（sqlite3），异步调用方经 asyncio.to_thread 包裹
（与 tools/question_search._fetch_by_ids 同模式）。

P4-E2 起本文件只负责 SQLite 那一侧；分派在 app/db.py（门面）。
PostgreSQL 侧是 app/db_postgres.py——**两份实现的函数签名与语义必须一致**，
否则门面分派就成了「同一函数两种行为」的陷阱。
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from collections.abc import Callable
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


class SqliteStore:
    """SQLite 业务库。一个实例绑定一个库文件；连接**按调用开关**（sqlite3 连接不能跨线程）。"""

    def __init__(self, db_path: Path, *, clock: Callable[[], str] | None = None) -> None:
        self.db_path = Path(db_path)
        # 时钟可注入：单测用固定时间戳控制排序（门面 db._now 会被 monkeypatch）
        self.clock: Callable[[], str] = clock or _now

    # ---------- 连接 ----------

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _read(self, fn, *args, **kwargs):
        """只读事务：开连接 → 执行 → 关闭。"""
        with closing(self._connect()) as conn:
            return fn(conn, *args, **kwargs)

    def _write(self, fn, *args, **kwargs):
        """写事务：`with conn` 成功提交、异常回滚；连接一定关闭。"""
        with closing(self._connect()) as conn:
            with conn:
                return fn(conn, *args, **kwargs)

    # ---------- schema ----------

    def ensure_schema(self) -> None:
        with closing(self._connect()) as conn:
            conn.executescript(SCHEMA)
            conn.commit()
            self._migrate(conn)

    def _migrate(self, conn: sqlite3.Connection) -> None:
        """轻量迁移（补列，幂等）。

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
        if self._table_exists(conn, "questions"):
            question_columns = {row["name"] for row in conn.execute("PRAGMA table_info(questions)")}
            if "user_id" not in question_columns:
                conn.execute("ALTER TABLE questions ADD COLUMN user_id TEXT")

    @staticmethod
    def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
        ).fetchone()
        return row is not None

    # ---------- users ----------

    def create_user(self, *, user_id: str, username: str, password_hash: str) -> bool:
        """建账号；用户名已存在（UNIQUE 冲突）返回 False。username 由调用方保证已小写化。"""
        try:
            self._write(
                lambda conn: conn.execute(
                    "INSERT INTO users (id, username, password_hash, created_at) VALUES (?, ?, ?, ?)",
                    (user_id, username, password_hash, self.clock()),
                )
            )
        except sqlite3.IntegrityError:
            return False
        return True

    def get_user(self, user_id: str) -> dict | None:
        row = self._read(
            lambda conn: conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        )
        return dict(row) if row else None

    def get_user_by_username(self, username: str) -> dict | None:
        row = self._read(
            lambda conn: conn.execute(
                "SELECT * FROM users WHERE username=?", (username,)
            ).fetchone()
        )
        return dict(row) if row else None

    def claim_orphan_interviews(self, user_id: str) -> int:
        """无归属场次（user_id IS NULL）归入该用户，返回认领数。

        只在注册时调用：首个注册账号认领阶段 1 的历史数据；后续账号注册时已无 NULL 行，
        自然认领 0 条（不依赖「用户数 == 0」判断，免并发竞态）。
        """

        def _claim(conn):
            return conn.execute(
                "UPDATE interviews SET user_id=? WHERE user_id IS NULL", (user_id,)
            ).rowcount

        return self._write(_claim)

    # ---------- interviews ----------

    def create_interview(
        self,
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
        self._write(
            lambda conn: conn.execute(
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
                    self.clock(),
                    resume_id,
                ),
            )
        )

    def finish_interview(self, interview_id: str) -> None:
        self._write(
            lambda conn: conn.execute(
                "UPDATE interviews SET status='finished', ended_at=? WHERE id=?",
                (self.clock(), interview_id),
            )
        )

    def save_answers(self, interview_id: str, records: list[dict[str, Any]]) -> None:
        """records 字段：question_id/domain/difficulty/candidate_answer/followup_count/
        skipped/score_json（question_id 为空 = 生成题，SPEC §8）。"""
        rows = [
            (
                interview_id,
                r["question_id"],
                r["domain"],
                r["difficulty"],
                r["candidate_answer"],
                r["followup_count"],
                r["skipped"],
                r["score_json"],
                self.clock(),
            )
            for r in records
        ]
        if not rows:
            return

        def _insert(conn):
            conn.executemany(
                "INSERT INTO answers (interview_id, question_id, domain, difficulty,"
                " candidate_answer, followup_count, skipped, score_json, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                rows,
            )

        self._write(_insert)

    def save_report(self, interview_id: str, payload: dict[str, Any]) -> None:
        """id = interview_id（1:1），二次保存自然覆盖（幂等，SPEC §8 落库产物）。"""
        self._write(
            lambda conn: conn.execute(
                "INSERT OR REPLACE INTO reports (id, interview_id, payload, created_at)"
                " VALUES (?, ?, ?, ?)",
                (interview_id, interview_id, json.dumps(payload, ensure_ascii=False), self.clock()),
            )
        )

    def finalize_interview(
        self,
        interview_id: str,
        records: list[dict[str, Any]],
        report: dict[str, Any],
    ) -> None:
        """**单事务**收尾：answers + report + interviews.status（P4-E2）。

        旧路径是三次独立写（answers → report → status），中途失败会留下
        「图 finished、业务 running、报告缺失」的半成品场次，且用户不能重试。
        这里把三步放进同一个事务：要么全成，要么全不成。

        幂等：先删本场旧 answers 再插（重放同一场不会重复计分）。
        """
        now = self.clock()
        payload = json.dumps(report, ensure_ascii=False)
        rows = [
            (
                interview_id,
                r["question_id"],
                r["domain"],
                r["difficulty"],
                r["candidate_answer"],
                r["followup_count"],
                r["skipped"],
                r["score_json"],
                now,
            )
            for r in records
        ]

        def _finalize(conn):
            conn.execute("DELETE FROM answers WHERE interview_id=?", (interview_id,))
            if rows:
                conn.executemany(
                    "INSERT INTO answers (interview_id, question_id, domain, difficulty,"
                    " candidate_answer, followup_count, skipped, score_json, created_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    rows,
                )
            conn.execute(
                "INSERT OR REPLACE INTO reports (id, interview_id, payload, created_at)"
                " VALUES (?, ?, ?, ?)",
                (interview_id, interview_id, payload, now),
            )
            conn.execute(
                "UPDATE interviews SET status='finished', ended_at=? WHERE id=?",
                (now, interview_id),
            )

        self._write(_finalize)

    def get_interview(self, interview_id: str) -> dict | None:
        row = self._read(
            lambda conn: conn.execute(
                "SELECT * FROM interviews WHERE id=?", (interview_id,)
            ).fetchone()
        )
        return dict(row) if row else None

    def delete_interview(self, interview_id: str) -> bool:
        """物理删除场次（T7a-R1）：interviews/answers/reports 三表。返回是否存在过。

        简历（P2-M11）按**引用计数**清理：简历是用户级资产、同一份可跑多场，删场次只在该
        简历已无任何场次引用时才一并删除——不静默把简历原文永久留在库里，也不误删还要用的。
        """
        deleted = 0

        def _delete(conn):
            nonlocal deleted
            row = conn.execute(
                "SELECT resume_id FROM interviews WHERE id=?", (interview_id,)
            ).fetchone()
            resume_id = row["resume_id"] if row else None
            deleted = conn.execute(
                "DELETE FROM interviews WHERE id=?", (interview_id,)
            ).rowcount
            conn.execute("DELETE FROM answers WHERE interview_id=?", (interview_id,))
            conn.execute("DELETE FROM reports WHERE interview_id=?", (interview_id,))
            if resume_id:
                conn.execute(
                    "DELETE FROM resumes WHERE id=?"
                    " AND NOT EXISTS (SELECT 1 FROM interviews WHERE resume_id=?)",
                    (resume_id, resume_id),
                )

        self._write(_delete)
        return deleted > 0

    def query_interviews(
        self,
        *,
        user_id: str,
        status: str | None = None,
        interview_type: str | None = None,
        since: str | None = None,
        page: int = 1,
        page_size: int = 10,
    ) -> tuple[list[dict], int, dict[str, int]]:
        """分页 + 过滤的历史列表（P2 面试记录页）：返回 (当前页行, 过滤后总数, 状态分布)。

        **user_id 必传**（FR-23）：隔离靠每个查询都带归属条件，避免漏过滤跨用户泄漏。
        - `interview_type="tech"` 同时匹配老场次的 NULL（P1-M11 口径：NULL 按技术面处理）
        - `since` 是 `YYYY-MM-DD`（UTC 日期），按 `started_at >= 当日 00:00 UTC` 比较
        - `total` 与 `counts` 都基于**过滤后的全集**，不受分页影响；
          `counts` 固定返回 finished/running 两键（status 过滤后另一类为 0）
        - 排序 `started_at DESC, id ASC`：同秒场次靠 id 兜底，OFFSET 分页不重不漏
        """
        where = ["user_id=?"]
        params: list[Any] = [user_id]
        if status is not None:
            where.append("status=?")
            params.append(status)
        if interview_type == INTERVIEW_TECH:
            where.append("(interview_type=? OR interview_type IS NULL)")
            params.append(interview_type)
        elif interview_type is not None:
            where.append("interview_type=?")
            params.append(interview_type)
        if since is not None:
            where.append("started_at>=?")
            params.append(f"{since}T00:00:00+00:00")
        clause = " AND ".join(where)

        def _query(conn):
            total = conn.execute(
                f"SELECT COUNT(*) FROM interviews WHERE {clause}", params
            ).fetchone()[0]
            by_status = {
                row[0]: row[1]
                for row in conn.execute(
                    f"SELECT status, COUNT(*) FROM interviews WHERE {clause} GROUP BY status",
                    params,
                )
            }
            rows = conn.execute(
                f"SELECT * FROM interviews WHERE {clause}"
                " ORDER BY started_at DESC, id ASC LIMIT ? OFFSET ?",
                [*params, page_size, (page - 1) * page_size],
            ).fetchall()
            return total, by_status, rows

        total, by_status, rows = self._read(_query)
        counts = {
            "finished": by_status.get("finished", 0),
            "running": by_status.get("running", 0),
        }
        return [dict(row) for row in rows], total, counts

    def list_reports(self, *, user_id: str, limit: int = 200) -> list[dict]:
        """用户全部已落库报告 + 场次元信息（FR-19 能力档案），按开始时间**升序**。

        升序是刻意的：档案曲线从左到右 = 时间从早到晚，排序在这里定死，上层不再重排。

        **以 reports 表为准，不按 interviews.status 过滤**（P1-M10 D5）：有报告才算数——
        status='finished' 但报告落库失败/缺失的边缘场次没有分数可画，混进来只会让曲线多一个空点。
        代价是这类场次在档案里不可见（用户会疑惑「我明明跑了那场」），列为已知待办。
        """
        rows = self._read(
            lambda conn: conn.execute(
                "SELECT i.id AS interview_id, r.payload, i.position, i.difficulty, i.started_at"
                " FROM reports r JOIN interviews i ON i.id = r.interview_id"
                " WHERE i.user_id=?"
                " ORDER BY i.started_at ASC, i.id ASC LIMIT ?",
                (user_id, limit),
            ).fetchall()
        )
        out = []
        for row in rows:
            item = dict(row)
            item["payload"] = json.loads(item["payload"])
            out.append(item)
        return out

    def count_finished_without_report(self, *, user_id: str) -> int:
        """本人**已完成但没有报告**的场次数（P2-M3）。

        档案按 reports 表取数（M10 D5，口径不变）→ 这类场次不在曲线里。计数回给前端，
        让档案页能说明「我明明跑了那场」——不补零、不画进曲线（它们没有分数）。
        判据与列表页一致（`status='finished'`）：进行中的场次不算，用户也不会期待它出现在档案里。
        """
        row = self._read(
            lambda conn: conn.execute(
                "SELECT COUNT(*) FROM interviews i"
                " WHERE i.user_id=? AND i.status='finished'"
                " AND NOT EXISTS (SELECT 1 FROM reports r WHERE r.interview_id = i.id)",
                (user_id,),
            ).fetchone()
        )
        return int(row[0])

    # ---------- resumes ----------

    def save_resume(
        self,
        *,
        resume_id: str,
        user_id: str,
        filename: str,
        text: str,
        parsed: dict[str, Any],
    ) -> None:
        """落一份简历（P2-M11）：原文 + 结构化结果。parsed 存 JSON 文本，读取侧解析。"""
        self._write(
            lambda conn: conn.execute(
                "INSERT INTO resumes (id, user_id, filename, text, parsed, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (
                    resume_id,
                    user_id,
                    filename,
                    text,
                    json.dumps(parsed, ensure_ascii=False),
                    self.clock(),
                ),
            )
        )

    def prune_unreferenced_resumes(self, *, user_id: str) -> int:
        """清掉该用户**没有任何场次引用**的旧简历，返回删除行数（P2-M11）。

        简历是用户级资产：创建场次时带上 resume_id、删场次后再无引用才销毁
        （delete_interview 的引用计数）。但「解析成功却没创建场次」会留下孤儿——
        下一次解析时顺手清掉，不留静默垃圾、也不引后台任务。

        **已知边界**：多标签同时开着创建页时，一边解析会清掉另一边尚未使用的简历
        （那一边创建时报「简历不存在」，重新解析即可）——单机 demo 可接受，如实记录。
        """
        return self._write(
            lambda conn: conn.execute(
                "DELETE FROM resumes WHERE user_id=?"
                " AND NOT EXISTS (SELECT 1 FROM interviews WHERE resume_id = resumes.id)",
                (user_id,),
            ).rowcount
        )

    def get_resume(self, *, user_id: str, resume_id: str) -> dict | None:
        """取本人简历；非本人 / 不存在一律 None（调用方按「不存在」404，不泄露存在性）。

        user_id 必传（同 bank_private 的隔离口径）：漏传在调用处直接 TypeError，
        而不是静默查出别人的简历。
        """
        row = self._read(
            lambda conn: conn.execute(
                "SELECT * FROM resumes WHERE id=? AND user_id=?", (resume_id, user_id)
            ).fetchone()
        )
        if row is None:
            return None
        item = dict(row)
        item["parsed"] = json.loads(item["parsed"]) if item["parsed"] else {}
        return item

    def get_report(self, interview_id: str) -> dict | None:
        row = self._read(
            lambda conn: conn.execute(
                "SELECT * FROM reports WHERE interview_id=?", (interview_id,)
            ).fetchone()
        )
        if row is None:
            return None
        return {"payload": json.loads(row["payload"]), "created_at": row["created_at"]}
