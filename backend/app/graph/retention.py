"""checkpoint 保留策略（P4-E3）：每场只留最近 N 张 + 最终一张。

为什么：checkpointer 每个 super-step 一张全量快照，而 `trace_log` /
`answered_questions` / `chat_history` 都只增不减——累计体积近似 O(n²)
（实测 15 题场 134 张 / 11.5MB）。压缩把单张压小（~3×），保留把**张数**压住，
两条腿一起走，库才是有界的。

**取舍（写进文档，别当没这回事）**：删掉更早的快照 = 更早的节点时间线不可回放。
默认保留最近 20 张（够看清最近几轮），最终那张必留（resume 的落点）。
需要完整回放的场景请把 `CHECKPOINT_RETENTION` 调大——**调大是安全的，调小会丢历史**。

保留的实现直接写 SQL：官方 saver 的 `prune` 只提供 "keep_latest / delete" 两种粗粒度策略
（基类里还是 NotImplementedError），没有「留最近 N 张」这一档。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RetentionResult:
    """一次保留操作的统计（脚本输出 & 指标都读它）。"""

    thread_id: str
    kept: int
    deleted_checkpoints: int
    deleted_writes: int
    deleted_blobs: int = 0


async def _keep_ids_sqlite(conn, thread_id: str, keep: int) -> list[str]:
    """取要保留的 checkpoint_id（最新的 N 张；最终一张必在其中）。"""
    cur = await conn.execute(
        "SELECT checkpoint_id FROM checkpoints WHERE thread_id = ?"
        " ORDER BY checkpoint_id DESC LIMIT ?",
        (thread_id, keep),
    )
    return [row[0] for row in await cur.fetchall()]


async def prune_sqlite(conn, thread_id: str, *, keep: int = 20) -> RetentionResult:
    """SQLite checkpointer 的保留（表结构见 langgraph-checkpoint-sqlite）。

    `writes` 表按 checkpoint_id 关联——删快照必须连它的写入一起删，
    否则时间线里会留下「没有对应快照的写入」（回放时读出半截步骤）。
    """
    keep_ids = await _keep_ids_sqlite(conn, thread_id, keep)
    if not keep_ids:
        return RetentionResult(thread_id=thread_id, kept=0, deleted_checkpoints=0, deleted_writes=0)
    placeholders = ",".join("?" for _ in keep_ids)
    params = [thread_id, *keep_ids]
    cur = await conn.execute(
        f"DELETE FROM checkpoints WHERE thread_id = ? AND checkpoint_id NOT IN ({placeholders})",
        params,
    )
    deleted_checkpoints = cur.rowcount or 0
    cur = await conn.execute(
        f"DELETE FROM writes WHERE thread_id = ? AND checkpoint_id NOT IN ({placeholders})",
        params,
    )
    deleted_writes = cur.rowcount or 0
    await conn.commit()
    return RetentionResult(
        thread_id=thread_id,
        kept=len(keep_ids),
        deleted_checkpoints=deleted_checkpoints,
        deleted_writes=deleted_writes,
    )


async def prune_postgres(pool, thread_id: str, *, keep: int = 20) -> RetentionResult:
    """PostgreSQL checkpointer 的保留（表结构见 langgraph-checkpoint-postgres）。

    PG 侧多一张 `checkpoint_blobs`（通道值的实际内容按 version 存放）。
    删掉快照后，一部分 blob 会变成**没人引用的孤儿**——它们占着磁盘，
    所以这里连同孤儿一起清（只删「没有任何现存快照引用」的 blob，删多了会读不出状态）。
    """
    async with pool.connection() as conn:
        cur = await conn.execute(
            "SELECT checkpoint_id FROM checkpoints WHERE thread_id = %s"
            " ORDER BY checkpoint_id DESC LIMIT %s",
            (thread_id, keep),
        )
        keep_ids = [row[0] for row in await cur.fetchall()]
        if not keep_ids:
            return RetentionResult(
                thread_id=thread_id, kept=0, deleted_checkpoints=0, deleted_writes=0
            )
        placeholders = ",".join("%s" for _ in keep_ids)
        params = [thread_id, *keep_ids]
        cur = await conn.execute(
            f"DELETE FROM checkpoints WHERE thread_id = %s AND checkpoint_id NOT IN ({placeholders})",
            params,
        )
        deleted_checkpoints = cur.rowcount or 0
        cur = await conn.execute(
            f"DELETE FROM checkpoint_writes WHERE thread_id = %s"
            f" AND checkpoint_id NOT IN ({placeholders})",
            params,
        )
        deleted_writes = cur.rowcount or 0
        cur = await conn.execute(
            "DELETE FROM checkpoint_blobs bl WHERE bl.thread_id = %s"
            " AND NOT EXISTS ("
            "   SELECT 1 FROM checkpoints c,"
            "   LATERAL jsonb_each_text(c.checkpoint -> 'channel_versions') AS cv"
            "   WHERE c.thread_id = bl.thread_id AND c.checkpoint_ns = bl.checkpoint_ns"
            "     AND cv.key = bl.channel AND cv.value = bl.version"
            " )",
            (thread_id,),
        )
        deleted_blobs = cur.rowcount or 0
        await conn.commit()
    return RetentionResult(
        thread_id=thread_id,
        kept=len(keep_ids),
        deleted_checkpoints=deleted_checkpoints,
        deleted_writes=deleted_writes,
        deleted_blobs=deleted_blobs,
    )


async def prune_all_sqlite(conn, *, keep: int = 20) -> list[RetentionResult]:
    """对 SQLite 里的所有 thread 各做一次保留（脚本入口用）。"""
    cur = await conn.execute("SELECT DISTINCT thread_id FROM checkpoints")
    threads = [row[0] for row in await cur.fetchall()]
    return [await prune_sqlite(conn, thread_id, keep=keep) for thread_id in threads]


async def prune_all_postgres(pool, *, keep: int = 20) -> list[RetentionResult]:
    """对 PG 里的所有 thread 各做一次保留（脚本入口用）。"""
    async with pool.connection() as conn:
        cur = await conn.execute("SELECT DISTINCT thread_id FROM checkpoints")
        threads = [row[0] for row in await cur.fetchall()]
    results = []
    for thread_id in threads:
        results.append(await prune_postgres(pool, thread_id, keep=keep))
    return results
