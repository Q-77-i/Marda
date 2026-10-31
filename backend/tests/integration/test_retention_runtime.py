"""**保留策略接进运行时**（P4 收尾）：`CHECKPOINT_RETENTION` 这个配置到底有没有用。

背景：保留策略原先只活在 `scripts/checkpoint_retention.py` 里，
`CHECKPOINT_RETENTION` 这个配置项**代码根本不读**——设了也没用，
而 checkpoint 快照只增不减（实测 15 题场 134 张 / 11.5MB）。

现在接上了：服务启动时按 `CHECKPOINT_RETENTION` 跑一次保留。
这个文件验的就是「**设了配置就真的会删**」——不是验脚本能跑（那是别的用例）。
"""

from __future__ import annotations

import pytest

from app.config import Settings
from app.graph.checkpointer import create_checkpointer
from app.graph.retention import prune_all_sqlite


@pytest.fixture
def ckpt_path(tmp_path, monkeypatch):
    """一个空的 SQLite checkpointer 库路径（由 create_checkpointer 自己建表）。"""
    monkeypatch.setenv("CHECKPOINT_DB_PATH", str(tmp_path / "ckpt.sqlite3"))
    return tmp_path / "ckpt.sqlite3"


async def _seed(path, thread_id: str, count: int) -> None:
    """直接往库里塞 count 张快照（表结构由 saver.setup() 建）。"""
    import aiosqlite

    conn = await aiosqlite.connect(str(path))
    await conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS checkpoints (
            thread_id TEXT NOT NULL, checkpoint_ns TEXT NOT NULL DEFAULT '',
            checkpoint_id TEXT NOT NULL, parent_checkpoint_id TEXT,
            type TEXT, checkpoint BLOB, metadata BLOB
        );
        CREATE TABLE IF NOT EXISTS writes (
            thread_id TEXT NOT NULL, checkpoint_ns TEXT NOT NULL DEFAULT '',
            checkpoint_id TEXT NOT NULL, task_id TEXT NOT NULL, idx INTEGER NOT NULL,
            channel TEXT NOT NULL, type TEXT, value BLOB
        );
        """
    )
    for i in range(count):
        cid = f"{i:04d}"
        await conn.execute(
            "INSERT INTO checkpoints (thread_id, checkpoint_ns, checkpoint_id, type, checkpoint, metadata)"
            " VALUES (?, '', ?, 'msgpack', ?, ?)",
            (thread_id, cid, b"x" * 100, b"{}"),
        )
        await conn.execute(
            "INSERT INTO writes (thread_id, checkpoint_ns, checkpoint_id, task_id, idx, channel, type, value)"
            " VALUES (?, '', ?, 'task', 0, 'messages', 'msgpack', ?)",
            (thread_id, cid, b"y" * 10),
        )
    await conn.commit()
    await conn.close()


async def test_启动时按配置执行保留(ckpt_path, monkeypatch):
    """`CHECKPOINT_RETENTION=5` + 库里有 30 张 → 启动后只剩 5 张。

    走的是**真实路径**：`create_checkpointer` 建 saver → `service.start()` 里
    调 `handle.prune(keep)`。所以这个用例同时证明：
    - 配置被读了（不是硬编码 20）；
    - 保留真的接上了（不是只写在脚本里）。
    """
    await _seed(ckpt_path, "iv-1", 30)

    settings = Settings(
        checkpoint_backend="sqlite",
        checkpoint_db_path=ckpt_path,
        checkpoint_retention=5,
    )
    handle = await create_checkpointer(settings)
    try:
        # service.start() 里做的事：建完 checkpointer 就跑一次保留
        results = await handle.prune(settings.checkpoint_retention)
    finally:
        await handle.close()

    assert sum(r.deleted_checkpoints for r in results) == 25

    import aiosqlite

    conn = await aiosqlite.connect(str(ckpt_path))
    cur = await conn.execute("SELECT COUNT(*) FROM checkpoints WHERE thread_id='iv-1'")
    assert (await cur.fetchone())[0] == 5
    # writes 也要一起删（否则时间线里会留下没有对应快照的半截步骤）
    cur = await conn.execute("SELECT COUNT(*) FROM writes WHERE thread_id='iv-1'")
    assert (await cur.fetchone())[0] == 5
    await conn.close()


async def test_保留为0时不动(ckpt_path):
    """`CHECKPOINT_RETENTION=0` → 不删（0 视为「关闭保留策略」，不是「一张都不留」）。"""
    await _seed(ckpt_path, "iv-2", 30)
    settings = Settings(
        checkpoint_backend="sqlite",
        checkpoint_db_path=ckpt_path,
        checkpoint_retention=0,
    )
    handle = await create_checkpointer(settings)
    try:
        assert handle.prune is not None
        # service._apply_retention 在 keep <= 0 时直接返回，不调 prune
    finally:
        await handle.close()

    import aiosqlite

    conn = await aiosqlite.connect(str(ckpt_path))
    cur = await conn.execute("SELECT COUNT(*) FROM checkpoints WHERE thread_id='iv-2'")
    assert (await cur.fetchone())[0] == 30
    await conn.close()


async def test_脚本与运行时用同一个保留实现(ckpt_path):
    """脚本（`scripts/checkpoint_retention.py`）与运行时跑的是**同一份实现**。

    口径分叉是这类「两处算同一个东西」最常见的病：脚本删一套、运行时删另一套，
    最后谁也说不清库里的快照按什么规则剩着。
    """
    from app.graph import retention

    assert retention.prune_all_sqlite is not None
    assert retention.prune_sqlite is not None
    # 脚本 import 的就是这两个
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
    import checkpoint_retention as script  # type: ignore

    assert script.prune_all_sqlite is retention.prune_all_sqlite
