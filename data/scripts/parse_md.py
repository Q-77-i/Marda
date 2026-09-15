"""解析个人题库 md（牛客补充版）→ 结构化 JSON。

层级：H1=轮次（一面/二面/三面）、H2=公司、H3=主题、H4=题目；
H4 下紧邻的 blockquote 是元信息（轮次可信度 / 来源 / 原帖 URL）。

两个坑：
1. 答案里的 fenced code block 含 `# 注释`，缩进后可能是 `####` —— 必须跟踪围栏状态；
2. 题目标题有双重编号（`#### 1. 1. xxx`）—— 循环剥离前导编号。

用法：python data/scripts/parse_md.py [--md 路径] [--out 路径]
"""

from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import re
from collections import Counter
from pathlib import Path
from typing import Final

import bootstrap  # noqa: F401  # 把 backend/ 加进 sys.path
from app.domain import DOMAIN_LABELS, ENABLED_DOMAINS
from mapping import DOMAIN_DIFFICULTY_OVERRIDE, ROUND_TO_DIFFICULTY, TOPIC_TO_DOMAIN

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MD = REPO_ROOT / "docs" / "题库" / "md" / "Agent面试-牛客补充版.md"
DEFAULT_OUT = REPO_ROOT / "data" / "parsed" / "questions.json"

SOURCE_NAME = "个人题库-牛客补充版"
LICENSE = "personal"

ROUND_RE = re.compile(r"^#\s+(一面|二面|三面)\s*$")
H2_RE = re.compile(r"^##\s+(.+?)\s*$")
H3_RE = re.compile(r"^###\s+(.+?)\s*$")
H4_RE = re.compile(r"^####\s+(.+?)\s*$")
LEADING_NUMBER_RE = re.compile(r"^(?:\d+\s*[.、]\s*)+")
FENCE_RE = re.compile(r"^\s*(?:```|~~~)")
# md 里元信息是 blockquote（`> 【轮次：…】`），xmind 的 notes 里没有 `>` —— 两种都要认
ROUND_META_RE = re.compile(r"^>?\s*【轮次：(明确|推断)】\s*(.*)$")
NO_ANSWER_MARK = "【原题保留，未收录答案】"
SOURCE_SEP = "｜来源："
BASIS_SEP = "依据："
URL_RE = re.compile(r"https?://\S+")
TAIL_RULE_RE = re.compile(r"\n+-{3,}\s*$")

SKIP_H2 = {"目录", "题量总览"}


def make_id(question: str) -> str:
    """question_id = q_ + md5(题目文本)[:12]，确定性、幂等 upsert 用。"""
    digest = hashlib.md5(question.encode("utf-8")).hexdigest()[:12]
    return f"q_{digest}"


def split_source(meta_tail: str) -> tuple[str, str]:
    """从元信息尾部提取（来源文本, 原帖 URL）。推断类只有依据，无来源。"""
    text = meta_tail.strip()
    if SOURCE_SEP in text:
        text = text.split(SOURCE_SEP, 1)[1]
    elif text.startswith(BASIS_SEP):
        return "", ""
    text = text.lstrip("｜").strip()
    url_match = URL_RE.search(text)
    if not url_match:
        return text, ""
    url = url_match.group(0)
    return text.replace(url, "").strip().strip("｜").strip(), url


def _make_question(raw_title: str, round_: str, company: str, topic: str, origin: str) -> dict:
    question = LEADING_NUMBER_RE.sub("", raw_title).strip()
    if not topic:
        raise ValueError(f"题目缺少所属 topic（H3）：{question}（{origin}）")
    domain = TOPIC_TO_DOMAIN.get(topic)
    if domain is None:
        raise ValueError(f"未知 topic「{topic}」，请补进 mapping.TOPIC_TO_DOMAIN：{question}（{origin}）")
    difficulty = DOMAIN_DIFFICULTY_OVERRIDE.get(domain) or ROUND_TO_DIFFICULTY.get(round_, "L2")
    return {
        "question_id": make_id(question),
        "question": question,
        "answer": "",
        "topic": topic,
        "domain": domain,
        "difficulty": difficulty,
        "company": company,
        "round": round_,
        "round_confidence": None,
        "source": SOURCE_NAME,
        "source_detail": "",
        "license": LICENSE,
        "url": "",
        "status": "enabled",
    }


def _finalize(question: dict, body: list[str]) -> None:
    """从正文里摘出元信息行，其余作为参考答案；定 status。"""
    kept: list[str] = []
    no_answer = False
    for line in body:
        meta = ROUND_META_RE.match(line)
        if meta:
            question["round_confidence"] = meta.group(1)
            question["source_detail"], question["url"] = split_source(meta.group(2))
            continue
        if NO_ANSWER_MARK in line:
            no_answer = True
            continue
        kept.append(line)

    answer = TAIL_RULE_RE.sub("", "\n".join(kept)).strip()
    question["answer"] = answer

    if no_answer or not answer or question["domain"] not in ENABLED_DOMAINS:
        question["status"] = "draft"


