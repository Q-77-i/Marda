"""出题节点（SPEC §4.4）：选域 → 检索 → 放宽难度 → LLM 兜底 → 面试官口吻提问。

降级链（CLAUDE.md）：题库（payload 过滤）→ 难度 ±1 → LLM 生成（from_bank=False 不入库）。
"""

from __future__ import annotations

from app import llm
from app.agents.prompts import ASK_BANK_TEMPLATE, ASK_GENERATE_TEMPLATE, ASK_SCENARIO_TEMPLATE
from app.agents.schemas import GeneratedQuestion
from app.domain import DOMAIN_LABELS
from app.graph.rules.difficulty import DIFFICULTY_ORDER
from app.graph.rules.quota import pick_domain
from app.graph.state import InterviewState, Phase, QuestionRecord, add_history
from app.tools import question_search


async def ask_node(state: InterviewState) -> dict:
    if state.phase is Phase.PROJECT:
        question = await _generate_scenario(state)
    else:
        question = await _pick_from_bank(state) or await _generate_tech(state)
    text = await llm.chat(
        [{"role": "system", "content": ASK_BANK_TEMPLATE.format(question=question.text)}]
    )
    add_history(state, "assistant", text)
    state.current_question = question
    if question.question_id:
        state.asked_ids.append(question.question_id)
    updates: dict = {
        "current_question": question,
        "asked_ids": state.asked_ids,
        "chat_history": state.chat_history,
    }
    # 首次出题（WARMUP 之后）：进入技术问答阶段
    if state.phase not in (Phase.TECH_BASE, Phase.PROJECT):
        updates["phase"] = Phase.TECH_BASE
    return updates


async def _pick_from_bank(state: InterviewState) -> QuestionRecord | None:
    """配额选域 + 难度放宽检索（原难度 → ±1，保底 L1 封顶 L3）。"""
    domain = pick_domain(state)
    for difficulty in _relax(state.difficulty):
        candidates = await question_search.search_questions(
            domain=domain, difficulty=difficulty, exclude_ids=state.asked_ids, k=3
        )
        if candidates:
            item = candidates[0]
            return QuestionRecord(
                question_id=item["question_id"],
                text=item["question"],
                domain=item["domain"],
                topic=item["topic"],
                difficulty=item["difficulty"],
                key_points=item["key_points"],
            )
    return None


def _relax(difficulty: str) -> list[str]:
    """命中不到时的难度放宽顺序：先原难度，再低一档、高一档。"""
    index = DIFFICULTY_ORDER.index(difficulty)
    return [
        DIFFICULTY_ORDER[i] for i in (index, index - 1, index + 1) if 0 <= i < len(DIFFICULTY_ORDER)
    ]


async def _generate_tech(state: InterviewState) -> QuestionRecord:
    """题库无匹配 → LLM 按同标准生成（PRD §4.3，标记不入正式库）。"""
    domain = pick_domain(state)
    generated = await llm.chat_json(
        [{"role": "system", "content": ASK_GENERATE_TEMPLATE.format(
            domain_label=DOMAIN_LABELS[domain], difficulty=state.difficulty)}],
        schema=GeneratedQuestion,
        temperature=0.7,
    )
    return QuestionRecord(
        text=generated.text,
        domain=domain,
        topic=generated.topic,
        difficulty=state.difficulty,
        key_points=generated.key_points,
        from_bank=False,
    )


async def _generate_scenario(state: InterviewState) -> QuestionRecord:
    """场景题（PRD §4.1 高阶架构设计题）：结合候选人项目经历由 LLM 定制。"""
    generated = await llm.chat_json(
        [{"role": "system", "content": ASK_SCENARIO_TEMPLATE.format(
            profile=state.candidate_profile or "（候选人未提供项目经历，出一道人人都能答的通用设计题）")}],
        schema=GeneratedQuestion,
        temperature=0.7,
    )
    return QuestionRecord(
        text=generated.text,
        domain="project",  # 场景题单列，不参与知识域统计（aggregate 口径）
        topic=generated.topic,
        difficulty="L3",
        key_points=generated.key_points,
        from_bank=False,
        question_type="scenario",  # 加问不计入配置题量（COUNTED_QUESTION_TYPES）
    )
