"""阶段推进与主动结束门槛（纯代码，SPEC §4.3）。

- 主动结束门槛：已答 ≥ ceil(question_count × 0.6)（PRD §4.5，否则拒绝并继续）；
- 阶段推进（轮次语义，T7a-R1）：技术轮答满（question_count − SCENARIO_COUNT）
  → 场景题（SCENARIO_COUNT 道）→ 反问。question_count 为全场问答轮次。
"""

from __future__ import annotations

from math import ceil

from app.domain import SCENARIO_COUNT
from app.graph.state import Phase

END_QUOTA_RATIO = 0.6 # 结束配额比例
FLOAT_EPSILON = 1e-9  # 0.6 浮点表示略小，乘出来的积直接 ceil 会差 1（如 15×0.6=8.999…）

# 主动结束指令（前端结束按钮发送「结束面试」，T5 契约沿用）
END_COMMANDS = frozenset({"结束面试", "结束"})


def is_end_command(text: str) -> bool:
    """用户消息是否为主动结束指令（纯代码判定，不进 prompt）。"""
    return text.strip() in END_COMMANDS


def end_quota(question_count: int) -> int:
    """主动结束所需的已答题量门槛（题数）。回放展示「未达门槛被挽留」用同一口径。"""
    return ceil(question_count * END_QUOTA_RATIO - FLOAT_EPSILON)


def meets_end_quota(answered_count: int, question_count: int) -> bool:
    """主动结束（结束按钮 / 结束指令）是否达到已答题量门槛。"""
    return answered_count >= end_quota(question_count)


def phase_after_answer(phase: Phase, answered_count: int, question_count: int) -> Phase:
    """一道题评分完成后应进入的阶段（TECH_BASE 技术轮答满 → PROJECT → CLOSING）。"""
    if phase == Phase.TECH_BASE and answered_count >= question_count - SCENARIO_COUNT:
        return Phase.PROJECT
    if phase == Phase.PROJECT:
        return Phase.CLOSING
    return phase
