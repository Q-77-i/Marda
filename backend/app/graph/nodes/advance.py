"""轮数推进节点（SPEC §4.2 图 ADV）：评分换题后的阶段推进（纯代码）。"""

from __future__ import annotations

from app.graph.rules.advance import phase_after_answer
from app.graph.state import InterviewState


def advance_node(state: InterviewState) -> dict:
    """TECH_BASE 答满 → PROJECT；PROJECT 完成 → CLOSING（rules/advance.py 已单测）。"""
    return {"phase": phase_after_answer(state.phase, state.answered_count, state.question_count)}
