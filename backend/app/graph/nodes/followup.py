"""追问节点（SPEC §4.5）：按追问决策生成追问文案，计数与追问日志落 state。"""

from __future__ import annotations

import asyncio

from app.agents.prompts import (
    FOLLOWUP_CLARIFY_TEMPLATE,
    FOLLOWUP_DEEPEN_BEHAVIORAL_TEMPLATE,
    FOLLOWUP_DEEPEN_TEMPLATE,
    FOLLOWUP_MISSING_TEMPLATE,
    persona_for,
)
from app.config import get_settings
from app.domain import QUESTION_TYPE_BEHAVIORAL
from app.graph.rules import stream
from app.graph.rules.follow_up import (
    Decision,
    explain_decision,
    remedy_used_total,
    unasked_missed,
)
from app.graph.state import InterviewState, TraceEvent, add_history, add_trace
from app.tools import images as image_store


def _deepen_only(question) -> bool:
    """行为面 deepen-only 判据（P1-M11 ①）：按题型而非会话类型——将来若混排也成立。"""
    return question.question_type == QUESTION_TYPE_BEHAVIORAL


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
        deepen_only=_deepen_only(question),
    )
    persona = persona_for(state.interview_type)
    # 图附件（P2-M6）：追问基于「该题累积的图」（首答+追问补充），取最近 N 张；
    # 文件缺失/损坏由 load_image_parts 跳过降级（不因图丢文件而拒答）
    parts = await asyncio.to_thread(
        image_store.load_image_parts, get_settings().upload_dir, state.interview_id, question.image_ids
    )
    extra: list[dict] = (
        [image_store.attachment_message(parts, image_store.NOTE_FOLLOWUP)] if parts else []
    )
    if decision is Decision.CLARIFY:
        question.clarify_used += 1
        prompt = FOLLOWUP_CLARIFY_TEMPLATE.format(
            persona=persona, question=question.text, answer=question.answer
        )
        text = await stream.speak(
            [{"role": "system", "content": prompt}, *extra],
            purpose="followup",  # 成本归因（P2-M10）
        )
    elif decision is Decision.MISSING:
        question.missing_used += 1
        # 只问没问过的漏点（同一 key_point 只追问一次），并写入 asked 集合
        unasked = unasked_missed(question.score, question.asked_key_points)
        question.asked_key_points.extend(unasked)
        prompt = FOLLOWUP_MISSING_TEMPLATE.format(
            persona=persona,
            question=question.text,
            answer=question.answer,
            missed_points="；".join(unasked),
        )
        text = await stream.speak(
            [{"role": "system", "content": prompt}, *extra],
            purpose="followup",  # 成本归因（P2-M10）
        )
    else:  # DEEPEN
        question.deepen_used += 1
        # 题库题直接发 follow_ups 元数据（P1-M4.5 拍板：零 LLM 调用，确定性可回放）；
        # 生成题/无元数据 → LLM 从问答上下文现场生成深挖追问
        # （行为面走行为面口吻模板：不同「底层机制/权衡」，问细节与情境，P1-M11）
        if question.from_bank and question.follow_ups:
            text = question.follow_ups[question.deepen_used - 1]
            stream.begin(text)  # 纯元数据文案：仍要走流式通道，否则会插到别的消息之后
        else:
            template = (
                FOLLOWUP_DEEPEN_BEHAVIORAL_TEMPLATE
                if _deepen_only(question)
                else FOLLOWUP_DEEPEN_TEMPLATE
            )
            prompt = template.format(
                persona=persona, question=question.text, answer=question.answer
            )
            text = await stream.speak(
            [{"role": "system", "content": prompt}, *extra],
            purpose="followup",  # 成本归因（P2-M10）
        )
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
