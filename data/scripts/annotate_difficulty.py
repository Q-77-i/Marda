"""LLM 难度重标注（P2-M1）：按产品已承诺的三档语义给题目标 L1/L2/L3。

**标尺不新造**——三档语义与前端创建表单的文案同源（`frontend/lib/constants.ts` 的
`DIFFICULTY_OPTIONS`）：L1 基础 = 概念与名词解释 / L2 进阶 = 原理、对比与选型 /
L3 深入 = 底层实现与设计权衡。根因是三个开源源没有难度标注、解析时一律落默认 L2
（实测 686 题），不是「标注成 L2」。

- deepseek-flash，显式关 thinking（CLAUDE.md 坑位 1：结构化输出必须关）
- 批处理（默认 20 题/批，批内按序号对齐——不让模型抄 question_id，抄错无法察觉）、
  并发 5、**可断点续跑**（原始产物里已有的 id 跳过）
- 输入只用「题干 + 域」：难度由题目本身的要求决定，不喂答案（省钱，且避免答案
  的深度污染判断——答案写得多深不等于题目问得多深）
- 原始产物落 `data/parsed/`（gitignore）；curated 落
  `data/curation/difficulty_annotations.json`（**只存 id → 档位**，不存题干原文——语料红线）
- 两个选择集：`main` = 待标注的存量题（enabled 公共题，排除 behavioral 与
  from-zero）；`from-zero` = 该源 89 道**真实标注**题，只作 rubric 校准（不写 curated）

用法：
  python data/scripts/annotate_difficulty.py --select from-zero   # 先校准 rubric
  python data/scripts/annotate_difficulty.py --select main        # 全量（可重复跑）
  python data/scripts/annotate_difficulty.py --report             # 一致率 / 分布 / QC 抽样
  python data/scripts/annotate_difficulty.py --curate             # 写 curated 文件
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import sqlite3
import time
from collections import Counter
from pathlib import Path
from typing import Final, Literal

import bootstrap  # noqa: F401  # 把 backend/ 加进 sys.path
from openai import AsyncOpenAI
from pydantic import BaseModel
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential_jitter

from app.config import get_settings
from app.domain import DOMAIN_LABELS

REPO_ROOT = Path(__file__).resolve().parents[2]
RAW_OUT = REPO_ROOT / "data" / "parsed" / "difficulty_raw.json"
CURATED_OUT = REPO_ROOT / "data" / "curation" / "difficulty_annotations.json"
QC_OUT = REPO_ROOT / "data" / "parsed" / "difficulty_qc_sample.md"

BATCH_SIZE = 20
CONCURRENCY = 5
LEVELS: Final = ("L1", "L2", "L3")
SOURCE_FROM_ZERO = "ai-agents-from-zero"

# 选择集：main = 待标注存量；from-zero = 真实标注的校准集（不写 curated）
SELECTS: Final[dict[str, str]] = {
    "main": (
        "status='enabled' AND user_id IS NULL"
        " AND domain != 'behavioral' AND source != 'ai-agents-from-zero'"
    ),
    "from-zero": f"status='enabled' AND user_id IS NULL AND source='{SOURCE_FROM_ZERO}'",
}

SYSTEM_PROMPT = """你是 AI/Agent 方向技术面试的资深面试官，正在为题库标注**难度档位**。
你判的是「这道题要求候选人答到多深」，不是「答案写得多长」。只输出 JSON 对象，
不要 markdown 代码块，不要任何解释。"""

USER_TEMPLATE = """难度分三档（与产品对用户的承诺一致）：
- L1 基础：概念与名词解释——「X 是什么」「X 有哪些类型 / 各承担什么职责」
- L2 进阶：原理、对比与选型——「X 是怎么工作的」「X 和 Y 的区别 / 怎么取舍」
- L3 深入：底层实现与设计权衡——「X 的底层怎么实现」「边界条件与失败模式」「为什么不选替代方案 / 量化权衡」

