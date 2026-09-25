"""轮数推进节点（SPEC §4.2 图 ADV）：评分换题后的阶段推进（纯代码）。"""

from __future__ import annotations

from app.graph.rules.advance import phase_after_answer
from app.graph.rules.follow_up import explain_decision
from app.graph.state import InterviewState, TraceEvent, add_trace


def advance_node(state: InterviewState) -> dict:
    """TECH_BASE 答满 → PROJECT；PROJECT 完成 → CLOSING（rules/advance.py 已单测）。"""
    phase = phase_after_answer(state.phase, state.answered_count, state.question_count)
    question = state.current_question
    # 回放证据（FR-21 换题原因）：与条件边同一函数重算，计数未变故与决策一致
    _, reason = explain_decision(
        question.score,
        follow_up_count=question.follow_up_count,
        clarify_used=question.clarify_used,
        missing_used=question.missing_used,
    )
    add_trace(state, TraceEvent.ADVANCE, {
        "reason": reason.value,
        "phase": phase.value,
    }, round_no=state.answered_count)
    return {"phase": phase, "trace_log": state.trace_log}
