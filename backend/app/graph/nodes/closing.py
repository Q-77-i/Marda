"""收尾节点：反问邀请 / 面试官作答 / 提前结束挽留（PRD §4.1 CLOSING 阶段）。

三条文案全部有固定兜底（P2-M9）：它们是礼节与转场，断 LLM 时换模板、
面试照常收尾——不能因为文案服务断了就把用户卡在收尾阶段。
"""

from __future__ import annotations

import asyncio
import logging

from app import llm
from app.agents import fallbacks
from app.agents.prompts import ANSWER_CANDIDATE_TEMPLATE, CLOSING_INVITE_TEMPLATE, REFUSE_END_TEMPLATE
from app.agents.prompts import persona_for
from app.config import get_settings
from app.graph.rules import degrade, stream
from app.domain import INTERVIEW_BEHAVIORAL
from app.graph.rules.advance import end_quota
from app.graph.state import InterviewState, TraceEvent, add_history, add_trace
from app.tools import images as image_store

logger = logging.getLogger(__name__)

CLOSING_QUESTION_LIMIT = 2  # PRD §4.1：候选人提问 1-2 个后收尾


async def closing_invite_node(state: InterviewState) -> dict:
    section = "行为面" if state.interview_type == INTERVIEW_BEHAVIORAL else "技术问答"
    persona = persona_for(state.interview_type)
    try:
        text = await stream.speak(
            [{"role": "system", "content": CLOSING_INVITE_TEMPLATE.format(
                persona=persona, section=section,
            )}],
            purpose="closing",  # 成本归因（P2-M10）
        )
    except llm.LLMError as exc:
        degrade.reraise_if_content(exc)  # 内容类不降级（见 degrade 模块）
        logger.warning("反问邀请生成失败，改用固定文案（P2-M9 降级）：%s", exc)
        degrade.mark(state, degrade.SCRIPT_FALLBACK)
        text = await stream.speak_fallback(
            "", fallbacks.CLOSING_INVITE_FALLBACK.format(persona=persona, section=section)
        )
    add_history(state, "assistant", text)
    return {"chat_history": state.chat_history, "degraded_reasons": state.degraded_reasons}


async def answer_candidate_node(state: InterviewState) -> dict:
    # 反问也可以带图（P2-M6）：「你能看看我这段代码吗」+ 截图是真实交互——不带图
    # 等于面试官对着图的问题盲答（同一「传了白看」缺口的另一处）
    parts = await asyncio.to_thread(
        image_store.load_image_parts, get_settings().upload_dir, state.interview_id, state.current_images
    )
    extra: list[dict] = (
        [image_store.attachment_message(parts, image_store.NOTE_CLOSING)] if parts else []
    )
    persona = persona_for(state.interview_type)
    try:
        text = await stream.speak(
            [{"role": "system", "content": ANSWER_CANDIDATE_TEMPLATE.format(
                persona=persona, content=state.user_input,
            )}, *extra],
            purpose="closing",  # 成本归因（P2-M10）
        )
    except llm.LLMError as exc:
        degrade.reraise_if_content(exc)  # 内容类不降级（见 degrade 模块）
        logger.warning("反问作答生成失败，改用固定文案（P2-M9 降级）：%s", exc)
        degrade.mark(state, degrade.SCRIPT_FALLBACK)
        text = await stream.speak_fallback(
            "",
            fallbacks.ANSWER_CANDIDATE_FALLBACK.format(
                persona=persona, content=state.user_input
            ),
        )
    state.closing_question_count += 1
    add_history(state, "user", state.user_input, image_ids=state.current_images)
    add_history(state, "assistant", text)
    return {
        "closing_question_count": state.closing_question_count,
        "chat_history": state.chat_history,
        "degraded_reasons": state.degraded_reasons,
    }


async def refuse_end_node(state: InterviewState) -> dict:
    """主动结束但未达 60% 门槛：礼貌挽留，不暴露门槛数字（PRD §4.5）。"""
    question = state.current_question.text if state.current_question else ""
    persona = persona_for(state.interview_type)
    try:
        text = await stream.speak(
            [{"role": "system", "content": REFUSE_END_TEMPLATE.format(
                persona=persona, question=question,
            )}],
            purpose="closing",  # 成本归因（P2-M10）
        )
    except llm.LLMError as exc:
        degrade.reraise_if_content(exc)  # 内容类不降级（见 degrade 模块）
        logger.warning("挽留文案生成失败，改用固定文案（P2-M9 降级）：%s", exc)
        degrade.mark(state, degrade.SCRIPT_FALLBACK)
        text = await stream.speak_fallback(
            "", fallbacks.REFUSE_END_FALLBACK.format(persona=persona, question=question)
        )
    add_history(state, "assistant", text)
    # 回放证据（FR-21）：挽留归当前正在答的轮次，门槛与 meets_end_quota 同源
    add_trace(state, TraceEvent.END_REFUSED, {
        "answered_count": state.answered_count,
        "threshold": end_quota(state.question_count),
    }, round_no=state.answered_count + 1)
    return {
        "chat_history": state.chat_history,
        "trace_log": state.trace_log,
        "degraded_reasons": state.degraded_reasons,
    }
