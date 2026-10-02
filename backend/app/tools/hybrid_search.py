"""混合检索（M3 会话 2）：dense + sparse 双路 RRF → SiliconFlow rerank → top k。

流程：query 本地 embed → Qdrant Query API prefetch（dense/sparse 各 RRF_CANDIDATES）
→ Fusion.RRF 融合候选 → SQLite join（payload 只存过滤字段；题干与关键点在 SQLite，
且 rerank 需要文档文本）→ rerank（文档 = 题干+关键点，与嵌入文本同源）→ top k。

输出键同 search_questions（完整题目行）；候选 ≤1 或调用方传 `rerank=False` 时跳过 rerank，
失败直接抛（降级/熔断留阶段 3）。消费方：M6 题库搜索 / M9 学习推荐——后者按查询形态传
`rerank=False`（P2-M2：多漏点长查询实测净贡献为负，走融合序）。
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from qdrant_client import models as qm

from app.config import get_settings
from app.domain import DOMAIN_LABELS
from app.tools.embedding import EmbeddingClient, question_doc_text, to_sparse_vector
from app.tools.question_search import COLLECTION, fetch_by_ids, get_qdrant_client
from app.tools.rerank import rerank as default_ranker

RRF_CANDIDATES = 30  # 每路候选数 = 融合候选数（rerank 输入）
# 域中文标签集合（值集，供 _rerank_query 判首段）
_DOMAIN_LABEL_VALUES = frozenset(DOMAIN_LABELS.values())
# 过滤维度（M6 FR-12 浏览筛选）：payload 字段同出题检索（SPEC §8.1），过滤下推到两路 prefetch
FILTER_KEYS = ("domain", "difficulty", "company", "round")


def _rerank_query(query: str) -> str:
    """rerank 的查询文本：剥离首段域名标签（P2-M2 长查询修复）。

    学习推荐的查询是「域中文标签 + 多条漏点」拼成的长文本，实测把交叉编码器带偏
    （missed_point NDCG@5：融合 0.813 → rerank 后 0.660）——标签是**同域所有候选的
    共性词**，只给泛泛的同域题加分；域约束本就由 `filters` 的 payload 过滤承担，
    标签在 rerank 里纯属重复。嵌入与 RRF 仍吃原始 query（那是健康的 0.813）。

    三条边界：只剥**首段且整体等于已知标签**时（`partition` 只切第一个「；」——漏点
    自身含「；」不影响判断，M12 记录过 187 条漏点里 4 条含「；」）；剥完为空则不剥
    （M9 的「无漏点 → 纯域名查询」退化成空查询会更糟）；无「；」的短查询逐字不变
    （题库搜索形态）。
    """
    head, sep, rest = query.partition("；")
    if sep and head.strip() in _DOMAIN_LABEL_VALUES and rest.strip():
        return rest.strip()
    return query


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
    rerank: bool = True,
    embedder: EmbeddingClient | None = None,
    qclient: Any | None = None,
    ranker: Callable[..., Awaitable[list[tuple[int, float]]]] | None = None,
    db_path: Path | None = None,
) -> list[dict[str, Any]]:
    """按相关性降序返回至多 k 条完整题目；filters 见 _payload_filter；其余可注入（单测用 fake）。

    `rerank=False`（P2-M2）：直接返回 RRF 融合序前 k 条、不调 rerank。**给多漏点长查询用**
    （学习推荐）——离线实测该场景下 rerank 三种形态净贡献**全为负**（长查询单次 −0.153 /
    剥标签 −0.042 / 逐漏点融合 −0.114，rrf 基线 NDCG@5 0.813，golden sha `1e40a8d6e84a`），
    而短查询（题库搜索）净贡献 +0.041 → 由调用方按查询形态选择，不在此处猜。
    """
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
    if len(ordered) <= 1 or not rerank:
        return ordered[:k]
    docs = [question_doc_text(row["question"], row["key_points"]) for row in ordered]
    ranked = await ranker(_rerank_query(query), docs, top_n=min(k, len(ordered)))
    return [ordered[index] for index, _ in ranked[:k]]
