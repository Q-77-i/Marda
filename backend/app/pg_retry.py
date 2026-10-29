"""PG 并发冲突的识别与重试——**幂等 DDL 专用**。

**为什么需要**：`--workers 2` 时两个 uvicorn worker 同时启动，会同时跑：
- 业务库 `ensure_schema`（一串 `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`）；
- checkpointer 的官方 `saver.setup()`（`CREATE TABLE` / `CREATE INDEX CONCURRENTLY`）。

两拨 DDL 撞在一起，PG 会判 `DeadlockDetected`（两个事务反向拿锁），
启动直接失败、worker 反复重启，服务永远停在 `health: starting`。

**关键认知**：`IF NOT EXISTS` 只保证**幂等**，不保证**并发安全**——这是两个属性，
之前只考虑了前者。既然 DDL 是幂等的，那么「撞了就退避重试」既安全又简单。

**只对并发冲突重试**：连不上、权限不足、SQL 写错这些重试也没用（只是拖长报错时间，
还会把真故障掩盖成「多等了一会儿」）。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")

# 这些都是「你来我也来」的典型，语义上都是「重试一次就好」
CONFLICT_NAMES = frozenset({
    "DeadlockDetected",      # 两个事务反向拿锁
    "UniqueViolation",       # 两个都先 SELECT 再 INSERT（系统目录 / 表名）
    "LockNotAvailable",      # lock_timeout / NOWAIT
    "SerializationFailure",  # SERIALIZABLE 隔离级别下的正常竞争
})


def is_concurrency_conflict(exc: BaseException) -> bool:
    """这个异常是不是「两个进程同时改 schema」造成的小冲突？

    按**类名**判断：psycopg 的异常类由 libpq 的 SQLSTATE 动态生成，
    直接 import `psycopg.errors.DeadlockDetected` 会把 psycopg 变成硬依赖
    （SQLite 形态下不该需要它）。类名在本项目的用法里足够稳定。
    """
    return type(exc).__name__ in CONFLICT_NAMES


async def retry_on_conflict(
    fn: Callable[[], Awaitable[T]],
    *,
    attempts: int = 5,
    base_delay: float = 0.2,
    label: str = "DDL",
) -> T:
    """跑 `fn()`，遇到并发冲突就指数退避重试；其它异常原样抛出。

    `base_delay` 从 0.2s 起翻倍（0.2 / 0.4 / 0.8 / 1.6），5 次总计约 3s——
    足够错开两个 worker 的启动窗口，又不会把「真挂了」拖成长时间无响应。
    """
    delay = base_delay
    for attempt in range(attempts):
        try:
            return await fn()
        except Exception as exc:  # noqa: BLE001 —— 要先分辨「能不能重试」
            if attempt == attempts - 1 or not is_concurrency_conflict(exc):
                raise
            logger.warning(
                "%s 撞上并发冲突（第 %d 次，%.1fs 后重试）：%s",
                label, attempt + 1, delay, exc,
            )
            await asyncio.sleep(delay)
            delay *= 2
    raise AssertionError("unreachable")  # pragma: no cover
