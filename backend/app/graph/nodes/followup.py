"""追问节点（SPEC §4.5）：按追问决策生成追问文案，计数与追问日志落 state。"""

from __future__ import annotations

from app import llm
from app.agents.prompts import (
    FOLLOWUP_CLARIFY_TEMPLATE,
    FOLLOWUP_DEEPEN_TEMPLATE,
    FOLLOWUP_MISSING_TEMPLATE,
)
from app.graph.rules.follow_up import (
    Decision,
    explain_decision,
    remedy_used_total,
    unasked_missed,
)
from app.graph.state import InterviewState, TraceEvent, add_history, add_trace


async def followup_node(state: InterviewState) -> dict:
    question = state.current_question
    # 重算决策（与条件边同一纯函数，幂等；节点内用它决定文案模板与回放原因码）
    decision, reason = explain_decision(
        question.score,
        question_count=state.question_count,
        clarify_used=question.clarify_used,
        missing_used=question.missing_used,
        deepen_used=question.deepen_used,
        remedy_used=remedy_used_total(state),
        asked_key_points=question.asked_key_points,
    )
    if decision is Decision.CLARIFY:
        question.clarify_used += 1
        prompt = FOLLOWUP_CLARIFY_TEMPLATE.format(question=question.text, answer=question.answer)
        text = await llm.chat([{"role": "system", "content": prompt}])
    elif decision is Decision.MISSING:
        question.missing_used += 1
        # 只问没问过的漏点（同一 key_point 只追问一次），并写入 asked 集合
        unasked = unasked_missed(question.score, question.asked_key_points)
        question.asked_key_points.extend(unasked)
        prompt = FOLLOWUP_MISSING_TEMPLATE.format(
            question=question.text,
            answer=question.answer,
            missed_points="；".join(unasked),
        )
        text = await llm.chat([{"role": "system", "content": prompt}])
    else:  # DEEPEN
        question.deepen_used += 1
        # 题库题直接发 follow_ups 元数据（P1-M4.5 拍板：零 LLM 调用，确定性可回放）；
        # 生成题/无元数据 → LLM 从问答上下文现场生成深挖追问
        if question.from_bank and question.follow_ups:
            text = question.follow_ups[question.deepen_used - 1]
        else:
            prompt = FOLLOWUP_DEEPEN_TEMPLATE.format(
                question=question.text, answer=question.answer
            )
            text = await llm.chat([{"role": "system", "content": prompt}])
    question.follow_up_count += 1
    question.followup_log.append(text)
    add_history(state, "assistant", text)
    # 回放证据（FR-21）：追问决策 + 原因（与条件边同源，见 explain_decision）
    add_trace(state, TraceEvent.FOLLOWUP, {
        "decision": decision.value,
        "reason": reason.value,
        "text": text,
    }, round_no=state.answered_count)
    return {
        "current_question": question,
        "chat_history": state.chat_history,
        "trace_log": state.trace_log,
    }
