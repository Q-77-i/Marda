"""P1-M12 会话 2：构建评分 golden 集（抽样 + 期望分预判 + 人工复核产物）。

用法：cd backend && uv run python scripts/eval_judge_golden.py [--seed 说明]

产出（都含题面/回答，**按语料红线 gitignore，只留本地**）：
- `data/eval/golden/judge_items.json`：样本 + 期望分（人审后为正式基准）
- `data/eval/golden/judge_review.md`：人工复核产物（每条的标注依据 + 最该看的行）

三臂（用户拍板）：
1. **real**（20 条）：真库技术面历史回答，**按分数段分层抽**（1-2/2-3/3-4/4-5 四档各 5）——
   随机抽可能全落中分段，分层保证质量谱覆盖；
2. **behavioral**（8 条）：真库行为面回答，四场各取 2 条。**已知局限**：真库行为面集中在
   低-中分段（实测单题均值 1.6–3.8，无 ≥4 样本），`limitations` 字段显式记录；
3. **persona**（3 题 × 弱/中/强）：**同一道题**的三个档次回答（不同题的弱中强测不出单调性）。

**期望分怎么来**：`deepseek-v4-pro` 温度 0 按独立 rubric 预判（与评分官 flash 异模型，
避免同源偏差互相抵消）→ review.md 人审 → 改 json → 重跑指标。**不用「当时实得」当基准**
（那是评分官自己的输出，等于自己给自己打分）；实得只写进复核文件供人对照。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pydantic import BaseModel, Field

from app import llm
from app.config import get_settings
from app.domain import INTERVIEW_BEHAVIORAL, INTERVIEW_TECH
from app.graph.rules.aggregate import dims_for

from evals import corpus, judge_golden, personas

REPO = Path(__file__).resolve().parents[2]
GOLDEN_PATH = REPO / "data" / "eval" / "golden" / "judge_items.json"
REVIEW_PATH = REPO / "data" / "eval" / "golden" / "judge_review.md"

REAL_COUNT = 20
BEHAVIORAL_COUNT = 8
PERSONA_QUESTIONS = 3  # 每题出弱/中/强三档
PERSONA_DOMAINS = ("rag", "agent-architecture", "tool-use")
BANDS = ((1, 2), (2, 3), (3, 4), (4, 5))  # 分数段（左闭右开，末档含 5.0）
REVIEW_ANSWER_CHARS = 320  # 复核文件里回答的展示长度（全文在 golden json 里）
ATTENTION_DELTA = 1.0  # 「标注期望 vs 当时实得」差多少就值得人看一眼


# ---- 抽样 ----


def _spread(rows: list[dict], count: int) -> list[dict]:
    """按序（分数升序）等距取 count 条：首末必取，中间均匀——比随机更可复现也更铺得开。"""
    if len(rows) <= count:
        return rows
    if count == 1:
        return [rows[len(rows) // 2]]
    step = (len(rows) - 1) / (count - 1)
    return [rows[round(i * step)] for i in range(count)]


def sample_real(samples: list[dict], *, count: int = REAL_COUNT) -> tuple[list[dict], list[str]]:
    """按总分分层抽（返回样本与告警：某档不足时写明，不静默少抽）。"""
    notes: list[str] = []
    per_band = count // len(BANDS)
    picked: list[dict] = []
    for low, high in BANDS:
        rows = [s for s in samples if low <= s["overall"] < high or (high == 5 and s["overall"] == 5)]
        rows.sort(key=lambda s: (s["overall"], s["interview_id"], s["question"]))
        got = _spread(rows, per_band)
        if len(got) < per_band:
            notes.append(f"分数段 [{low},{high}) 只有 {len(rows)} 条真实回答，不足 {per_band} 条")
        picked.extend(got)
    return picked, notes


def sample_behavioral(samples: list[dict], *, count: int = BEHAVIORAL_COUNT) -> list[dict]:
    """四场各取若干（场内按分数等距），保证跨场次铺开而不是挤在一场里。"""
    by_interview: dict[str, list[dict]] = {}
    for row in samples:
        by_interview.setdefault(row["interview_id"], []).append(row)
    per = max(1, count // max(1, len(by_interview)))
    picked: list[dict] = []
    for _, rows in sorted(by_interview.items()):
        rows.sort(key=lambda s: (s["overall"], s["question"]))
        picked.extend(_spread(rows, per))
    return picked[:count]


def persona_questions(db_path: Path) -> list[dict]:
    """每个域取「关键点最多」的启用公共题（要点最丰富，最考验覆盖判定）；同分按 id 稳定。"""
    import sqlite3

    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        out = []
        for domain in PERSONA_DOMAINS:
            row = con.execute(
                "SELECT id, question, key_points FROM questions"
                " WHERE status='enabled' AND user_id IS NULL AND domain=?"
                " ORDER BY json_array_length(key_points) DESC, id ASC LIMIT 1",
                (domain,),
            ).fetchone()
            if row is None:
                raise SystemExit(f"域 {domain} 没有启用中的公共题，persona 臂抽不出来")
            out.append({
                "question_id": row["id"],
                "question": row["question"],
                "key_points": [str(p) for p in json.loads(row["key_points"] or "[]")],
                "domain": domain,
            })
        return out
    finally:
        con.close()


# ---- 期望分标注（v4-pro，独立 rubric） ----


class _TechExpected(BaseModel):
    technical_depth: int = Field(ge=1, le=5)
    fundamentals: int = Field(ge=1, le=5)
    project_experience: int = Field(ge=1, le=5)
    communication: int = Field(ge=1, le=5)
    problem_solving: int = Field(ge=1, le=5)
    covered_indexes: list[int]
    note: str


class _BehavioralExpected(BaseModel):
    communication: int = Field(ge=1, le=5)
    logic_structure: int = Field(ge=1, le=5)
    project_experience: int = Field(ge=1, le=5)
    values_motivation: int = Field(ge=1, le=5)
    career_stability: int = Field(ge=1, le=5)
    covered_indexes: list[int]
    note: str


STRICT_TECH = """1. **严格**：这是要给评分官当基准的，「答得还行」不是 4 分。4 分 = 触及原理/权衡；
   5 分 = 讲透且有边界条件；3 分 = 方向对但停在表层；2 分 = 只言片语；1 分 = 答非所问。
