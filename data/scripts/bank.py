"""题库公共层：题目记录骨架、来源明细与同题合并（SPEC §8.1）。

`parse_md.py`（个人题库）与 `parse_open.py`（开源语料）共用这一层——同一道题在不同语料里
出现时，题干 md5 相同 → 同一 question_id → 合并成一条多源记录，出处明细逐源留存。

**主源优先级**（`SOURCE_PRIORITY`，数值越小越优先）：个人题库恒为 0——它人工整理、逐题校对过，
开源源答案再长也不顶替；开源语料同为 10（同档之间按质量裁决）。新增源必须在此登记，
未登记的排在其后。主源裁决 `rank()` 与「每源留最优」的合并规则见 `merge_sources`。
"""

from __future__ import annotations

import difflib
import hashlib
import re
from collections import Counter
from typing import Final

from app.domain import DOMAIN_LABELS, ENABLED_DOMAINS

SOURCE_PERSONAL: Final = "个人题库-牛客补充版"
# 开源语料（WenQu 登记表的 MIT/Apache ANSWERS 档，SPEC §8.1）
SOURCE_FROM_ZERO: Final = "ai-agents-from-zero"
SOURCE_AGENT_GUIDE: Final = "ai-agent-interview-guide"
SOURCE_FAQ: Final = "FAQ_Of_LLM_Interview"
SOURCE_LLM_GUIDE: Final = "llm-interview-guide"


# 开源语料同档（10）：它们之间的次序由 rank() 的后续键（能用/答案长）裁决
SOURCE_PRIORITY: Final[dict[str, int]] = {
    SOURCE_PERSONAL: 0,
    SOURCE_FROM_ZERO: 10,
    SOURCE_AGENT_GUIDE: 10,
    SOURCE_FAQ: 10,
    SOURCE_LLM_GUIDE: 10,
}
UNRANKED_PRIORITY: Final = 100


def make_id(question: str) -> str:
    """question_id = q_ + md5(题目文本)[:12]，确定性、幂等 upsert 用。

    题干相同即同 id——跨源合并（同一道题出现在两个语料里）由此天然成立。
    """
    digest = hashlib.md5(question.encode("utf-8")).hexdigest()[:12]
    return f"q_{digest}"


def source_rank(source: str) -> int:
    return SOURCE_PRIORITY.get(source, UNRANKED_PRIORITY)


def source_record(source: str, *, license: str, url: str = "", source_detail: str = "") -> dict:
    """来源明细记录（question_sources 一行）：license 按源记、不按题记（SPEC §8.1）。"""
    return {"source": source, "license": license, "url": url, "source_detail": source_detail}


def new_question(
    text: str,
    *,
    topic: str,
    domain: str,
    difficulty: str,
    source: str,
    license: str,
    company: str | None = None,
    round_: str | None = None,
    url: str = "",
    source_detail: str = "",
) -> dict:
    """题目记录骨架（各源 adapter 的公共起点）：题干、归类与来源明细四要素。"""
    if not topic:
        raise ValueError(f"题目缺少 topic（分类无法归一化）：{text}")
    if not domain:
        raise ValueError(f"题目缺少 domain（topic 未映射）：{text}")
    if difficulty not in {"L1", "L2", "L3"}:
        raise ValueError(f"难度未归一化：{difficulty}（{text}）")
    return {
        "question_id": make_id(text),
        "question": text,
        "answer": "",
        "topic": topic,
        "domain": domain,
        "difficulty": difficulty,
        "company": company,
        "round": round_,
        "round_confidence": None,
        "source": source,  # 主源 = 答案主源，明细见 sources
        "sources": [source_record(source, license=license, url=url, source_detail=source_detail)],
        "status": "enabled",
    }


FENCE_LINE_RE = re.compile(r"^\s*```.*$", re.M)
# 占位与空壳实测在 0～2 字（`xx`、空代码块），题库里最短的真答案 8 字、真实语料 15 字
MIN_ANSWER_CHARS: Final = 5


def answer_substance(answer: str) -> int:
    """答案的实质字符数：去掉代码围栏行（```lang / ```）与首尾空白后还剩多少。

    围栏只剥「行」不剥内容——` ```python\\n\\n``` ` 这种空壳剥完就是 0；正文里的代码块照算。
    """
    return len(FENCE_LINE_RE.sub("", answer).strip())


def finalize_status(question: dict) -> None:
    """无答案、无实质答案或域未启用 → draft（draft 只进 SQLite，不富化、不进向量库）。

    无实质答案 = 源里的占位（`答案：xx`）与空代码块这类空壳：留着题面但按没有答案处理，
    否则它会以 enabled 身份去富化、进向量库，实际给不出任何参考答案。
    """
    if question["domain"] not in ENABLED_DOMAINS:
        question["status"] = "draft"
    elif answer_substance(question["answer"]) < MIN_ANSWER_CHARS:
        question["status"] = "draft"


def rank(question: dict) -> tuple:
    """同题择优依据（`min` 取优）：主源优先级 > 能用 > 答案长 > 轮次可信（SPEC §8.1）。

    完全同分时 min() 取首个 = 先导入者优先——「导入时间」这一层靠列表序稳定实现，
    不额外记时间戳（谁先被解析进列表，谁就是先导入的）。
    """
    return (
        source_rank(question["source"]),
        0 if question["status"] == "enabled" else 1,
        -len(question["answer"]),
        0 if question["round_confidence"] == "明确" else 1,
    )