判定规则：
1. 以题干**要求的最高认知层次**为准（既要概念又要权衡 → 按 L3 算）
2. 只看题干判断，不看答案
3. 题干信息确实不足以判断时给 L2（中档）
4. **三档都要用**——依据定义如实判断，不要把大多数题都堆在 L2

题目（共 {n} 题）：
{items}

输出 JSON：{{"levels": [{{"n": 1, "level": "L1"}}, {{"n": 2, "level": "L3"}}, …]}}
必须覆盖 1 到 {n} 每一题，level 取 L1 / L2 / L3 之一。"""


class LevelItem(BaseModel):
    n: int
    level: Literal["L1", "L2", "L3"]


class BatchResult(BaseModel):
    levels: list[LevelItem]


def load_questions(db_path: Path, select: str) -> list[dict]:
    """从真库只读取题（题序按 id 固定，断点续跑稳定）。"""
    with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            f"SELECT id, question, domain, difficulty FROM questions WHERE {select} ORDER BY id"
        ).fetchall()
    return [dict(row) for row in rows]


def build_messages(batch: list[dict]) -> list[dict]:
    items = "\n".join(
        f"{index}. [{DOMAIN_LABELS.get(q['domain'], q['domain'])}] {q['question']}"
        for index, q in enumerate(batch, 1)
    )
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": USER_TEMPLATE.format(n=len(batch), items=items)},
    ]


def to_levels(batch: list[dict], result: BatchResult) -> dict[str, str]:
    """批内序号 → question_id 映射；**覆盖不全直接报错**（缺口不许静默漏过）。

    按序号而不是让模型抄 question_id：抄错 id 无法察觉，序号对不齐立刻能发现。
    """
    by_n = {item.n: item.level for item in result.levels}
    if sorted(by_n) != list(range(1, len(batch) + 1)):
        raise ValueError(f"批次覆盖不全：期望 1..{len(batch)}，返回 {sorted(by_n)}")
    return {batch[n - 1]["id"]: level for n, level in by_n.items()}


@retry(
    retry=retry_if_exception_type(Exception),
    stop=stop_after_attempt(4),
    wait=wait_exponential_jitter(initial=1, max=20),
    reraise=True,
)
async def _call(client: AsyncOpenAI, model: str, batch: list[dict]) -> tuple[dict[str, str], int, int]:
    response = await client.chat.completions.create(
        model=model,
        messages=build_messages(batch),
        response_format={"type": "json_object"},
        max_tokens=1500,
        temperature=0.0,
        extra_body={"thinking": {"type": "disabled"}},  # 坑位 1：结构化输出必须关 thinking
    )
    raw = response.choices[0].message.content or ""
    levels = to_levels(batch, BatchResult.model_validate_json(raw))
    usage = response.usage
    return (
        levels,
        getattr(usage, "prompt_tokens", 0) or 0,
        getattr(usage, "completion_tokens", 0) or 0,
    )


def read_raw() -> dict:
    if RAW_OUT.exists():
        return json.loads(RAW_OUT.read_text(encoding="utf-8"))
    return {"meta": {}, "levels": {}}


def _write_raw(raw: dict) -> None:
    raw["meta"] = {
        "model": get_settings().deepseek_model,
        "rubric": "L1 概念与名词解释 / L2 原理、对比与选型 / L3 底层实现与设计权衡",
        "annotated": len(raw["levels"]),
    }
    RAW_OUT.parent.mkdir(parents=True, exist_ok=True)
    RAW_OUT.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")


async def annotate(db_path: Path, select: str, limit: int | None, concurrency: int) -> None:
    settings = get_settings()
    questions = load_questions(db_path, select)
    raw = read_raw()
    todo = [q for q in questions if q["id"] not in raw["levels"]]
    if limit:
        todo = todo[:limit]
    print(f"选择集 {select}：{len(questions)} 题，已有标注 {len(questions) - len(todo)}，本次待标注 {len(todo)}")
    if not todo:
        return

    batches = [todo[i : i + BATCH_SIZE] for i in range(0, len(todo), BATCH_SIZE)]
    client = AsyncOpenAI(
        api_key=settings.deepseek_api_key, base_url=settings.deepseek_base_url, timeout=120.0
    )
    semaphore = asyncio.Semaphore(concurrency)
    failures: list[tuple[str, str]] = []
    counters = {"done": 0, "prompt_tokens": 0, "completion_tokens": 0}
    started = time.monotonic()

    async def worker(batch: list[dict]) -> None:
        async with semaphore:
            try:
                levels, prompt_tokens, completion_tokens = await _call(
                    client, settings.deepseek_model, batch
                )
            except Exception as error:  # 单批失败不拖垮整批，最后统一汇报
                failures.append((batch[0]["id"], f"{type(error).__name__}: {error}"))
                return
            raw["levels"].update(levels)
            counters["done"] += len(batch)
            counters["prompt_tokens"] += prompt_tokens
            counters["completion_tokens"] += completion_tokens
            done_batches = counters["done"] // BATCH_SIZE
            if done_batches % 5 == 0 and counters["done"] % BATCH_SIZE == 0:
                _write_raw(raw)  # 边跑边落盘：中断了也不丢已标注的部分
                elapsed = time.monotonic() - started
                print(f"  {counters['done']}/{len(todo)}  用时 {elapsed:.0f}s", flush=True)

    await asyncio.gather(*(worker(batch) for batch in batches))
    _write_raw(raw)

    cost_in = counters["prompt_tokens"] / 1_000_000 * 0.15
    cost_out = counters["completion_tokens"] / 1_000_000 * 0.60
    print(f"\n本次标注 {counters['done']} 题，失败批次 {len(failures)}")
    print(f"token：输入 {counters['prompt_tokens']}，输出 {counters['completion_tokens']}")
    print(f"估算成本：${cost_in + cost_out:.4f}（flash 全价 $0.15/$0.60 每 1M）")
    for question_id, error in failures[:10]:
        print(f"  失败批次（起于 {question_id}）: {error[:140]}")
    print(f"原始产物：{RAW_OUT}（累计 {len(raw['levels'])} 条）")


def report(db_path: Path) -> None:
    """一致率（对 from-zero 真实标注 / 个人库轮次映射）+ 分布 + 分层 QC 抽样。"""
    raw = read_raw()
    levels: dict[str, str] = raw["levels"]

    def questions(select: str) -> list[dict]:
        return load_questions(db_path, select)

    print("=== 覆盖 ===")
    for name, select in SELECTS.items():
        items = questions(select)
        done = sum(1 for q in items if q["id"] in levels)
        print(f"  {name}: {done}/{len(items)}")

    print("\n=== 标注后分布（main 集）===")
    dist = Counter(levels[q["id"]] for q in questions(SELECTS["main"]) if q["id"] in levels)
    total = sum(dist.values())
    if not total:
        print("  （main 集尚未标注）")
    for level in LEVELS:
        share = f"{dist[level] / total * 100:.1f}%" if total else "—"
        print(f"  {level}: {dist[level]}（{share}）")

    print("\n=== 与存量标签的一致率 ===")
    for name, select, note in (
        ("from-zero 真实标注", SELECTS["from-zero"], "源仓库的人工标注"),
        ("个人库轮次映射", "status='enabled' AND user_id IS NULL AND source='个人题库-牛客补充版'", "一面→L1/二面→L2/三面→L3，启发式"),
    ):
        pairs = [(levels[q["id"]], q["difficulty"]) for q in questions(select) if q["id"] in levels]
        if not pairs:
            print(f"  {name}: 未标注，跳过")
            continue
        exact = sum(1 for new, old in pairs if new == old)
        adjacent = sum(1 for new, old in pairs if abs(LEVELS.index(new) - LEVELS.index(old)) <= 1)
        print(f"  {name}（{len(pairs)} 条，{note}）：完全一致 {exact / len(pairs) * 100:.1f}%，相邻一致 {adjacent / len(pairs) * 100:.1f}%")
        matrix = Counter(pairs)
        for old in LEVELS:
            row = " ".join(f"{old}→{new}:{matrix[(new, old)]:>4}" for new in LEVELS)
            print(f"    {row}")

    print("\n=== 个人库难度变化预览（当前 → 标注）===")
    personal = questions("status='enabled' AND user_id IS NULL AND source='个人题库-牛客补充版' AND domain != 'behavioral'")
    changes = Counter(
        (q["difficulty"], levels[q["id"]]) for q in personal if q["id"] in levels and levels[q["id"]] != q["difficulty"]
    )
    for (old, new), count in sorted(changes.items()):
        print(f"  {old} → {new}: {count}")
    print(f"  合计改动 {sum(changes.values())} / {len(personal)}")

    # 分层 QC 抽样（每档 7 条，固定种子）
    rng = random.Random(20261002)
    by_level: dict[str, list[dict]] = {level: [] for level in LEVELS}
    main = questions(SELECTS["main"])
    for question in main:
        if question["id"] in levels:
            by_level[levels[question["id"]]].append(question)
    lines = ["# 难度标注抽样质检（每档 7 条，固定种子）", ""]
    print("\n=== QC 抽样速览 ===")
    for level in LEVELS:
        sample = rng.sample(by_level[level], min(7, len(by_level[level])))
        for question in sample:
            lines += [f"- **{level}**（原 {question['difficulty']}）[{question['domain']}] {question['question']}"]
        print(f"\n[{level}] 抽 {len(sample)} 条：")
        for question in sample:
            print(f"   （原 {question['difficulty']}）[{question['domain'][:18]}] {question['question'][:46]}")
    QC_OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\n质检表已写出：{QC_OUT}")


def curate(db_path: Path) -> None:
    """写 curated 文件：只含 main 集的 id → 档位（校准集不写）。"""
    raw = read_raw()
    levels = raw["levels"]
    main = load_questions(db_path, SELECTS["main"])
    missing = [q["id"] for q in main if q["id"] not in levels]
    if missing:
        raise SystemExit(f"main 集还有 {len(missing)} 题未标注（先跑 --select main）：{missing[:5]}")
    payload = {
        "_说明": (
            "LLM 难度重标注（P2-M1）：key = question_id（题干内容哈希，题干改一字即失效，"
            "未命中的条目会在 combine/apply 的运行报告里列出来，不静默）。档位语义与前端创建表单同源："
            "L1 基础=概念与名词解释 / L2 进阶=原理、对比与选型 / L3 深入=底层实现与设计权衡。"
            "**不记题干原文**（语料红线，data/scripts/check_redline.py 会拦）：要查某个 id 是哪道题，"
            "在本地库 SELECT question FROM questions WHERE id = '…'。"
            "from-zero 源的 89 道真实标注不在本表内（保留原标注，并作为 rubric 校准集）。"
        ),
        "meta": raw["meta"] | {"scope": "enabled 公共题，排除 behavioral 与 from-zero", "count": len(main)},
        "levels": {q["id"]: levels[q["id"]] for q in main},
    }
    CURATED_OUT.parent.mkdir(parents=True, exist_ok=True)
    CURATED_OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    dist = Counter(payload["levels"].values())
    print(f"已写出 {CURATED_OUT}：{len(payload['levels'])} 条 {dict(dist)}")


def main() -> None:
    parser = argparse.ArgumentParser(description="LLM 难度重标注（P2-M1）")
    parser.add_argument("--select", choices=sorted(SELECTS), help="标注哪个选择集")
    parser.add_argument("--limit", type=int, default=None, help="只跑前 N 题（小样试跑）")
    parser.add_argument("--concurrency", type=int, default=CONCURRENCY)
    parser.add_argument("--report", action="store_true", help="一致率 / 分布 / QC 抽样")
    parser.add_argument("--curate", action="store_true", help="写 curated 文件")
    args = parser.parse_args()

    db_path = get_settings().db_path
    if args.report:
        report(db_path)
    elif args.curate:
        curate(db_path)
    elif args.select:
        asyncio.run(annotate(db_path, SELECTS[args.select], args.limit, args.concurrency))
    else:
        parser.error("需要 --select / --report / --curate 之一")


if __name__ == "__main__":
    main()
