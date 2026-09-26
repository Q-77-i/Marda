"""开场节点（SPEC §4.2 图 INTRO）：面试官自我介绍 + 流程与时长说明 → 等候选人自我介绍。"""

from __future__ import annotations

from app import llm
from app.agents.prompts import INTRO_TEMPLATE
from app.graph.rules.transition import estimated_minutes
from app.graph.state import InterviewState, Phase, add_history


async def intro_node(state: InterviewState) -> dict:
    text = await llm.chat(
        [{"role": "system", "content": INTRO_TEMPLATE.format(
            position=state.position,
            question_count=state.question_count,
            duration=estimated_minutes(state.question_count),  # 时长插槽（P1-M4.7-D）
        )}]
    )
    add_history(state, "assistant", text)
    return {"phase": Phase.WARMUP, "chat_history": state.chat_history}
