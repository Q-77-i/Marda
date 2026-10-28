"""业务库的 PostgreSQL 实现（P4-E2）：与 db_sqlite 同签名同语义。

为什么业务库要上 PG（升级方案 §2.2）：
- 业务库与 checkpointer 都要跨进程共享写（多 worker），SQLite 的写锁会变成尾延迟；
- finalize 必须是**单事务**（answers + report + status），PG 的事务语义直接可用；
- 账号/简历这类用户级数据需要真正的并发安全约束。

方言差异（升级方案 §4.2.2）：
| SQLite | PostgreSQL |
| --- | --- |
| `?` 占位符 | `%s` |
| `INTEGER PRIMARY KEY AUTOINCREMENT` | `BIGSERIAL PRIMARY KEY` |
| `INSERT OR REPLACE INTO reports` | `INSERT ... ON CONFLICT (id) DO UPDATE SET ...` |
| `PRAGMA table_info` 迁移 | `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` |
| `COLLATE NOCASE` | 用户名已在 API 层小写化，普通 `UNIQUE` 即可 |
| `json.dumps` 存 TEXT | 保持 TEXT（最少改动）；不急着上 JSONB |

连接方式：`psycopg`（同步）+ `psycopg_pool.ConnectionPool`，调用方仍用
`asyncio.to_thread` 包裹（与 SQLite 侧同构，路由层感知不到后端差异）。

**依赖是可选的**：本模块只在 DB_BACKEND=postgres 时被 import/use，
没装 psycopg 也不影响 SQLite 形态的开发与单测。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from collections.abc import Callable
from typing import Any

from app.domain import INTERVIEW_TECH
from app.dsn import normalize_dsn

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    username TEXT NOT NULL UNIQUE,
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
    id BIGSERIAL PRIMARY KEY,
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
    payload TEXT,
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS resumes (
    id TEXT PRIMARY KEY,
    user_id TEXT,
    filename TEXT,
    text TEXT,
    parsed TEXT,
    created_at TEXT
);
CREATE INDEX IF NOT EXISTS interviews_user_id_idx ON interviews (user_id);
CREATE INDEX IF NOT EXISTS answers_interview_id_idx ON answers (interview_id);
CREATE INDEX IF NOT EXISTS reports_interview_id_idx ON reports (interview_id);
CREATE INDEX IF NOT EXISTS resumes_user_id_idx ON resumes (user_id);
"""

# 轻量迁移：补列（幂等）。SQLite 侧用 PRAGMA 探测，PG 侧用 IF NOT EXISTS。
MIGRATIONS = [
    "ALTER TABLE interviews ADD COLUMN IF NOT EXISTS user_id TEXT",
    "ALTER TABLE interviews ADD COLUMN IF NOT EXISTS interview_type TEXT",
    "ALTER TABLE interviews ADD COLUMN IF NOT EXISTS resume_id TEXT",
]


def _now() -> str:
    """UTC ISO 时间戳（微秒精度，同秒多场次排序不丢序）——与 SQLite 侧同格式。"""
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


