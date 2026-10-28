#!/usr/bin/env python
"""checkpoint 体积统计（P4-E1）：现在到底多大？压缩了多少？哪一场最大？

回答的是升级方案 §2.1 里那个「只能靠 du 发现」的问题——没有指标，膨胀是事后才知道的。

用法（**必须在能访问库的环境里跑**：容器内，或宿主机上库文件可读时）：

    # SQLite checkpointer（开发/单机形态）
    uv run python scripts/checkpoint_stats.py --sqlite ../data/checkpoints.sqlite3

    # PostgreSQL checkpointer（生产形态）
    uv run python scripts/checkpoint_stats.py --postgres "$CHECKPOINT_DATABASE_URL"

输出：每个 thread 的快照张数、payload 字节、压缩前后对比、最新快照时间；
末尾给总计与 Top N。**只读**，不改数据。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "backend"))

# Windows 上 psycopg 的异步模式需要 Selector 事件循环（策略只对新建的 loop 生效，
# 所以必须在 asyncio.run 之前切）。
from app.graph.checkpointer import ensure_compatible_event_loop  # noqa: E402

ensure_compatible_event_loop()


@dataclass
class ThreadStat:
    """一个 thread（一场面试）的 checkpoint 统计。"""

    thread_id: str
    checkpoints: int
    writes: int
    payload_bytes: int
    raw_bytes: int  # 压缩前的估算（解压后长度）
    latest_at: str = ""

    @property
    def ratio(self) -> float:
        """压缩比（原始 / 压缩后）。1.0 = 没压。"""
        return round(self.raw_bytes / self.payload_bytes, 2) if self.payload_bytes else 0.0


def _decompress(payload: bytes, type_: str | None = None) -> int:
    """返回解压后的长度；不是 zlib 数据就按原样算。

    `type_` 给的是库里存的类型串（`msgpack+zlib` → 压缩前）。没给就试解压。
    """
    import zlib

    if type_ is not None and not type_.endswith("+zlib"):
        return len(payload)
    try:
        return len(zlib.decompress(payload))
    except zlib.error:
        return len(payload)


def stats_sqlite(path: Path) -> list[ThreadStat]:
    """SQLite checkpointer 的统计（表结构见 langgraph-checkpoint-sqlite）。"""
    out: list[ThreadStat] = []
    with sqlite3.connect(path) as conn:
        rows = conn.execute(
            "SELECT thread_id, COUNT(*) AS n, SUM(LENGTH(checkpoint)) AS payload,"
            " SUM(LENGTH(metadata)) AS meta FROM checkpoints GROUP BY thread_id"
        ).fetchall()
        writes = dict(
            conn.execute(
                "SELECT thread_id, COUNT(*) FROM writes GROUP BY thread_id"
            ).fetchall()
        )
        latest = dict(
            conn.execute(
                "SELECT thread_id, MAX(checkpoint_id) FROM checkpoints GROUP BY thread_id"
            ).fetchall()
        )
        # 压缩比按样本算（全量解压对大库太慢）：取每场最大的一张快照估
        for thread_id, n, payload, meta in rows:
            sample = conn.execute(
                "SELECT checkpoint FROM checkpoints WHERE thread_id=?"
                " ORDER BY LENGTH(checkpoint) DESC LIMIT 1",
                (thread_id,),
            ).fetchone()
            raw = 0
            if sample and sample[0]:
                raw = _decompress(sample[0]) * (n or 1)
            out.append(
                ThreadStat(
                    thread_id=thread_id,
                    checkpoints=n,
                    writes=writes.get(thread_id, 0),
                    payload_bytes=int(payload or 0) + int(meta or 0),
                    raw_bytes=int(raw),
                    latest_at=latest.get(thread_id, ""),
                )
            )
    return out


async def stats_postgres(dsn: str) -> list[ThreadStat]:
    """PostgreSQL checkpointer 的统计（表结构见 langgraph-checkpoint-postgres）。

    压缩比靠**解压 blob** 算：type 带 `+zlib` 的 blob 解压后才是原始大小。
    （PG 侧的通道值存在 checkpoint_blobs 里，type 单独一列——这与 SQLite 版的
    `LENGTH(checkpoint)` 口径不同，不能照抄。）
    """
    from psycopg_pool import AsyncConnectionPool

    out: list[ThreadStat] = []
    async with AsyncConnectionPool(dsn, kwargs={"autocommit": True}, open=False) as pool:
        await pool.open(wait=True)
        async with pool.connection() as conn:
            cur = await conn.execute(
                "SELECT thread_id, COUNT(*) AS n,"
                " COALESCE(SUM(LENGTH(checkpoint::text)), 0) AS json_bytes"
                " FROM checkpoints GROUP BY thread_id"
            )
            rows = await cur.fetchall()
            cur = await conn.execute(
                "SELECT thread_id, COUNT(*) FROM checkpoint_writes GROUP BY thread_id"
            )
            writes = {row[0]: row[1] for row in await cur.fetchall()}
            # blob：压缩后字节数 + 解压后的原始字节数
            cur = await conn.execute(
                "SELECT thread_id, type, blob FROM checkpoint_blobs"
            )
            blob_bytes: dict[str, int] = {}
            blob_raw: dict[str, int] = {}
            for thread_id, type_, blob in await cur.fetchall():
                data = bytes(blob)
                blob_bytes[thread_id] = blob_bytes.get(thread_id, 0) + len(data)
                blob_raw[thread_id] = blob_raw.get(thread_id, 0) + _decompress(data, type_)
            for thread_id, n, json_bytes in rows:
                out.append(
                    ThreadStat(
                        thread_id=thread_id,
                        checkpoints=n,
                        writes=writes.get(thread_id, 0),
                        payload_bytes=int(json_bytes) + blob_bytes.get(thread_id, 0),
                        raw_bytes=int(json_bytes) + blob_raw.get(thread_id, 0),
                        latest_at="",
                    )
                )
    return out


def render(stats: list[ThreadStat], top: int = 10) -> str:
    """人读的汇总（对齐 fmt）。"""
    if not stats:
        return "（没有 checkpoint）"
    stats = sorted(stats, key=lambda s: s.payload_bytes, reverse=True)
    total_bytes = sum(s.payload_bytes for s in stats)
    total_raw = sum(s.raw_bytes for s in stats)
    lines = [
        f"{'thread':<34} {'张数':>6} {'writes':>7} {'压缩后':>12} {'压缩前(估)':>12} {'压缩比':>7}",
        "-" * 96,
    ]
    for stat in stats[:top]:
        lines.append(
            f"{stat.thread_id[:34]:<34} {stat.checkpoints:>6} {stat.writes:>7}"
            f" {stat.payload_bytes:>12,} {stat.raw_bytes:>12,} {stat.ratio:>7}"
        )
    lines.append("-" * 96)
    lines.append(
        f"合计：{len(stats)} 场 · {sum(s.checkpoints for s in stats):,} 张快照 · "
        f"压缩后 {total_bytes:,} 字节（{total_bytes / 1024 / 1024:.2f} MB）· "
        f"压缩前约 {total_raw:,} 字节（{total_raw / 1024 / 1024:.2f} MB）· "
        f"整体压缩比 {round(total_raw / total_bytes, 2) if total_bytes else 0}"
    )
    if len(stats) > top:
        lines.append(f"（只列前 {top} 场，共 {len(stats)} 场）")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="checkpoint 体积统计（只读）")
    parser.add_argument("--sqlite", type=Path, help="SQLite checkpointer 文件路径")
    parser.add_argument("--postgres", help="PostgreSQL 连接串（CHECKPOINT_DATABASE_URL）")
    parser.add_argument("--top", type=int, default=10, help="列前 N 场（默认 10）")
    parser.add_argument("--json", action="store_true", help="输出 JSON（便于接监控）")
    args = parser.parse_args()

    if not args.sqlite and not args.postgres:
        parser.error("至少给一个 --sqlite 或 --postgres")

    if args.sqlite:
        stats = stats_sqlite(args.sqlite)
    else:
        stats = _run(stats_postgres(args.postgres))

    if args.json:
        print(
            json.dumps(
                [
                    {
                        "thread_id": s.thread_id,
                        "checkpoints": s.checkpoints,
                        "writes": s.writes,
                        "payload_bytes": s.payload_bytes,
                        "raw_bytes": s.raw_bytes,
                        "ratio": s.ratio,
                    }
                    for s in stats
                ],
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        print(render(stats, top=args.top))
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
