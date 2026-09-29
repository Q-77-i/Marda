"""容量校验（纯代码，PRD FR-14 / SPEC §9）：所选配置的题库直供能力。

口径（P1-M6 拍板 2A）：校验**直供**——每个技术域在该难度下的 enabled 题数
≥ 该域配额（配额与引擎同源：quota.tech_quota = allocate_quota(N − project_count(N))）。
不足**不阻断创建**（引擎有「原档 → 低一档 → 高一档」放宽 + LLM 生成兜底），
只作为创建表单的禁用与提示依据。

项目深挖题由 LLM 按候选人简历生成（from_bank=False）、不占题库供给，故不参与校验；
配额为 0 的域同样不校验（该场次不会问到它）。
"""

from __future__ import annotations

ADAPTIVE = "adaptive"
DIFFICULTY_OPTIONS: tuple[str, ...] = (ADAPTIVE, "L1", "L2", "L3")
ADAPTIVE_BASE = "L1"  # 自适应起点（rules/difficulty 从 L1 起，靠连击升降）


def base_difficulty(value: str) -> str:
    """选项值 → 实际选题难度：自适应按起点 L1 校验（升档靠引擎放宽，不由题库预供）。"""
    return ADAPTIVE_BASE if value == ADAPTIVE else value


def check_capacity(
    question_count: int, difficulty: str, supply: dict[str, dict[str, int]]
) -> list[dict]:
    """直供不足的域清单 `[{domain, required, available}]`（按权重表顺序）；空 = 可直供。

    supply = {难度: {域: enabled 题数}}（tools/bank_query.difficulty_supply）。
    """
    from app.graph.rules.quota import tech_quota  # 局部导入：与 quota 相互独立、避免环

    quota = tech_quota(question_count)
    available = supply.get(base_difficulty(difficulty), {})
    return [
        {"domain": domain, "required": need, "available": available.get(domain, 0)}
        for domain, need in quota.items()
        if need > 0 and available.get(domain, 0) < need
    ]


def capacity_grid(question_counts: list[int], supply: dict[str, dict[str, int]]) -> list[dict]:
    """创建表单的全部选项（难度 × 题数）：`{difficulty, base, question_count, ok, shortfalls}`。"""
    rows: list[dict] = []
    for option in DIFFICULTY_OPTIONS:
        for count in question_counts:
            shortfalls = check_capacity(count, option, supply)
            rows.append({
                "difficulty": option,
                "base": base_difficulty(option),
                "question_count": count,
                "ok": not shortfalls,
                "shortfalls": shortfalls,
            })
    return rows
