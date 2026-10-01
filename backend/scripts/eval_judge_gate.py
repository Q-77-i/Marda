"""P1-M12 会话 2：评分门禁（失败非零退出）。

用法：
    uv run python scripts/eval_judge_gate.py                 # 跑一遍生产臂（K=5）再判
    uv run python scripts/eval_judge_gate.py --result <file> # 判已有的结果文件（不重新调用）

**什么时候跑**：改评分 prompt、评分维度表、评分模型、温度之后——评分漂移会直接进能力曲线
（PRD §9 风险表），所以这几类改动必须过门禁。

**阈值怎么来的**：基线（`data/eval/results/judge-<…>-baseline.md`）的值 + 余量。余量不是拍的：
同一基线连跑两次，指标自身的波动量级就是余量的下界（`data/eval/results/` 里两次 baseline
的差值），取其若干倍留出安全边际。**改 golden（哈希变）= 换基准**——门禁拒绝比较，
必须重跑基线并重设下面的常量。
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.graph.nodes.judge import JUDGE_TEMPERATURE

from evals import judge_golden, judge_run

REPO = Path(__file__).resolve().parents[2]
GOLDEN_PATH = REPO / "data" / "eval" / "golden" / "judge_items.json"
RESULT_DIR = REPO / "data" / "eval" / "results"
GATE_RUNS = 5  # 必须与基线一致：σ̄ 是 K 的函数，K 变了就不能比

# ---- 基线（出处：judge-20261001-172746-baseline2.json，2026-10-01） ----
GOLDEN_SHA256 = "8d9afad76fdf96b8f47e77324ec5901e6396d9a5210ede652a753abd2983b3e2"
BASELINE = {
    "mean_overall_std": 0.1131,      # 噪声地板 σ̄
    "exact_match_rate": 0.3243,      # 只进报告、不进门禁（理由见 TOLERANCE 注释）
    "mae_overall": 0.2249,
    "bias_overall": 0.0173,
    "coverage_ratio_mae": 0.0760,
    "monotone_groups": 3,
}
# ---- 余量 ----
# 依据：同一 golden 连跑**三轮**生产臂的**指标自身波动**——
#   σ̄ 0.126 / 0.113 / 0.095 · MAE 0.227 / 0.225 / 0.209 · 偏置 +0.039 / +0.017 / +0.031。
# 余量取观测波动的 2–4 倍（给指标抽样噪声留空间），改评分口径后超出即判回归。
# **完全一致率不进检查项**：它是 37 条样本上的二值统计量，单次抽样噪声 ≈0.077
# （sqrt(0.32·0.68/37)），比「值得叫停的退化」还大——拿它设阈值只会误报。
# 它和覆盖率 Jaccard 照常进报告，读的时候带上这个噪声量级。
TOLERANCE = {
    "mean_overall_std": 0.05,
    "mae_overall": 0.05,
    "bias_overall": 0.10,
    "coverage_ratio_mae": 0.05,
}


def _load_golden() -> dict:
    digest = hashlib.sha256(GOLDEN_PATH.read_bytes()).hexdigest()
    if digest != GOLDEN_SHA256:
        raise SystemExit(
            f"golden 哈希不一致（{digest[:12]} ≠ 基线 {GOLDEN_SHA256[:12]}）：**换了基准就不能比**。\n"
            "重跑 scripts/eval_judge_run.py 建立新基线，并把本文件的 BASELINE / GOLDEN_SHA256 更新。"
        )
    return judge_golden.load(GOLDEN_PATH)


def _extract(result: dict) -> dict:
    """从结果文件里取出门禁关心的数（生产臂）。"""
    arm = result["arms"].get("production")
    if arm is None:
        raise SystemExit("结果文件里没有生产臂（production），无法判。")
    con, acc = arm["overall"]["consistency"], arm["overall"]["accuracy"]
    return {
        "mean_overall_std": con["mean_overall_std"],
        "exact_match_rate": con["exact_match_rate"],
        "mae_overall": acc["mae_overall"],
        "bias_overall": acc["bias_overall"],
        "coverage_ratio_mae": acc.get("coverage_ratio_mae"),
        "monotone_groups": sum(1 for row in arm["monotonicity"] if row["monotone"]),
        "total_groups": len(arm["monotonicity"]),
        "dropped_items": arm.get("dropped_items") or [],
        "failures": sum((arm.get("failures") or {}).values()),
    }


def _checks(got: dict) -> list[tuple[str, bool, str]]:
    """(项, 是否通过, 说明)。全部列出来再汇总——一次看清哪里坏了。"""
    out: list[tuple[str, bool, str]] = []
    std_limit = BASELINE["mean_overall_std"] + TOLERANCE["mean_overall_std"]
    out.append((
        "噪声地板 σ̄",
        got["mean_overall_std"] <= std_limit,
        f"{got['mean_overall_std']:.3f} ≤ {std_limit:.3f}（基线 {BASELINE['mean_overall_std']:.3f}）",
    ))
    mae_limit = BASELINE["mae_overall"] + TOLERANCE["mae_overall"]
    out.append((
        "总分 MAE",
        got["mae_overall"] <= mae_limit,
        f"{got['mae_overall']:.3f} ≤ {mae_limit:.3f}（基线 {BASELINE['mae_overall']:.3f}；"
        f"低于 σ̄ 时本就不构成结论）",
    ))
    bias_limit = abs(BASELINE["bias_overall"]) + TOLERANCE["bias_overall"]
    out.append((
        "偏置绝对值",
        abs(got["bias_overall"]) <= bias_limit,
        f"{abs(got['bias_overall']):.3f} ≤ {bias_limit:.3f}（基线 {BASELINE['bias_overall']:+.3f}）",
    ))
    if got["coverage_ratio_mae"] is not None and BASELINE["coverage_ratio_mae"]:
        ratio_limit = BASELINE["coverage_ratio_mae"] + TOLERANCE["coverage_ratio_mae"]
        out.append((
            "覆盖率比例误差",
            got["coverage_ratio_mae"] <= ratio_limit,
            f"{got['coverage_ratio_mae']:.3f} ≤ {ratio_limit:.3f}（基线 {BASELINE['coverage_ratio_mae']:.3f}）",
        ))
    out.append((
        "三档单调",
        got["monotone_groups"] >= BASELINE["monotone_groups"],
        f"{got['monotone_groups']}/{got['total_groups']} 组（基线 {BASELINE['monotone_groups']} 组）",
    ))
    out.append((
        "无掉出样本",
        not got["dropped_items"],
        f"{len(got['dropped_items'])} 条" + (f"：{got['dropped_items']}" if got["dropped_items"] else ""),
    ))
    out.append(("调用零失败", got["failures"] == 0, f"{got['failures']} 次"))
    return out


async def _run_fresh() -> dict:
    doc = _load_golden()
    print(f"跑生产臂（温度 {JUDGE_TEMPERATURE}，每条 {GATE_RUNS} 次）…", flush=True)
    arm = await judge_run.run_and_aggregate(
        doc["items"], runs=GATE_RUNS, temperature=JUDGE_TEMPERATURE
    )
    return {"arms": {"production": arm}}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--result", default="", help="已有结果文件（不重新调用）")
    args = parser.parse_args()

    if args.result:
        _load_golden()  # 哈希校验照旧（结果文件本身也记了哈希，这里以现场 golden 为准）
        result = json.loads(Path(args.result).read_text(encoding="utf-8"))
        recorded = (result.get("golden") or {}).get("sha256")
        if recorded and recorded != GOLDEN_SHA256:
            raise SystemExit(f"结果文件的 golden 哈希（{recorded[:12]}）与门禁基线不一致，拒绝比较。")
    else:
        result = asyncio.run(_run_fresh())

    got = _extract(result)
    checks = _checks(got)
    failed = [name for name, ok, _ in checks if not ok]
    print("\n评分门禁：")
    for name, ok, detail in checks:
        print(f"  {'✅' if ok else '❌'} {name}：{detail}")
    if failed:
        print(f"\n未通过：{'、'.join(failed)}")
        sys.exit(1)
    print("\n全部通过。")


if __name__ == "__main__":
    main()
