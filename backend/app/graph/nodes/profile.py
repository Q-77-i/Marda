"""自我介绍提炼节点（SPEC §4.2 图 PROFILE）：结构化提炼，供场景题定制与报告生成。"""

from __future__ import annotations

from app import llm
from app.agents.prompts import PROFILE_TEMPLATE
from app.agents.schemas import ProfileExtraction
from app.graph.state import InterviewState, add_history


async def profile_node(state: InterviewState) -> dict:
    extraction = await llm.chat_json(
        [{"role": "system", "content": PROFILE_TEMPLATE.format(content=state.user_input)}],
        schema=ProfileExtraction,
        temperature=0.3,
    )
    profile = extraction.summary
    if extraction.projects:
        profile += "；项目经历：" + "；".join(extraction.projects)
    add_history(state, "user", state.user_input)
    return {"candidate_profile": profile, "chat_history": state.chat_history}
