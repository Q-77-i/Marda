"""评分节点（SPEC §4.5）：LLM 结构化评分 + 确定性副作用（计数/难度/记录）。

副作用只在首次评分时做（追问补充重评只覆盖 score）——计数/难度一题一次，
answered_questions 保持每题一条最终记录；回答则累加保留（首答 + 追问补充，SPEC §4.1），
复盘卡（FR-25）据此展示「我的回答（含追问轮）」。

行为面（P1-M11）：换行为面评分官与五维 schema；难度自适应**机制保留不关**（少一个分支），
但行为面下难度不参与出题、只作 state 里的死数据（D3）。
"""

from __future__ import annotations

from app import llm
from app.agents.prompts import BEHAVIORAL_JUDGE_TEMPLATE, JUDGE_TEMPLATE
from app.domain import INTERVIEW_BEHAVIORAL
from app.graph.rules.difficulty import update_difficulty
from app.graph.state import (
    InterviewState,
    TraceEvent,
    add_history,
    add_trace,
    merge_answer,
    score_schema_for,
)

JUDGE_TEMPERATURE = 0.3
"""评分官温度（生产值）。**离线评测（P1-M12 会话 2）以它为「生产臂」的基准**——
温度 0 只作对照实验，改这个值要连带回归评分/报告/PDF（评分波动直接进能力曲线）。"""


def judge_messages(
    *,
    question: str,
    key_points: list[str],
    answer: str,
    followup_log: list[str] | tuple[str, ...] = (),
    interview_type: str,
) -> list[dict]:
    """评分官的对话消息（**生产与离线评测的唯一构造入口**）。

    评测 harness 若自己拼一遍 prompt，模板一改就会静默失配——量到的是旧口径。
    故这里抽成公共函数：`judge_node` 与 `evals.judge_run` 都从这里拿消息，
    「评测测的就是生产 prompt」由单测钉死。
    """
    template = (
        BEHAVIORAL_JUDGE_TEMPLATE
        if interview_type == INTERVIEW_BEHAVIORAL
        else JUDGE_TEMPLATE
    )
    return [{"role": "system", "content": template.format(
        question=question,
        key_points="\n".join(f"- {k}" for k in key_points),
        followup_log="\n".join(f"- {line}" for line in followup_log) or "无",
        content=answer,
    )}]


async def judge_node(state: InterviewState) -> dict:
    question = state.current_question
    difficulty_before = state.difficulty
    is_first = question.score is None
    # 先合并再评分（P1-M4.5-R1）：判官看到累计回答（首答+全部追问补充，按标记分段），
    # 「评分以当前掌握程度为准」的 prompt 口径才真正可执行；覆盖率允许下降，反映真实掌握程度
    question.answer = merge_answer(question.answer, state.user_input)
    score = await llm.chat_json(
        judge_messages(
            question=question.text,
            key_points=question.key_points,
            answer=question.answer,
            followup_log=question.followup_log,
            interview_type=state.interview_type,
        ),
        schema=score_schema_for(state.interview_type),
        temperature=JUDGE_TEMPERATURE,
    )
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
