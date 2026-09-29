"""题库路由（FR-12 浏览搜索 / FR-14 容量校验，SPEC §7）。

全部端点需登录（题库是公共资产，无归属隔离；M7 私有题库另立维度）。
浏览走 SQL 分页，关键词走混合检索（向量 + rerank，M3）；容量校验只读供给统计，
**不阻断创建**（引擎有难度放宽 + LLM 生成兜底，禁用与否是前端表单的展示决策）。
"""

from __future__ import annotations

import asyncio
from typing import Literal

from fastapi import APIRouter, Depends, Query

from app.api.auth import get_current_user
from app.config import get_settings
from app.graph.rules.capacity import capacity_grid
from app.tools import bank_query, hybrid_search

router = APIRouter(prefix="/api/bank", tags=["bank"], dependencies=[Depends(get_current_user)])

SEARCH_LIMIT = 20  # 关键词模式单页条数（相关性排序下分页无意义，超出部分不收）
DEFAULT_COUNTS = "5,10,15"  # 与前端 QUESTION_COUNT_OPTIONS 一致的兜底
COUNT_MIN, COUNT_MAX = 2, 20  # 同创建接口的轮次下限/上限

Filters = dict[str, str | None]


def _parse_counts(raw: str) -> list[int]:
    """`"5,10,15"` → `[5, 10, 15]`；非法项丢弃、越界夹紧，去重后升序。"""
    counts = set()
    for part in raw.split(","):
        part = part.strip()
        if part.isdigit():
            counts.add(min(max(int(part), COUNT_MIN), COUNT_MAX))
    return sorted(counts) or [int(c) for c in DEFAULT_COUNTS.split(",")]


@router.get("/questions")
async def list_questions(
    q: str | None = None,
    domain: str | None = None,
    difficulty: Literal["L1", "L2", "L3"] | None = None,
    company: str | None = None,
    round: str | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=bank_query.BROWSE_PAGE_SIZE, ge=1,
                           le=bank_query.BROWSE_MAX_PAGE_SIZE),
):
    """题库浏览（筛选 + 分页）或关键词检索（混合检索，单页 top N）。

    `mode` 区分两者：browse 有 total 可翻页；search 无 total（相关性排序不翻页）。
    """
    db_path = get_settings().db_path
    filters: Filters = {
        "domain": domain, "difficulty": difficulty, "company": company, "round": round
    }
    if q and q.strip():
        items = await hybrid_search.hybrid_search(
            q.strip(), k=SEARCH_LIMIT, filters=filters, db_path=db_path
        )
        return {
            "mode": "search",
            "total": None,
            "page": 1,
            "page_size": SEARCH_LIMIT,
            "items": await asyncio.to_thread(bank_query.attach_sources, db_path, items),
        }
    items, total = await asyncio.to_thread(
        bank_query.browse_questions, db_path, filters=filters, page=page, page_size=page_size
    )
    return {
        "mode": "browse",
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": await asyncio.to_thread(bank_query.attach_sources, db_path, items),
    }


@router.get("/facets")
async def get_facets():
    """四维分面取值与计数（仅 enabled）——前端筛选项由此生成，不硬编候选值。"""
    return await asyncio.to_thread(bank_query.bank_facets, get_settings().db_path)


@router.get("/capacity")
async def get_capacity(counts: str = DEFAULT_COUNTS):
    """FR-14：难度 × 题数的题库直供能力（含不足明细，供表单禁用与提示）。"""
    db_path = get_settings().db_path
    supply = await asyncio.to_thread(bank_query.difficulty_supply, db_path)
    return {"options": capacity_grid(_parse_counts(counts), supply)}
