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
from app.dsn import normalize_dsn
from app.graph.serde import make_serde

logger = logging.getLogger(__name__)


def compatible_loop_factory():
    """返回适合 psycopg 异步模式的 loop 工厂（Windows 上是 Selector，其它平台 None）。

    为什么需要：Windows 默认的 `ProactorEventLoop` 不支持 `add_reader` 这类接口，
    psycopg 的异步连接直接报
    “Psycopg cannot use the 'ProactorEventLoop' to run in async mode”。
    同步 psycopg 不受影响 → **业务库（同步）在 Windows 上照常跑**，
    但 **PG checkpointer（官方 saver 是异步的）在 Windows 上必须换 Selector**。

    Linux 生产环境的默认 loop 就是 Selector → 这条只影响 Windows 本机开发/测试。

    **用法**：`asyncio.Runner(loop_factory=compatible_loop_factory())`
    （3.12+ 的正规做法；`loop_factory=None` 就是平台默认）。
    为什么不用 `set_event_loop_policy`：3.14 起 policy API 已弃用，
    且 Windows 的默认 policy 是**私有类** `_WindowsProactorEventLoopPolicy`，
    `isinstance(policy, asyncio.WindowsProactorEventLoopPolicy)` 会**静默失效**——
    换了个寂寞，还以为已经换过了。
    """
    import asyncio
    import sys

    if sys.platform != "win32":
        return None
    return asyncio.SelectorEventLoop


def uvicorn_loop_factory():
    """**给 uvicorn 用的** loop factory（零参数，直接返回 loop 实例）。

    为什么需要单独一个：`ensure_compatible_event_loop()` 切的是 **policy**，
    而 **uvicorn 绕开 policy**——它的 `asyncio_loop_factory` 在 Windows 上
    直接 `return asyncio.ProactorEventLoop`（实测 uvicorn 0.36+ 源码），
    跟当前 policy 无关。所以**切 policy 对 uvicorn 完全无效**，
    Windows 上 uvicorn + PG checkpointer 照样报 ProactorEventLoop 错。

    uvicorn 的 `Config(loop=...)` 支持 `"<module>:<attr>"` 形式的自定义 factory
    （见 `uvicorn.importer.import_from_string`），但它要求 factory 是**零参数**的
    → 启动命令加 `--loop app.graph.checkpointer:uvicorn_loop_factory`。

    **注意 `--workers 2`**：多 worker 模式下 uvicorn 用**子进程**起 worker，
    每个子进程会重新 import 本模块 → factory 在每个进程里各自生效。
    """
    import asyncio
    import sys

    if sys.platform != "win32":
        # 非 Windows：默认就是 Selector，交给 uvicorn 自己的默认即可
        return None
    return asyncio.SelectorEventLoop()


def ensure_compatible_event_loop() -> None:
    """旧入口（保留兼容）：把全局事件循环策略切成 Selector。

    新代码请用 `compatible_loop_factory()` + `asyncio.Runner(loop_factory=...)`——
    policy API 在 3.14 已弃用。这条留着是因为 pytest-asyncio 早期版本
    认策略不认 factory；本仓库的 pytest-asyncio 已经是 1.x（认 factory），
    所以它只是**兜底**。
    """
    import asyncio
    import sys

    if sys.platform != "win32":
        return
    policy = asyncio.get_event_loop_policy()
    # 按**类名**判断：3.14 的默认 policy 是私有类 `_WindowsProactorEventLoopPolicy`，
    # 用 isinstance(公开类) 判断会失效（换了个寂寞）。
    if "Proactor" in type(policy).__name__:
        with __import__("warnings").catch_warnings():
            __import__("warnings").simplefilter("ignore", DeprecationWarning)
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


@dataclass
class CheckpointerHandle:
    """checkpointer + 关闭钩子。

    `close` 由 service lifespan 统一调用：SQLite 关连接，PG 关连接池。

    `prune` 是**保留策略的入口**（P4-E3）：按 `CHECKPOINT_RETENTION` 删掉更早的快照。
    为什么挂在句柄上而不是让 service 自己去连库：两种后端的连接对象不一样
    （SQLite 是 aiosqlite 连接、PG 是 AsyncConnectionPool），
    句柄是唯一同时握着两者的地方。
    """

    saver: object
    backend: str
    close: Callable[[], Awaitable[None]]
    prune: Callable[[int], Awaitable[list]] | None = None


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
    # **setup() 建表**：官方 saver 是懒建表的（第一次 resume 才建），
    # 但启动时要跑保留策略（读 checkpoints 表）——表不存在会直接报错。
    # 所以这里显式 setup：建表是幂等的，且「启动即建表」比「第一次面试才建」更可预期。
    await saver.setup()

    async def _close() -> None:
        await conn.close()

    async def _prune(keep: int):
        from app.graph.retention import prune_all_sqlite

        return await prune_all_sqlite(conn, keep=keep)

    logger.info("checkpointer: sqlite %s", settings.checkpoint_db_path)
    return CheckpointerHandle(
        saver=saver, backend="sqlite", close=_close, prune=_prune
    )


async def _create_postgres(settings: Settings) -> CheckpointerHandle:
    if not settings.checkpoint_database_url:
        raise RuntimeError("CHECKPOINT_BACKEND=postgres 时必须配置 CHECKPOINT_DATABASE_URL")
    ensure_compatible_event_loop()
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
    from psycopg_pool import AsyncConnectionPool

    pool = AsyncConnectionPool(
        # **localhost → 127.0.0.1**：Windows 上 localhost 优先解析成 IPv6 `::1`，
        # 而 PG 发布的端口通常只听 IPv4。裸 psycopg 会回退，**连接池不会**——
        # 只在 ::1 上试一次就报 PoolTimeout，看着像「PG 连不上」（实测：同一 DSN，
        # 裸连 OK、池必挂；换 127.0.0.1 立刻正常）。详见 app/dsn.py。
        normalize_dsn(settings.checkpoint_database_url),
        min_size=1,
        max_size=settings.checkpoint_pool_max_size,
        # **autocommit=True**：官方 saver 是按 psycopg2 的语义写的（psycopg2 默认 autocommit），
        # psycopg3 改了默认（非 autocommit）。不开就会撞上官方迁移里的
        # `CREATE INDEX CONCURRENTLY` ——它不能在事务块里跑，于是 setup() 直接失败：
        # ActiveSqlTransaction: CREATE INDEX CONCURRENTLY cannot run inside a transaction block。
        # 开了之后每条语句自动提交，与官方 saver 的假设一致。
        kwargs={"autocommit": True},
        open=False,
    )
    await pool.open(wait=True)
    saver = AsyncPostgresSaver(pool, serde=make_serde())
    await saver.setup()

    async def _close() -> None:
        await pool.close()

    async def _prune(keep: int):
        from app.graph.retention import prune_all_postgres

        return await prune_all_postgres(pool, keep=keep)

    logger.info("checkpointer: postgres（多 worker 共享状态）")
    return CheckpointerHandle(
        saver=saver, backend="postgres", close=_close, prune=_prune
    )
