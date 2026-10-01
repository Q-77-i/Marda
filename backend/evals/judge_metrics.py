"""评分一致性/准确性指标（P1-M12 会话 2）：**纯函数**，不碰网络与库。

读法（用户拍板口径）：**先看噪声地板**——`consistency_report` 量的是「同一道题、
同一份回答，重复评 K 次，分数自己晃多少」。它是尺子：`accuracy_report` 的 MAE
小于这个晃动量时，**分不出是评分官偏了还是它本来就这么晃**。两者顺序读，不混谈。

数据形状（都可 JSON 序列化，便于落盘与复算）：

- item：golden 里的一条样本（`question` / `key_points` / `expected`）——见 `judge_golden.validate`；
- run：一次评分调用的结果 `{"dims": {维: 1-5 整数}, "covered": [str], "missed": [str],
  "error_flag": bool}`——由 `judge_run.score_to_record` 从评分模型转来。

`covered` / `missed` 是**关键点原文**（评分官按 rubric 逐条判定），与 `key_points`
逐字比对失败的点计进 `unmatched_key_points`（评分官编的点或回显失配，标称不静默）。
"""

from __future__ import annotations

import statistics
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

TIER_ORDER: tuple[str, ...] = ("weak", "medium", "strong")
"""persona 臂的三档（同一道题的三种回答质量），单调性按此序检查。"""

HIT_TOLERANCE = 0.5
"""维度「命中」容差：多次运行均值和期望分之差 ≤ 0.5 即算命中（即四舍五入到期望分）。"""


def _std(values: Sequence[float]) -> float:
    """样本标准差（n−1）；单次运行记 0（没有波动可言，不是错误）。"""
    return statistics.stdev(values) if len(values) >= 2 else 0.0


