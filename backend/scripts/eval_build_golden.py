"""P1-M12 会话 1：构建 golden 检索集（种子 → 候选池 → LLM 判级 → 人工复核产物）。

用法：cd backend && set -a && source ../.env && set +a && uv run python scripts/eval_build_golden.py
依赖：Qdrant + embedding 容器在跑（`docker compose up -d qdrant embedding`）、
      DEEPSEEK_API_KEY / SILICONFLOW_API_KEY（后者只在 hybrid 变体的 rerank 用到）。

产物（都在 `data/eval/golden/`）：
- `retrieval_queries.json`  **判级草稿 → 人工复核后即为正式基准**（指标只认这一份）
- `review.md`               给人工看的那一份（top-10 判定 + 最该看的行）
- `ranking_snapshot.json`   本次的四变体排名与候选池快照（复核时的上下文，运行时不消费）

两类种子：
- `bank_search`：写在本文件里的 18 条（M3 七组探针的正式化 + 六域各 3 条），
  形态 = 用户在题库页搜索框里会输入的话；
- `missed_point`：真库报告经 **生产函数 `build_query_items`** 派生的漏点查询——
  与 FR-20 学习推荐消费的是同一个函数，golden 里的 query 文本与线上逐字一致。
  只有域名、没有漏点的那种（纯域名回退）**不入 golden**：域内每题同等相关，
  分级标注无从谈起；那一路由 RAGAS 的 ContextRelevancy 衡量（见 run_ragas_context.py）。
"""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import date
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings
from app.domain import DOMAIN_LABELS
from app.tools.question_search import fetch_by_ids
from app.tools.recommend import build_query_items
from evals import corpus, golden, label_relevance, retrieve

OUT_DIR = Path(__file__).resolve().parents[2] / "data" / "eval" / "golden"
LABEL_CONCURRENCY = 4  # 并发判级条数（每条内部还有分批；flash 并发限 2500，够用）
SELFCHECK_QUERIES = 8  # 抽样重判的条数：golden 的噪声会原样传给指标，得先量出来

BANK_SEARCH_SEEDS: list[dict[str, str]] = [
    # tool-use
    {"query": "工具调用失败怎么处理", "domain": "tool-use", "source": "M3 探针"},
    {"query": "Function Calling 的参数怎么设计", "domain": "tool-use", "source": "六域覆盖"},
    {"query": "MCP 协议解决了什么问题", "domain": "tool-use", "source": "M3 探针"},
    # agent-architecture
    {"query": "多 Agent 架构怎么设计", "domain": "agent-architecture", "source": "M3 探针"},
    {"query": "Agent 和 Workflow 有什么区别", "domain": "agent-architecture", "source": "六域覆盖"},
    {"query": "什么时候该用 Agent", "domain": "agent-architecture", "source": "六域覆盖"},
    # planning-reasoning
    {"query": "ReAct 和 Plan-and-Execute 的区别", "domain": "planning-reasoning", "source": "M3 探针"},
    {"query": "任务规划有哪些范式", "domain": "planning-reasoning", "source": "六域覆盖"},
    {"query": "Reflexion 是什么", "domain": "planning-reasoning", "source": "六域覆盖"},
    # memory
    {"query": "上下文超长了怎么办", "domain": "memory", "source": "六域覆盖"},
    {"query": "长期记忆怎么存", "domain": "memory", "source": "六域覆盖"},
    {"query": "记忆压缩策略怎么定", "domain": "memory", "source": "六域覆盖"},
    # rag
    {"query": "怎么评估 RAG 系统", "domain": "rag", "source": "M3 探针"},
    {"query": "chunk 大小怎么定", "domain": "rag", "source": "M3 探针"},
    {"query": "RAG 和微调怎么选", "domain": "rag", "source": "六域覆盖"},
    # engineering-observability
    {"query": "Agent 怎么评测", "domain": "engineering-observability", "source": "M3 探针"},
    {"query": "prompt 注入怎么防", "domain": "engineering-observability", "source": "六域覆盖"},
    {"query": "Agent 系统的 token 成本怎么优化", "domain": "engineering-observability", "source": "六域覆盖"},
]


def _missed_point_seeds(payloads: list[dict], notes: list[str]) -> list[dict]:
    """报告 → 漏点查询种子（复用生产函数；纯域名回退跳过并记进 notes）。"""
    seen: set[str] = set()
    out: list[dict] = []
    for row in payloads:
        for item in build_query_items(row["payload"]):
            query = item["query"]
            if len(query.split("；")) <= 1:
                notes.append(
                    f"报告 {row['interview_id'][:8]} 的 {item['domain']} 无漏点 → 退化为纯域名查询，"
                    "无判级依据，不入 golden（由 RAGAS 那一路衡量）"
                )
                continue
            if query in seen:  # 不同场次漏点完全相同（同一套参考答案）→ 只留一条，避免重复计入均值
                continue
            seen.add(query)
            out.append({
                "scene": "missed_point",
                "query": query,
                "domain": item["domain"],
                "filters": {"domain": item["domain"]},
                "source": f"报告 {row['interview_id'][:8]} · {DOMAIN_LABELS.get(item['domain'], item['domain'])}",
            })
    return out


