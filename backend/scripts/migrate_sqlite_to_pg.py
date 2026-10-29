#!/usr/bin/env python
"""业务库迁移：SQLite → PostgreSQL（P4-E2）。

范围：**5 张业务表** users / resumes / interviews / answers / reports。
**不迁** questions（题库是离线资产，留在 SQLite）与 checkpoints（状态另库，见保留策略说明）。

用法：

    uv run python scripts/migrate_sqlite_to_pg.py \
        --sqlite ../data/marda.sqlite3 \
        --postgres "$DATABASE_URL" \
        [--force]

规矩：
- 目标库必须为空（否则拒绝跑），`--force` 先清空业务表再迁；
- **保留原 id 与时间戳**（迁移后前端/历史链接照旧能用）；
- 迁完逐表打印 count + 抽样校验，数量对不上就整体回滚（目标库清空）；
- SQLite 原文件**不动**——它就是回滚备份。
"""

from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "backend"))

logger = logging.getLogger("marda.migrate")

# 依赖顺序：先被引用的后被引用的
TABLES = ("users", "resumes", "interviews", "answers", "reports")

COLUMNS = {
    "users": ("id", "username", "password_hash", "created_at"),
    "resumes": ("id", "user_id", "filename", "text", "parsed", "created_at"),
    "interviews": (
        "id",
        "thread_id",
        "user_id",
        "position",
        "interview_type",
        "question_count",
        "phase",
        "difficulty",
        "status",
        "started_at",
        "ended_at",
        "resume_id",
    ),
    "answers": (
        "id",
        "interview_id",
        "question_id",
        "domain",
        "difficulty",
        "candidate_answer",
        "followup_count",
        "skipped",
        "score_json",
        "created_at",
    ),
    "reports": ("id", "interview_id", "payload", "created_at"),
}

# answers.id 是自增主键：PG 侧是 BIGSERIAL，迁移时**显式带原 id**（保持一致）
EXPLICIT_ID = {"answers"}


def read_source(path: Path, table: str) -> list[tuple]:
    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
        if not exists:
            return []
        cols = [row[1] for row in conn.execute(f"PRAGMA table_info({table})")]
        wanted = [c for c in COLUMNS[table] if c in cols]
        rows = conn.execute(f"SELECT {', '.join(wanted)} FROM {table}").fetchall()
        return [tuple(row) for row in rows]


def _reset_sequences(conn) -> None:
    """把 EXPLICIT_ID 各表的自增序列拨到「表内最大 id 之后」。

    背景：迁移是**显式插入原 id**（保持 id 一致），而 PG 的 `BIGSERIAL`
    **序列不会因此前进**——序列只在用默认值插入时才自增。不归位的话，
    新场次 `finalize_interview` 第一次插 answers 会拿到已被占用的 id → 主键冲突。

    **空表要特判**：`setval(seq, 0, true)` 会越界（序列下界是 1，
    PG 报 `setval: value 0 is out of bounds for sequence`）。
    空表用 `setval(seq, 1, false)`——`is_called=false` 表示「下一个 nextval 返回 1」。

    **注意 setval 不是事务性的**：回滚不会撤销它。所以调用方必须把它放在
    「失败能清理」的位置（见 migrate 的 except 分支），不能指望事务兜底。
    """
    for table in EXPLICIT_ID:
        seq = conn.execute(
            "SELECT pg_get_serial_sequence(%s, 'id')", (table,)
        ).fetchone()[0]
        if seq is None:
            continue
        max_id = conn.execute(
            f"SELECT COALESCE(MAX(id), 0) FROM {table}"
        ).fetchone()[0]
        if max_id > 0:
            conn.execute("SELECT setval(%s, %s, true)", (seq, max_id))
            logger.info("序列归位：%s → %s 之后（表内最大 id %s）", table, seq, max_id)
        else:
            conn.execute("SELECT setval(%s, 1, false)", (seq,))
            logger.info("序列归位：%s → 起点（表为空，下一个 id 为 1）", table, seq)


def _cleanup(conn) -> None:
    """迁移失败后的清理：清空业务表 + 序列回起点。

    **序列也要清**：`setval` 非事务性，事务回滚不会撤销它；只清表不清序列的话，
    库里会留下「表空、序列停在老位置」的错位状态。
    """
    for table in reversed(TABLES):
        conn.execute(f"DELETE FROM {table}")
    for table in EXPLICIT_ID:
        seq = conn.execute(
            "SELECT pg_get_serial_sequence(%s, 'id')", (table,)
        ).fetchone()[0]
        if seq is not None:
            conn.execute("SELECT setval(%s, 1, false)", (seq,))
    conn.commit()


