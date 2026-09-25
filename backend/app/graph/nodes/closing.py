"""收尾节点：反问邀请 / 面试官作答 / 提前结束挽留（PRD §4.1 CLOSING 阶段）。"""

from __future__ import annotations

from app import llm
from app.agents.prompts import ANSWER_CANDIDATE_TEMPLATE, CLOSING_INVITE_TEMPLATE, REFUSE_END_TEMPLATE
from app.graph.rules.advance import end_quota
from app.graph.state import InterviewState, TraceEvent, add_history, add_trace

CLOSING_QUESTION_LIMIT = 2  # PRD §4.1：候选人提问 1-2 个后收尾


async def closing_invite_node(state: InterviewState) -> dict:
    text = await llm.chat([{"role": "system", "content": CLOSING_INVITE_TEMPLATE}])
    add_history(state, "assistant", text)
    return {"chat_history": state.chat_history}


async def answer_candidate_node(state: InterviewState) -> dict:
    text = await llm.chat(
        [{"role": "system", "content": ANSWER_CANDIDATE_TEMPLATE.format(content=state.user_input)}]
    )
    state.closing_question_count += 1
    add_history(state, "user", state.user_input)
    add_history(state, "assistant", text)
    return {"closing_question_count": state.closing_question_count, "chat_history": state.chat_history}


async def refuse_end_node(state: InterviewState) -> dict:
    """主动结束但未达 60% 门槛：礼貌挽留，不暴露门槛数字（PRD §4.5）。"""
    question = state.current_question.text if state.current_question else ""
    text = await llm.chat(
        [{"role": "system", "content": REFUSE_END_TEMPLATE.format(question=question)}]
    )
    add_history(state, "assistant", text)
    # 回放证据（FR-21）：挽留归当前正在答的轮次，门槛与 meets_end_quota 同源
    add_trace(state, TraceEvent.END_REFUSED, {
        "answered_count": state.answered_count,
        "threshold": end_quota(state.question_count),
    }, round_no=state.answered_count + 1)
    return {"chat_history": state.chat_history, "trace_log": state.trace_log}
