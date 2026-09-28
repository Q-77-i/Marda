"""合并个人题库与开源语料 → 单一题库 JSON（入库前的最后一步）。

个人题库（`parse_md` → questions_enriched.json，342 题、已富化）与开源语料（`parse_open` →
questions_open.json）按题干 md5 合并：同题一条多源，主源裁决见 `bank.rank`（个人题库恒 0，
开源答案再长也不顶替）。个人题库的既有字段必须逐字不变——本脚本自带零回归校验，
只允许新增 `sources` 明细行；校验不过非零退出，不允许带病入库。

产出 data/parsed/questions_combined.json，并打印跨源合并明细与相似度 ≥0.9 的人工确认清单
（近似重复**只报不并**，按 SPEC §8.1 需人工登记白名单）。

用法：python data/scripts/combine.py
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import bootstrap  # noqa: F401  # 把 backend/ 加进 sys.path
from bank import (
    SOURCE_PERSONAL,
    find_duplicates,
    merge_exact_duplicates,
    print_stats,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PERSONAL = REPO_ROOT / "data" / "parsed" / "questions_enriched.json"
DEFAULT_OPEN = REPO_ROOT / "data" / "parsed" / "questions_open.json"
DEFAULT_OUT = REPO_ROOT / "data" / "parsed" / "questions_combined.json"

# 个人题库除 sources 外必须逐字不变的字段：动了任何一个都是回归
FROZEN_FIELDS = (
    "question",
    "answer",
    "topic",
    "domain",
    "difficulty",
    "company",
    "round",
    "round_confidence",
    "status",
    "key_points",
    "follow_ups",
    "source",
)


def load(path: Path) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))["questions"]


def verify_frozen(before: list[dict], merged: dict[str, dict]) -> list[str]:
    """个人题库零回归校验：题还在、关键字段逐字相同；只允许 sources 增行。"""
    problems: list[str] = []
    for record in before:
        after = merged.get(record["question_id"])
        if after is None:
            problems.append(f"题目丢失：{record['question']}")
            continue
        for field in FROZEN_FIELDS:
            if after.get(field) != record.get(field):
                problems.append(
                    f"{field} 被改动：{record['question'][:30]}"
                    f"（{record.get(field)!r} → {after.get(field)!r}）"
                )
    return problems


def _corpus(question: dict) -> str:
    """这条题归属哪个语料：含个人题库源即「个人」，否则「开源」。"""
    return "个人" if any(s["source"] == SOURCE_PERSONAL for s in question["sources"]) else "开源"


def report_merges(questions: list[dict], merge_report: list[dict]) -> int:
    """打印跨源合并明细，返回其中「涉及个人题库」的组数。"""
    by_id = {q["question_id"]: q for q in questions}
    involving_personal = 0
    if not merge_report:
        print("\n跨源同题干合并：0 组")
        return 0

    print(f"\n跨源同题干合并：{len(merge_report)} 组")
    for item in sorted(
        merge_report, key=lambda i: _corpus(by_id[i["question_id"]]) != "个人"
    ):
        question = by_id[item["question_id"]]
        if _corpus(question) == "个人":
            involving_personal += 1
        mark = "★" if _corpus(question) == "个人" else " "
        print(f"  {mark} [{item['question_id']}] 「{item['question'][:44]}」")
        print(f"      保留 {item['kept']}")
        for dropped in item["dropped"]:
            print(f"      合并 {dropped}")
    return involving_personal


def report_duplicates(questions: list[dict]) -> None:
    """相似度 ≥0.9 的候选对——只报不并，等人工登记白名单（SPEC §8.1）。"""
    by_id = {q["question_id"]: q for q in questions}
    duplicates = find_duplicates(questions)
    mixed = [p for p in duplicates if _corpus(by_id[p[0]]) != _corpus(by_id[p[1]])]
    print(f"\n相似度 ≥0.9 的重复候选：{len(duplicates)} 对（其中个人×开源 {len(mixed)} 对，只报不并）")
    for id_a, id_b, ratio in sorted(duplicates, key=lambda p: p in mixed, reverse=True):
        text_a, text_b = by_id[id_a]["question"], by_id[id_b]["question"]
        mark = "★" if (id_a, id_b, ratio) in mixed else " "
        print(f"  {mark} {ratio}  「{text_a[:40]}」 ↔ 「{text_b[:40]}」")


def main() -> None:
    parser = argparse.ArgumentParser(description="合并个人题库与开源语料 → 单一题库 JSON")
    parser.add_argument("--personal", type=Path, default=DEFAULT_PERSONAL)
    parser.add_argument("--open", dest="open_path", type=Path, default=DEFAULT_OPEN)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()

    personal = load(args.personal)
    open_corpus = load(args.open_path)
    print(f"个人题库 {len(personal)} 题（{args.personal.name}）")
    print(f"开源语料 {len(open_corpus)} 题（{args.open_path.name}）")

    # 输入序：个人题库在前——完全同分时先导入者优先（须与最终入库口径一致）
    questions, merge_report = merge_exact_duplicates(personal + open_corpus)
    by_id = {q["question_id"]: q for q in questions}

    involving_personal = report_merges(questions, merge_report)

    problems = verify_frozen(personal, by_id)
    if problems:
        print(f"\n个人题库零回归校验：不通过（{len(problems)} 处）")
        for problem in problems:
            print(f"  {problem}")
        sys.exit(1)
    print(f"\n个人题库零回归校验：通过（{len(personal)} 题字段逐字未变，合并只新增 sources 明细）")
    print(f"  其中与开源语料同题合并的：{involving_personal} 题（个人题库为主源，开源明细追加）")

    print_stats(questions)
    report_duplicates(questions)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "meta": {
            "origin": {"personal": str(args.personal), "open": str(args.open_path)},
            "personal_total": len(personal),
            "open_total": len(open_corpus),
            "total": len(questions),
            "merged": len(merge_report),
        },
        "questions": questions,
    }
    args.out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n已写出：{args.out}")


if __name__ == "__main__":
    main()
