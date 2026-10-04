"""自我介绍提炼节点（SPEC §4.2 图 PROFILE）：结构化提炼，供场景题定制与报告生成。"""

from __future__ import annotations

import asyncio
import logging

from app import llm
from app.agents.prompts import PROFILE_TEMPLATE
from app.agents.schemas import ProfileExtraction
from app.config import get_settings
from app.graph.rules import degrade
from app.graph.state import InterviewState, add_history
from app.tools import images as image_store

logger = logging.getLogger(__name__)


async def profile_node(state: InterviewState) -> dict:
    # 自我介绍也可能带图（P2-M6）：架构图/项目截图对「项目经历提炼」是有效输入
    # （提炼结果喂给后续项目深挖题的定制）；无图时消息列表与接入前逐字一致
    parts = await asyncio.to_thread(
        image_store.load_image_parts, get_settings().upload_dir, state.interview_id, state.current_images
    )
    extra: list[dict] = (
        [image_store.attachment_message(parts, image_store.NOTE_PROFILE)] if parts else []
    )
    try:
        extraction = await llm.chat_json(
            [{"role": "system", "content": PROFILE_TEMPLATE.format(content=state.user_input)}, *extra],
            schema=ProfileExtraction,
            temperature=0.3,
        )
    except llm.LLMError as exc:
        degrade.reraise_if_content(exc)  # 内容类不降级（见 degrade 模块）
        # 降级（P2-M9）：提炼只影响后续出题的个性化（项目深挖题会引用画像），
        # 跳过即可——自我介绍照常记进 chat_history，面试继续。
        logger.warning("自我介绍提炼失败，跳过（P2-M9 降级）：%s", exc)
        degrade.mark(state, degrade.PROFILE_SKIPPED)
        add_history(state, "user", state.user_input, image_ids=state.current_images)
        return {
            "chat_history": state.chat_history,
            "degraded_reasons": state.degraded_reasons,
        }
    profile = extraction.summary
    if extraction.projects:
        profile += "；项目经历：" + "；".join(extraction.projects)
    add_history(state, "user", state.user_input, image_ids=state.current_images)
    return {"candidate_profile": profile, "chat_history": state.chat_history}
