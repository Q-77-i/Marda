"""题库浏览查询（FR-12 / SPEC §9）：SQL 分页 + 分面计数 + 来源明细 + 供给统计。

与出题检索（question_search.py）的分工：出题只要「过滤 + 随机 k 条」，浏览要
总条数（分页）、来源合规四要素（M5 拆表后的 question_sources）与各维取值计数，
故走独立 SQL，不复用 fetch_by_ids（后者只 select 固定列、无计数）。

**只覆盖公共题库**（P1-M7）：本模块每个查询都带 `user_id IS NULL`——私有题是别人的
上传内容，绝不能漏进公共浏览/搜索/分面/容量。私有题自己的浏览走 bank_private
（那里 user_id 必传）。两个模块的分工就是「公共」与「某人的」，不重叠。
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from app.graph.rules.difficulty import DIFFICULTY_ORDER  # 难度档位顺序（单一来源，§4.3）

BROWSE_PAGE_SIZE = 10
BROWSE_MAX_PAGE_SIZE = 50

# 公共题判定（唯一写法，供本模块所有查询复用）
PUBLIC = "user_id IS NULL"

# 浏览项字段（= PRD §4.6 数据模型；answer/key_points/follow_ups 齐出，前端折叠展示）
_FIELDS = (
    "id, question, answer, key_points, follow_ups, domain, topic, difficulty, "
    "company, round, source"
)
# 筛选维度 → 列名（值相等；空值表示不筛）
_FILTER_COLUMNS = ("domain", "difficulty", "company", "round")


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _where(filters: dict[str, str | None]) -> tuple[str, list[str]]:
    clauses, params = ["status='enabled'", PUBLIC], []
    for column in _FILTER_COLUMNS:
        value = filters.get(column)
        if value:
            clauses.append(f"{column}=?")
            params.append(value)
    return " AND ".join(clauses), params


def _item(row: sqlite3.Row) -> dict[str, Any]:
    item = dict(row)
    item["question_id"] = item.pop("id")
    item["key_points"] = json.loads(item["key_points"]) if item["key_points"] else []
    item["follow_ups"] = json.loads(item["follow_ups"]) if item["follow_ups"] else []
    return item


def browse_questions(
    db_path: Path,
    *,
    filters: dict[str, str | None] | None = None,
    page: int = 1,
    page_size: int = BROWSE_PAGE_SIZE,
) -> tuple[list[dict[str, Any]], int]:
    """按筛选分页取题（domain/difficulty 排序 + id 兜底 → 分页稳定），返回 (items, total)。"""
    where, params = _where(filters or {})
    page_size = max(1, min(page_size, BROWSE_MAX_PAGE_SIZE))
    offset = (max(1, page) - 1) * page_size
    with _connect(db_path) as conn:
        total = conn.execute(f"SELECT COUNT(*) FROM questions WHERE {where}", params).fetchone()[0]
        rows = conn.execute(
            f"SELECT {_FIELDS} FROM questions WHERE {where}"
            f" ORDER BY domain, difficulty, id LIMIT ? OFFSET ?",
            [*params, page_size, offset],
        ).fetchall()
    return [_item(row) for row in rows], total


def get_question(db_path: Path, question_id: str) -> dict[str, Any] | None:
    """按 id 取单题（**只认公共 + enabled**）；取不到返回 None。

    不区分「不存在 / 别人的私有题 / 已归档」——私有题的 id 不该成为可探测的信号
    （同 M7「跨用户一律 404」的口径）。P2-M10 起供 MCP 题库查询 server 用。
    """
    where, _ = _where({})
    with _connect(db_path) as conn:
        row = conn.execute(
            f"SELECT {_FIELDS} FROM questions WHERE id=? AND {where}", (question_id,)
        ).fetchone()
    return _item(row) if row else None


def fetch_sources(db_path: Path, question_ids: list[str]) -> dict[str, list[dict[str, Any]]]:
    """按 id 批量取来源明细（一题多源，按 id 分组）。"""
    if not question_ids:
        return {}
    placeholders = ",".join("?" * len(question_ids))
    with _connect(db_path) as conn:
        rows = conn.execute(
            f"SELECT question_id, source, license, url, source_detail FROM question_sources"
            f" WHERE question_id IN ({placeholders}) AND status='enabled'"
            f" ORDER BY question_id, source",
            question_ids,
        ).fetchall()
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(row["question_id"], []).append(dict(row))
    return grouped


def attach_sources(db_path: Path, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """给题目项挂 `sources`（**主源排首位**——questions.source 是答案主源，M5 主源裁决口径）。"""
    grouped = fetch_sources(db_path, [item["question_id"] for item in items])
    for item in items:
        primary = item.get("source") or ""
        sources = grouped.get(item["question_id"], [])
        item["sources"] = sorted(sources, key=lambda s: (s["source"] != primary, s["source"]))
    return items


def bank_facets(db_path: Path) -> dict[str, list[dict[str, Any]]]:
    """四维分面取值与计数（仅公共 + enabled；计数降序、同数按值升序）。

    例外：**难度按档位排**（L1→L3）——它是有序维度，按计数排会把 L2 顶到 L1 前面，
    而下游（筛选下拉）要的是档位顺序；厂商/面次没有天然顺序，计数序才有意义。
    """
    facets: dict[str, list[dict[str, Any]]] = {}
    with _connect(db_path) as conn:
        for column in _FILTER_COLUMNS:
            rows = conn.execute(
                f"SELECT {column} AS value, COUNT(*) AS count FROM questions"
                f" WHERE status='enabled' AND {PUBLIC} AND {column} IS NOT NULL AND {column} != ''"
                f" GROUP BY {column} ORDER BY count DESC, value"
            ).fetchall()
            values = [{"value": r["value"], "count": r["count"]} for r in rows]
            if column == "difficulty":
                values.sort(key=lambda f: DIFFICULTY_ORDER.index(f["value"])
                            if f["value"] in DIFFICULTY_ORDER else len(DIFFICULTY_ORDER))
            facets[column] = values
    return facets


def difficulty_supply(db_path: Path, *, user_id: str | None = None) -> dict[str, dict[str, int]]:
    """{难度: {域: enabled 题数}}——容量校验（rules/capacity）的供给输入。

    公共题恒计入；给了 user_id 再叠加**该用户自己的私有题**（FR-14 与 M7 合流：
    私有题参与出题，容量就该算上它们，否则表单会在他题目充足时误报不足）。
    他人的私有题不计入——容量是「这个用户能问到多少题」。
    """
    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT difficulty, domain, COUNT(*) AS n FROM questions"
            f" WHERE status='enabled' AND ({PUBLIC} OR user_id=?) GROUP BY difficulty, domain",
            (user_id,),
        ).fetchall()
    supply: dict[str, dict[str, int]] = {}
    for row in rows:
        supply.setdefault(row["difficulty"], {})[row["domain"]] = row["n"]
    return supply