def _mean(values: Sequence[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _overall(dims: Mapping[str, int]) -> float:
    return _mean(list(dims.values()))


def _jaccard(a: set[str], b: set[str]) -> float:
    """两轮覆盖集合的 Jaccard；两边都空取 1（都判「一个没答到」是稳定判定，非分歧）。"""
    if not a and not b:
        return 1.0
    return len(a & b) / len(a | b)


def _covered_indexes(run: Mapping[str, Any], key_points: Sequence[str]) -> tuple[set[int], int]:
    """评分官返回的 covered 原文 → key_points 下标集合；不在表内的点单独计数。

    逐字比对（去首尾空白）：评分官被要求「必须从给定 key_points 中逐条判定」，
    返回的点本该是原文；对不上就说明它自己造了点，这个数不该被悄悄吞掉。
    """
    index_of = {point.strip(): i for i, point in enumerate(key_points)}
    matched: set[int] = set()
    unmatched = 0
    for point in run.get("covered") or []:
        key = str(point).strip()
        if key in index_of:
            matched.add(index_of[key])
        else:
            unmatched += 1
    return matched, unmatched


# ---- 一致性（噪声地板） ----


def item_consistency(item: dict, runs: Sequence[dict]) -> dict:
    """一道题 K 次运行的波动：总分/各维标准差、完全一致率、覆盖率集合稳定性。"""
    overalls = [_overall(run["dims"]) for run in runs]
    dims = sorted(runs[0]["dims"]) if runs else []
    key_points = item.get("key_points") or []

    dim_stds = {dim: _std([run["dims"][dim] for run in runs]) for dim in dims}
    exact_match = all(run["dims"] == runs[0]["dims"] for run in runs) if runs else False
    flags = {bool(run.get("error_flag")) for run in runs}
    unmatched = sum(_covered_indexes(run, key_points)[1] for run in runs)

    coverage_jaccard: float | None = None
    coverage_ratio_std: float | None = None
    if key_points:
        sets = [_covered_indexes(run, key_points)[0] for run in runs]
        pairs = [
            _jaccard(sets[i], sets[j]) for i in range(len(sets)) for j in range(i + 1, len(sets))
        ]
        coverage_jaccard = _mean(pairs) if pairs else 1.0
        # 覆盖比例（驱动追问决策的 70% 阈值）：分母为 0 的运行不计入（它没判任何点）
        ratios = [
            len(run.get("covered") or []) / total
            for run in runs
            if (total := len(run.get("covered") or []) + len(run.get("missed") or []))
        ]
        coverage_ratio_std = _std(ratios) if ratios else None

    return {
        "id": item["id"],
        "arm": item.get("arm"),
        "interview_type": item.get("interview_type"),
        "n_runs": len(runs),
        # 逐项不取整（只汇总取整）：先舍入再平均会二次舍入，0.35355 这类边界值
        # 会随机落到 0.3535/0.3536——指标要跨版本比，数字必须只由输入决定
        "overall_mean": _mean(overalls),
        "overall_std": _std(overalls),
        "overall_range": max(overalls) - min(overalls) if overalls else 0.0,
        "exact_match": exact_match,
        "dim_stds": dim_stds,
        "coverage_jaccard": coverage_jaccard,
        "coverage_ratio_std": coverage_ratio_std,
        "error_flag_flip": len(flags) > 1,
        "unmatched_key_points": unmatched,
    }


def _filtered(items: Iterable[dict], *, arm: str | None, interview_type: str | None) -> list[dict]:
    return [
        item
        for item in items
        if (arm is None or item.get("arm") == arm)
        and (interview_type is None or item.get("interview_type") == interview_type)
    ]


def consistency_report(
    items: Sequence[dict],
    runs_by_item: Mapping[str, Sequence[dict]],
    *,
    arm: str | None = None,
    interview_type: str | None = None,
) -> dict:
    """一致性的汇总（默认全量；可按臂/按类型切片）。

    `mean_overall_std` 是**噪声地板**的头号数字——报告与门禁都以它为准；
    `max_overall_std` + `worst_item` 给尾部（哪道题最不稳）。
    """
    selected = _filtered(items, arm=arm, interview_type=interview_type)
    per_item = [item_consistency(item, runs_by_item[item["id"]]) for item in selected]
    dim_std_values = [std for row in per_item for std in row["dim_stds"].values()]
    jaccards = [row["coverage_jaccard"] for row in per_item if row["coverage_jaccard"] is not None]
    ratio_stds = [row["coverage_ratio_std"] for row in per_item if row["coverage_ratio_std"] is not None]
    worst = max(per_item, key=lambda row: row["overall_std"], default=None)
    return {
        "arm": arm,
        "interview_type": interview_type,
        "n_items": len(per_item),
        "runs_per_item": min((row["n_runs"] for row in per_item), default=0),
        "mean_overall_std": round(_mean([row["overall_std"] for row in per_item]), 4),
        "max_overall_std": worst["overall_std"] if worst else 0.0,
        "worst_item": worst["id"] if worst else None,
        "mean_overall_range": round(_mean([row["overall_range"] for row in per_item]), 4),
        "exact_match_rate": round(_mean([1.0 if row["exact_match"] else 0.0 for row in per_item]), 4),
        "mean_dim_std": round(_mean(dim_std_values), 4),
        "mean_coverage_jaccard": None if not jaccards else round(_mean(jaccards), 4),
        "mean_coverage_ratio_std": None if not ratio_stds else round(_mean(ratio_stds), 4),
        "error_flag_flip_rate": round(
            _mean([1.0 if row["error_flag_flip"] else 0.0 for row in per_item]), 4
        ),
        "unmatched_key_points": sum(row["unmatched_key_points"] for row in per_item),
        "per_item": per_item,
    }


# ---- 准确性（vs golden 期望分） ----


def item_accuracy(item: dict, runs: Sequence[dict]) -> dict:
    """一道题的判分与期望之差：总分/各维的绝对误差、偏置、覆盖率两个口径。

    `signed_err` 为正 = 评分官**偏高**（放水方向），为负 = 偏低（苛刻方向）——
    只看 MAE 分不出这两种毛病，故两者都给。

    **覆盖率为什么给两个口径**：集合口径（P/R/F1）按关键点**原文精确匹配**，而评分官
    偶尔会截断/改写长要点（实测 7/185 次运行：把带从句的长要点只回前半句，或换成自己的说法），
    这类点对不上 → 集合口径**低估**。比例口径（`coverage_ratio_err`）只比「答到了几成」，
    不受改写影响，且它正是驱动 70% 追问阈值的那个量——两个都读：集合口径严格、比例口径稳。
    """
    expected_dims: dict[str, int] = item["expected"]["dims"]
    expected_overall = _overall(expected_dims)
    overall_mean = _mean([_overall(run["dims"]) for run in runs])

    dim_errs: dict[str, float] = {}
    for dim, want in expected_dims.items():
        got = _mean([run["dims"][dim] for run in runs if dim in run["dims"]])
        dim_errs[dim] = got - want
    hits = [abs(err) <= HIT_TOLERANCE for err in dim_errs.values()]

    key_points = item.get("key_points") or []
    expected_indexes = set(item["expected"].get("covered_indexes") or [])
    precision = recall = f1 = None
    expected_ratio = coverage_ratio = ratio_err = None
    if key_points:
        scores = []
        for run in runs:
            got, _ = _covered_indexes(run, key_points)
            tp = len(got & expected_indexes)
            p = tp / len(got) if got else (1.0 if not expected_indexes else 0.0)
            r = tp / len(expected_indexes) if expected_indexes else 1.0
            scores.append((p, r, 2 * p * r / (p + r) if p + r else 0.0))
        precision = _mean([s[0] for s in scores])
        recall = _mean([s[1] for s in scores])
        f1 = _mean([s[2] for s in scores])
        expected_ratio = len(expected_indexes) / len(key_points)
        judged = [run for run in runs if len(run.get("covered") or []) + len(run.get("missed") or [])]
        if judged:
            coverage_ratio = _mean([
                len(run.get("covered") or []) / (len(run.get("covered") or []) + len(run.get("missed") or []))
                for run in judged
            ])
            ratio_err = abs(coverage_ratio - expected_ratio)

    return {
        "id": item["id"],
        "arm": item.get("arm"),
        "interview_type": item.get("interview_type"),
        "expected_overall": expected_overall,
        "overall_mean": overall_mean,
        "abs_err": abs(overall_mean - expected_overall),
        "signed_err": overall_mean - expected_overall,
        "dim_errs": dim_errs,
        "dim_hit_rate": _mean([1.0 if h else 0.0 for h in hits]),
        "coverage_precision": precision,
        "coverage_recall": recall,
        "coverage_f1": f1,
        "expected_coverage_ratio": expected_ratio,
        "coverage_ratio": coverage_ratio,
        "coverage_ratio_err": ratio_err,
    }


def accuracy_report(
    items: Sequence[dict],
    runs_by_item: Mapping[str, Sequence[dict]],
    *,
    arm: str | None = None,
    interview_type: str | None = None,
) -> dict:
    """准确性的汇总（默认全量；可按臂/按类型切片）。

    **读它之前先读噪声地板**：`mae_overall` 低于 `consistency_report` 的
    `mean_overall_std` 时，误差已在评分官自身的晃动量以内。
    """
    selected = _filtered(items, arm=arm, interview_type=interview_type)
    per_item = [item_accuracy(item, runs_by_item[item["id"]]) for item in selected]
    dim_names = sorted({dim for row in per_item for dim in row["dim_errs"]})
    f1s = [row["coverage_f1"] for row in per_item if row["coverage_f1"] is not None]
    ratio_errs = [row["coverage_ratio_err"] for row in per_item if row["coverage_ratio_err"] is not None]
    return {
        "arm": arm,
        "interview_type": interview_type,
        "n_items": len(per_item),
        "mae_overall": round(_mean([row["abs_err"] for row in per_item]), 4),
        "bias_overall": round(_mean([row["signed_err"] for row in per_item]), 4),
        "max_abs_err": max((row["abs_err"] for row in per_item), default=0.0),
        "per_dim_mae": {
            dim: round(_mean([abs(row["dim_errs"].get(dim, 0.0)) for row in per_item]), 4)
            for dim in dim_names
        },
        "per_dim_bias": {
            dim: round(_mean([row["dim_errs"].get(dim, 0.0) for row in per_item]), 4)
            for dim in dim_names
        },
        "dim_hit_rate": round(_mean([row["dim_hit_rate"] for row in per_item]), 4),
        "coverage_f1": None if not f1s else round(_mean(f1s), 4),
        "coverage_ratio_mae": None if not ratio_errs else round(_mean(ratio_errs), 4),
        "per_item": per_item,
    }


# ---- 三档单调性（persona 臂：同一道题 × 弱/中/强） ----


def monotonicity_report(items: Sequence[dict], runs_by_item: Mapping[str, Sequence[dict]]) -> list[dict]:
    """每个 persona 组（同一道题的弱/中/强三答）档位是否**严格递增**。

    每档取**该题 K 次运行的中位数**（抗单次异常值）；只有两档齐备时也不判错，
    但 `complete=False` 会被亮出来（缺档的组不构成完整的区分度证据）。
    """
    groups: dict[str, list[dict]] = {}
    for item in items:
        if item.get("group"):
            groups.setdefault(item["group"], []).append(item)

    out = []
    for group, members in groups.items():
        tiers: dict[str, float] = {}
        for item in members:
            overalls = [_overall(run["dims"]) for run in runs_by_item[item["id"]]]
            if item.get("tier") in TIER_ORDER:
                tiers[item["tier"]] = statistics.median(overalls)
        ordered = [tiers[tier] for tier in TIER_ORDER if tier in tiers]
        gaps = [b - a for a, b in zip(ordered, ordered[1:])]
        out.append({
            "group": group,
            "question": members[0].get("question"),
            "tiers": tiers,
            "gaps": gaps,
            "complete": len(tiers) == len(TIER_ORDER),
            "monotone": all(gap > 0 for gap in gaps),
        })
    return out