2. 回答长 ≠ 分高：堆砌术语但没讲清机制的，压在 3 分及以下。"""

STRICT_BEHAVIORAL = """1. **严格**：这是要给评分官当基准的，「态度不错」不是 4 分。4 分 = 经历具体、有个人动作与
   可验证结果；5 分 = 讲述有主线（情境→任务→行动→结果）且能复盘得失；3 分 = 说得清但笼统、
   缺细节；2 分 = 只言片语、回避细节；1 分 = 答非所问或全程说不清。
2. **回避不算答**：「记不太清」「跟着团队做的」这类占位表述，对应维度压到 2 分及以下。"""

LABEL_TEMPLATE = """你是资深面试评委，正在为评分官的**校准基准**打分（这不是面试，是标注）。
给定题目、关键点与候选人回答，请判定这份回答**应得**的分数——按维度逐一给 1-5 整数，
并判定它覆盖了哪几条关键点。

评分维度：
{dims}

判定要求：
{strict}
3. covered_indexes 填**确实讲到了**的关键点序号（见下方编号），没讲到的不填；
   讲了但很浅、只有名词的，不算覆盖。
4. note 写一句判定理由（供人工复核，不会展示给候选人）。

【题目】{question}
【关键点】（编号从 0 开始）
{key_points}
【候选人回答】
{answer}
"""


async def label_expected(item: dict, *, index: int) -> dict:
    dims = dims_for(item["interview_type"])
    key_points = "\n".join(f"[{i}] {p}" for i, p in enumerate(item["key_points"])) or "（无）"
    schema = _TechExpected if item["interview_type"] == INTERVIEW_TECH else _BehavioralExpected
    strict = STRICT_TECH if item["interview_type"] == INTERVIEW_TECH else STRICT_BEHAVIORAL
    got = await llm.chat_json(
        [{"role": "system", "content": LABEL_TEMPLATE.format(
            dims="\n".join(f"- {key} {label}" for key, label in dims.items()),
            strict=strict,
            question=item["question"],
            key_points=key_points,
            answer=item["answer"],
        )}],
        schema=schema,
        temperature=0.0,  # 标注要可复现
        model=get_settings().deepseek_pro_model,  # 与评分官（flash）异模型：避免同源偏差
    )
    data = got.model_dump()
    covered = [i for i in data.pop("covered_indexes") if 0 <= i < len(item["key_points"])]
    note = data.pop("note")
    return {"dims": data, "covered_indexes": sorted(set(covered)), "note": note}


# ---- 复核文件 ----


def _truncate(text: str, limit: int = REVIEW_ANSWER_CHARS) -> str:
    text = text.strip()
    return text if len(text) <= limit else text[:limit] + f"…（全文 {len(text)} 字）"


def render_review(doc: dict, *, extra_notes: list[str]) -> str:
    out = [
        "# 评分 golden · 人工复核",
        "",
        f"生成：{doc['created']}　|　{len(doc['items'])} 条样本"
        f"（real {sum(1 for i in doc['items'] if i['arm'] == 'real')} / "
        f"behavioral {sum(1 for i in doc['items'] if i['arm'] == 'behavioral')} / "
        f"persona {sum(1 for i in doc['items'] if i['arm'] == 'persona')}）",
        "",
        "**怎么读**：每条给「标注期望」（deepseek-v4-pro 温度 0 预判）"
        "与「当时实得」（真库样本才有——评分官当时的输出，只作对照，**不是基准**）。"
        "复核结论见下方「复核结论」节；要改标注就改 "
        "`data/eval/golden/judge_items.json` 里对应条目的 `expected`，然后重跑 "
        "`scripts/eval_judge_run.py`。",
        "",
        f"> 期望分维度表按会话类型取（技术面/行为面各一套）。覆盖下标："
        f"`covered_indexes` 是 key_points 的序号（从 0 开始）。",
        "",
    ]
    if extra_notes:
        out += ["## 构建告警", ""] + [f"- {note}" for note in extra_notes] + [""]

    review = doc.get("review")
    if review:
        out += [
            "## 复核结论",
            "",
            f"- 复核人：{review['reviewer']}（{review['date']}）",
            f"- 范围：{review['scope']}",
            f"- 结论：{review['conclusion']}",
            "",
        ]
        for note in review.get("notes", []):
            out.append(f"- {note}")
        out.append("")

    out += _attention_section(doc)
    out += _persona_section(doc)

    for item in doc["items"]:
        head = f"### {item['id']} · {item['arm']}"
        if item.get("tier"):
            head += f" · {item['tier']}"
        out += [head, "", f"**题干**：{item['question']}", ""]
        if item["key_points"]:
            out += ["**关键点**：", ""]
            out += [f"{i}. {p}" for i, p in enumerate(item["key_points"])]
            out += [""]
        expected = item["expected"]
        exp_overall = sum(expected["dims"].values()) / len(expected["dims"])
        out += [
            f"**标注期望**：总分 {exp_overall:.2f}　"
            + "、".join(f"{k}={v}" for k, v in expected["dims"].items()),
            "",
            f"- 覆盖下标：{expected['covered_indexes']}　理由：{expected.get('note', '')}",
        ]
        prov = item.get("provenance") or {}
        if prov:
            out.append(f"- 当时实得：{prov.get('overall')}（评分官输出，仅对照）")
        out += ["", f"**回答**：{_truncate(item['answer'])}", ""]
    return "\n".join(out)


def _attention_section(doc: dict) -> list[str]:
    rows: list[str] = []
    for item in doc["items"]:
        prov = item.get("provenance") or {}
        if not item.get("tier") and prov.get("overall") is not None:
            exp = sum(item["expected"]["dims"].values()) / len(item["expected"]["dims"])
            if abs(exp - prov["overall"]) >= ATTENTION_DELTA:
                rows.append(
                    f"`{item['id']}` 标注 {exp:.2f} vs 当时实得 {prov['overall']:.2f}"
                    f"（差 {exp - prov['overall']:+.2f}）"
                )
        if item["key_points"] and not item["expected"]["covered_indexes"] and len(item["answer"]) > 300:
            rows.append(f"`{item['id']}` 长回答（{len(item['answer'])} 字）却判零覆盖——是不是漏判了？")
    if not rows:
        return []
    return ["## 最该看的行", "", f"共 {len(rows)} 条：", ""] + [f"- {r}" for r in rows] + [""]


def _persona_section(doc: dict) -> list[str]:
    groups: dict[str, list[dict]] = {}
    for item in doc["items"]:
        if item.get("group"):
            groups.setdefault(item["group"], []).append(item)
    if not groups:
        return []
    out = ["## persona 三档（同题弱/中/强，标注期望也应递增）", "", "| 组 | 弱 | 中 | 强 | 单调 |", "| - | - | - | - | - |"]
    for group, members in sorted(groups.items()):
        by_tier = {m["tier"]: sum(m["expected"]["dims"].values()) / len(m["expected"]["dims"]) for m in members}
        values = [by_tier.get(t) for t in personas.TIERS]
        labels = [f"{v:.2f}" if v is not None else "-" for v in values]
        ordered = [v for v in values if v is not None]
        monotone = all(b > a for a, b in zip(ordered, ordered[1:]))
        out.append(f"| {group} | {' | '.join(labels)} | {'✅' if monotone else '❌ 标注不单调'} |")
    return out + [""]


# ---- 主流程 ----


async def build(*, db_path: Path) -> tuple[dict, list[str]]:
    notes: list[str] = []
    items: list[dict] = []

    real, band_notes = sample_real(corpus.answer_samples(db_path, interview_type=INTERVIEW_TECH))
    notes += band_notes
    for index, sample in enumerate(real, 1):
        items.append({
            "id": f"real-{index:02d}",
            "arm": "real",
            "interview_type": INTERVIEW_TECH,
            "question": sample["question"],
            "key_points": sample["key_points"],
            "answer": sample["answer"],
            "provenance": {
                "interview_id": sample["interview_id"],
                "question_id": sample["question_id"],
                "overall": sample["overall"],
                "domain": sample["domain"],
                "key_points_from": sample["key_points_from"],
            },
        })

    sample_beh = corpus.answer_samples(db_path, interview_type=INTERVIEW_BEHAVIORAL)
    for index, sample in enumerate(sample_behavioral(sample_beh), 1):
        items.append({
            "id": f"beh-{index:02d}",
            "arm": "behavioral",
            "interview_type": INTERVIEW_BEHAVIORAL,
            "question": sample["question"],
            "key_points": sample["key_points"],
            "answer": sample["answer"],
            "provenance": {
                "interview_id": sample["interview_id"],
                "question_id": sample["question_id"],
                "overall": sample["overall"],
                "domain": sample["domain"],
                "key_points_from": sample["key_points_from"],
            },
        })

    for q_index, question in enumerate(persona_questions(db_path), 1):
        for tier in personas.TIERS:
            answer = await personas.generate_tier_answer(question["question"], tier)
            items.append({
                "id": f"persona-{q_index}-{tier}",
                "arm": "persona",
                "interview_type": INTERVIEW_TECH,
                "question": question["question"],
                "key_points": question["key_points"],
                "answer": answer,
                "group": f"pg{q_index}",
                "tier": tier,
                "provenance": {"question_id": question["question_id"], "domain": question["domain"]},
            })

    for index, item in enumerate(items, 1):
        print(f"  标注期望 {index}/{len(items)}：{item['id']}", flush=True)
        item["expected"] = await label_expected(item, index=index)

    if sample_beh and len(sample_beh) < BEHAVIORAL_COUNT:
        notes.append(f"真库行为面回答共 {len(sample_beh)} 条，少于计划 {BEHAVIORAL_COUNT} 条")
    doc = {
        "version": 1,
        "created": datetime.now().astimezone().isoformat(timespec="seconds"),
        "runs": 5,
        "temperature": 0.3,
        "labeler": {
            "model": get_settings().deepseek_pro_model,
            "temperature": 0.0,
            "note": "期望分为独立 rubric 预判，需人工复核后才算正式基准",
        },
        "limitations": [
            "行为面样本集中在低-中分段（真库实测单题均值 1.6–3.8，无 ≥4 分样本），"
            "高分段的评分一致性未覆盖",
            "真库样本的 score 是最终记录（含追问补充的合并回答），followup_log 统一按「无」评"
            "——中间轮次的评分任务不入 golden（重建其输入需要 trace，代价不值）",
        ],
        "items": items,
    }
    problems = judge_golden.validate(doc)
    if problems:
        raise SystemExit("构建出的 golden 不合法：\n" + "\n".join(f"- {p}" for p in problems))
    return doc, notes


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default="", help="业务库路径（默认取 settings.db_path）")
    args = parser.parse_args()
    db_path = Path(args.db) if args.db else get_settings().db_path

    print(f"读真库（只读）：{db_path}")
    doc, notes = await build(db_path=db_path)
    judge_golden.dump(doc, GOLDEN_PATH)
    REVIEW_PATH.write_text(render_review(doc, extra_notes=notes), encoding="utf-8")

    print(f"\n样本 {len(doc['items'])} 条 → {GOLDEN_PATH}")
    print(f"复核文件 → {REVIEW_PATH}")
    for note in notes:
        print(f"  ⚠️ {note}")
    print("\n下一步：人工过一遍复核文件（改 json 里的 expected），再跑 eval_judge_run.py")


if __name__ == "__main__":
    asyncio.run(main())
