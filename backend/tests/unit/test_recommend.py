"""学习推荐单测（FR-20）：查询构造全分支 + 分组编排。

检索注入 fake（不打真 Qdrant/嵌入/rerank——真实链路由 smoke_api 验），
来源挂载 monkeypatch（attach_sources 的 SQL 由 test_bank_query 覆盖）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.tools import recommend


@pytest.fixture(autouse=True)
def _test_env(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setenv("SILICONFLOW_API_KEY", "test-key")
    monkeypatch.setenv("JWT_SECRET", "t" * 32)
    recommend.get_settings.cache_clear()
    yield
    recommend.get_settings.cache_clear()


def _comment(domain: str, *, missed: list[str] | None = None, qid: str | None = None) -> dict:
    return {"domain": domain, "missed_key_points": missed or [], "question_id": qid}


def _payload(weaknesses: list[str], comments: list[dict], advice: list[dict] | None = None) -> dict:
    return {
        "weaknesses": weaknesses,
        "per_question_comments": comments,
        "study_advice": advice or [],
    }


# ---- build_query_items（纯函数）----


def test_查询拼域名与漏点():
    payload = _payload(
        ["rag"],
        [_comment("rag", missed=["切片粒度怎么定", "rerank 模型怎么选"], qid="q_1")],
    )

    item = recommend.build_query_items(payload)[0]

    assert item["domain"] == "rag"
    assert item["query"] == "RAG；切片粒度怎么定；rerank 模型怎么选"
    # missed 与 query 同源（离线评测按单条漏点打分用，切字符串不可靠：漏点自身可能含「；」）
    assert item["missed"] == ["切片粒度怎么定", "rerank 模型怎么选"]


def test_漏点去重且保序():
    """同一知识点在多题里重复漏（追问后重评也会重记）→ 只拼一次。"""
    payload = _payload(
        ["rag"],
        [
            _comment("rag", missed=["切片粒度", "召回评估"]),
            _comment("rag", missed=["召回评估", "混合检索权重"]),
        ],
    )

    assert recommend.build_query_items(payload)[0]["query"] == "RAG；切片粒度；召回评估；混合检索权重"


def test_漏点超限截断():
    missed = [f"知识点{i}" for i in range(recommend.MAX_QUERY_POINTS + 3)]

    item = recommend.build_query_items(_payload(["rag"], [_comment("rag", missed=missed)]))[0]

    assert item["query"].endswith(f"知识点{recommend.MAX_QUERY_POINTS - 1}")
    assert f"知识点{recommend.MAX_QUERY_POINTS}" not in item["query"]


def test_无漏点回退域名():
    """全答对（无可漏点）时查询只剩域名——查空串会让 hybrid_search 抛 ValueError。"""
    payload = _payload(["memory"], [_comment("memory", qid="q_1")])

    item = recommend.build_query_items(payload)[0]

    assert item["query"] == "Memory"
    assert item["asked_ids"] == ["q_1"]


def test_空短板返回空():
    """旧 payload / 全程无技术题 → 不产生任何检索项。"""
    assert recommend.build_query_items(_payload([], [_comment("rag")])) == []
    assert recommend.build_query_items({}) == []


def test_已问题id按域归集且跳过生成题():
    """生成题（场景题/LLM 兜底题）question_id 为空 → 无从排除，也不该混进排除集。"""
    payload = _payload(
        ["rag", "memory"],
        [
            _comment("rag", qid="q_rag"),
            _comment("rag", qid=None),  # 生成题
            _comment("memory", qid="q_mem"),
            _comment("project", qid="q_proj"),  # 项目深挖题不属于短板域
        ],
    )

    items = recommend.build_query_items(payload)

    assert items[0]["asked_ids"] == ["q_rag"]
    assert items[1]["asked_ids"] == ["q_mem"]


def test_学习建议按域挂上_缺省为None():
    payload = _payload(
        ["rag", "memory"],
        [_comment("rag")],
        advice=[{"domain": "rag", "advice": "先补切片粒度"}],
    )

    items = recommend.build_query_items(payload)

    assert items[0]["advice"] == "先补切片粒度"
    assert items[1]["advice"] is None


def test_旧payload缺字段不崩():
    """FR-25 之前的报告没有逐题漏点/学习建议字段，推荐退化为「按域检索」。"""
    payload = {"weaknesses": ["rag"], "per_question_comments": [{"domain": "rag"}]}

    items = recommend.build_query_items(payload)

    assert items[0]["query"] == "RAG"
    assert items[0]["asked_ids"] == []
    assert items[0]["advice"] is None


# ---- recommend_for_report（编排）----


def _row(qid: str, domain: str = "rag") -> dict:
    return {"question_id": qid, "question": f"{qid} 题干", "domain": domain, "source": "src"}


class FakeSearcher:
    """按域返回预置命中（截到 k，模拟 hybrid_search 的 top-k 语义）。"""

    def __init__(self, hits: dict[str, list[dict]] | None = None) -> None:
        self._hits = hits or {}
        self.calls: list[dict] = []

    async def __call__(self, query, *, k, filters, rerank=True, db_path):
        self.calls.append({"query": query, "k": k, "filters": filters, "rerank": rerank})
        return [dict(row) for row in self._hits.get(filters["domain"], [])][:k]


@pytest.fixture
def attach(monkeypatch):
    """拦截来源挂载（返回原文即断言依据）。"""

    def _attach(db_path, items):
        for item in items:
            item["sources"] = [{"source": "src", "license": "MIT", "url": "u", "source_detail": None}]
        return items

    monkeypatch.setattr(recommend.bank_query, "attach_sources", _attach)


async def test_逐域检索_过滤条件与条数含已问数(attach):
    """条数 = k + 本场该域已问数：最多这么多条会被滤掉，滤后仍 ≥ k（不设固定 margin）。"""
    payload = _payload(
        ["rag"],
        [_comment("rag", qid="q_1"), _comment("rag", qid="q_2"), _comment("rag", qid=None)],
    )
    searcher = FakeSearcher({"rag": [_row("q_new")]})

    await recommend.recommend_for_report(payload, k=3, searcher=searcher, db_path=Path("x"))

    assert searcher.calls[0]["k"] == 3 + 2  # 生成题不计（无 id 无从排除）
    assert searcher.calls[0]["filters"] == {"domain": "rag"}
    assert searcher.calls[0]["query"] == "RAG"  # 无漏点 → 回退域名
    assert searcher.calls[0]["rerank"] is True  # 无漏点回退纯域名 → 仍走 rerank


async def test_多漏点长查询跳过rerank(attach):
    """P2-M2：有漏点 = 多漏点长查询 → 检索层跳过 rerank（离线实测该场景净贡献为负）。"""
    payload = _payload(["rag"], [_comment("rag", missed=["切片粒度", "召回评估"], qid="q_1")])
    searcher = FakeSearcher({"rag": [_row("q_new")]})

    await recommend.recommend_for_report(payload, searcher=searcher, db_path=Path("x"))

    assert searcher.calls[0]["rerank"] is False


async def test_过滤已问题目_保序并截到k(attach):
    payload = _payload(["rag"], [_comment("rag", qid="q_old")])
    hits = [_row("q_old"), _row("q_a"), _row("q_b"), _row("q_c"), _row("q_d")]
    searcher = FakeSearcher({"rag": hits})

    groups = await recommend.recommend_for_report(payload, k=3, searcher=searcher, db_path=Path("x"))

    assert [card["question_id"] for card in groups[0]["cards"]] == ["q_a", "q_b", "q_c"]
    assert groups[0]["status"] == "ok"


async def test_分组三态_有卡片_全练过_无题(attach):
    """空分组不静默隐藏：`exhausted`（该域题都问过）与 `empty`（题库里没题）分开。"""
    payload = _payload(
        ["rag", "memory", "tool-use"],
        [_comment("rag", qid="q_1"), _comment("memory", qid="q_2")],
    )
    searcher = FakeSearcher({
        "rag": [_row("q_1")],        # 命中全是已问 → 过滤后空
        "memory": [],                # 该域没有命中
        "tool-use": [_row("q_new", "tool-use")],
    })

    groups = await recommend.recommend_for_report(payload, searcher=searcher, db_path=Path("x"))

    assert [g["status"] for g in groups] == ["exhausted", "empty", "ok"]
    assert groups[0]["cards"] == [] and groups[1]["cards"] == []


async def test_分组顺序同短板顺序(attach):
    """gather 并发但结果保序——分组顺序即报告里短板域的展示顺序。"""
    payload = _payload(["memory", "rag", "tool-use"], [])
    searcher = FakeSearcher({d: [_row(f"q_{d}", d)] for d in ("rag", "memory", "tool-use")})

    groups = await recommend.recommend_for_report(payload, searcher=searcher, db_path=Path("x"))

    assert [g["domain"] for g in groups] == ["memory", "rag", "tool-use"]


async def test_无短板不检索(attach):
    payload = _payload([], [_comment("rag", qid="q_1")])
    searcher = FakeSearcher({"rag": [_row("q_1")]})

    assert await recommend.recommend_for_report(payload, searcher=searcher, db_path=Path("x")) == []
    assert searcher.calls == []


async def test_卡片挂来源明细(attach):
    payload = _payload(["rag"], [])
    searcher = FakeSearcher({"rag": [_row("q_a")]})

    groups = await recommend.recommend_for_report(payload, searcher=searcher, db_path=Path("x"))

    assert groups[0]["cards"][0]["sources"][0]["license"] == "MIT"
