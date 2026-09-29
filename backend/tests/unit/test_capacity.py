"""容量校验单测（PRD FR-14 / SPEC §9）：纯逻辑、配额与引擎同源。

供给按真实形态构造（{难度: {域: 条数}}），不碰 DB；DB 侧由 test_bank_query 覆盖。
"""

from __future__ import annotations

from app.graph.rules.capacity import base_difficulty, capacity_grid, check_capacity
from app.graph.rules.quota import tech_quota


def _quota(count: int) -> dict[str, int]:
    """只需供的域（配额 > 0）。"""
    return {d: n for d, n in tech_quota(count).items() if n > 0}


def test_供给恰好等于配额即可直供():
    assert check_capacity(10, "L2", {"L2": _quota(10)}) == []


def test_单域不足只报该域():
    supply = _quota(10)
    supply["rag"] -= 1
    assert check_capacity(10, "L2", {"L2": supply}) == [
        {"domain": "rag", "required": _quota(10)["rag"], "available": supply["rag"]}
    ]


def test_配额为零的域不校验():
    """N=2：只有 1 道技术题，其余是 LLM 生成的项目深挖题——不该因没库存被禁。"""
    needed = _quota(2)
    assert len(needed) == 1
    domain = next(iter(needed))
    assert check_capacity(2, "L3", {"L3": {domain: needed[domain]}}) == []


def test_难度缺失视为零供给():
    out = check_capacity(10, "L3", {"L2": _quota(10)})
    assert len(out) == len(_quota(10))
    assert all(item["available"] == 0 for item in out)


def test_自适应按起点L1校验():
    """只给 L1 供给即可通过——不因高难度档缺货被误杀（升档靠引擎放宽兜底）。"""
    assert base_difficulty("adaptive") == "L1"
    assert base_difficulty("L3") == "L3"
    assert check_capacity(15, "adaptive", {"L1": _quota(15)}) == []


def test_真库形态的L3边界():
    """真库（2026-09-29）L3 供给：planning-reasoning 仅 1 题。

    10 题场该域配额 1 → 直供够；15 题场配额 2 → 报不足。
    """
    supply = {"L3": {
        "agent-architecture": 28, "rag": 7, "planning-reasoning": 1,
        "tool-use": 4, "memory": 3, "engineering-observability": 25,
    }}
    assert check_capacity(10, "L3", supply) == []
    assert check_capacity(15, "L3", supply) == [
        {"domain": "planning-reasoning", "required": 2, "available": 1}
    ]


def test_capacity_grid覆盖全部组合():
    supply = {"L1": _quota(15), "L2": _quota(15), "L3": _quota(15)}
    grid = capacity_grid([5, 15], supply)
    assert len(grid) == 8  # 4 难度 × 2 题数
    assert [row["difficulty"] for row in grid] == (
        ["adaptive"] * 2 + ["L1"] * 2 + ["L2"] * 2 + ["L3"] * 2
    )
    assert all(row["ok"] for row in grid)
    assert all(
        set(row) == {"difficulty", "base", "question_count", "ok", "shortfalls"} for row in grid
    )