def parse_text(text: str, *, origin: str = "<memory>") -> list[dict]:
    questions: list[dict] = []
    round_ = company = topic = None
    current: dict | None = None
    body: list[str] = []
    in_fence = False
    started = False

    def flush() -> None:
        nonlocal current, body
        if current is not None:
            _finalize(current, body)
            questions.append(current)
        current, body = None, []

    for line in text.splitlines():
        line = line.rstrip()

        if FENCE_RE.match(line):
            in_fence = not in_fence
            if current is not None:
                body.append(line)
            continue

        if not in_fence:
            round_match = ROUND_RE.match(line)
            if round_match:
                flush()
                started = True
                round_ = round_match.group(1)
                company = topic = None
                continue

            if started:
                h2 = H2_RE.match(line)
                if h2:
                    flush()
                    company = None if h2.group(1) in SKIP_H2 else h2.group(1)
                    topic = None
                    continue

                h3 = H3_RE.match(line)
                if h3:
                    flush()
                    topic = h3.group(1)
                    continue

                h4 = H4_RE.match(line)
                if h4:
                    flush()
                    current = _make_question(h4.group(1), round_, company, topic, origin)
                    continue

        if current is not None and started:
            body.append(line)

    flush()
    return questions


def parse_file(path: Path) -> list[dict]:
    return parse_text(path.read_text(encoding="utf-8"), origin=str(path))


def _rank(question: dict) -> tuple:
    """同题择优依据：能用 > 答案长 > 轮次可信。"""
    return (
        question["status"] == "enabled",
        len(question["answer"]),
        question["round_confidence"] == "明确",
    )


def _merge_group(group: list[dict]) -> dict:
    """同组（同题）择优保留一份，company/round 聚合去重。"""
    best = max(group, key=_rank)
    record = dict(best)
    record["company"] = "、".join(dict.fromkeys(q["company"] for q in group if q["company"]))
    record["round"] = "、".join(dict.fromkeys(q["round"] for q in group if q["round"]))
    return record


def merge_exact_duplicates(questions: list[dict]) -> tuple[list[dict], list[dict]]:
    """同一题干（question_id 相同）合并成一条。

    md 里存在"原题保留，未收录答案"存根与有答案正本并列的情况，题干完全相同 →
    md5 相同 → SQLite 主键冲突。这里按 _rank 择优保留一份，company/round 聚合去重。
    返回（合并后列表, 合并明细）供人工复核。
    """
    grouped: dict[str, list[dict]] = {}
    for question in questions:
        grouped.setdefault(question["question_id"], []).append(question)

    merged: list[dict] = []
    report: list[dict] = []
    for group in grouped.values():
        best = max(group, key=_rank)
        if len(group) == 1:
            merged.append(best)
            continue

        report.append(
            {
                "question_id": best["question_id"],
                "question": best["question"],
                "kept": f"{best['company']}/{best['round']} {best['domain']} 答案 {len(best['answer'])} 字",
                "dropped": [
                    f"{q['company']}/{q['round']} {q['domain']} 答案 {len(q['answer'])} 字"
                    for q in group
                    if q is not best
                ],
            }
        )
        merged.append(_merge_group(group))
    return merged, report


# 人工确认的近似重复对（find_duplicates ≥0.9 的同题异写，题干不同 → 不同 question_id）。
# 每对是两个题干的前缀；匹配不到或多于一条时报错，防止题库改动后白名单静默失效。
APPROVED_MERGE_PAIRS: Final[list[tuple[str, str]]] = [
    (
        "LLM 推理优化做过哪些工作？用过 continuous batching、KV Cache、vLLM 吗",
        "LLM 推理优化做了哪些？用过 Continuous Batching、KV Cache、vLLM 吗",
    ),
    (
        "Prompt 调优 “修好一类、坏了另一类” 怎么解决",
        "Prompt 调优遇到「修好一类、坏了另一类」怎么解决",
    ),
]


