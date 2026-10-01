"""检索指标纯函数单测（P1-M12 会话 1）。

金标口径在这里钉死（golden 文件头同口径）：
- **分级相关度** 0/1/2，NDCG 增益 = 2^g − 1（直接相关的权重是部分相关的 3 倍）；
- **二值相关集** = grade ≥ 1（Recall/MRR 用）；
- **池内口径**：池外题目视作 0，指标只对池内标注负责（跨版本可比，不是绝对召回率）。

退化输入（无任何相关项）返回 0.0 而不是抛错——golden 校验会把这类 query 拦在入口，
指标函数只保证「不炸」；这条行为有单测，免得日后被当成 bug 改掉。
真栈基线由 `scripts/eval_retrieval_run.py` 产出（真 Qdrant/嵌入/rerank，不进 pytest）。
"""

from __future__ import annotations

import math

import pytest

from evals.retrieval_metrics import (
    aggregate,
    evaluate,
    ndcg_at_k,
    precision_at_k,
    recall_at_k,
)


class TestRecallAtK:
    def test_全命中为1(self):
        assert recall_at_k(["a", "b", "c"], {"a", "b"}, 3) == 1.0

    def test_按_k_截断(self):
        # 相关项 b 在 rank 2，k=1 时取不到 → 0
        assert recall_at_k(["a", "b"], {"b"}, 1) == 0.0
        assert recall_at_k(["a", "b"], {"b"}, 2) == 1.0

    def test_部分命中按比例(self):
        assert recall_at_k(["a", "x", "b"], {"a", "b", "c", "d"}, 3) == 0.5

    def test_空相关集返回0而非抛错(self):
        assert recall_at_k(["a", "b"], set(), 2) == 0.0


class TestNdcgAtK:
    def test_理想排序为1(self):
        grades = {"a": 2, "b": 1}
        assert ndcg_at_k(["a", "b"], grades, 10) == pytest.approx(1.0)

    def test_分级增益_部分相关在前分更低(self):
        # IDCG@10 = 3/log2(2) + 1/log2(3)；实际 = 1/log2(2) + 3/log2(3)
        grades = {"a": 2, "b": 1}
        ideal = 3 / math.log2(2) + 1 / math.log2(3)
        actual = 1 / math.log2(2) + 3 / math.log2(3)
        assert ndcg_at_k(["b", "a"], grades, 10) == pytest.approx(actual / ideal)

    def test_理想排序按_k_截断(self):
        # k=1 时 IDCG 只看最高增益那一项（grade 2 → 3）→ 首条是 grade 2 才满分
        grades = {"a": 2, "b": 1}
        assert ndcg_at_k(["a"], grades, 1) == pytest.approx(1.0)
        assert ndcg_at_k(["b"], grades, 1) == pytest.approx(1 / 3)

    def test_池外题目视作0(self):
        grades = {"a": 2}
        assert ndcg_at_k(["z", "a"], grades, 10) < 1.0

    def test_空相关集返回0而非抛错(self):
        assert ndcg_at_k(["a"], {}, 10) == 0.0


class TestPrecisionAtK:
    def test_全相关为1(self):
        assert precision_at_k(["a", "b"], {"a", "b"}, 2) == 1.0

    def test_半数相关(self):
        assert precision_at_k(["a", "x"], {"a"}, 2) == 0.5

    def test_前k条都不相关为0(self):
        assert precision_at_k(["x", "y", "a"], {"a"}, 2) == 0.0

    def test_分母是k不是命中数(self):
        # 返回不足 k 条时仍除以 k——否则「少返回」会被算成高精度
        assert precision_at_k(["a"], {"a"}, 5) == pytest.approx(1 / 5)


class TestEvaluate:
    def test_指标键形状(self):
        row = evaluate(["a", "b"], {"a": 2}, k_values=(5, 10))
        assert set(row) == {
            "ndcg@5", "recall@5", "precision@5",
            "ndcg@10", "recall@10", "precision@10",
        }

    def test_与单项函数一致(self):
        ranked, grades = ["x", "a", "b"], {"a": 2, "b": 1}
        row = evaluate(ranked, grades, k_values=(3,))
        assert row["ndcg@3"] == pytest.approx(ndcg_at_k(ranked, grades, 3))
        assert row["recall@3"] == pytest.approx(recall_at_k(ranked, {"a", "b"}, 3))
        assert row["precision@3"] == pytest.approx(precision_at_k(ranked, {"a", "b"}, 3))


class TestAggregate:
    def test_逐指标取均值(self):
        rows = [{"ndcg@5": 1.0, "precision@5": 1.0}, {"ndcg@5": 0.0, "precision@5": 0.5}]
        assert aggregate(rows) == {"ndcg@5": 0.5, "precision@5": 0.75}

    def test_空组返回空字典_不产出0(self):
        # 空组与「全是 0 分的组」必须分得开：前者是没数据，后者是确实差
        assert aggregate([]) == {}

    def test_列顺序稳定(self):
        assert list(aggregate([{"b": 1.0, "a": 2.0}])) == ["b", "a"]
