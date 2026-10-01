"""P1-M12 会话 1：RAGAS ContextRelevance —— 推荐内容对**单条漏点**的覆盖度（离线）。

用法：cd backend && uv run python scripts/eval_ragas_context.py

对每份真库技术面报告跑**生产推荐函数** `recommend_for_report`（与 `/recommendations`
端点同一个函数），拿到该场的推荐卡片；打分单元是**一条漏点**：

    user_input        = 该场某个短板域漏掉的一个知识点
    retrieved_contexts = 这个域推荐出来的 3 道题（doc 文本）

分数 = 推荐内容里与该漏点相关的陈述占比。分组取「组内各漏点均值」，总体取「各组的均值」
（按组等权——否则漏点多的域会顶掉漏点少的域）。

**为什么按单条漏点而不是按拼好的整条 query**：整条 query 是「域名 + 最多 6 个漏点」，
拿它打分会被**查询宽窄**带偏——越宽泛的 query，检索内容越「看起来都相关」（实测：纯域名
查询恒得 1.0）。单条漏点是具体知识点，分数才有分辨力。

**三条实现口径（都是实测定下来的）**：
- **contexts 用 doc 文本（题干 + 关键点）**，不用光题干：ragas 0.3 的 ContextRelevance
  （原 ContextRelevancy）从 context 里抽**陈述句**判相关性，纯问句会被判 0；
  doc 文本正是检索时嵌入的同一份文本，「检索到的内容相不相关」用它才对得上。
- **无漏点的组不参与**（`missed` 为空，即查询退化为纯域名）：没有「要补的知识点」，
  这个指标**无从打分**——不是 0 分也不是 1 分，是「不适用」。这类组单列计数。
- **LLM 走 DeepSeek**：`llm_factory` 内部建 `ChatOpenAI`，只认 `OPENAI_API_KEY` 这个
  环境变量名，故在此转换（不打印）。CLAUDE.md 坑位 1（json_schema 400）没有触发——
  ragas 走的是 prompt 内注入 schema 的路径，不是 `response_format`。
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings
from app.tools.embedding import question_doc_text
from app.tools.recommend import build_query_items, recommend_for_report
from evals import corpus

REPO = Path(__file__).resolve().parents[2]
RESULT_DIR = REPO / "data" / "eval" / "results"



async def main() -> None:
    settings = get_settings()
    # 直接赋值而不是 setdefault：开发机 shell 里常有别的 OPENAI_API_KEY，
    # 用 setdefault 会静默沿用那个键 → ragas 每个样本 401 并**吞成 nan 分数**
    os.environ["OPENAI_API_KEY"] = settings.deepseek_api_key

    from ragas.dataset_schema import SingleTurnSample
    from ragas.llms import llm_factory
    from ragas.metrics import ContextRelevance

    metric = ContextRelevance(
        llm=llm_factory(settings.deepseek_model, base_url=settings.deepseek_base_url)
    )

    reports = corpus.tech_report_payloads(settings.db_path)
    groups: list[dict] = []
    skipped_no_missed = 0
    for report in reports:
        payload = report["payload"]
        items = {item["domain"]: item for item in build_query_items(payload)}
        for group in await recommend_for_report(payload):
            item = items.get(group["domain"])
            if group["status"] != "ok" or not group["cards"] or item is None:
                continue
            if not item["missed"]:
                skipped_no_missed += 1
                continue
            contexts = [
                question_doc_text(card["question"], card["key_points"]) for card in group["cards"]
            ]
            points = []
            for point in item["missed"]:
                score = float(await metric.single_turn_ascore(
                    SingleTurnSample(user_input=point, retrieved_contexts=contexts)
                ))
                if score != score:  # nan：ragas 把内部报错吞成 nan（实测踩过 401 那一版）
                    raise SystemExit(
                        f"ContextRelevance 返回 nan（{group['domain']} / {point[:24]}）——"
                        "ragas 吞了内部错误，往上翻日志找真正的报错，别拿 nan 当分数"
                    )
                points.append({"point": point, "score": score})
            groups.append({
                "interview_id": report["interview_id"][:8],
                "domain": group["domain"],
                "cards": [card["question"] for card in group["cards"]],
                "points": points,
                "score": sum(p["score"] for p in points) / len(points),  # 组内按漏点等权
            })

    by_domain: dict[str, list[float]] = defaultdict(list)
    for group in groups:
        by_domain[group["domain"]].append(group["score"])
    overall = (
        sum(group["score"] for group in groups) / len(groups) if groups else 0.0
    )
    point_scores = [p["score"] for group in groups for p in group["points"]]

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    RESULT_DIR.mkdir(parents=True, exist_ok=True)
    out = {
        "created": datetime.now().astimezone().isoformat(timespec="seconds"),
        "metric": f"ragas.ContextRelevance (llm={settings.deepseek_model})",
        "unit": "一条漏点 × 该域推荐卡片；组 = 组内漏点均值；总体 = 组间等权均值",
        "overall": overall,
        "groups": len(groups),
        "points": len(point_scores),
        "point_mean": sum(point_scores) / len(point_scores) if point_scores else 0.0,
        "skipped_no_missed_groups": skipped_no_missed,
        "by_domain": {
            domain: {"n": len(vals), "mean": sum(vals) / len(vals)}
            for domain, vals in sorted(by_domain.items())
        },
        "rows": groups,
    }
    (RESULT_DIR / f"ragas-context-{stamp}.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    print(f"RAGAS ContextRelevance · {len(reports)} 份技术面报告")
    print(f"  可打分分组 {len(groups)}（漏点 {len(point_scores)} 条）· 不适用分组 {skipped_no_missed}（无漏点，查询退化为纯域名）")
    print(f"  总体（组间等权）{overall:.3f}　|　漏点级均值 {out['point_mean']:.3f}")
    for domain, stats in out["by_domain"].items():
        print(f"    {domain:<26} n={stats['n']}  {stats['mean']:.3f}")
    for group in sorted(groups, key=lambda g: g["score"]):
        print(f"    {group['score']:.2f}  {group['interview_id']} {group['domain']:<26} {len(group['points'])} 个漏点")
    print(f"结果：{RESULT_DIR / f'ragas-context-{stamp}.json'}")


if __name__ == "__main__":
    asyncio.run(main())
