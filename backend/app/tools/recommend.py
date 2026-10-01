"""学习推荐（FR-20）：报告短板域 → 知识点查询 → 混合检索 → 资料卡片。

业务闭环的最后一环（模拟面试 → 发现短板 → 针对性学习）：报告的 `weaknesses` 给出
短板域，逐题复盘的 `missed_key_points` 给出**具体漏掉的知识点**，两者拼成查询交给
M3 的 `hybrid_search`（同一套嵌入 + RRF + rerank，不另起检索链路）。数据源全是
报告 payload 本身——推荐**不重算分数、不落库**（题库更新即新鲜，PDF 导出零回归）。

三条口径（2026-09-30 拍板）：
- **查询 = 域名 + 该域漏点关键词**：评分官输出直接驱动检索，零新增 LLM 调用；
  无漏点时回退域名（`hybrid_search` 收到空串会抛 ValueError）。
- **排除本场已问过的题**：复盘卡已给过它们的参考答案，推荐要给同域**新材料**。
  检索条数取 `k + 本场该域已问数`——最多只有这么多条会被过滤掉，故过滤后仍 ≥ k
  （题库够的话）。比固定 margin 稳：不依赖「题库比 margin 厚」这种假设。
- **检索失败直接抛**（同 hybrid_search 的 rerank 口径）：调用方按 500 处置，
  前端给错误态 + 重试，绝不静默给半份推荐。
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from app.config import get_settings
from app.domain import DOMAIN_LABELS
from app.tools import bank_query, hybrid_search

RECOMMEND_K = 3  # 每个短板域的推荐条数
MAX_QUERY_POINTS = 6  # 查询里最多拼几个漏点关键词（再多会稀释检索意图）


def build_query_items(payload: dict) -> list[dict]:
    """报告 payload → 逐短板域的检索项（纯函数，可单测）。

    返回 `[{domain, query, advice, asked_ids, missed}]`，顺序同 payload 的 `weaknesses`
    （= 报告里短板域的展示顺序）。`weaknesses` 为空（旧 payload / 全程无技术题）→ `[]`。

    `missed` 是拼进 `query` 的那几个漏点原文（去重后截到 `MAX_QUERY_POINTS`）——检索只看
    拼好的 `query`，但**离线评测**要按单条漏点打分（一条 query 里塞六个漏点时，「推荐内容
    覆盖了几个漏点」比「整条 query 的相关性」更接近用户要的答案），切字符串不可靠（漏点
    自身含「；」），故在这里把列表一并带出，选择逻辑只有一份。
    """
    weaknesses = [domain for domain in (payload.get("weaknesses") or []) if domain]
    if not weaknesses:
        return []
    comments = payload.get("per_question_comments") or []
    advice = {item.get("domain"): item.get("advice") for item in (payload.get("study_advice") or [])}
    items = []
    for domain in weaknesses:
        missed: list[str] = []
        asked_ids: list[str] = []
        for comment in comments:
            if comment.get("domain") != domain:
                continue
            if comment.get("question_id"):
                asked_ids.append(comment["question_id"])
            for point in comment.get("missed_key_points") or []:
                text = str(point).strip()
                if text and text not in missed:  # 同一点在多题里重复漏 → 只拼一次
                    missed.append(text)
        # 域名取**中文标签**：题干与关键点都是中文，嵌入时英文 key 是噪点
        label = DOMAIN_LABELS.get(domain, domain)
        items.append({
            "domain": domain,
            "query": "；".join([label, *missed[:MAX_QUERY_POINTS]]),
            "advice": advice.get(domain),
            "asked_ids": asked_ids,
            "missed": missed[:MAX_QUERY_POINTS],
        })
    return items


async def recommend_for_report(
    payload: dict,
    *,
    k: int = RECOMMEND_K,
    searcher: Callable[..., Awaitable[list[dict[str, Any]]]] | None = None,
    db_path: Path | None = None,
) -> list[dict]:
    """逐短板域检索推荐卡片；`searcher`/`db_path` 可注入（单测用 fake，同 hybrid_search 模式）。

    各域并发检索，返回顺序同 `weaknesses`（`gather` 保序，与完成先后无关）。
    """
    items = build_query_items(payload)
    if not items:
        return []
    searcher = searcher or hybrid_search.hybrid_search
    db_path = db_path or get_settings().db_path
    return list(await asyncio.gather(*[
        _recommend_one(item, k=k, searcher=searcher, db_path=db_path) for item in items
    ]))


async def _recommend_one(
    item: dict,
    *,
    k: int,
    searcher: Callable[..., Awaitable[list[dict[str, Any]]]],
    db_path: Path,
) -> dict:
    """一个短板域 → 一个分组。

    `status` 三态（空分组**不静默隐藏**——用户会以为系统漏了）：
    `ok` 有卡片；`exhausted` 命中的候选全是本场问过的（该域已无检索得到的新题）；
    `empty` 该域一道题都没命中。
    """
    asked = set(item["asked_ids"])
    hits = await searcher(
        item["query"],
        k=k + len(asked),  # 最多 len(asked) 条会被滤掉 → 滤后仍 ≥ k
        filters={"domain": item["domain"]},
        db_path=db_path,
    )
    fresh = [row for row in hits if row["question_id"] not in asked][:k]
    cards = await asyncio.to_thread(bank_query.attach_sources, db_path, fresh) if fresh else []
    return {
        "domain": item["domain"],
        "advice": item["advice"],
        "status": "ok" if cards else ("exhausted" if hits else "empty"),
        "cards": cards,
    }