def migrate(sqlite_path: Path, dsn: str, *, force: bool = False) -> dict[str, int]:
    """执行迁移；返回 {表: 行数}。失败抛异常（目标库已清空，SQLite 原文件未动）。"""
    from psycopg_pool import ConnectionPool

    from app.dsn import normalize_dsn

    # normalize_dsn：Windows 上 localhost → ::1，而**连接池不回退 IPv4/IPv6**——
    # 只在 ::1 上试一次就报 PoolTimeout，看着像「PG 连不上」（见 app/dsn.py）。
    pool = ConnectionPool(
        normalize_dsn(dsn), min_size=1, max_size=2, open=True
    )
    counts: dict[str, int] = {}
    try:
        with pool.connection() as conn:
            from app.db_postgres import MIGRATIONS, SCHEMA

            conn.execute(SCHEMA)
            for stmt in MIGRATIONS:
                conn.execute(stmt)
            conn.commit()

            # 目标库必须为空（answers 用 id 判空）
            for table in TABLES:
                cur = conn.execute(f"SELECT COUNT(*) FROM {table}")
                n = cur.fetchone()[0]
                if n and not force:
                    raise SystemExit(
                        f"目标库表 {table} 已有 {n} 行；确认要覆盖请加 --force（会先清空业务表）"
                    )
            if force:
                # 清空 + 序列回起点（DELETE 不动序列，清空后序列会停在旧位置）
                _cleanup(conn)

            try:
                with conn.transaction():
                    for table in TABLES:
                        rows = read_source(sqlite_path, table)
                        if not rows:
                            counts[table] = 0
                            continue
                        cols = COLUMNS[table]
                        placeholders = ", ".join("%s" for _ in cols)
                        columns = ", ".join(cols)
                        sql = f"INSERT INTO {table} ({columns}) VALUES ({placeholders})"
                        with conn.cursor() as cur:
                            cur.executemany(sql, rows)
                        counts[table] = len(rows)
                # 序列归位：**必须在同一个 try 里**——setval 非事务性，
                # 它自己失败时数据已经提交了（事务已结束），不清理就留下
                # 「已迁移但序列错位」的半成品库。
                _reset_sequences(conn)
                conn.commit()
            except Exception:
                conn.rollback()
                _cleanup(conn)
                raise

        # 校验：逐表 count 必须与源一致
        with pool.connection() as conn:
            for table in TABLES:
                cur = conn.execute(f"SELECT COUNT(*) FROM {table}")
                target = cur.fetchone()[0]
                source = len(read_source(sqlite_path, table))
                if target != source:
                    # 原文案说「已回滚清空」但那时数据其实已经提交了——文案撒谎。
                    # 现在真的清（表 + 序列），让这句话成立。
                    _cleanup(conn)
                    raise SystemExit(
                        f"校验失败：{table} 源 {source} 行 / 目标 {target} 行（已清空目标库）"
                    )
    finally:
        pool.close()
    return counts


def sample(dsn: str, table: str, limit: int = 3) -> list[dict]:
    """抽样打印（人眼核对：用户名、时间戳、报告 payload 是否合理）。"""
    from psycopg_pool import ConnectionPool

    from app.dsn import normalize_dsn

    pool = ConnectionPool(normalize_dsn(dsn), min_size=1, max_size=1, open=True)
    try:
        with pool.connection() as conn:
            cur = conn.execute(f"SELECT * FROM {table} LIMIT %s", (limit,))
            return [dict(row) for row in cur.fetchall()]
    finally:
        pool.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="业务库 SQLite → PostgreSQL 迁移")
    parser.add_argument("--sqlite", type=Path, required=True, help="源 SQLite 文件")
    parser.add_argument("--postgres", required=True, help="目标 PostgreSQL 连接串")
    parser.add_argument("--force", action="store_true", help="清空目标业务表后再迁")
    args = parser.parse_args()

    if not args.sqlite.exists():
        parser.error(f"源库不存在：{args.sqlite}")

    counts = migrate(args.sqlite, args.postgres, force=args.force)
    print("迁移完成：")
    for table in TABLES:
        print(f"  {table}: {counts.get(table, 0)} 行")
    for table in ("users", "interviews"):
        print(f"\n{table} 抽样：")
        for row in sample(args.postgres, table):
            text = json.dumps(row, ensure_ascii=False, default=str)
            print("  " + (text[:160] + "…" if len(text) > 160 else text))
    print("\n提示：SQLite 原文件保留未动——回滚就是把 DB_BACKEND 改回 sqlite。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
