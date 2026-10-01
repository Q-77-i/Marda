"""池构建纯函数单测（P1-M12 会话 1）。

`rank_variants` 本身要真 Qdrant/嵌入/rerank，归 scripts 的真栈运行；这里只钉
`build_pool` 的两条口径：**截断按 POOL_LIMITS**、**并集保序去重**。
"""

from __future__ import annotations

from evals.retrieve import POOL_LIMITS, build_pool


def test_按各变体上限截断():
    variants = {"dense": [f"d{i}" for i in range(50)], "sparse": [], "rrf": [], "hybrid": []}
    assert len(build_pool(variants)) == POOL_LIMITS["dense"]


def test_并集去重且保序():
    variants = {
        "dense": ["a", "b"],
        "sparse": ["b", "c"],
        "rrf": ["c", "d"],
        "hybrid": ["d", "e"],
    }
    assert build_pool(variants) == ["a", "b", "c", "d", "e"]


def test_池比单变体大_漏检才可见():
    # hybrid 只返回 30 条，池必须能装进别的变体捞到的题，否则 Recall@30 恒 1
    variants = {
        "dense": [f"d{i}" for i in range(20)],
        "sparse": [f"s{i}" for i in range(20)],
        "rrf": [f"d{i}" for i in range(30)],
        "hybrid": [f"d{i}" for i in range(30)],
    }
    # 20（dense）+ 20（sparse 新增）+ 10（rrf 的 d20–d29）+ 0（hybrid 与 rrf 重合）= 50
    assert len(build_pool(variants)) == 50


def test_缺变体不炸():
    assert build_pool({"hybrid": ["a"]}) == ["a"]
