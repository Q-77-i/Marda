"""评分节点（SPEC §4.5）：LLM 结构化评分 + 确定性副作用（计数/难度/记录）。

副作用只在首次评分时做（追问补充重评只覆盖 score）——计数/难度一题一次，
answered_questions 保持每题一条最终记录；回答则累加保留（首答 + 追问补充，SPEC §4.1），
复盘卡（FR-25）据此展示「我的回答（含追问轮）」。
"""

from __future__ import annotations

from app import llm
from app.agents.prompts import JUDGE_TEMPLATE
from app.graph.rules.difficulty import update_difficulty
from app.graph.state import InterviewState, ScoreItem, TraceEvent, add_history, add_trace, merge_answer


async def judge_node(state: InterviewState) -> dict:
    question = state.current_question
    difficulty_before = state.difficulty
    score = await llm.chat_json(
        [{"role": "system", "content": JUDGE_TEMPLATE.format(
            question=question.text,
            key_points="\n".join(f"- {k}" for k in question.key_points),
            followup_log="\n".join(f"- {line}" for line in question.followup_log) or "无",
            content=state.user_input,
        )}],
        schema=ScoreItem,
        temperature=0.3,
    )
    is_first = question.score is None
    question.answer = merge_answer(question.answer, state.user_input)
    question.score = score
    add_history(state, "user", state.user_input)
    updates: dict = {"current_question": question, "chat_history": state.chat_history}
    if is_first:
        state.answered_count += 1
        state.answered_questions.append(question)
        update_difficulty(state)
        updates.update({
            "answered_count": state.answered_count,
            "answered_questions": state.answered_questions,
            "difficulty": state.difficulty,
            "consecutive_good": state.consecutive_good,
            "consecutive_bad": state.consecutive_bad,
        })
    else:
        state.answered_questions[-1] = question  # 追问重评：覆盖该题最终记录
        updates["answered_questions"] = state.answered_questions
    # 回放证据（FR-21）：输入 = 本轮回答原文，输出 = 五维/覆盖率，状态变化 = 难度
    add_trace(state, TraceEvent.JUDGE, {
        "answer": state.user_input,
        "score": score.model_dump(),
        "coverage": round(score.coverage, 4),
        "difficulty": state.difficulty,
        "difficulty_changed": state.difficulty != difficulty_before,
    }, round_no=state.answered_count)
    updates["trace_log"] = state.trace_log
    return updates
