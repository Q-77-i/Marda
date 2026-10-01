"""检索指标纯函数（P1-M12 会话 1）：Recall@k / NDCG@k / Precision@k。

三条口径（golden 文件头与报告里同口径复述）：

1. **分级相关度 0/1/2**，NDCG 增益取 `2^g − 1`（0/1/3）——「直接相关」的权重是
   「部分相关」的 3 倍，比线性增益更贴「用户要的是能补上这个漏点的那道题」。
2. **二值相关集** = grade ≥ 1，供 Recall / Precision 使用。
3. **池内口径**：golden 只标注候选池内的题，池外一律视作 grade 0。所以这里的
   Recall 是「池内 Recall」——跨版本可比（同一批标注、同一个池口径），**不能当
   绝对召回率读**。池的构建见 `retrieve.build_pool`。

退化输入（一条 query 没有任何相关项）在入口被 golden 校验拦掉（要求 ≥1 条 grade ≥ 1）；
指标函数对这种输入返回 0.0 而不是抛错，保证批量评测中途不会因一条脏数据整体失败。
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence

# NDCG 增益表：2^g − 1。golden 校验保证 g ∈ {0,1,2}，故直接索引（未知档位应炸而不是静默算 0）
GAIN: dict[int, float] = {0: 0.0, 1: 1.0, 2: 3.0}

RELEVANT_GRADE = 1  # ≥ 该档计入二值相关集

DEFAULT_K_VALUES: tuple[int, ...] = (5, 10)


def _dcg(grades: Sequence[int]) -> float:
    """DCG = Σ (2^g − 1) / log2(rank + 1)，rank 从 1 起（log2(1+1) = 1，不额外折价首条）。"""
    return sum(GAIN[g] / math.log2(rank + 1) for rank, g in enumerate(grades, start=1))


def ndcg_at_k(ranked_ids: Sequence[str], grades: Mapping[str, int], k: int) -> float:
    """NDCG@k：理想排序（IDCG）由**标注全集**按增益降序推出、截到 k。

    注意 IDCG 截断的是全集排序的前 k 条，不是「检索结果里出现过的那些」——
    否则漏检越多 IDCG 越小、NDCG 反而虚高。
    """
    actual = _dcg([grades.get(qid, 0) for qid in ranked_ids[:k]])
    ideal = _dcg(sorted(grades.values(), reverse=True)[:k])
    return actual / ideal if ideal > 0 else 0.0


def recall_at_k(ranked_ids: Sequence[str], relevant_ids: set[str], k: int) -> float:
    """二值 Recall@k = |相关 ∩ 前 k| / |相关|。相关集为空 → 0.0（见模块头）。"""
    if not relevant_ids:
        return 0.0
    return len(set(ranked_ids[:k]) & relevant_ids) / len(relevant_ids)


def precision_at_k(ranked_ids: Sequence[str], relevant_ids: set[str], k: int) -> float:
    """二值 Precision@k = |相关 ∩ 前 k| / k。

    本库同题多（一条查询常有几十条相关候选），Recall 只说明「捞回多少」，
    Precision 才说明「前 k 条里有没有混进不相关的」——两者一起看才是全貌。
    """
    if k <= 0:
        return 0.0
    return len(set(ranked_ids[:k]) & relevant_ids) / k


def evaluate(
    ranked_ids: Sequence[str],
    grades: Mapping[str, int],
    *,
    k_values: Sequence[int] = DEFAULT_K_VALUES,
) -> dict[str, float]:
    """一条 query 的指标集，键形如 `ndcg@5` / `recall@10` / `precision@5`。"""
    relevant = {qid for qid, grade in grades.items() if grade >= RELEVANT_GRADE}
    row: dict[str, float] = {}
    for k in k_values:
        row[f"ndcg@{k}"] = ndcg_at_k(ranked_ids, grades, k)
        row[f"recall@{k}"] = recall_at_k(ranked_ids, relevant, k)
        row[f"precision@{k}"] = precision_at_k(ranked_ids, relevant, k)
    return row


def aggregate(rows: Sequence[Mapping[str, float]]) -> dict[str, float]:
    """逐指标取均值；空组返回 `{}`——「没有数据」与「指标全 0」在报告里必须分得开。"""
    if not rows:
        return {}
    keys = list(rows[0])
    return {key: sum(row[key] for row in rows) / len(rows) for key in keys}
