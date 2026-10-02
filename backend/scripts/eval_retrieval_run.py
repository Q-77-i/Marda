"""P1-M12 会话 1：检索基线（四变体 × golden 全量）。

用法：cd backend && uv run python scripts/eval_retrieval_run.py [--label 说明]

读 `data/eval/golden/retrieval_queries.json`（人工复核后的正式基准）→ 逐条**实时重算**
四变体排名（不读快照——快照只是复核时的上下文，读了就测不出「改动之后」）→ 出指标 →
写 `data/eval/results/retrieval-<日期>[-<label>].{json,md}`。

**怎么用**：重嵌、换 rerank 模型、改检索参数之后重跑同一条命令，对比两份 json 的
`overall`；指标掉了就是回归。报告里的「rerank 净贡献」「融合 vs 单路」是把 M3 会话 2
的定性结论（rerank 改判 5/7）量化后的持续观测点。

**跨版本可比的前提**是同一份 golden：结果文件记下 golden 的内容哈希，哈希不同就只能
各自读、不能直接比——换 golden = 换基准。
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings
from app.tools.question_search import fetch_by_ids
from app.tools.recommend import build_query_items
from evals import corpus, golden as golden_mod
from evals import retrieval_metrics, retrieve

REPO = Path(__file__).resolve().parents[2]
GOLDEN_PATH = REPO / "data" / "eval" / "golden" / "retrieval_queries.json"
RESULT_DIR = REPO / "data" / "eval" / "results"
K_VALUES = (5, 10)
CONCURRENCY = 4
BOTTOM_N = 5  # 人读报告里列最差的几条 query


def long_query_ids(doc: dict, db_path) -> set[str]:
    """missed_point 条目 → 该走「不 rerank」的 id 集合（P2-M2 生产口径）。

    判据与线上一致：**该查询在当前真库报告里派生出非空漏点列表**（= 多漏点长查询）→
    跳过 rerank。现场从真库报告经**生产函数** `build_query_items` 派生（golden 文件不动 →
    哈希不变 → 与旧结果严格可比）；**查不到即非零退出**——静默按默认口径跑会测不出修复。
    """
    with_missed = {
        item["query"]
        for row in corpus.tech_report_payloads(db_path)
        for item in build_query_items(row["payload"])
        if item["missed"]
    }
    out: set[str] = set()
    for item in doc["queries"]:
        if item["scene"] != "missed_point":
            continue
        if item["query"] not in with_missed:
            raise SystemExit(
                f"golden {item['id']} 的查询在真库报告里找不到漏点（报告被改过？）——拒绝静默降级"
            )
        out.add(item["id"])
    return out


async def _one(item: dict, sem: asyncio.Semaphore, no_rerank: set[str]) -> dict[str, Any]:
    async with sem:
        variants = await retrieve.rank_variants(
            item["query"], filters=item.get("filters"), rerank=item["id"] not in no_rerank,
        )
    return {
        "id": item["id"],
        "scene": item["scene"],
        "domain": item.get("domain"),
        "query": item["query"],
        "top3": variants["hybrid"][:3],  # 人读报告的「检索 top-3」列（排名不再重算）
        "variants": {
            name: retrieval_metrics.evaluate(ranked, item["grades"], k_values=K_VALUES)
            for name, ranked in variants.items()
        },
    }


def _group(rows: list[dict], key: str) -> dict[str, dict[str, dict[str, float]]]:
    """按 `key`（scene / domain）分组后逐变体聚合。"""
    buckets: dict[str, list[dict]] = {}
    for row in rows:
        buckets.setdefault(str(row[key] or "未知"), []).append(row)
    return {
        name: {
            variant: retrieval_metrics.aggregate([row["variants"][variant] for row in members])
            for variant in retrieve.VARIANTS
        }
        for name, members in sorted(buckets.items())
    }


def _delta(a: dict[str, float], b: dict[str, float]) -> dict[str, float]:
    """a − b（逐指标），用于「rerank 净贡献」「融合 vs 单路」这类差值行。"""
    return {key: a[key] - b[key] for key in a}


def _fmt(value: float) -> str:
    return f"{value:.3f}"


def _table(rows: list[tuple[str, dict[str, dict[str, float]]]]) -> list[str]:
    metrics = [f"{name}@{k}" for k in K_VALUES for name in ("ndcg", "recall", "precision")]
    out = [
        "| 组 | 变体 | " + " | ".join(metrics) + " |",
        "| - | - | " + " | ".join("-" for _ in metrics) + " |",
    ]
    for label, per_variant in rows:
        for variant in retrieve.VARIANTS:
            values = per_variant.get(variant) or {}
            out.append(
                f"| {label} | {variant} | "
                + " | ".join(_fmt(values.get(m, 0.0)) for m in metrics)
                + " |"
            )
    return out


def _render_md(report: dict) -> str:
    overall = report["overall"]
    out = [
        "# 检索基线",
        "",
        f"生成：{report['created']}　|　golden：{report['golden']['created']}"
        f"（{report['golden']['queries']} 条 query，sha256 `{report['golden']['sha256'][:12]}`）",
        "",
        "> 池内口径：池外题目视作不相关。跨版本可比的前提是**同一份 golden**（上文哈希一致）。",
        "",
        "## 总体（全部 query 平均）",
        "",
        *_table([("全部", overall)]),
        "",
        "### 变体差值（谁贡献了什么，NDCG@5）",
        "",
        "| 组 | rerank 净贡献（hybrid−rrf） | 融合净贡献（rrf−单路最优） | sparse−dense |",
        "| - | - | - | - |",
    ]
    for label, per in [("全部", overall), *report["by_scene"].items()]:
        out.append(
            f"| {label} | {_fmt(_delta(per['hybrid'], per['rrf'])['ndcg@5'])} "
            f"| {_fmt(max(_delta(per['rrf'], per['dense'])['ndcg@5'], _delta(per['rrf'], per['sparse'])['ndcg@5']))} "
            f"| {_fmt(_delta(per['sparse'], per['dense'])['ndcg@5'])} |"
        )
    out += [
        "",
        "> 读法：净贡献为负 = 这一步把排序改差了。按场景分行是刻意的——**查询长度是 "
        "rerank 效果的主要变量**（missed_point 的查询是「域名标签 + 多个漏点」拼起来的长文本，"
        "bank_search 是十几个字的短查询），总体均值会把两者抵消掉。",
        "> 注：这里只陈述数，不写结论——结论随每次运行的数据变，写死在模板里会过期。",
        "> 噪声地板：同 golden 连跑两次，dense/sparse 逐位一致、rrf ±0.003、hybrid ±0.005"
        "（rerank 是远程 API，分数非确定）——**小于 0.005 的差值不要当结论读**。",
        "",
        "## 按场景",
        "",
        *_table([(name, per) for name, per in report["by_scene"].items()]),
        "",
        "## 按知识域",
        "",
        *_table([(name, per) for name, per in report["by_domain"].items()]),
        "",
        f"## 最差的 {BOTTOM_N} 条（按 hybrid NDCG@5）",
        "",
        "| query | 场景 | 域 | NDCG@5 | NDCG@10 | 检索 top-3 |",
        "| - | - | - | - | - | - |",
    ]
    for row in report["bottom"]:
        out.append(
            f"| {row['query'][:34]}… | {row['scene']} | {row['domain'] or '-'} "
            f"| {_fmt(row['variants']['hybrid']['ndcg@5'])} | {_fmt(row['variants']['hybrid']['ndcg@10'])} "
            f"| {'／'.join(t[:16] for t in row['top3'])} |"
        )
    out.append("")
    return "\n".join(out)


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--label", default="", help="结果文件名后缀（如 before-rerank）")
    args = parser.parse_args()

    doc = golden_mod.load(GOLDEN_PATH)
    digest = hashlib.sha256(GOLDEN_PATH.read_bytes()).hexdigest()
    no_rerank = await asyncio.to_thread(long_query_ids, doc, get_settings().db_path)
    print(f"长查询口径：{len(no_rerank)} 条 missed_point 查询跳过 rerank（真库报告派生，与线上同判据）")
    sem = asyncio.Semaphore(CONCURRENCY)
    rows = list(await asyncio.gather(*[_one(item, sem, no_rerank) for item in doc["queries"]]))

    texts = await asyncio.to_thread(
        fetch_by_ids, get_settings().db_path, [qid for row in rows for qid in row["top3"]]
    )
    titles = {row["question_id"]: row["question"] for row in texts}
    for row in rows:
        row["top3"] = [titles.get(qid, qid) for qid in row["top3"]]

    report = {
        "created": datetime.now().astimezone().isoformat(timespec="seconds"),
        "golden": {
            "path": str(GOLDEN_PATH.relative_to(REPO)),
            "created": doc["created"],
            "queries": len(doc["queries"]),
            "sha256": digest,
        },
        "k_values": list(K_VALUES),
        "overall": {
            variant: retrieval_metrics.aggregate([row["variants"][variant] for row in rows])
            for variant in retrieve.VARIANTS
        },
        "by_scene": _group(rows, "scene"),
        "by_domain": _group(rows, "domain"),
        "per_query": [
            {k: row[k] for k in ("id", "scene", "domain", "query", "variants")} for row in rows
        ],
        "bottom": sorted(rows, key=lambda r: r["variants"]["hybrid"]["ndcg@5"])[:BOTTOM_N],
    }

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    stem = f"retrieval-{stamp}" + (f"-{args.label}" if args.label else "")
    RESULT_DIR.mkdir(parents=True, exist_ok=True)
    (RESULT_DIR / f"{stem}.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (RESULT_DIR / f"{stem}.md").write_text(_render_md(report), encoding="utf-8")

    print(f"golden {report['golden']['queries']} 条 · 哈希 {digest[:12]}")
    for variant in retrieve.VARIANTS:
        values = report["overall"][variant]
        print(
            f"  {variant:7s} NDCG@5 {_fmt(values['ndcg@5'])} · NDCG@10 {_fmt(values['ndcg@10'])}"
            f" · Recall@5 {_fmt(values['recall@5'])} · Recall@10 {_fmt(values['recall@10'])}"
            f" · P@5 {_fmt(values['precision@5'])}"
        )
    print(f"结果：{RESULT_DIR / stem}.md")


if __name__ == "__main__":
    asyncio.run(main())
