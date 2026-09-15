"""LLM 富化：为题库补 key_points（评分依据）与 follow_ups（预置追问集）。

- deepseek-flash，**显式关 thinking**（CLAUDE.md 坑位 1/2：思考模式 + tool_choice 会炸）
- 每题一次调用，并发 5（限流按并发数设计，见坑位 3）；可断点续跑，已富化的题跳过
- 只处理 status=enabled 的题；输出 data/parsed/questions_enriched.json

用法：
  python data/scripts/enrich.py --limit 3      # 小样试跑
  python data/scripts/enrich.py                # 全量
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import time
from collections import Counter
from pathlib import Path

import bootstrap  # noqa: F401  # 把 backend/ 加进 sys.path
from openai import AsyncOpenAI
from pydantic import BaseModel, Field
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential_jitter

from app.config import get_settings
from app.domain import DOMAIN_LABELS

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_IN = REPO_ROOT / "data" / "parsed" / "questions.json"
DEFAULT_OUT = REPO_ROOT / "data" / "parsed" / "questions_enriched.json"
QC_OUT = REPO_ROOT / "data" / "parsed" / "enrich_qc_sample.md"

CONCURRENCY = 5
MAX_ANSWER_CHARS = 2000  # 实测最长 887 字，截断仅作保险

SYSTEM_PROMPT = """你是资深 AI/Agent 方向技术面试官，正在为面试题库做结构化标注。
你的输出会被面试系统的「评分官」直接当作打分依据，所以每一条都必须是可判定的技术要点，不能是空话。
只输出 JSON 对象，不要 markdown 代码块，不要任何解释。"""

USER_TEMPLATE = """题目：{question}
知识域：{domain_label}｜主题：{topic}｜难度：{difficulty}

参考答案：
{answer}
{extra}
请输出 JSON，恰好两个字段：
{{"key_points": ["…", "…"], "follow_ups": ["…", "…"]}}