class PostgresStore:
    """PostgreSQL 业务库。一个实例 = 一个连接池。"""

    def __init__(self, pool: Any, *, clock: Callable[[], str] | None = None) -> None:
        self._pool = pool
        # 时钟可注入：与 SQLite 侧同口径（门面 db._now 会被 monkeypatch）
        self.clock: Callable[[], str] = clock or _now

    @classmethod
    def connect(
        cls,
        dsn: str,
        *,
        min_size: int = 1,
        max_size: int = 5,
        timeout: float = 10.0,
    ) -> "PostgresStore":
        """建连接池（同步）。pool 首次连接失败会抛——**启动即失败优于运行中失败**。

        DSN 会先过 `normalize_dsn`（localhost → 127.0.0.1）：连接池不像裸连接那样回退
        IPv4/IPv6，Windows 上 `localhost` 优先走 `::1` 会直接变成 PoolTimeout。
        """
        from psycopg.rows import dict_row
        from psycopg_pool import ConnectionPool

        pool = ConnectionPool(
            normalize_dsn(dsn),
            min_size=min_size,
            max_size=max_size,
            timeout=timeout,
            # 行工厂 = dict_row：**psycopg 默认返回元组**，`dict(row)` 会抛
            # "dictionary update sequence element #0 has length 4; 2 is required"。
            # 这类「只在真库上才暴露」的坑，正是集成测试存在的意义。
            kwargs={"row_factory": dict_row},
            open=True,
        )
        return cls(pool)

    def close(self) -> None:
        self._pool.close()

    # ---------- schema ----------

    def ensure_schema(self) -> None:
        with self._pool.connection() as conn:
            conn.execute(SCHEMA)
            for stmt in MIGRATIONS:
                conn.execute(stmt)
            conn.commit()

    # ---------- users ----------

    def create_user(self, *, user_id: str, username: str, password_hash: str) -> bool:
        """建账号；用户名已存在（UNIQUE 冲突）返回 False。username 由调用方保证已小写化。"""
        import psycopg

        try:
            with self._pool.connection() as conn:
                conn.execute(
                    "INSERT INTO users (id, username, password_hash, created_at) VALUES (%s, %s, %s, %s)",
                    (user_id, username, password_hash, self.clock()),
                )
                conn.commit()
        except psycopg.errors.UniqueViolation:
            return False
        return True

    def get_user(self, user_id: str) -> dict | None:
        with self._pool.connection() as conn:
            row = conn.execute("SELECT * FROM users WHERE id=%s", (user_id,)).fetchone()
        return dict(row) if row else None

    def get_user_by_username(self, username: str) -> dict | None:
        with self._pool.connection() as conn:
            row = conn.execute(
                "SELECT * FROM users WHERE username=%s", (username,)
            ).fetchone()
        return dict(row) if row else None

    def claim_orphan_interviews(self, user_id: str) -> int:
        with self._pool.connection() as conn:
            cur = conn.execute(
                "UPDATE interviews SET user_id=%s WHERE user_id IS NULL", (user_id,)
            )
            count = cur.rowcount
            conn.commit()
        return count

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
        with self._pool.connection() as conn:
            conn.execute(
                "INSERT INTO interviews (id, thread_id, user_id, position, interview_type,"
                " question_count, phase, difficulty, status, started_at, resume_id)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'running', %s, %s)",
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
            conn.commit()

    def finish_interview(self, interview_id: str) -> None:
        with self._pool.connection() as conn:
            conn.execute(
                "UPDATE interviews SET status='finished', ended_at=%s WHERE id=%s",
                (self.clock(), interview_id),
            )
            conn.commit()

    def save_answers(self, interview_id: str, records: list[dict[str, Any]]) -> None:
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
        with self._pool.connection() as conn:
            with conn.cursor() as cur:
                cur.executemany(
                    "INSERT INTO answers (interview_id, question_id, domain, difficulty,"
                    " candidate_answer, followup_count, skipped, score_json, created_at)"
                    " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                    rows,
                )
            conn.commit()

    def save_report(self, interview_id: str, payload: dict[str, Any]) -> None:
        with self._pool.connection() as conn:
            conn.execute(
                "INSERT INTO reports (id, interview_id, payload, created_at) VALUES (%s, %s, %s, %s)"
                " ON CONFLICT (id) DO UPDATE SET interview_id=EXCLUDED.interview_id,"
                " payload=EXCLUDED.payload, created_at=EXCLUDED.created_at",
                (interview_id, interview_id, json.dumps(payload, ensure_ascii=False), self.clock()),
            )
            conn.commit()

    def finalize_interview(
        self,
        interview_id: str,
        records: list[dict[str, Any]],
        report: dict[str, Any],
    ) -> None:
        """**单事务**收尾：answers + report + interviews.status（P4-E2）。

        与 SQLite 侧同语义：先删本场旧 answers 再插（幂等，不重复计分），
        report upsert，最后把场次标 finished——三步同生共死。
        """
        now = self.clock()
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
        with self._pool.connection() as conn:
            with conn.transaction():
                conn.execute("DELETE FROM answers WHERE interview_id=%s", (interview_id,))
                if rows:
                    with conn.cursor() as cur:
                        cur.executemany(
                            "INSERT INTO answers (interview_id, question_id, domain, difficulty,"
                            " candidate_answer, followup_count, skipped, score_json, created_at)"
                            " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                            rows,
                        )
                conn.execute(
                    "INSERT INTO reports (id, interview_id, payload, created_at) VALUES (%s, %s, %s, %s)"
                    " ON CONFLICT (id) DO UPDATE SET interview_id=EXCLUDED.interview_id,"
                    " payload=EXCLUDED.payload, created_at=EXCLUDED.created_at",
                    (interview_id, interview_id, json.dumps(report, ensure_ascii=False), now),
                )
                conn.execute(
                    "UPDATE interviews SET status='finished', ended_at=%s WHERE id=%s",
                    (now, interview_id),
                )

    def get_interview(self, interview_id: str) -> dict | None:
        with self._pool.connection() as conn:
            row = conn.execute(
                "SELECT * FROM interviews WHERE id=%s", (interview_id,)
            ).fetchone()
        return dict(row) if row else None

    def delete_interview(self, interview_id: str) -> bool:
        with self._pool.connection() as conn:
            with conn.transaction():
                row = conn.execute(
                    "SELECT resume_id FROM interviews WHERE id=%s", (interview_id,)
                ).fetchone()
                resume_id = row["resume_id"] if row else None
                cur = conn.execute("DELETE FROM interviews WHERE id=%s", (interview_id,))
                deleted = cur.rowcount
                conn.execute("DELETE FROM answers WHERE interview_id=%s", (interview_id,))
                conn.execute("DELETE FROM reports WHERE interview_id=%s", (interview_id,))
                if resume_id:
                    conn.execute(
                        "DELETE FROM resumes WHERE id=%s"
                        " AND NOT EXISTS (SELECT 1 FROM interviews WHERE resume_id=%s)",
                        (resume_id, resume_id),
                    )
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
        where = ["user_id=%s"]
        params: list[Any] = [user_id]
        if status is not None:
            where.append("status=%s")
            params.append(status)
        if interview_type == INTERVIEW_TECH:
            where.append("(interview_type=%s OR interview_type IS NULL)")
            params.append(interview_type)
        elif interview_type is not None:
            where.append("interview_type=%s")
            params.append(interview_type)
        if since is not None:
            where.append("started_at>=%s")
            params.append(f"{since}T00:00:00+00:00")
        clause = " AND ".join(where)

        with self._pool.connection() as conn:
            total = conn.execute(
                f"SELECT COUNT(*) AS n FROM interviews WHERE {clause}", params
            ).fetchone()["n"]
            # dict_row：**按列名取**，不用下标——下标是 tuple 的写法，改行工厂就会崩
            by_status = {
                row["status"]: row["n"]
                for row in conn.execute(
                    f"SELECT status, COUNT(*) AS n FROM interviews WHERE {clause}"
                    " GROUP BY status",
                    params,
                )
            }
            rows = conn.execute(
                f"SELECT * FROM interviews WHERE {clause}"
                " ORDER BY started_at DESC, id ASC LIMIT %s OFFSET %s",
                [*params, page_size, (page - 1) * page_size],
            ).fetchall()
        counts = {
            "finished": by_status.get("finished", 0),
            "running": by_status.get("running", 0),
        }
        return [dict(row) for row in rows], total, counts

    def list_reports(self, *, user_id: str, limit: int = 200) -> list[dict]:
        with self._pool.connection() as conn:
            rows = conn.execute(
                "SELECT i.id AS interview_id, r.payload, i.position, i.difficulty, i.started_at"
                " FROM reports r JOIN interviews i ON i.id = r.interview_id"
                " WHERE i.user_id=%s"
                " ORDER BY i.started_at ASC, i.id ASC LIMIT %s",
                (user_id, limit),
            ).fetchall()
        out = []
        for row in rows:
            item = dict(row)
            item["payload"] = json.loads(item["payload"])
            out.append(item)
        return out

    def count_finished_without_report(self, *, user_id: str) -> int:
        with self._pool.connection() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM interviews i"
                " WHERE i.user_id=%s AND i.status='finished'"
                " AND NOT EXISTS (SELECT 1 FROM reports r WHERE r.interview_id = i.id)",
                (user_id,),
            ).fetchone()
        return int(row["n"])

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
        with self._pool.connection() as conn:
            conn.execute(
                "INSERT INTO resumes (id, user_id, filename, text, parsed, created_at)"
                " VALUES (%s, %s, %s, %s, %s, %s)",
                (
                    resume_id,
                    user_id,
                    filename,
                    text,
                    json.dumps(parsed, ensure_ascii=False),
                    self.clock(),
                ),
            )
            conn.commit()

    def prune_unreferenced_resumes(self, *, user_id: str) -> int:
        with self._pool.connection() as conn:
            cur = conn.execute(
                "DELETE FROM resumes WHERE user_id=%s"
                " AND NOT EXISTS (SELECT 1 FROM interviews WHERE resume_id = resumes.id)",
                (user_id,),
            )
            count = cur.rowcount
            conn.commit()
        return count

    def get_resume(self, *, user_id: str, resume_id: str) -> dict | None:
        with self._pool.connection() as conn:
            row = conn.execute(
                "SELECT * FROM resumes WHERE id=%s AND user_id=%s", (resume_id, user_id)
            ).fetchone()
        if row is None:
            return None
        item = dict(row)
        item["parsed"] = json.loads(item["parsed"]) if item["parsed"] else {}
        return item

    def get_report(self, interview_id: str) -> dict | None:
        with self._pool.connection() as conn:
            row = conn.execute(
                "SELECT * FROM reports WHERE interview_id=%s", (interview_id,)
            ).fetchone()
        if row is None:
            return None
        return {"payload": json.loads(row["payload"]), "created_at": row["created_at"]}


