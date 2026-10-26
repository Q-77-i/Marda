"""checkpointer 后端选择（P4-E3）：SQLite（默认）| PostgreSQL（多 worker 共享）。

为什么业务库和 checkpointer 要分开选：两者的生命周期与容量特征不同——
业务库是用户资产（小、可备份、要求事务）；checkpointer 是**过程状态**（大、按场次产生、
要有保留策略）。合成一个库会让「删一场的快照」和「删一个用户的报告」互相牵连。

`CHECKPOINT_BACKEND=postgres` 时用官方 `AsyncPostgresSaver`（独立库 marda_checkpoints），
多 worker 共享同一份状态——这是「任意 worker 都能 resume 同一场」的前提。

**旧 SQLite checkpoint 不迁移**（升级方案 §4.1.1）：手工搬 checkpoints/writes 两张表
会把 serde 版本与 schema 假设一起搬走，风险远大于收益。旧场次保留业务报告，
但不再保证能 resume、不再支持节点回放——这一条写进了对外文档。
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from app.config import Settings
from app.graph.serde import make_serde

logger = logging.getLogger(__name__)


@dataclass
class CheckpointerHandle:
    """checkpointer + 关闭钩子。

    `close` 由 service lifespan 统一调用：SQLite 关连接，PG 关连接池。
    """

    saver: object
    backend: str
    close: Callable[[], Awaitable[None]]


async def create_checkpointer(settings: Settings) -> CheckpointerHandle:
    """按配置建 checkpointer。

    PG 侧要点：
    - 独立库 + `AsyncConnectionPool`（多 worker 各自一个池，状态在 PG 里共享）；
    - 启动时 `setup()` 建表/迁移——**启动即失败**优于运行中第一次 resume 才炸；
    - 序列化器统一用带 zlib 压缩的那个（新旧 type 双读，老数据照样能读）。
    """
    if settings.checkpoint_backend == "postgres":
        return await _create_postgres(settings)
    return await _create_sqlite(settings)


async def _create_sqlite(settings: Settings) -> CheckpointerHandle:
    import aiosqlite
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

    conn = await aiosqlite.connect(str(settings.checkpoint_db_path))
    saver = AsyncSqliteSaver(conn, serde=make_serde())

    async def _close() -> None:
        await conn.close()

    logger.info("checkpointer: sqlite %s", settings.checkpoint_db_path)
    return CheckpointerHandle(saver=saver, backend="sqlite", close=_close)


async def _create_postgres(settings: Settings) -> CheckpointerHandle:
    if not settings.checkpoint_database_url:
        raise RuntimeError("CHECKPOINT_BACKEND=postgres 时必须配置 CHECKPOINT_DATABASE_URL")
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
    from psycopg_pool import AsyncConnectionPool

    pool = AsyncConnectionPool(
        settings.checkpoint_database_url,
        min_size=1,
        max_size=settings.checkpoint_pool_max_size,
        open=False,
    )
    await pool.open(wait=True)
    saver = AsyncPostgresSaver(pool, serde=make_serde())
    await saver.setup()

    async def _close() -> None:
        await pool.close()

    logger.info("checkpointer: postgres（多 worker 共享状态）")
    return CheckpointerHandle(saver=saver, backend="postgres", close=_close)
