"""评分 golden 集的格式、校验与读写（P1-M12 会话 2）。

与检索 golden（`golden.py`）同一条纪律：**一条坏样本会让指标悄悄失真**（比如
期望分缺了一维 → 那一维的 MAE 恒 0），所以进门就炸，不靠自觉。校验逐条列全、不早退。

样本三臂（`arm`）：

- `real`：真库技术面历史回答（**按分数段分层抽**，见 `scripts/eval_judge_golden.py`）；
- `behavioral`：真库行为面历史回答（小样本；已知局限：集中在低-中分段）；
- `persona`：同一道题的**弱/中/强三个回答**（`group` 相同、`tier` 不同）——
  只有同题的三档才能测「评分官能不能把好坏分开」，不同题的弱中强不构成单调性证据。

`expected` 是**人审过的期望分**（LLM 温度 0 预判 → review.md 人工抽检 → 改文件），
不是「当时实得」——实得分数是评分官自己的输出，拿它当基准等于自己给自己打分。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.domain import INTERVIEW_TYPES
from app.graph.rules.aggregate import dims_for

from evals.judge_metrics import TIER_ORDER

ARMS: tuple[str, ...] = ("real", "behavioral", "persona")

# 臂与类型的绑定：真库样本的臂由场次类型决定，不能标错（错标会让切片读错）
_ARM_TYPES: dict[str, str] = {"real": "tech", "behavioral": "behavioral"}
_PERSONA_TYPES: tuple[str, ...] = ("tech", "behavioral")


def validate(doc: dict) -> list[str]:
    """返回问题清单（空列表 = 通过）。逐条列全，一次看全比来回试便宜。"""
    problems: list[str] = []
    for key in ("version", "created", "runs", "temperature", "items"):
        if key not in doc:
            problems.append(f"缺少顶层字段 {key}")
    if problems:
        return problems
    if not isinstance(doc["runs"], int) or doc["runs"] < 2:
        problems.append("runs 必须是 ≥2 的整数（单次运行没有波动可言）")
    if not isinstance(doc["items"], list) or not doc["items"]:
        return ["items 必须是非空列表"]

    seen_ids: set[str] = set()
    group_questions: dict[str, str] = {}
    group_tiers: dict[str, set[str]] = {}
    for index, item in enumerate(doc["items"]):
        where = f"items[{index}]"
        iid = item.get("id")
        if not iid or not isinstance(iid, str):
            problems.append(f"{where}: 缺 id")
        elif iid in seen_ids:
            problems.append(f"{where}: id 重复（{iid}）")
        else:
            seen_ids.add(iid)

        arm = item.get("arm")
        itype = item.get("interview_type")
        if arm not in ARMS:
            problems.append(f"{where}: arm 必须是 {ARMS} 之一，实际 {arm!r}")
        if itype not in INTERVIEW_TYPES:
            problems.append(f"{where}: interview_type 必须是 {sorted(INTERVIEW_TYPES)} 之一")
        elif arm in _ARM_TYPES and itype != _ARM_TYPES[arm]:
            problems.append(f"{where}: arm={arm} 的样本 interview_type 必须是 {_ARM_TYPES[arm]}")
        elif arm == "persona" and itype not in _PERSONA_TYPES:
            problems.append(f"{where}: persona 臂的 interview_type 必须是 {_PERSONA_TYPES} 之一")

        if not (item.get("question") or "").strip():
            problems.append(f"{where}: question 为空")
        if not (item.get("answer") or "").strip():
            problems.append(f"{where}: answer 为空")
        key_points = item.get("key_points")
        if not isinstance(key_points, list):
            problems.append(f"{where}: key_points 必须是列表（可为空）")

        expected = item.get("expected")
        if not isinstance(expected, dict):
            problems.append(f"{where}: 缺 expected")
        else:
            dims = expected.get("dims")
            want_dims = set(dims_for(itype if itype in INTERVIEW_TYPES else "tech"))
            if not isinstance(dims, dict):
                problems.append(f"{where}: expected.dims 必须是映射")
            else:
                if set(dims) != want_dims:
                    problems.append(
                        f"{where}: expected.dims 维度键必须与 {itype} 的维度表完全一致"
                        f"（期望 {sorted(want_dims)}，实际 {sorted(dims)}）"
                    )
                bad = {k: v for k, v in dims.items() if not isinstance(v, int) or not 1 <= v <= 5}
                if bad:
                    problems.append(f"{where}: expected.dims 取值必须是 1-5 整数，越界 {bad}")
            covered = expected.get("covered_indexes")
            if not isinstance(covered, list) or any(not isinstance(i, int) for i in covered):
                problems.append(f"{where}: expected.covered_indexes 必须是整数列表")
            elif isinstance(key_points, list) and any(
                i < 0 or i >= len(key_points) for i in covered
            ):
                problems.append(f"{where}: expected.covered_indexes 越界（key_points 共 {len(key_points)} 条）")

        if arm == "persona":
            group, tier = item.get("group"), item.get("tier")
            if not group or not isinstance(group, str):
                problems.append(f"{where}: persona 臂必须带 group")
            if tier not in TIER_ORDER:
                problems.append(f"{where}: persona 臂的 tier 必须是 {TIER_ORDER} 之一")
            elif group:
                if tier in group_tiers.setdefault(group, set()):
                    problems.append(f"{where}: 组 {group} 里 tier={tier} 重复")
                group_tiers[group].add(tier)
                question = (item.get("question") or "").strip()
                if group in group_questions and group_questions[group] != question:
                    problems.append(
                        f"{where}: 组 {group} 内题干不一致——弱/中/强必须是**同一道题**"
                        "（不同题的三个回答测不出单调性）"
                    )
                group_questions.setdefault(group, question)

    for group, tiers in group_tiers.items():
        if len(tiers) < 2:
            problems.append(f"组 {group} 只有 {sorted(tiers)} 一档，构成不了单调性检查")
    return problems


def load(path: Path) -> dict[str, Any]:
    """读取并校验；有问题直接抛（把全部问题拼进消息，不用一条条试）。"""
    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    problems = validate(doc)
    if problems:
        raise ValueError(f"judge golden 不合法（{path}）：\n" + "\n".join(f"- {p}" for p in problems))
    return doc


def dump(doc: dict[str, Any], path: Path) -> None:
    """写盘：缩进 2、保留非 ASCII（中文样本要人读），键序保持插入序。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def slice_items(doc: dict, *, arm: str | None = None, interview_type: str | None = None) -> list[dict]:
    """按臂/类型切片（报告的分组口径与指标汇总共用同一实现，避免两处口径漂移）。"""
    return [
        item
        for item in doc["items"]
        if (arm is None or item.get("arm") == arm)
        and (interview_type is None or item.get("interview_type") == interview_type)
    ]
