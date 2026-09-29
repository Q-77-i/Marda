"""混合检索（M3 会话 2）：dense + sparse 双路 RRF → SiliconFlow rerank → top k。

流程：query 本地 embed → Qdrant Query API prefetch（dense/sparse 各 RRF_CANDIDATES）
→ Fusion.RRF 融合候选 → SQLite join（payload 只存过滤字段；题干与关键点在 SQLite，
且 rerank 需要文档文本）→ rerank（文档 = 题干+关键点，与嵌入文本同源）→ top k。

输出键同 search_questions（完整题目行）；候选 ≤1 跳过 rerank，rerank 失败
直接抛（降级/熔断留阶段 3）。消费方：M6 题库搜索 / M9 学习推荐。
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from qdrant_client import models as qm

from app.config import get_settings
from app.tools.embedding import EmbeddingClient, question_doc_text, to_sparse_vector
from app.tools.question_search import COLLECTION, fetch_by_ids, get_qdrant_client
from app.tools.rerank import rerank as default_ranker

RRF_CANDIDATES = 30  # 每路候选数 = 融合候选数（rerank 输入）
# 过滤维度（M6 FR-12 浏览筛选）：payload 字段同出题检索（SPEC §8.1），过滤下推到两路 prefetch
FILTER_KEYS = ("domain", "difficulty", "company", "round")


def _payload_filter(filters: dict[str, str | None] | None) -> qm.Filter | None:
    """筛选字典 → Qdrant Filter（空值 = 不筛；全空 → None，不构造空 Filter）。

    调用方必须把结果挂到**每一路 prefetch 上**：`limit` 是 prefetch 级的，只挂顶层
    会让两路先各取满全集候选再融合，过滤只能筛掉融合结果——被筛子集小时召回不完整。
    另：q/client 的参数名是 `query_filter`，写 `filter` 抛 `Unknown arguments`（探测踩到）。
    """
    conditions = [
        qm.FieldCondition(key=key, match=qm.MatchValue(value=value))
        for key in FILTER_KEYS
        if (value := (filters or {}).get(key))
    ]
    return qm.Filter(must=conditions) if conditions else None


async def hybrid_search(
    query: str,
    *,
    k: int = 5,
    filters: dict[str, str | None] | None = None,
    embedder: EmbeddingClient | None = None,
    qclient: Any | None = None,
    ranker: Callable[..., Awaitable[list[tuple[int, float]]]] | None = None,
    db_path: Path | None = None,
) -> list[dict[str, Any]]:
    """按相关性降序返回至多 k 条完整题目；filters 见 _payload_filter；其余可注入（单测用 fake）。"""
    if not query.strip():
        raise ValueError("查询文本不能为空")
    embedder = embedder or EmbeddingClient()
    qclient = qclient or get_qdrant_client()
    ranker = ranker or default_ranker
    db_path = db_path or get_settings().db_path
    qfilter = _payload_filter(filters)

    vector = (await embedder.embed([query]))[0]
    result = await qclient.query_points(
        collection_name=COLLECTION,
        prefetch=[
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
        limit=RRF_CANDIDATES,
        with_payload=True,
    )
    candidate_ids = [
        qid
        for p in result.points
        if p.payload and (qid := p.payload.get("question_id"))
    ]
    if not candidate_ids:
        return []
    rows = await asyncio.to_thread(fetch_by_ids, db_path, candidate_ids)
    by_id = {row["question_id"]: row for row in rows}
    ordered = [by_id[qid] for qid in candidate_ids if qid in by_id]  # join 不保序，按候选序重排
    if len(ordered) <= 1:
        return ordered[:k]
    docs = [question_doc_text(row["question"], row["key_points"]) for row in ordered]
    ranked = await ranker(query, docs, top_n=min(k, len(ordered)))
    return [ordered[index] for index, _ in ranked[:k]]
