"""四个检索变体真栈取数（P1-M12 会话 1）：dense / sparse / RRF / hybrid。

M3 会话 2 用 7 组 query 人工对比过三路，结论是「双路 RRF 解决字面匹配、rerank 解决
内容匹配，两者缺一不可」——但那是定性的（踩坑记录原话：量化 NDCG 留 M12）。会话 1
用同一批 golden 标注把这件事量化：四个变体跑同一批 query、出同一套指标。

**生产路径不重写**：`hybrid` 变体直接调 `app.tools.hybrid_search`——它与 M6 题库搜索、
M9 学习推荐消费的是同一个函数，指标才对生产有意义；其余三路直接驱动 Qdrant，
嵌入与 payload 过滤都复用生产实现，保证「变体之间的差异只在检索策略本身」。
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from qdrant_client import models as qm

from app.config import get_settings
from app.tools.embedding import EmbeddingClient, to_sparse_vector
from app.tools.hybrid_search import (
    RRF_CANDIDATES,
    _payload_filter,  # noqa: PLC2701 —— 私有但必须复用：过滤语义（FILTER_KEYS/空值不筛）不能有第二份实现
    hybrid_search,
)
from app.tools.question_search import COLLECTION, get_qdrant_client

VARIANTS: tuple[str, ...] = ("dense", "sparse", "rrf", "hybrid")
"""变体顺序 = 报告展示顺序，也是池构建的取用顺序（无深浅含义）。

`rrf` = 融合但不 rerank，与 `hybrid` 的差就是 rerank 的贡献（M3 定性结论的量化版）。
"""

POOL_LIMITS: dict[str, int] = {"dense": 50, "sparse": 50, "rrf": 30, "hybrid": 30}
"""池 = 各变体前 N 条并集。**池必须比生产返回的 30 条深**，否则「相关但没被检索到」
在池口径下不可见，Recall 会虚高——单路放到 50 就是为捞出 hybrid top-30 之外的题
（实测：三条代表 query 的池落在 60–70）。融合两路维持 30 = 生产口径。"""

DEFAULT_K = 60  # 单变体默认取回的候选数（够深，池由 POOL_LIMITS 再截断）


def build_pool(variants: Mapping[str, Sequence[str]]) -> list[str]:
    """池 = 各变体前 POOL_LIMITS 条并集，保序去重（纯函数）。

    池外题目在指标里视作 grade 0——这是「池内口径」的来源，标注也只标池内候选。
    """
    pool: list[str] = []
    seen: set[str] = set()
    for name in VARIANTS:
        for qid in list(variants.get(name, []))[: POOL_LIMITS[name]]:
            if qid not in seen:
                seen.add(qid)
                pool.append(qid)
    return pool


def _ids(result: Any) -> list[str]:
    return [
        qid
        for point in result.points
        if point.payload and (qid := point.payload.get("question_id"))
    ]


async def rank_variants(
    query: str,
    *,
    k: int = DEFAULT_K,
    filters: dict[str, str | None] | None = None,
    rerank: bool = True,
    embedder: EmbeddingClient | None = None,
    qclient: Any | None = None,
    ranker: Callable[..., Awaitable[list[tuple[int, float]]]] | None = None,
    db_path: Path | None = None,
) -> dict[str, list[str]]:
    """一条 query → 四个变体的 ranked id 列表（各截到 k）。

    `rerank=False` 只影响 hybrid 变体（透传给生产函数，同 recommend 的长查询口径）。
    """
    if not query.strip():
        raise ValueError("查询文本不能为空")
    embedder = embedder or EmbeddingClient()
    qclient = qclient or get_qdrant_client()
    db_path = db_path or get_settings().db_path
    qfilter = _payload_filter(filters)

    vector = (await embedder.embed([query]))[0]

    async def _single(using: str, value: Any) -> list[str]:
        result = await qclient.query_points(
            collection_name=COLLECTION,
            query=value,
            using=using,
            limit=k,
            query_filter=qfilter,
            with_payload=True,
        )
        return _ids(result)

    async def _rrf() -> list[str]:
        result = await qclient.query_points(
            collection_name=COLLECTION,
            prefetch=[
                # 两路 prefetch 深度取生产的 RRF_CANDIDATES（单一来源）——rrf 变体 = 生产融合，
                # 与 hybrid 只差 rerank 一步，两者的指标差才是 rerank 的净贡献
                qm.Prefetch(
                    query=vector.dense, using="dense", limit=RRF_CANDIDATES, filter=qfilter
                ),
                qm.Prefetch(
                    query=to_sparse_vector(vector.sparse),
                    using="sparse",
                    limit=RRF_CANDIDATES,
                    filter=qfilter,
                ),
            ],
            query=qm.FusionQuery(fusion=qm.Fusion.RRF),
            limit=k,
            with_payload=True,
        )
        return _ids(result)

    dense_ids, sparse_ids, rrf_ids = await asyncio.gather(
        _single("dense", vector.dense),
        _single("sparse", to_sparse_vector(vector.sparse)),
        _rrf(),
    )
    # hybrid 走生产函数（内部会再嵌入一次——一条 query 多一次本地嵌入，换「与生产逐字同一路径」值得）
    rows = await hybrid_search(
        query, k=k, filters=filters, rerank=rerank,
        embedder=embedder, ranker=ranker, db_path=db_path,
    )
    return {
        "dense": dense_ids,
        "sparse": sparse_ids,
        "rrf": rrf_ids,
        "hybrid": [row["question_id"] for row in rows],
    }
