"""题库检索（出题节点专用，SPEC §4.4 / §5 / §8.1）。

demo 阶段出题检索 = payload 过滤 + 随机：出题没有查询文本，dense 检索没有输入；
dense top-k 留给阶段 2 追问/学习推送（接口不变）。

两层数据源分工（T2 口径）：Qdrant payload 只存过滤字段
（question_id/domain/topic/difficulty/company/round，且只含 enabled 题），
题目全文与 key_points 检索命中后 join SQLite（SPEC §8.1）。
"""

from __future__ import annotations

import asyncio
import json
import random
import sqlite3
from functools import lru_cache
from pathlib import Path
from typing import Any

from qdrant_client import AsyncQdrantClient, models as qm

from app.config import get_settings

COLLECTION = "questions"
SCROLL_LIMIT = 100  # 单域×难度组合远小于此数，一次 scroll 足够


@lru_cache
def _get_client() -> AsyncQdrantClient:
    """单例复用连接；测试 monkeypatch 此函数注入 fake。"""
    return AsyncQdrantClient(url=get_settings().qdrant_url)


def _fetch_by_ids(db_path: Path, ids: list[str]) -> list[dict[str, Any]]:
    """SQLite join 完整题目（同步，由 search_questions 以 to_thread 包裹）。

    出参 key 与 ingest 管道 JSON 一致（question_id/question/key_points/…）。
    """
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        placeholders = ",".join("?" * len(ids))
        rows = conn.execute(
            f"SELECT id, question, answer, key_points, follow_ups, domain, topic, difficulty, "
            f"company, round FROM questions WHERE id IN ({placeholders}) AND status='enabled'",
            ids,
        ).fetchall()
    out: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        item["question_id"] = item.pop("id")
        item["key_points"] = json.loads(item["key_points"]) if item["key_points"] else []
        item["follow_ups"] = json.loads(item["follow_ups"]) if item["follow_ups"] else []
        out.append(item)
    return out


async def search_questions(
    *,
    domain: str,
    difficulty: str,
    exclude_ids: list[str] | None = None,
    k: int = 3,
) -> list[dict[str, Any]]:
    """按 domain/difficulty 过滤题库，排除已问，随机取至多 k 条完整题目。"""
    points, _ = await _get_client().scroll(
        collection_name=COLLECTION,
        scroll_filter=qm.Filter(
            must=[
                qm.FieldCondition(key="domain", match=qm.MatchValue(value=domain)),
                qm.FieldCondition(key="difficulty", match=qm.MatchValue(value=difficulty)),
            ]
        ),
        limit=SCROLL_LIMIT,
        with_payload=True,
        with_vectors=False,
    )
    exclude = set(exclude_ids or [])
    ids = [
        qid
        for p in points
        if p.payload and (qid := p.payload.get("question_id")) and qid not in exclude
    ]
    if not ids:
        return []
    rows = await asyncio.to_thread(_fetch_by_ids, get_settings().db_path, ids)
    return random.sample(rows, min(k, len(rows)))


async def fetch_reference_answers(question_ids: list[str]) -> dict[str, str]:
    """按 id 批量取参考答案全文（报告复盘用，FR-25 / SPEC §4.6）。

    与出题检索同源（SQLite join，只含 enabled 题）：生成题/场景题无 id、已归档题查不到，
    都自然缺席，调用方按 id 取即可，取不到为 None（前端不渲染参考区）。
    """
    if not question_ids:
        return {}
    rows = await asyncio.to_thread(_fetch_by_ids, get_settings().db_path, question_ids)
    return {row["question_id"]: row["answer"] for row in rows}
