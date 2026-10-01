"""golden 查询集的格式、校验与读写（P1-M12 会话 1）。

一个 golden 文件同时是**标注**与**口径说明书**：头部的 `pool` / `grading` 字段写清
「池怎么来的」「分级怎么定的」，因为指标只能在这个口径内解释（换池 = 换基准，
新旧数字不可比）。校验放在 `load` 里而不是靠自觉——一条没有相关项的 query
会让 NDCG 恒 0 并悄悄拉低均值，必须在进门时就炸。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.tools.hybrid_search import FILTER_KEYS

SCENES: tuple[str, ...] = ("bank_search", "missed_point")
"""查询场景（决定评测时用不用 filter，见 `scripts/eval_retrieval_run.py`）：

- `bank_search`：题库页搜索框形态（无 filter）——也是 M3 探针 query 的归处；
- `missed_point`：学习推荐的漏点驱动查询（带 domain filter，与 FR-20 同形）。

**没有「纯域名回退」这一档**：域名标签查询除了 filter 不含任何信息，域内每题
同等「相关」，分级标注无从谈起——它由 RAGAS 那一路（`ContextRelevancy`）衡量。
"""

GRADES: tuple[int, ...] = (0, 1, 2)
GRADE_MEANINGS: dict[str, str] = {
    "2": "直接命中：这道题问的就是该需求的主题",
    "1": "相邻知识点：同一具体子方向，但不是同一件事",
    "0": "不相关：含「同属 Agent/RAG 等大主题、但子方向不同」",
}


def validate(doc: dict) -> list[str]:
    """返回问题清单（空列表 = 通过）。逐条列全，不早退——一次看全比来回试便宜。"""
    problems: list[str] = []
    for key in ("version", "created", "pool", "grading", "queries"):
        if key not in doc:
            problems.append(f"缺少顶层字段 {key}")
    if problems:
        return problems
    if not isinstance(doc["queries"], list) or not doc["queries"]:
        return ["queries 必须是非空列表"]

    seen_ids: set[str] = set()
    for index, item in enumerate(doc["queries"]):
        where = f"queries[{index}]"
        qid = item.get("id")
        if not qid or not isinstance(qid, str):
            problems.append(f"{where}: 缺 id")
        elif qid in seen_ids:
            problems.append(f"{where}: id 重复（{qid}）")
        else:
            seen_ids.add(qid)
        if item.get("scene") not in SCENES:
            problems.append(f"{where}: scene 必须是 {SCENES} 之一，实际 {item.get('scene')!r}")
        if not (item.get("query") or "").strip():
            problems.append(f"{where}: query 文本为空")
        if item.get("domain") is not None and not isinstance(item["domain"], str):
            problems.append(f"{where}: domain 必须是字符串或 null")
        filters = item.get("filters")
        if filters is not None:
            if not isinstance(filters, dict):
                problems.append(f"{where}: filters 必须是 dict 或 null")
            else:
                unknown = set(filters) - set(FILTER_KEYS)
                if unknown:
                    problems.append(f"{where}: filters 含未知维度 {sorted(unknown)}")
        grades = item.get("grades")
        if not isinstance(grades, dict) or not grades:
            problems.append(f"{where}: grades 必须是非空映射")
            continue
        bad = {k: v for k, v in grades.items() if v not in GRADES}
        if bad:
            problems.append(f"{where}: grades 取值越界 {bad}")
        if not any(g >= 1 for g in grades.values()):
            problems.append(f"{where}: 没有任何 grade ≥ 1 的相关项（该 query 的指标无意义）")
    return problems


def load(path: Path) -> dict[str, Any]:
    """读取并校验；有问题直接抛（把全部问题拼进消息，不用一条条试）。"""
    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    problems = validate(doc)
    if problems:
        raise ValueError(f"golden 文件不合法（{path}）：\n" + "\n".join(f"- {p}" for p in problems))
    return doc


def dump(doc: dict[str, Any], path: Path) -> None:
    """写盘：缩进 2、保留非 ASCII（中文标注要人读），键序保持插入序（diff 更可读）。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def relevant_ids(item: dict) -> set[str]:
    """一条 query 的二值相关集（grade ≥ 1），Recall/MRR 用。"""
    return {qid for qid, grade in item["grades"].items() if grade >= 1}