key_points：3-6 条，每条 ≤30 字。必须取自参考答案且可判定（含技术名词或明确结论），
禁止复述题干，禁止"理解深刻""思路清晰"这类无法判分的评价。
follow_ups：3-5 条，每条 ≤40 字。面试官顺着本题继续往下钻的追问，考察候选人是否真懂
（原理、边界、取舍、踩坑、量化）。"""

ALGORITHM_EXTRA = """
注意：本题是手撕算法题，key_points 请覆盖 思路 / 时间复杂度或空间复杂度 / 边界或易错点 三类。"""


class Enrichment(BaseModel):
    key_points: list[str] = Field(min_length=3, max_length=8)
    follow_ups: list[str] = Field(min_length=3, max_length=6)


def _build_messages(question: dict) -> list[dict]:
    extra = ALGORITHM_EXTRA if question["domain"] == "algorithms" else ""
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": USER_TEMPLATE.format(
                question=question["question"],
                domain_label=DOMAIN_LABELS.get(question["domain"], question["domain"]),
                topic=question["topic"],
                difficulty=question["difficulty"],
                answer=question["answer"][:MAX_ANSWER_CHARS],
                extra=extra,
            ),
        },
    ]


@retry(
    retry=retry_if_exception_type(Exception),
    stop=stop_after_attempt(4),
    wait=wait_exponential_jitter(initial=1, max=20),
    reraise=True,
)
async def _call(client: AsyncOpenAI, model: str, question: dict) -> tuple[Enrichment, int, int]:
    response = await client.chat.completions.create(
        model=model,
        messages=_build_messages(question),
        response_format={"type": "json_object"},
        max_tokens=800,
        temperature=0.3,
        extra_body={"thinking": {"type": "disabled"}},  # 坑位 1：结构化输出必须关 thinking
    )
    raw = response.choices[0].message.content or ""
    usage = response.usage
    return (
        Enrichment.model_validate_json(raw),
        getattr(usage, "prompt_tokens", 0) or 0,
        getattr(usage, "completion_tokens", 0) or 0,
    )


async def enrich_all(questions: list[dict], out_path: Path, concurrency: int, limit: int | None) -> None:
    settings = get_settings()
    client = AsyncOpenAI(api_key=settings.deepseek_api_key, base_url=settings.deepseek_base_url, timeout=90.0)

    previous: dict[str, dict] = {}
    if out_path.exists():
        for item in json.loads(out_path.read_text(encoding="utf-8"))["questions"]:
            if item.get("key_points") and item.get("follow_ups"):
                previous[item["question_id"]] = item
        print(f"检测到已有产物，已富化 {len(previous)} 题将跳过")

    todo = [q for q in questions if q["status"] == "enabled" and q["question_id"] not in previous]
    if limit:
        todo = todo[:limit]
    print(f"待富化 {len(todo)} 题（enabled 共 {sum(1 for q in questions if q['status'] == 'enabled')}），并发 {concurrency}")

    semaphore = asyncio.Semaphore(concurrency)
    enriched: dict[str, dict] = {}
    failures: list[tuple[str, str]] = []
    counters = {"done": 0, "prompt_tokens": 0, "completion_tokens": 0}
    started = time.monotonic()

    async def worker(question: dict) -> None:
        async with semaphore:
            try:
                result, prompt_tokens, completion_tokens = await _call(client, settings.deepseek_model, question)
            except Exception as error:  # 单题失败不拖垮整批，最后统一汇报
                failures.append((question["question_id"], f"{type(error).__name__}: {error}"))
                return
            enriched[question["question_id"]] = result.model_dump()
            counters["done"] += 1
            counters["prompt_tokens"] += prompt_tokens
            counters["completion_tokens"] += completion_tokens
            if counters["done"] % 25 == 0 or counters["done"] == len(todo):
                elapsed = time.monotonic() - started
                print(f"  {counters['done']}/{len(todo)}  用时 {elapsed:.0f}s", flush=True)

    await asyncio.gather(*(worker(q) for q in todo))

    merged = []
    for question in questions:
        record = dict(question)
        payload = enriched.get(question["question_id"]) or previous.get(question["question_id"])
        if payload:
            record["key_points"] = payload["key_points"]
            record["follow_ups"] = payload["follow_ups"]
        merged.append(record)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(
            {
                "meta": {
                    "model": settings.deepseek_model,
                    "enriched": sum(1 for q in merged if q.get("key_points")),
                    "failed": len(failures),
                },
                "questions": merged,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    cost_in = counters["prompt_tokens"] / 1_000_000 * 0.15
    cost_out = counters["completion_tokens"] / 1_000_000 * 0.60
    print(f"\n本次调用 {counters['done']} 次，失败 {len(failures)} 次")
    print(f"token：输入 {counters['prompt_tokens']}，输出 {counters['completion_tokens']}")
    print(f"估算成本：${cost_in + cost_out:.4f}（flash 全价 $0.15/$0.60 每 1M）")
    for question_id, error in failures[:10]:
        print(f"  失败 {question_id}: {error[:120]}")
    print(f"已写出：{out_path}")


def write_qc_sample(questions: list[dict], size: int = 20, model: str = "?") -> None:
    """抽样质检表：跨域分层抽样，交人工确认富化质量。"""
    enriched = [q for q in questions if q.get("key_points")]
    if not enriched:
        return

    by_domain: dict[str, list[dict]] = {}
    for question in enriched:
        by_domain.setdefault(question["domain"], []).append(question)

    rng = random.Random(20260916)  # 固定种子，便于复现
    per_domain = max(1, size // len(by_domain))
    sample: list[dict] = []
    for domain, items in sorted(by_domain.items()):
        sample += rng.sample(items, min(per_domain, len(items)))
    sample = sample[:size]

    lines = [
        f"# 富化抽样质检（{len(sample)} 条，跨域分层）",
        "",
        f"来源：{DEFAULT_OUT.name}｜模型：{model}",
        "",
    ]
    for index, question in enumerate(sample, 1):
        lines += [
            f"## {index}. {question['question']}",
            "",
            f"- 域/难度/来源：`{question['domain']}` / {question['difficulty']} / {question['company']} {question['round']}",
            f"- 参考答案（{len(question['answer'])} 字）：{question['answer'][:120]}{'…' if len(question['answer']) > 120 else ''}",
            "- **key_points**：",
            *[f"  - {point}" for point in question["key_points"]],
            "- **follow_ups**：",
            *[f"  - {item}" for item in question["follow_ups"]],
            "",
        ]
    QC_OUT.write_text("\n".join(lines), encoding="utf-8")
    print(f"质检抽样已写出：{QC_OUT}（{len(sample)} 条）")

    print("\n=== 质检抽样速览 ===")
    for index, question in enumerate(sample, 1):
        print(f"\n{index}. [{question['domain']}] {question['question'][:46]}")
        for point in question["key_points"]:
            print(f"     · {point}")
        for item in question["follow_ups"]:
            print(f"     ? {item}")


def main() -> None:
    parser = argparse.ArgumentParser(description="LLM 富化题库（key_points / follow_ups）")
    parser.add_argument("--in", dest="in_path", type=Path, default=DEFAULT_IN)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--concurrency", type=int, default=CONCURRENCY)
    parser.add_argument("--limit", type=int, default=None, help="只跑前 N 题（小样试跑）")
    parser.add_argument("--qc-only", action="store_true", help="不调用 LLM，仅从现有产物出质检表")
    args = parser.parse_args()

    questions = json.loads(args.in_path.read_text(encoding="utf-8"))["questions"]
    print(f"读入 {len(questions)} 题；状态分布 {dict(Counter(q['status'] for q in questions))}")

    if not args.qc_only:
        asyncio.run(enrich_all(questions, args.out, args.concurrency, args.limit))

    if args.out.exists():
        payload = json.loads(args.out.read_text(encoding="utf-8"))
        write_qc_sample(payload["questions"], model=payload["meta"].get("model", "?"))


if __name__ == "__main__":
    main()
