"""题库与容量接口集成测试（FR-12 / FR-14）：ASGI 全链路 + tmp 库。

浏览/分面/容量走真 SQL（自建 questions / question_sources 并插桩）；关键词模式
monkeypatch 混合检索（真链路已由 test_hybrid_search 单测 + M3 探针覆盖）。
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from app.tools import hybrid_search
from bank_fixture import create_tables, insert_questions, insert_sources, question_row

# 真库形态（2026-09-29）的简化版：L3 各域 2 题，唯 planning-reasoning 仅 1 题
# → L3 × 10 可直供（该域配额 1）、L3 × 15 不足（配额 2）。
_L3_DOMAINS = [
    "agent-architecture", "rag", "planning-reasoning", "tool-use",
    "memory", "engineering-observability",
]


@pytest.fixture
def bank_db(client):
    """在临时业务库上建题库表并插桩（client 先起，保证 DB_PATH 已注入）。

    L1/L2 每域 3 题（充足）；L3 每域 2 题、唯 planning-reasoning 1 题。
    """
    path = Path(os.environ["DB_PATH"])
    create_tables(path)
    rows = [
        question_row("q1", domain="rag", difficulty="L1"),
        question_row("q2", domain="rag", difficulty="L2", company="字节跳动", round_="二面"),
        question_row("q3", domain="memory", difficulty="L3", company=None, round_=None,
                     source="FAQ_Of_LLM_Interview"),
        question_row("q9", domain="rag", difficulty="L1", status="draft"),
    ]
    for domain in _L3_DOMAINS:
        for difficulty, count in (("L1", 3), ("L2", 3), ("L3", 2)):
            if domain == "planning-reasoning" and difficulty == "L3":
                count = 1
            rows += [
                question_row(f"q_{difficulty}_{domain}_{n}", domain=domain,
                             difficulty=difficulty, company=None, round_=None)
                for n in range(count)
            ]
    insert_questions(path, rows)
    insert_sources(path, [
        ("q1", "个人题库", "PRIVATE", "https://x/1", "牛客补充版", "enabled"),
        ("q1", "FAQ_Of_LLM_Interview", "MIT", "https://x/2", "faq.md", "enabled"),
    ])
    return path


async def test_浏览筛选与分页(client, bank_db):
    r = await client.get("/api/bank/questions", params={"page_size": 5})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["mode"] == "browse" and body["page_size"] == 5
    assert body["total"] == 50  # 51 行 − 1 条 draft
    assert len(body["items"]) == 5

    r = await client.get("/api/bank/questions", params={"domain": "rag", "difficulty": "L2"})
    body = r.json()
    assert body["total"] == 4  # q2 + 3 条插桩（draft 的 q9 是 L1，不在内）
    assert [i["question_id"] for i in body["items"]] == [
        "q2", "q_L2_rag_0", "q_L2_rag_1", "q_L2_rag_2"
    ]
    assert {i["difficulty"] for i in body["items"]} == {"L2"}


async def test_浏览项带来源合规四要素(client, bank_db):
    r = await client.get("/api/bank/questions", params={"domain": "rag", "difficulty": "L1"})
    item = r.json()["items"][0]
    assert item["question_id"] == "q1"
    assert [s["source"] for s in item["sources"]] == ["个人题库", "FAQ_Of_LLM_Interview"]  # 主源首位
    assert item["sources"][1]["license"] == "MIT" and item["sources"][1]["url"] == "https://x/2"
    assert item["key_points"] == ["k1", "k2"]


async def test_关键词模式走混合检索且透传筛选(client, bank_db, monkeypatch):
    seen: dict = {}

    async def fake_search(query, *, k, filters, db_path):
        seen.update({"query": query, "k": k, "filters": filters})
        return [{
            "question_id": "q3", "question": "召回题", "answer": "答案", "key_points": [],
            "follow_ups": [], "domain": "memory", "topic": "t", "difficulty": "L3",
            "company": None, "round": None, "source": "FAQ_Of_LLM_Interview",
        }]

    monkeypatch.setattr(hybrid_search, "hybrid_search", fake_search)
    r = await client.get(
        "/api/bank/questions", params={"q": "  RAG 切片粒度 ", "domain": "memory"}
    )
    body = r.json()
    assert body["mode"] == "search" and body["total"] is None
    assert [i["question_id"] for i in body["items"]] == ["q3"]
    assert seen["query"] == "RAG 切片粒度" and seen["k"] == 20  # query 去空格、单页 20
    assert seen["filters"] == {"domain": "memory", "difficulty": None, "company": None, "round": None}


async def test_空关键词退回浏览模式(client, bank_db):
    r = await client.get("/api/bank/questions", params={"q": "   "})
    assert r.json()["mode"] == "browse"


async def test_分面(client, bank_db):
    body = (await client.get("/api/bank/facets")).json()
    assert {f["value"] for f in body["domain"]} == set(_L3_DOMAINS) | {"rag", "memory"}
    assert {f["value"] for f in body["company"]} == {"腾讯", "字节跳动"}
    assert {f["value"] for f in body["round"]} == {"一面", "二面"}


async def test_容量校验L3边界与不足明细(client, bank_db):
    body = (await client.get("/api/bank/capacity")).json()
    grid = {(o["difficulty"], o["question_count"]): o for o in body["options"]}
    assert len(grid) == 12  # 4 难度 × 3 题数

    assert grid[("L2", 15)]["ok"] is True
    assert grid[("L3", 10)]["ok"] is True  # 该域配额 1、供给 1
    short = grid[("L3", 15)]
    assert short["ok"] is False and short["base"] == "L3"
    assert short["shortfalls"] == [
        {"domain": "planning-reasoning", "required": 2, "available": 1}
    ]
    # 自适应按 L1 起点校验（L1 供给充足）
    assert grid[("adaptive", 15)]["ok"] is True and grid[("adaptive", 15)]["base"] == "L1"


async def test_容量校验题数可指定(client, bank_db):
    body = (await client.get("/api/bank/capacity", params={"counts": "10", "x": "1"})).json()
    assert [o["question_count"] for o in body["options"]] == [10] * 4


async def test_未登录一律401(anon_client, bank_db):
    for url in ("/api/bank/questions", "/api/bank/facets", "/api/bank/capacity"):
        assert (await anon_client.get(url)).status_code == 401