async def _collect(seed: dict, sem: asyncio.Semaphore, rows_by_id: dict, notes: list[str]) -> dict | None:
    """一条种子 → 候选池 + 四变体排名 + 判级。池为空 → 记 note 并返回 None。"""
    async with sem:
        variants = await retrieve.rank_variants(seed["query"], filters=seed.get("filters"))
        pool = retrieve.build_pool(variants)
        if not pool:
            notes.append(f"{seed['query'][:30]}… 候选池为空，已跳过")
            return None
        db_path = get_settings().db_path
        rows = await asyncio.to_thread(fetch_by_ids, db_path, pool)
        by_id = {row["question_id"]: row for row in rows}
        ordered = [qid for qid in pool if qid in by_id]  # join 不保序，按池序重排
        if len(ordered) < len(pool):
            notes.append(
                f"{seed['query'][:30]}… 有 {len(pool) - len(ordered)} 条候选在 SQLite 里查不到（Qdrant/SQLite 不同步？）"
            )
        rows_by_id.update(by_id)
        candidates = [by_id[qid] for qid in ordered]
        grades, missing = await label_relevance.label_query(seed["query"], candidates)
        if missing:
            notes.append(f"{seed['query'][:30]}… 有 {len(missing)} 条 LLM 未判出（已按 0 计）")
        return {
            "scene": seed["scene"],
            "query": seed["query"],
            "domain": seed.get("domain"),
            "filters": seed.get("filters"),
            "source": seed["source"],
            "grades": {qid: grades[qid] for qid in ordered},
            "ranked": variants["hybrid"],
            "variants": variants,
        }


async def _selfcheck(entries: list[dict], rows_by_id: dict, sem: asyncio.Semaphore) -> str:
    """抽样重判一遍：一致率是这套 golden 的可信度上限，写进复核文件让人心里有数。

    重判用**同一个** prompt 与温度（0），所以剩下的差异就是 LLM 标注本身的抖动。
    """
    step = max(1, len(entries) // SELFCHECK_QUERIES)
    sample = entries[::step][:SELFCHECK_QUERIES]
    results = await asyncio.gather(*[
        _relabel(entry, rows_by_id, sem) for entry in sample
    ])
    same = sum(r["same"] for r in results)
    total = sum(r["total"] for r in results)
    line = f"标注自一致率（抽样重判 {len(sample)} 条 query / {total} 条判定）：{same / total:.1%}"
    print(line)
    return line


async def _relabel(entry: dict, rows_by_id: dict, sem: asyncio.Semaphore) -> dict:
    async with sem:
        pool = list(entry["grades"])
        again, _ = await label_relevance.label_query(
            entry["query"], [rows_by_id[qid] for qid in pool if qid in rows_by_id]
        )
    same = sum(1 for qid in pool if again.get(qid) == entry["grades"][qid])
    return {"same": same, "total": len(pool)}


async def main() -> None:
    settings = get_settings()
    notes: list[str] = []
    seeds: list[dict] = [
        {
            "scene": "bank_search",
            "query": item["query"],
            "domain": item["domain"],
            "filters": None,
            "source": item["source"],
        }
        for item in BANK_SEARCH_SEEDS
    ]
    reports = corpus.tech_report_payloads(settings.db_path)
    seeds += _missed_point_seeds(reports, notes)
    print(f"种子 {len(seeds)} 条（bank_search {len(BANK_SEARCH_SEEDS)} + missed_point {len(seeds) - len(BANK_SEARCH_SEEDS)}）")

    sem = asyncio.Semaphore(LABEL_CONCURRENCY)
    rows_by_id: dict[str, dict] = {}
    built = await asyncio.gather(*[_collect(seed, sem, rows_by_id, notes) for seed in seeds])

    entries, queries = [], []
    for index, (seed, item) in enumerate(zip(seeds, built, strict=True), start=1):
        if item is None:
            continue
        qid = f"q{index:02d}"
        entries.append({"id": qid, **item})
        queries.append({
            "id": qid,
            "scene": item["scene"],
            "query": item["query"],
            "domain": item["domain"],
            "filters": item["filters"],
            "source": item["source"],
            "grades": item["grades"],
        })
    created = date.today().isoformat()
    agreement = await _selfcheck(entries, rows_by_id, sem)
    doc = {
        "version": 1,
        "created": created,
        "pool": {
            "policy": "union(dense@20, sparse@20, rrf@30, hybrid@30)，按各 query 的 filters 同配置取池",
            "note": "池外题目视作 grade 0（池内口径）；池与排名随检索引擎状态变化，标注只对题目本身负责",
        },
        "grading": golden.GRADE_MEANINGS,
        "queries": queries,
    }
    problems = golden.validate(doc)
    if problems:
        print("golden 校验未过：" + "\n".join(f"- {p}" for p in problems))
        return
    golden.dump(doc, OUT_DIR / "retrieval_queries.json")
    (OUT_DIR / "review.md").write_text(
        label_relevance.render_review(
            entries,
            rows_by_id,
            created=created,
            extra_notes=notes,
            summary=[agreement + "——golden 自身的噪声会原样传给指标，重判有出入的那几档优先怀疑"],
        ),
        encoding="utf-8",
    )
    golden.dump(
        {
            "created": created,
            "variants": {entry["id"]: entry["variants"] for entry in entries},
            "pool": {entry["id"]: list(entry["grades"]) for entry in entries},
        },
        OUT_DIR / "ranking_snapshot.json",
    )

    positives = sum(1 for entry in entries for g in entry["grades"].values() if g == 2)
    partials = sum(1 for entry in entries for g in entry["grades"].values() if g == 1)
    total = sum(len(entry["grades"]) for entry in entries)
    print(
        f"完成：{len(entries)} 条 query · 候选 {total} 条"
        f"（直接相关 {positives} / 部分相关 {partials}）"
    )
    print(f"复核产物：{OUT_DIR / 'review.md'}")
    if notes:
        print(f"构建告警 {len(notes)} 条（见 review.md「构建告警」段）")


if __name__ == "__main__":
    asyncio.run(main())