def merge_sources(group: list[dict]) -> list[dict]:
    """合并同题多源明细：每源留一条，按主源优先级排序（SPEC §8.1）。

    同一源在同一题上有多条记录（同一题出现在该源的多篇面经里）时只留**最优**那条——
    question_sources 主键是 (question_id, source)，本就不允许多条；按 rank 排后再取首个，
    是为了让出处指向「答案真正来自的那一篇」：占位存根（无答案、无出处）排名靠后，
    不会把有答案正本的 URL 挤掉。被合并掉的明细在合并报告里可见，人工可回溯。
    """
    seen: dict[str, dict] = {}
    for question in sorted(group, key=rank):  # 稳定排序：同分仍是先导入者在前
        for record in question["sources"]:
            seen.setdefault(record["source"], dict(record))
    return sorted(seen.values(), key=lambda r: (source_rank(r["source"]), r["source"]))


def _join_meta(group: list[dict], field: str) -> str | None:
    """同题多源的面经元信息聚合；都没有就是 None，不能退化成空串（空串入库存的是 ''）。"""
    values = dict.fromkeys(q[field] for q in group if q[field])
    return "、".join(values) if values else None


def merge_group(group: list[dict]) -> dict:
    """同组（同题）择优保留一份（`source` 即主源），company/round 聚合去重、来源明细合并。"""
    best = min(group, key=rank)
    record = dict(best)
    record["company"] = _join_meta(group, "company")
    record["round"] = _join_meta(group, "round")
    record["sources"] = merge_sources(group)
    return record


def describe(question: dict) -> str:
    """合并报告里的一行描述（跨源合并必须能看出这条来自哪个语料）。"""
    return (
        f"{question['source']} {question['company']}/{question['round']}"
        f" {question['domain']} 答案 {len(question['answer'])} 字"
    )


def merge_exact_duplicates(questions: list[dict]) -> tuple[list[dict], list[dict]]:
    """同一题干（question_id 相同）合并成一条，返回（合并后列表, 合并明细）。

    两种来源：md 里"原题保留，未收录答案"存根与有答案正本并列（题干相同 → md5 相同 →
    SQLite 主键冲突）；以及同一道题出现在不同语料里（跨源合并的入口）。按 rank 择优保留
    一份，company/round 聚合去重。合并明细供人工复核。
    """
    grouped: dict[str, list[dict]] = {}
    for question in questions:
        grouped.setdefault(question["question_id"], []).append(question)

    merged: list[dict] = []
    report: list[dict] = []
    for group in grouped.values():
        best = min(group, key=rank)
        if len(group) == 1:
            merged.append(best)
            continue

        report.append(
            {
                "question_id": best["question_id"],
                "question": best["question"],
                "kept": describe(best),
                "dropped": [describe(q) for q in group if q is not best],
            }
        )
        merged.append(merge_group(group))
    return merged, report


def normalize(text: str) -> str:
    """去空白与标点，用于相似度比对。"""
    return re.sub(r"[\s，。？！、；：（）()【】\[\]「」“”\"'`~·—\-/|]+", "", text).lower()


def find_duplicates(questions: list[dict], threshold: float = 0.9) -> list[tuple[str, str, float]]:
    """题目文本相似度 ≥ threshold 的候选对，供人工确认（不自动删）。"""
    pairs: list[tuple[str, str, float]] = []
    normalized = [(q["question_id"], normalize(q["question"])) for q in questions]
    for i, (id_a, text_a) in enumerate(normalized):
        for id_b, text_b in normalized[i + 1:]:
            if abs(len(text_a) - len(text_b)) > max(len(text_a), len(text_b)) * 0.3:
                continue
            ratio = difflib.SequenceMatcher(None, text_a, text_b).ratio()
            if ratio >= threshold:
                pairs.append((id_a, id_b, round(ratio, 3)))
    return sorted(pairs, key=lambda p: -p[2])


def print_stats(questions: list[dict]) -> None:
    """题库总览（各源解析脚本共用）。"""
    total = len(questions)
    print(f"题目总数：{total}")
    print(f"状态：{dict(Counter(q['status'] for q in questions))}")
    print("\n来源 × 题量：", dict(Counter(q["source"] for q in questions)))
    print("轮次 × 题量：", dict(Counter(q["round"] for q in questions)))
    print("公司 × 题量：", dict(Counter(q["company"] for q in questions).most_common(8)), "…")
    print("\n知识域 × 题量：")
    for domain, count in Counter(q["domain"] for q in questions).most_common():
        print(f"  {domain:<26} {count:>3}  ({DOMAIN_LABELS.get(domain, '?')})")
    print("\n难度 × 题量：", dict(Counter(q["difficulty"] for q in questions)))
    print("轮次可信度：", dict(Counter(q["round_confidence"] for q in questions)))
    print(f"带原帖 URL：{sum(1 for q in questions for s in q['sources'] if s['url'])} 条来源明细")
    print("主源分布：", dict(Counter(q["source"] for q in questions)))

    enabled = [q for q in questions if q["status"] == "enabled"]
    required = ["question", "answer", "topic", "domain", "difficulty", "source"]
    complete = sum(
        1
        for q in enabled
        if all(q[f] for f in required) and all(s["source"] and s["license"] for s in q["sources"])
    )
    rate = complete / len(enabled) * 100 if enabled else 0.0
    print(f"\nenabled {len(enabled)} 题，必填字段完整率：{rate:.1f}%")
