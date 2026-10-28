#!/usr/bin/env python
"""checkpoint 保留策略执行器（P4-E3）：每场只留最近 N 张 + 最终一张。

用法：

    # SQLite
    uv run python scripts/checkpoint_retention.py --sqlite ../data/checkpoints.sqlite3 --keep 20
    # PostgreSQL
    uv run python scripts/checkpoint_retention.py --postgres "$CHECKPOINT_DATABASE_URL" --keep 20

**删掉更早的快照 = 更早的节点时间线不可回放**（取舍见 docs/P4-生产化.md §4.3）。
`--keep` 调大是安全的；调小会丢历史。想看会删多少，先跑 `--dry-run`。
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "backend"))

# Windows 上 psycopg 的异步模式需要 Selector 事件循环（策略只对新建的 loop 生效，
# 所以必须在 asyncio.run 之前切）。
from app.graph.checkpointer import ensure_compatible_event_loop  # noqa: E402

ensure_compatible_event_loop()

from app.graph.retention import prune_all_postgres, prune_all_sqlite  # noqa: E402


async def _run_sqlite(path: Path, keep: int, dry_run: bool) -> None:
    import aiosqlite

    conn = await aiosqlite.connect(str(path))
    try:
        if dry_run:
            cur = await conn.execute(
                "SELECT thread_id, COUNT(*) FROM checkpoints GROUP BY thread_id"
            )
            rows = await cur.fetchall()
            total = sum(r[1] for r in rows)
            print(f"[dry-run] {len(rows)} 场 · 共 {total} 张快照（keep={keep}）")
            for thread_id, n in rows:
                if n > keep:
                    print(f"  {thread_id}：{n} 张 → 预计删 {n - keep} 张")
            return
        results = await prune_all_sqlite(conn, keep=keep)
    finally:
        await conn.close()
    for result in results:
        print(
            f"{result.thread_id}: 留 {result.kept} 张 · 删快照 {result.deleted_checkpoints}"
            f" · 删写入 {result.deleted_writes}"
        )


async def _run_postgres(dsn: str, keep: int, dry_run: bool) -> None:
    from psycopg_pool import AsyncConnectionPool

    async with AsyncConnectionPool(dsn, open=False) as pool:
        await pool.open(wait=True)
        if dry_run:
            async with pool.connection() as conn:
                cur = await conn.execute(
                    "SELECT thread_id, COUNT(*) FROM checkpoints GROUP BY thread_id"
                )
                rows = await cur.fetchall()
            total = sum(r[1] for r in rows)
            print(f"[dry-run] {len(rows)} 场 · 共 {total} 张快照（keep={keep}）")
            for thread_id, n in rows:
                if n > keep:
                    print(f"  {thread_id}：{n} 张 → 预计删 {n - keep} 张")
            return
        results = await prune_all_postgres(pool, keep=keep)
    for result in results:
        print(
            f"{result.thread_id}: 留 {result.kept} 张 · 删快照 {result.deleted_checkpoints}"
            f" · 删写入 {result.deleted_writes} · 删孤儿 blob {result.deleted_blobs}"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="checkpoint 保留（删更早的快照）")
    parser.add_argument("--sqlite", type=Path, help="SQLite checkpointer 文件路径")
    parser.add_argument("--postgres", help="PostgreSQL 连接串")
    parser.add_argument("--keep", type=int, default=20, help="每场保留最近 N 张（默认 20）")
    parser.add_argument("--dry-run", action="store_true", help="只看会删多少，不真删")
    args = parser.parse_args()

    if not args.sqlite and not args.postgres:
        parser.error("至少给一个 --sqlite 或 --postgres")

    if args.sqlite:
        _run(_run_sqlite(args.sqlite, args.keep, args.dry_run))
    else:
        _run(_run_postgres(args.postgres, args.keep, args.dry_run))
    return 0


def _run(coro):
    """跑协程：Windows 上用 Selector loop（psycopg 异步模式必需）。

    `asyncio.Runner(loop_factory=...)` 是 3.12+ 的正规做法；policy API 在 3.14 已弃用。
    """
    from app.graph.checkpointer import compatible_loop_factory

    with asyncio.Runner(loop_factory=compatible_loop_factory()) as runner:
        return runner.run(coro)


if __name__ == "__main__":
    raise SystemExit(main())