# ---------- 池缓存（进程内）----------

_STORES: dict[str, PostgresStore] = {}


def get_store(
    dsn: str,
    *,
    min_size: int | None = None,
    max_size: int | None = None,
    timeout: float | None = None,
) -> PostgresStore:
    """按 DSN 取（并缓存）连接池。

    缓存的理由：门面每次调用都传 DSN，不缓存就会给每次查询建一个新池——
    连接数会瞬间打满 PG。进程内缓存 = 一个 worker 一个池。

    池参数缺省读配置（DB_POOL_MIN_SIZE / DB_POOL_MAX_SIZE / DB_CONNECT_TIMEOUT_S）。

    **DSN 先过 `normalize_dsn`**（localhost → 127.0.0.1，见那里的说明），
    且**缓存键用归一化后的 DSN** —— 否则 ``localhost`` 与 ``127.0.0.1`` 会各建一个池。
    """
    dsn = normalize_dsn(dsn)
    if min_size is None or max_size is None or timeout is None:
        from app.config import get_settings

        settings = get_settings()
        min_size = settings.db_pool_min_size if min_size is None else min_size
        max_size = settings.db_pool_max_size if max_size is None else max_size
        timeout = settings.db_connect_timeout_s if timeout is None else timeout
    store = _STORES.get(dsn)
    if store is None:
        store = PostgresStore.connect(
            dsn, min_size=min_size, max_size=max_size, timeout=timeout
        )
        _STORES[dsn] = store
    return store


def close_all() -> None:
    """关掉全部连接池（测试 / 进程退出用）。"""
    for store in list(_STORES.values()):
        store.close()
    _STORES.clear()
