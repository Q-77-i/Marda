"""解析 xmind 题库（与 md 同源）→ 结构化 JSON，并和 md 解析结果对账。

xmind = zip → content.json → 3 个 sheet（一面/二面/三面），
树：root → 轮次(sheet title) → 公司 → 主题 → 题目(叶子，notes=参考答案)。
notes 首行同样是【轮次：明确/推断】元信息，故复用 parse_md 的构造与定稿逻辑。

用途：md 是主数据源，xmind 用作**交叉校验**（题数/题干一致率/答案差异），不重复入库。

用法：python data/scripts/parse_xmind.py [--xmind 路径] [--md-json 路径]
"""

from __future__ import annotations

import argparse
import json
import zipfile
from pathlib import Path

import bootstrap  # noqa: F401  # 把 backend/ 加进 sys.path
from parse_md import (
    DEFAULT_OUT as MD_JSON,
    _finalize,
    _make_question,
    find_duplicates,
    merge_approved_pairs,
    merge_exact_duplicates,
    print_stats,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_XMIND = REPO_ROOT / "docs" / "题库" / "xmind" / "Agent面试-牛客补充版.xmind"
DEFAULT_OUT = REPO_ROOT / "data" / "parsed" / "questions_xmind.json"


def _children(topic: dict) -> list[dict]:
    return topic.get("children", {}).get("attached", [])


def _notes_text(topic: dict) -> str:
    return topic.get("notes", {}).get("plain", {}).get("content", "")


def parse_xmind(path: Path) -> list[dict]:
    with zipfile.ZipFile(path) as archive:
        sheets = json.loads(archive.read("content.json"))

    questions: list[dict] = []
    for sheet in sheets:
        # 树：root(Agent面试) → 轮次 → 公司 → 主题 → 题目；sheet title 同为轮次，作兜底
        for round_node in _children(sheet["rootTopic"]):
            round_ = round_node.get("title") or sheet.get("title", "")
            for company_node in _children(round_node):
                company = company_node.get("title", "")
                for topic_node in _children(company_node):
                    topic = topic_node.get("title", "")
                    for leaf in _children(topic_node):
                        question = _make_question(leaf.get("title", ""), round_, company, topic, str(path))
                        _finalize(question, _notes_text(leaf).splitlines())
                        questions.append(question)
    return questions


def compare_with_md(xmind_questions: list[dict], md_path: Path) -> None:
    """对账：题量、题干一致率、单边题、同题答案长度差异。"""
    if not md_path.exists():
        print(f"\n[跳过对账] 未找到 md 解析产物：{md_path}（先跑 parse_md.py）")
        return

    md_questions = json.loads(md_path.read_text(encoding="utf-8"))["questions"]
    md_by_id = {q["question_id"]: q for q in md_questions}
    xm_by_id = {q["question_id"]: q for q in xmind_questions}

    only_md = set(md_by_id) - set(xm_by_id)
    only_xm = set(xm_by_id) - set(md_by_id)
    both = set(md_by_id) & set(xm_by_id)
    overlap = len(both) / max(len(md_by_id), len(xm_by_id)) * 100

    print("\n=== 与 md 解析结果对账 ===")
    print(f"md {len(md_by_id)} 条 / xmind {len(xm_by_id)} 条，题干一致（同 question_id）{len(both)} 条 → 重合率 {overlap:.1f}%")

    if only_md:
        print(f"\n仅 md 有 {len(only_md)} 条：")
        for qid in sorted(only_md)[:10]:
            print(f"  [{qid}] {md_by_id[qid]['question'][:48]}")
    if only_xm:
        print(f"\n仅 xmind 有 {len(only_xm)} 条：")
        for qid in sorted(only_xm)[:10]:
            print(f"  [{qid}] {xm_by_id[qid]['question'][:48]}")

    lengths = [(md_by_id[qid], xm_by_id[qid]) for qid in both]
    shorter = [
        (md_q, xm_q, len(md_q["answer"]), len(xm_q["answer"]))
        for md_q, xm_q in lengths
        if abs(len(md_q["answer"]) - len(xm_q["answer"])) > 40
    ]
    print(f"\n同题答案长度差 >40 字的：{len(shorter)} 条（md / xmind）")
    for md_q, xm_q, lm, lx in sorted(shorter, key=lambda t: -abs(t[2] - t[3]))[:10]:
        print(f"  {lm:>4} / {lx:<4}  {md_q['question'][:44]}")


def main() -> None:
    parser = argparse.ArgumentParser(description="解析 xmind 题库并与 md 结果对账")
    parser.add_argument("--xmind", type=Path, default=DEFAULT_XMIND)
    parser.add_argument("--md-json", type=Path, default=MD_JSON)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()

    questions = parse_xmind(args.xmind)
    raw_total = len(questions)
    questions, merge_report = merge_exact_duplicates(questions)
    print(f"xmind 解析：{raw_total} → 合并后 {len(questions)} 条（{len(merge_report)} 组同题干）")

    questions, approved_report = merge_approved_pairs(questions)
    if approved_report:
        print(f"白名单近似合并：再合并 {len(approved_report)} 对（与 md 侧同一白名单）")

    print_stats(questions)

    duplicates = find_duplicates(questions)
    print(f"\n相似度 ≥0.9 的重复候选：{len(duplicates)} 对")

    compare_with_md(questions, args.md_json)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    payload = {"meta": {"origin": str(args.xmind), "raw_total": raw_total, "total": len(questions)}, "questions": questions}
    args.out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n已写出：{args.out}")


if __name__ == "__main__":
    main()
