"""评分一致性/准确性指标纯函数（P1-M12 会话 2）。

口径（用户拍板 ⑤）：**先量噪声地板**（同题同答 K 次的评分波动），再看 vs 基准的 MAE——
「同一回答评两次差多少」是尺子（决定 MAE 到多少才算超容忍），两者不混读。
本文件钉住指标本身的语义（分母、边界、缺数据时的 None 而非 0）。
"""

from __future__ import annotations

import pytest

from evals.judge_metrics import (
    accuracy_report,
    consistency_report,
    item_accuracy,
    item_consistency,
    monotonicity_report,
)

DIMS = ("technical_depth", "fundamentals", "project_experience", "communication", "problem_solving")


def _item(*, iid="i1", arm="real", interview_type="tech", key_points=("a", "b", "c"), expected=3, covered=(0, 1)):
    return {
        "id": iid,
        "arm": arm,
        "interview_type": interview_type,
        "question": "题",
        "key_points": list(key_points),
        "answer": "答",
        "expected": {"dims": {d: expected for d in DIMS}, "covered_indexes": list(covered), "note": ""},
    }


def _run(*, score=3, covered=("a", "b"), missed=("c",), error=False, dims=None):
    return {
        "dims": dims or {d: score for d in DIMS},
        "covered": list(covered),
        "missed": list(missed),
        "error_flag": error,
    }


# ---- 一致性（噪声地板） ----


def test_完全一致时波动为零():
    item = _item()
    runs = [_run(score=4) for _ in range(5)]
    got = item_consistency(item, runs)
    assert got["overall_std"] == 0.0
    assert got["overall_range"] == 0.0
    assert got["exact_match"] is True
    assert got["coverage_jaccard"] == 1.0
    assert got["error_flag_flip"] is False


def test_波动用样本标准差():
    item = _item()
    runs = [_run(score=3), _run(score=4)]  # 3、4 → 样本标准差 ≈ 0.7071
    got = item_consistency(item, runs)
    assert got["overall_mean"] == pytest.approx(3.5)
    assert got["overall_std"] == pytest.approx(0.7071, abs=1e-4)
    assert got["overall_range"] == pytest.approx(1.0)
    assert got["exact_match"] is False


def test_单次运行标准差记零而不是报错():
    """K=1 只有噪声地板之外的用途（跑冒烟），不该因除零炸掉。"""
    got = item_consistency(_item(), [_run()])
    assert got["overall_std"] == 0.0


def test_覆盖率_jaccard_取两两均值():
    # {a,b} vs {b,c} vs {a,b}：pair(1/3、1、1/3) → 均值 5/9
    runs = [_run(covered=("a", "b"), missed=("c",)), _run(covered=("b", "c"), missed=("a",)), _run(covered=("a", "b"), missed=("c",))]
    got = item_consistency(_item(), runs)
    assert got["coverage_jaccard"] == pytest.approx(5 / 9, abs=1e-4)


def test_两边都没覆盖任何点算一致():
    """Jaccard(∅,∅) 取 1：两轮都判「一个关键点没答到」本身是稳定判定（空≠分歧）。"""
    runs = [_run(covered=(), missed=("a", "b", "c")) for _ in range(2)]
    assert item_consistency(_item(), runs)["coverage_jaccard"] == 1.0


def test_无关键点跳过覆盖率而不算零():
    runs = [_run(covered=(), missed=()) for _ in range(3)]
    got = item_consistency(_item(key_points=()), runs)
    assert got["coverage_jaccard"] is None
    assert got["coverage_ratio_std"] is None


def test_error_flag_翻转计入():
    runs = [_run(error=False), _run(error=True), _run(error=False)]
    assert item_consistency(_item(), runs)["error_flag_flip"] is True


def test_评分官返回关键点表外的点被计数():
    """不在 key_points 里的「覆盖」是评分官编的点（或原文回显失配），单独亮出来。"""
    runs = [_run(covered=("a", "b", "编的点"), missed=("c",))]
    assert item_consistency(_item(), runs)["unmatched_key_points"] == 1


def test_覆盖率比例波动独立于集合():
    # 两轮都是「覆盖 2 个」，但一个漏点少一个：比例 2/3 vs 2/4 → 样本标准差 ≈ 0.1179
    runs = [_run(covered=("a", "b"), missed=("c",)), _run(covered=("a", "b"), missed=("c", "d"))]
    got = item_consistency(_item(), runs)
    assert got["coverage_ratio_std"] == pytest.approx(0.1179, abs=1e-3)


def test_一致性汇总取均值与最差项():
    items = [_item(iid="i1"), _item(iid="i2")]
    runs = {
        "i1": [_run(score=3), _run(score=3)],  # std 0
        "i2": [_run(score=3), _run(score=4)],  # std 0.7071
    }
    got = consistency_report(items, runs)
    assert got["n_items"] == 2
    assert got["mean_overall_std"] == pytest.approx(0.3536, abs=1e-4)
    assert got["max_overall_std"] == pytest.approx(0.7071, abs=1e-4)
    assert got["worst_item"] == "i2"
    assert got["exact_match_rate"] == 0.5
    assert got["runs_per_item"] == 2


# ---- 准确性（vs 基准） ----


def test_mae_与偏置符号相反():
    item = _item(expected=3)
    runs = [_run(score=4) for _ in range(4)]  # 恒高 1 分
    got = item_accuracy(item, runs)
    assert got["abs_err"] == pytest.approx(1.0)
    assert got["signed_err"] == pytest.approx(1.0)  # 正 = 评分官偏高（放水方向）
    assert got["dim_hit_rate"] == 0.0


