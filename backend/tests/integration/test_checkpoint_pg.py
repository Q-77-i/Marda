"""PostgreSQL checkpointer 集成测试（P4-E3）：跨实例 resume + 保留策略 + 压缩。

**需要真 PG**（MARDA_TEST_CHECKPOINT_URL），默认跳过。

这三件事只有 PG 才验得到：
1. **跨实例 resume**—— saver A 写的状态，saver B（=另一个 worker）能接着跑；
2. **保留后仍能 resume**——删掉更早的快照，最新那张必须完好；
3. **压缩生效**——blob 的 type 是 `msgpack+zlib`（PG 里 blob 是 BYTEA，type 单独存）。
"""

from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.postgres

CKPT_DSN = os.environ.get("MARDA_TEST_CHECKPOINT_URL")
requires_pg_ckpt = pytest.mark.skipif(
    not CKPT_DSN, reason="未配置 MARDA_TEST_CHECKPOINT_URL"
)


@pytest.fixture
async def pool():
    from psycopg_pool import AsyncConnectionPool

    pool = AsyncConnectionPool(CKPT_DSN, min_size=1, max_size=3, open=False)
    await pool.open(wait=True)
    yield pool
    await pool.close()


@pytest.fixture
async def saver(pool):
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

    from app.graph.serde import make_serde

    saver = AsyncPostgresSaver(pool, serde=make_serde())
    await saver.setup()
    # 清库（测试专用）
    async with pool.connection() as conn:
        await conn.execute("DELETE FROM checkpoints")
        await conn.execute("DELETE FROM checkpoint_writes")
        await conn.execute("DELETE FROM checkpoint_blobs")
        await conn.commit()
    yield saver


@requires_pg_ckpt
async def test_跨实例resume(saver, pool):
    """worker A 写的状态，worker B（另一个 saver 实例）能接着跑。"""
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
    from langgraph.types import Command

    from app.graph.graph import build_graph, run_config
    from app.graph.serde import make_serde
    from app.graph.state import InterviewState

    graph_a = build_graph(checkpointer=saver)
    config = run_config("iv-pg", 3)
    state = InterviewState(interview_id="iv-pg", position="Agent", question_count=3)
    await graph_a.ainvoke(state, config=config)

    saver_b = AsyncPostgresSaver(pool, serde=make_serde())
    graph_b = build_graph(checkpointer=saver_b)
    snapshot = await graph_b.aget_state(config)
    assert snapshot.values, "另一个 saver 实例应能读到状态"
    assert snapshot.values["interview_id"] == "iv-pg"


@requires_pg_ckpt
async def test_保留后最新快照仍可resume(saver, pool):
    """删掉更早的快照后，最新那张必须完好（resume 的落点）。"""
    from app.graph.retention import prune_postgres

    from app.graph.graph import build_graph, run_config
    from app.graph.state import InterviewState

    graph = build_graph(checkpointer=saver)
    config = run_config("iv-keep", 3)
    for i in range(6):
        await graph.ainvoke(
            InterviewState(interview_id="iv-keep", position="Agent", question_count=3),
            config=config,
        )

    result = await prune_postgres(pool, "iv-keep", keep=2)
    assert result.kept == 2
    assert result.deleted_checkpoints > 0

    snapshot = await graph.aget_state(config)
    assert snapshot.values, "保留后最新快照仍应可读"


@requires_pg_ckpt
async def test_压缩生效(saver, pool):
    """blob 的 type 应带 +zlib 后缀（压缩真的落到 PG 里了）。"""
    from app.graph.graph import build_graph, run_config
    from app.graph.state import InterviewState

    graph = build_graph(checkpointer=saver)
    config = run_config("iv-zip", 3)
    await graph.ainvoke(
        InterviewState(interview_id="iv-zip", position="Agent", question_count=3),
        config=config,
    )
    async with pool.connection() as conn:
        cur = await conn.execute("SELECT DISTINCT type FROM checkpoint_blobs")
        types = [row[0] for row in await cur.fetchall()]
    assert any(t.endswith("+zlib") for t in types), f"blob 未压缩：{types}"
