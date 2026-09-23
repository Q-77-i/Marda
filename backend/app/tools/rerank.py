"""SiliconFlow rerank 客户端（bge-reranker-v2-m3，M3 会话 2）。

httpx 直调 POST {base_url}/rerank（OpenAI 兼容端点之外的独立 rerank 端点）：
- 请求 {model, query, documents, top_n?, return_documents: false}
- 响应 results: [{index, relevance_score}]（服务端已按分降序）
- 失败直接抛（降级/熔断留阶段 3）
"""

from __future__ import annotations

import math

import httpx

from app.config import get_settings

RERANK_MODEL = "BAAI/bge-reranker-v2-m3"
RERANK_TIMEOUT = 30.0


async def rerank(
    query: str,
    documents: list[str],
    *,
    top_n: int | None = None,
    base_url: str | None = None,
    api_key: str | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> list[tuple[int, float]]:
    """按相关性降序返回 [(文档下标, 分数)]；documents 为空不发请求，返回 []。"""
    if not documents:
        return []
    settings = get_settings()
    body: dict = {
        "model": RERANK_MODEL,
        "query": query,
        "documents": documents,
        "return_documents": False,
    }
    if top_n is not None:
        body["top_n"] = top_n
    async with httpx.AsyncClient(
        base_url=base_url or settings.siliconflow_base_url,
        timeout=RERANK_TIMEOUT,
        transport=transport,
    ) as client:
        response = await client.post(
            "/rerank",
            headers={"Authorization": f"Bearer {api_key or settings.siliconflow_api_key}"},
            json=body,
        )
        response.raise_for_status()
        return _parse(response.json(), total=len(documents))


def _parse(payload: dict, *, total: int) -> list[tuple[int, float]]:
    """校验并解析 results：index 在范围内且唯一、score 为数值；保序返回。

    形状校验报错而不是静默放行——索引错位会打乱候选顺序，服务端与
    调用方的契约漂移在检索侧只会表现为结果莫名变差。
    """
    results = payload.get("results")
    if not isinstance(results, list) or not results:
        raise RuntimeError("rerank 返回异常：results 缺失或为空")
    out: list[tuple[int, float]] = []
    seen: set[int] = set()
    for item in results:
        index, score = item.get("index"), item.get("relevance_score")
        if not isinstance(index, int) or not 0 <= index < total or index in seen:
            raise RuntimeError(f"rerank 返回异常：index {index!r}（总数 {total}，已见 {sorted(seen)}）")
        if not isinstance(score, (int, float)) or not math.isfinite(float(score)):
            raise RuntimeError(f"rerank 返回异常：relevance_score {score!r}")
        seen.add(index)
        out.append((index, float(score)))
    return out
