"""开场节点（SPEC §4.2 图 INTRO）：面试官自我介绍 + 流程与时长说明 → 等候选人自我介绍。"""

from __future__ import annotations

from app.agents.prompts import INTRO_TEMPLATE, persona_for
from app.domain import INTERVIEW_BEHAVIORAL
from app.graph.rules import stream
from app.graph.rules.transition import estimated_minutes
from app.graph.state import InterviewState, Phase, add_history


async def intro_node(state: InterviewState) -> dict:
    kind = (
        "行为面（HR 面）模拟面试"
        if state.interview_type == INTERVIEW_BEHAVIORAL
        else "技术模拟面试"
    )
    text = await stream.speak(
        [{"role": "system", "content": INTRO_TEMPLATE.format(
            persona=persona_for(state.interview_type),
            position=state.position,
            kind=kind,
            question_count=state.question_count,
            duration=estimated_minutes(state.question_count),  # 时长插槽（P1-M4.7-D）
        )}]
    )
    add_history(state, "assistant", text)
    return {"phase": Phase.WARMUP, "chat_history": state.chat_history}
