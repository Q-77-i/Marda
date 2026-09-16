"""追问节点（SPEC §4.5）：按追问决策生成追问文案，计数与追问日志落 state。"""

from __future__ import annotations

from app import llm
from app.agents.prompts import FOLLOWUP_CLARIFY_TEMPLATE, FOLLOWUP_MISSING_TEMPLATE
from app.graph.rules.follow_up import Decision, decide_follow_up
from app.graph.state import InterviewState, add_history


async def followup_node(state: InterviewState) -> dict:
    question = state.current_question
    # 重算决策（与条件边同一纯函数，幂等；节点内用它决定文案模板）
    decision = decide_follow_up(
        question.score,
        follow_up_count=question.follow_up_count,
        clarify_used=question.clarify_used,
        missing_used=question.missing_used,
    )
    if decision is Decision.CLARIFY:
        question.clarify_used += 1
        prompt = FOLLOWUP_CLARIFY_TEMPLATE.format(question=question.text, answer=question.answer)
    else:
        question.missing_used += 1
        prompt = FOLLOWUP_MISSING_TEMPLATE.format(
            question=question.text,
            answer=question.answer,
            missed_points="；".join(question.score.missed_key_points),
        )
    question.follow_up_count += 1
    text = await llm.chat([{"role": "system", "content": prompt}])
    question.followup_log.append(text)
    add_history(state, "assistant", text)
    return {"current_question": question, "chat_history": state.chat_history}
