"""P1-M12 会话 2：评分一致性/准确性基线（生产温度 + 温度 0 对照）。

用法：cd backend && uv run python scripts/eval_judge_run.py [--label 说明] [--runs 5] [--no-control]

读 `data/eval/golden/judge_items.json` → 每条**重复评 K 次**（真 LLM）→ 出指标 →
写 `data/eval/results/judge-<时间戳>[-<label>].{json,md}`。

两条臂：
- **生产臂**：`judge.JUDGE_TEMPERATURE`（0.3）——噪声地板与准确性都以它为准；
- **对照臂**：温度 0——`--no-control` 可关。**只做实验**：要不要改生产温度是独立决策
  （影响评分/报告/PDF/能力曲线，要单独回归），本脚本不改任何生产常量。

读法与口径见 SPEC §4.13「评分一致性」节：先读噪声地板，再读 MAE——
MAE 小于评分官自己的晃动幅度时，分不出「偏了」还是「本来就晃」。
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.graph.nodes.judge import JUDGE_TEMPERATURE

from evals import judge_golden, judge_metrics, judge_run

REPO = Path(__file__).resolve().parents[2]
GOLDEN_PATH = REPO / "data" / "eval" / "golden" / "judge_items.json"
RESULT_DIR = REPO / "data" / "eval" / "results"

CONTROL_TEMPERATURE = 0.0
WORST_N = 5


async def _run_arm(items: list[dict], *, runs: int, temperature: float, label: str) -> dict:
    print(f"  跑 {label}（温度 {temperature}，每条 {runs} 次）…", flush=True)
    report = await judge_run.run_and_aggregate(items, runs=runs, temperature=temperature)
    if report["dropped_items"]:
        print(f"    ⚠️ {len(report['dropped_items'])} 条有效运行不足 2 次，未进指标：{report['dropped_items']}")
    if report["failures"]:
        print(f"    ⚠️ 调用失败 {sum(report['failures'].values())} 次（重试后仍失败），明细见结果文件")
    return report


def _fmt(value: float | None, digits: int = 3) -> str:
    return "-" if value is None else f"{value:.{digits}f}"


def _consistency_table(rows: list[tuple[str, dict]]) -> list[str]:
    out = [
        "| 切面 | σ̄（平均总分波动） | 最大波动 | 完全一致率 | 覆盖率 Jaccard | error_flag 翻转率 |",
        "| - | - | - | - | - | - |",
    ]
    for label, con in rows:
        out.append(
            f"| {label} | {_fmt(con['mean_overall_std'])} | {_fmt(con['max_overall_std'])} "
            f"| {_fmt(con['exact_match_rate'])} | {_fmt(con['mean_coverage_jaccard'])} "
            f"| {_fmt(con['error_flag_flip_rate'])} |"
        )
    return out


def _accuracy_table(rows: list[tuple[str, dict]]) -> list[str]:
    out = [
        "| 切面 | 总分 MAE | 偏置（正=偏高） | 最大误差 | 维度命中率 | 覆盖率 F1（严格） | 覆盖率比例误差 |",
        "| - | - | - | - | - | - | - |",
    ]
    for label, acc in rows:
        out.append(
            f"| {label} | {_fmt(acc['mae_overall'])} | {_fmt(acc['bias_overall'], 4)} "
            f"| {_fmt(acc['max_abs_err'])} | {_fmt(acc['dim_hit_rate'])} "
            f"| {_fmt(acc['coverage_f1'])} | {_fmt(acc.get('coverage_ratio_mae'))} |"
        )
    return out


def _render_md(report: dict) -> str:
    prod = report["arms"]["production"]
    con = prod["overall"]["consistency"]
    acc = prod["overall"]["accuracy"]
    out = [
        "# 评分一致性基线",
        "",
        f"生成：{report['created']}　|　golden：{report['golden']['created']}"
        f"（{report['golden']['items']} 条样本，sha256 `{report['golden']['sha256'][:12]}`）"
        f"　|　每条 {report['runs']} 次",
        "",
        "> 跨版本可比的前提是**同一份 golden**（哈希一致）。样本含题库原文与用户回答，"
        "按语料红线该文件与 golden 一样只留本地。",
        "",
        "## 一、噪声地板（生产温度 " + str(prod["temperature"]) + "）",
        "",
        f"同一道题、同一份回答重复评 {report['runs']} 次：**平均总分波动 σ̄ = {_fmt(con['mean_overall_std'])}**，"
        f"完全一致率 {_fmt(con['exact_match_rate'])}。",
        "",
        "**这个数先读**：它是后面的 MAE 的尺子——误差小于 σ̄ 时，分不出「评分官偏了」"
        "还是「它本来就晃这么多」。",
        "",
        *_consistency_table([("全部", con)] + [(f"arm={k}", v["consistency"]) for k, v in prod["by_arm"].items()]),
        "",
        f"- 最不稳的一条：`{con['worst_item']}`（σ = {_fmt(con['max_overall_std'])}）",
        f"- 覆盖率（驱动 70% 追问阈值的那条线）的集合稳定性 Jaccard = "
        f"{_fmt(con['mean_coverage_jaccard'])}（1 = 每次判的覆盖集合完全一样）",
        f"- **评分官未按原文返回的关键点**：{con['unmatched_key_points']} 次/共 {con['n_items'] * con['runs_per_item']} 次运行"
        f"——实测表现为**截断/改写长要点**（不是编造），集合口径因此略低估；比例误差列见下节",
        "",
        "## 二、准确性（vs 人审期望分）",
        "",
        *_accuracy_table([("全部", acc)] + [(f"arm={k}", v["accuracy"]) for k, v in prod["by_arm"].items()]),
        "",
        "### 各维 MAE / 偏置（按会话类型分开——两套五维是不同的键）",
        "",
        "> 覆盖率两个口径：**F1 严格**（按关键点原文精确匹配，评分官改写要点会被计为未覆盖 → 偏低）；"
        "**比例误差**只比「答到了几成」，不受改写影响，且它就是 70% 追问阈值的输入。",
        "",
    ]
    for itype, label in (("tech", "技术面五维"), ("behavioral", "行为面五维")):
        slice_ = prod["by_type"].get(itype)
        if not slice_:
            continue
        dims = slice_["accuracy"]["per_dim_mae"]
        out += [f"**{label}**（{slice_['accuracy']['n_items']} 条）", "", "| 维度 | MAE | 偏置（正=评分官偏高） |", "| - | - | - |"]
        for dim in dims:
            out.append(f"| {dim} | {_fmt(dims[dim])} | {_fmt(slice_['accuracy']['per_dim_bias'][dim], 4)} |")
        out.append("")
    out += ["## 三、三档单调性（同一道题的弱/中/强）", ""]
    mono = prod["monotonicity"]
    out += ["| 组 | 弱 | 中 | 强 | 档间差 | 判定 |", "| - | - | - | - | - | - |"]
    for row in mono:
        tiers = row["tiers"]
        out.append(
            f"| {row['group']} | {_fmt(tiers.get('weak'), 2)} | {_fmt(tiers.get('medium'), 2)} "
            f"| {_fmt(tiers.get('strong'), 2)} "
            f"| {'、'.join(_fmt(g, 2) for g in row['gaps']) or '-'} "
            f"| {'✅ 单调' if row['monotone'] else '❌ 不单调'}"
            f"{'' if row['complete'] else '（缺档）'} |"
        )
    passed = sum(1 for row in mono if row["monotone"])
    out += ["", f"通过 {passed}/{len(mono)} 组。", ""]

    if "control" in report["arms"]:
        ctrl = report["arms"]["control"]
        c_con = ctrl["overall"]["consistency"]
        c_acc = ctrl["overall"]["accuracy"]
        out += [
            "## 四、温度对照（0.0 实验臂——**不改生产**）",
            "",
            "| 臂 | 温度 | σ̄ | 完全一致率 | 总分 MAE | 维度命中率 |",
            "| - | - | - | - | - | - |",
            f"| 生产 | {prod['temperature']} | {_fmt(con['mean_overall_std'])} "
            f"| {_fmt(con['exact_match_rate'])} | {_fmt(acc['mae_overall'])} | {_fmt(acc['dim_hit_rate'])} |",
            f"| 对照 | {ctrl['temperature']} | {_fmt(c_con['mean_overall_std'])} "
            f"| {_fmt(c_con['exact_match_rate'])} | {_fmt(c_acc['mae_overall'])} | {_fmt(c_acc['dim_hit_rate'])} |",
            "",
            "> 只陈述数：是否改生产温度是**独立决策**（影响评分/报告/PDF/能力曲线，需单独回归），"
            "本脚本不改任何生产常量。",
            "",
        ]

    out += [f"## 五、最不稳的 {WORST_N} 条 / 误差最大的 {WORST_N} 条", ""]
    out += ["| 样本 | 臂 | σ | 总分均值 |", "| - | - | - | - |"]
    worst = sorted(con["per_item"], key=lambda r: r["overall_std"], reverse=True)[:WORST_N]
    for row in worst:
        out.append(f"| `{row['id']}` | {row['arm']} | {_fmt(row['overall_std'])} | {_fmt(row['overall_mean'], 2)} |")
    out += ["", "| 样本 | 臂 | 期望 | 实评 | 误差 |", "| - | - | - | - | - |"]
    worst_err = sorted(acc["per_item"], key=lambda r: r["abs_err"], reverse=True)[:WORST_N]
    for row in worst_err:
        out.append(
            f"| `{row['id']}` | {row['arm']} | {_fmt(row['expected_overall'], 2)} "
            f"| {_fmt(row['overall_mean'], 2)} | {_fmt(row['signed_err'], 2)} |"
        )
    out.append("")
    return "\n".join(out)


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--label", default="", help="结果文件名后缀（如 after-prompt-fix）")
    parser.add_argument("--runs", type=int, default=5, help="每条重复评分次数（默认 5）")
    parser.add_argument("--no-control", action="store_true", help="不跑温度 0 对照臂")
    parser.add_argument("--golden", default=str(GOLDEN_PATH))
    args = parser.parse_args()

    golden_path = Path(args.golden)
    doc = judge_golden.load(golden_path)
    items = doc["items"]
    runs = max(2, args.runs)
    print(f"golden {len(items)} 条（runs={runs}）：{golden_path}")

    arms = {"production": await _run_arm(items, runs=runs, temperature=JUDGE_TEMPERATURE, label="生产臂")}
    if not args.no_control:
        arms["control"] = await _run_arm(items, runs=runs, temperature=CONTROL_TEMPERATURE, label="对照臂")

    report = {
        "created": datetime.now().astimezone().isoformat(timespec="seconds"),
        "golden": {
            "path": str(golden_path.relative_to(REPO)) if REPO in golden_path.parents else str(golden_path),
            "created": doc["created"],
            "items": len(items),
            "sha256": hashlib.sha256(golden_path.read_bytes()).hexdigest(),
        },
        "runs": runs,
        "arms": arms,
    }

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    stem = f"judge-{stamp}" + (f"-{args.label}" if args.label else "")
    RESULT_DIR.mkdir(parents=True, exist_ok=True)
    (RESULT_DIR / f"{stem}.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (RESULT_DIR / f"{stem}.md").write_text(_render_md(report), encoding="utf-8")

    prod = arms["production"]["overall"]
    print(
        f"\n生产臂：σ̄ {_fmt(prod['consistency']['mean_overall_std'])}"
        f" · 完全一致率 {_fmt(prod['consistency']['exact_match_rate'])}"
        f" · 总分 MAE {_fmt(prod['accuracy']['mae_overall'])}"
        f" · 偏置 {_fmt(prod['accuracy']['bias_overall'], 4)}"
    )
    if "control" in arms:
        ctrl = arms["control"]["overall"]
        print(
            f"对照臂（温度 0）：σ̄ {_fmt(ctrl['consistency']['mean_overall_std'])}"
            f" · 总分 MAE {_fmt(ctrl['accuracy']['mae_overall'])}"
        )
    mono = arms["production"]["monotonicity"]
    print(f"三档单调：{sum(1 for row in mono if row['monotone'])}/{len(mono)} 组通过")
    print(f"结果：{RESULT_DIR / stem}.md")


if __name__ == "__main__":
    asyncio.run(main())
