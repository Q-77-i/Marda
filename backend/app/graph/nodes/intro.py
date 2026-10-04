"""开场节点（SPEC §4.2 图 INTRO）：面试官自我介绍 + 流程与时长说明 → 等候选人自我介绍。

断 LLM 时（P2-M9）改用固定开场白（模板插槽与 LLM 版同源：岗位/题量/时长），
面试照常推进——开场白是装饰，不是面试内容。
"""

from __future__ import annotations

import logging

from app import llm
from app.agents import fallbacks
from app.agents.prompts import INTRO_TEMPLATE, persona_for
from app.domain import INTERVIEW_BEHAVIORAL
from app.graph.rules import degrade, stream
from app.graph.rules.transition import estimated_minutes
from app.graph.state import InterviewState, Phase, add_history

logger = logging.getLogger(__name__)


async def intro_node(state: InterviewState) -> dict:
    kind = (
        "行为面（HR 面）模拟面试"
        if state.interview_type == INTERVIEW_BEHAVIORAL
        else "技术模拟面试"
    )
    slots = {
        "persona": persona_for(state.interview_type),
        "position": state.position,
        "kind": kind,
        "question_count": state.question_count,
        "duration": estimated_minutes(state.question_count),  # 时长插槽（P1-M4.7-D）
    }
    try:
        text = await stream.speak(
            [{"role": "system", "content": INTRO_TEMPLATE.format(**slots)}],
            purpose="opening",  # 成本归因（P2-M10）
        )
    except llm.LLMError as exc:
        degrade.reraise_if_content(exc)  # 内容类不降级（见 degrade 模块）
        logger.warning("开场白生成失败，改用固定文案（P2-M9 降级）：%s", exc)
        degrade.mark(state, degrade.SCRIPT_FALLBACK)
        text = await stream.speak_fallback("", fallbacks.INTRO_FALLBACK.format(**slots))
    add_history(state, "assistant", text)
    return {
        "phase": Phase.WARMUP,
        "chat_history": state.chat_history,
        "degraded_reasons": state.degraded_reasons,
    }
