"""嵌入服务客户端（本地 BGE-M3，M3）：一次调用返回 dense + sparse。

与模型解耦：api/ingest 只认 HTTP 契约（POST /embed），容器内走
`http://embedding:8091`、宿主机开发走 `localhost:8091`（config.embedding_url）。

单请求文本数有上限（服务侧 MAX_BATCH），故客户端按 BATCH_SIZE 分批、保序拼接。
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx
from qdrant_client.models import SparseVector

from app.config import get_settings

BATCH_SIZE = 64
EMBED_TIMEOUT = 300.0  # CPU 推理：64 条文本量级为数十秒


@dataclass(frozen=True)
class Embedding:
    """单条文本的向量：dense（L2 归一，1024d）+ sparse（token_id → 权重）。"""

    dense: list[float]
    sparse: dict[int, float]


class EmbeddingClient:
    def __init__(
        self,
        base_url: str | None = None,
        *,
        timeout: float = EMBED_TIMEOUT,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base_url = (base_url or get_settings().embedding_url).rstrip("/")
        self._timeout = timeout
        self._transport = transport

    async def embed(self, texts: list[str]) -> list[Embedding]:
        """按输入顺序返回每条文本的向量；空输入直接返回空列表。"""
        out: list[Embedding] = []
        async with httpx.AsyncClient(
            base_url=self._base_url, timeout=self._timeout, transport=self._transport
        ) as client:
            for start in range(0, len(texts), BATCH_SIZE):
                chunk = texts[start : start + BATCH_SIZE]
                response = await client.post("/embed", json={"texts": chunk})
                response.raise_for_status()
                out.extend(_parse(response.json(), expected=len(chunk)))
        return out


def to_sparse_vector(weights: dict[int, float]) -> SparseVector:
    """sparse 权重 → Qdrant 稀疏向量（indices 升序，与 values 一一对应）。"""
    pairs = sorted(weights.items())
    return SparseVector(indices=[i for i, _ in pairs], values=[v for _, v in pairs])


def question_doc_text(question: str, key_points: list[str]) -> str:
    """题目的嵌入/rerank 文档文本 = 题干 + 关键点（M3 拍板，单一来源）。

    ingest（嵌入）与 hybrid_search（rerank）共用：关键点是答案的要点提炼，
    题库搜索按考点召回靠它；答案全文过长会稀释题干。两处文本格式漂移会让
    rerank 打分对象与嵌入对象不一致，故只保留这一份定义。
    """
    points = [str(p).strip() for p in (key_points or []) if p]
    return "\n".join([question, *(p for p in points if p)])


def _parse(payload: dict, *, expected: int) -> list[Embedding]:
    """校验服务返回的形状（条数/维度），转成 Embedding。

    形状不符即报错，而不是把错位向量写进向量库——嵌入服务与调用方的
    模型配置漂移（维度不一致）在 Qdrant 侧只会表现为检索结果莫名变差。
    """
    dense, sparse = payload.get("dense") or [], payload.get("sparse") or []
    dim = payload.get("dim")
    if len(dense) != expected or len(sparse) != expected:
        raise RuntimeError(f"嵌入服务返回 {len(dense)}/{len(sparse)} 条，期望 {expected} 条")
    if not isinstance(dim, int) or dim <= 0:
        raise RuntimeError(f"嵌入服务返回非法维度：{dim!r}")
    out: list[Embedding] = []
    for vector, weights in zip(dense, sparse):
        if len(vector) != dim:
            raise RuntimeError(f"dense 维度 {len(vector)} 与声明的 {dim} 不一致")
        out.append(Embedding(dense=[float(v) for v in vector], sparse={int(k): float(v) for k, v in weights.items()}))
    return out