def merge_approved_pairs(questions: list[dict]) -> tuple[list[dict], list[dict]]:
    """按 APPROVED_MERGE_PAIRS 合并人工确认的近似重复题。

    择优逻辑与 merge_exact_duplicates 相同（_rank：enabled > 答案长 > 轮次可信）。
    只处理题干异写产生的重复（question_id 不同）——同 id 的重复在前面已合并。
    """
    by_prefix: dict[str, dict] = {}
    for question in questions:
        for prefix in (p for pair in APPROVED_MERGE_PAIRS for p in pair):
            if question["question"].startswith(prefix):
                if prefix in by_prefix:
                    raise ValueError(f"白名单前缀「{prefix}」匹配到多条题目，请核对：{question['question']}")
                by_prefix[prefix] = question

    missing = [prefix for pair in APPROVED_MERGE_PAIRS for prefix in pair if prefix not in by_prefix]
    if missing:
        raise ValueError(f"白名单前缀在题库中匹配不到：{missing}")

    drop_ids: set[str] = set()
    replaced: dict[str, dict] = {}
    report: list[dict] = []
    for prefix_a, prefix_b in APPROVED_MERGE_PAIRS:
        group = [by_prefix[prefix_a], by_prefix[prefix_b]]
        best = max(group, key=_rank)
        dropped = [q for q in group if q is not best]
        drop_ids.update(q["question_id"] for q in dropped)
        replaced[best["question_id"]] = _merge_group(group)
        report.append(
            {
                "question_id": best["question_id"],
                "question": best["question"],
                "kept": f"{best['company']}/{best['round']} {best['domain']} 答案 {len(best['answer'])} 字",
                "dropped": [
                    f"{q['company']}/{q['round']} {q['domain']} 答案 {len(q['answer'])} 字" for q in dropped
                ],
            }
        )

    merged = [replaced.get(q["question_id"], q) for q in questions if q["question_id"] not in drop_ids]
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
    total = len(questions)
    print(f"题目总数：{total}")
    print(f"状态：{dict(Counter(q['status'] for q in questions))}")
    print("\n轮次 × 题量：", dict(Counter(q["round"] for q in questions)))
    print("公司 × 题量：", dict(Counter(q["company"] for q in questions)))
    print("\n知识域 × 题量：")
    for domain, count in Counter(q["domain"] for q in questions).most_common():
        print(f"  {domain:<26} {count:>3}  ({DOMAIN_LABELS.get(domain, '?')})")
    print("\n难度 × 题量：", dict(Counter(q["difficulty"] for q in questions)))
    print("轮次可信度：", dict(Counter(q["round_confidence"] for q in questions)))
    print(f"带原帖 URL：{sum(1 for q in questions if q['url'])}")

    enabled = [q for q in questions if q["status"] == "enabled"]
    required = ["question", "answer", "topic", "domain", "difficulty", "source", "license"]
    complete = sum(1 for q in enabled if all(q[f] for f in required))
    rate = complete / len(enabled) * 100 if enabled else 0.0
    print(f"\nenabled {len(enabled)} 题，必填字段完整率：{rate:.1f}%")


def main() -> None:
    parser = argparse.ArgumentParser(description="解析个人题库 md → 结构化 JSON")
    parser.add_argument("--md", type=Path, default=DEFAULT_MD)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()

    questions = parse_file(args.md)
    raw_total = len(questions)
    questions, merge_report = merge_exact_duplicates(questions)

    if merge_report:
        print(f"同题干合并：{raw_total} → {len(questions)} 条（{len(merge_report)} 组）")
        for item in merge_report:
            print(f"  [{item['question_id']}] 「{item['question'][:44]}」")
            print(f"      保留 {item['kept']}")
            for dropped in item["dropped"]:
                print(f"      合并 {dropped}")
        print()

    questions, approved_report = merge_approved_pairs(questions)
    if approved_report:
        print(f"白名单近似合并：{len(questions) + len(approved_report)} → {len(questions)} 条（{len(approved_report)} 对，人工确认）")
        for item in approved_report:
            print(f"  [{item['question_id']}] 「{item['question'][:44]}」")
            print(f"      保留 {item['kept']}")
            for dropped in item["dropped"]:
                print(f"      合并 {dropped}")
        print()

    print_stats(questions)

    duplicates = find_duplicates(questions)
    print(f"\n相似度 ≥0.9 的重复候选：{len(duplicates)} 对（供人工确认，不自动处理）")
    seen: set[frozenset[str]] = set()
    for id_a, id_b, ratio in duplicates:
        key = frozenset((id_a, id_b))
        if key in seen:
            continue
        seen.add(key)
        text_a = next(q["question"] for q in questions if q["question_id"] == id_a)
        text_b = next(q["question"] for q in questions if q["question_id"] == id_b)
        print(f"  {ratio}  「{text_a[:40]}」 ↔ 「{text_b[:40]}」")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "meta": {
            "origin": str(args.md),
            "raw_total": raw_total,
            "total": len(questions),
            "merged": len(merge_report),
        },
        "questions": questions,
    }
    args.out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n已写出：{args.out}")


if __name__ == "__main__":
    main()