def test_维度命中率按正负零点五取整():
    item = _item(expected=3)
    runs = [_run(score=3) for _ in range(3)] + [_run(score=4) for _ in range(2)]  # 均值 3.4
    got = item_accuracy(item, runs)
    assert got["dim_hit_rate"] == 1.0  # |3.4 − 3| ≤ 0.5
    assert got["abs_err"] == pytest.approx(0.4, abs=1e-4)


def test_覆盖率_f1_用索引集比对():
    item = _item(covered=(0, 2))  # 期望覆盖 a、c
    runs = [_run(covered=("a",), missed=("b", "c"))]  # 只覆盖 a：P=1、R=0.5
    got = item_accuracy(item, runs)
    assert got["coverage_precision"] == pytest.approx(1.0)
    assert got["coverage_recall"] == pytest.approx(0.5)
    assert got["coverage_f1"] == pytest.approx(2 / 3, abs=1e-4)


def test_覆盖率匹配按关键点原文精确比对():
    item = _item(key_points=("a", "b", "c"), covered=(0, 1))
    runs = [_run(covered=("a", "b"), missed=("c",))]  # 完美命中
    got = item_accuracy(item, runs)
    assert got["coverage_f1"] == 1.0


def test_无关键点时覆盖率指标为_None():
    item = _item(key_points=(), covered=())
    got = item_accuracy(item, [_run(covered=(), missed=())])
    assert got["coverage_f1"] is None
    assert got["coverage_ratio_err"] is None


def test_覆盖率比例口径不受关键点改写影响():
    """评分官把长要点写短（实测行为）会让集合口径低估覆盖率，比例口径不受影响。"""
    item = _item(key_points=("第一条要点带一个从句，后半句是关键条件", "第二条要点"), covered=(0, 1))
    # 评分官两边都答到了，但第一条按自己的话写短了 → 集合口径对不上、比例口径仍然全对
    runs = [_run(covered=("第一条要点带一个从句", "第二条要点"), missed=()) for _ in range(3)]
    got = item_accuracy(item, runs)
    assert got["coverage_f1"] < 1.0  # 集合口径被改写拖低
    assert got["expected_coverage_ratio"] == 1.0
    assert got["coverage_ratio"] == 1.0
    assert got["coverage_ratio_err"] == 0.0


def test_覆盖率比例误差按绝对差聚合():
    item = _item(key_points=("a", "b", "c", "d"), covered=(0, 1))  # 期望 0.5
    runs = [_run(covered=("a",), missed=("b", "c", "d")) for _ in range(2)]  # 实评 0.25
    got = item_accuracy(item, runs)
    assert got["coverage_ratio_err"] == pytest.approx(0.25)
    assert accuracy_report([item], {"i1": runs})["coverage_ratio_mae"] == pytest.approx(0.25)


def test_准确性汇总在臂内取均值():
    items = [_item(iid="i1", expected=3), _item(iid="i2", arm="persona", expected=5)]
    runs = {"i1": [_run(score=3)], "i2": [_run(score=3)]}  # i1 完美、i2 差 2 分
    got = accuracy_report(items, runs, arm="real")
    assert got["n_items"] == 1
    assert got["mae_overall"] == pytest.approx(0.0)
    assert got["per_dim_mae"]["technical_depth"] == pytest.approx(0.0)
    full = accuracy_report(items, runs)
    assert full["n_items"] == 2
    assert full["mae_overall"] == pytest.approx(1.0)  # (0 + 2) / 2


# ---- 三档单调性（persona 臂，同一道题的弱/中/强） ----


def _persona_group():
    return [
        {**_item(iid="p-w", arm="persona"), "group": "g1", "tier": "weak"},
        {**_item(iid="p-m", arm="persona"), "group": "g1", "tier": "medium"},
        {**_item(iid="p-s", arm="persona"), "group": "g1", "tier": "strong"},
    ]


def test_三档单调通过并入组间差():
    items = _persona_group()
    runs = {
        "p-w": [_run(score=2)],
        "p-m": [_run(score=3)],
        "p-s": [_run(score=5)],
    }
    got = monotonicity_report(items, runs)
    assert len(got) == 1
    assert got[0]["group"] == "g1"
    assert got[0]["monotone"] is True
    assert got[0]["gaps"] == [pytest.approx(1.0), pytest.approx(2.0)]


def test_三档不单调判失败并保留档位分数():
    items = _persona_group()
    runs = {"p-w": [_run(score=3)], "p-m": [_run(score=2)], "p-s": [_run(score=4)]}
    got = monotonicity_report(items, runs)
    assert got[0]["monotone"] is False
    assert got[0]["tiers"] == {"weak": 3.0, "medium": 2.0, "strong": 4.0}


def test_每档取多次运行的中位数():
    """中位数抗噪：5 次里 1 次异常偏高不该翻转档位判定。"""
    items = _persona_group()
    runs = {
        "p-w": [_run(score=2)] * 4 + [_run(score=5)],
        "p-m": [_run(score=3)] * 5,
        "p-s": [_run(score=4)] * 5,
    }
    got = monotonicity_report(items, runs)
    assert got[0]["tiers"]["weak"] == 2.0
    assert got[0]["monotone"] is True


def test_组内缺档位不参与也不报错():
    items = _persona_group()[:2]  # 只有 weak + medium
    got = monotonicity_report(items, {"p-w": [_run(score=2)], "p-m": [_run(score=3)]})
    assert got[0]["complete"] is False
    assert got[0]["monotone"] is True  # 已知两档有序
