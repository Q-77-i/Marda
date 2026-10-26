"""checkpoint 保留策略单测（P4-E3）：留最近 N 张 + 最终一张，其余删掉。

用**真实的 SQLite checkpointer 库**（表结构与 langgraph-checkpoint-sqlite 一致），
因为保留逻辑就是一段 SQL——用假表测等于没测。

断言围绕三件事：
1. 留 N 张、删其余；
2. **连 writes 一起删**（否则时间线里会留下没有对应快照的半截步骤）；
3. 保留后**最新那张还在**（resume 的落点不能被删掉）。
"""

from __future__ import annotations

import pytest

from app.graph.retention import prune_sqlite

# langgraph-checkpoint-sqlite 的真实表结构（保持一致：SQL 写错不会因为表结构变了而蒙对）
SCHEMA = """
CREATE TABLE IF NOT EXISTS checkpoints (
    thread_id TEXT NOT NULL,
    checkpoint_ns TEXT NOT NULL DEFAULT '',
    checkpoint_id TEXT NOT NULL,
    parent_checkpoint_id TEXT,
    type TEXT,
    checkpoint BLOB,
    metadata BLOB,
    PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id)
);
CREATE TABLE IF NOT EXISTS writes (
    thread_id TEXT NOT NULL,
    checkpoint_ns TEXT NOT NULL DEFAULT '',
    checkpoint_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    idx INTEGER NOT NULL,
    channel TEXT NOT NULL,
    type TEXT,
    value BLOB,
    PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id, task_id, idx)
);
"""


@pytest.fixture
async def ckpt_db(tmp_path):
    import aiosqlite

    conn = await aiosqlite.connect(str(tmp_path / "ckpt.sqlite3"))
    await conn.executescript(SCHEMA)
    await conn.commit()
    yield conn
    await conn.close()


async def _seed(conn, thread_id: str, count: int) -> list[str]:
    """造 count 张快照 + 每张一条写入（checkpoint_id 按字典序 = 时间序）。"""
    ids = []
    for i in range(count):
        cid = f"{i:04d}"
        ids.append(cid)
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
    return ids


async def test_只留最近N张(ckpt_db):
    await _seed(ckpt_db, "iv-1", 30)
    result = await prune_sqlite(ckpt_db, "iv-1", keep=20)
    assert result.kept == 20
    assert result.deleted_checkpoints == 10
    cur = await ckpt_db.execute("SELECT COUNT(*) FROM checkpoints WHERE thread_id='iv-1'")
    assert (await cur.fetchone())[0] == 20


async def test_最新的那张必须留着(ckpt_db):
    """最终一张是 resume 的落点——删了这场就废了。"""
    await _seed(ckpt_db, "iv-1", 30)
    await prune_sqlite(ckpt_db, "iv-1", keep=5)
    cur = await ckpt_db.execute(
        "SELECT checkpoint_id FROM checkpoints WHERE thread_id='iv-1' ORDER BY checkpoint_id DESC LIMIT 1"
    )
    assert (await cur.fetchone())[0] == "0029"


async def test_写入也要一起删(ckpt_db):
    """留下没有对应快照的写入 = 时间线里出现半截步骤。"""
    await _seed(ckpt_db, "iv-1", 30)
    result = await prune_sqlite(ckpt_db, "iv-1", keep=20)
    assert result.deleted_writes == 10
    cur = await ckpt_db.execute("SELECT COUNT(*) FROM writes WHERE thread_id='iv-1'")
    assert (await cur.fetchone())[0] == 20


async def test_不同场次互不影响(ckpt_db):
    await _seed(ckpt_db, "iv-1", 30)
    await _seed(ckpt_db, "iv-2", 3)
    await prune_sqlite(ckpt_db, "iv-1", keep=20)
    cur = await ckpt_db.execute("SELECT COUNT(*) FROM checkpoints WHERE thread_id='iv-2'")
    assert (await cur.fetchone())[0] == 3


async def test_空场次不报错(ckpt_db):
    result = await prune_sqlite(ckpt_db, "iv-none", keep=20)
    assert result.kept == 0
    assert result.deleted_checkpoints == 0
