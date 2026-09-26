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
from app.graph.rules.transition import buffer_line, transition_line
from app.graph.state import InterviewState, Phase, QuestionRecord, TraceEvent, add_history, add_trace
from app.tools import question_search


async def ask_node(state: InterviewState) -> dict:
    if state.phase is Phase.TECH_BASE:
        question, hits = await _pick_from_bank(state)
        if question is None:
            question, hits = await _generate_tech(state), 0
    else:
        # PROJECT 阶段与首题（WARMUP 之后）：项目深挖题（P1-M4.6-C 前置）
        question, hits = await _generate_scenario(state), 0
    # 人味层（P1-M4.7-D）：答错缓冲独立成条（它回应的是上一题），衔接语与题目同一条消息
    buffer = buffer_line(state)
    if buffer:
        add_history(state, "assistant", buffer)
    text = await llm.chat(
        [{"role": "system", "content": ASK_BANK_TEMPLATE.format(
            question=question.text,
            profile=state.candidate_profile or "（候选人未提供项目背景）",
        )}]
    )
    add_history(state, "assistant", f"{transition_line(state, question)}{text}")
    state.current_question = question
    if question.question_id:
        state.asked_ids.append(question.question_id)
    # 回放证据（FR-21）：输入 = 配额选定的域/难度，工具输出 = 检索命中情况
    add_trace(state, TraceEvent.ASK, {
        "domain": question.domain,
        "difficulty": question.difficulty,
        "question_type": question.question_type,
        "from_bank": question.from_bank,
        "question_id": question.question_id,
        "question": question.text,
        "hits": hits,
    }, round_no=state.answered_count + 1)
    updates: dict = {
        "current_question": question,
        "asked_ids": state.asked_ids,
        "chat_history": state.chat_history,
        "trace_log": state.trace_log,
    }
    # 首次出题（WARMUP 之后）：进入项目深挖阶段
    if state.phase not in (Phase.TECH_BASE, Phase.PROJECT):
        updates["phase"] = Phase.PROJECT
    return updates


async def _pick_from_bank(state: InterviewState) -> tuple[QuestionRecord | None, int]:
    """配额选域 + 难度放宽检索（原难度 → ±1，保底 L1 封顶 L3）。

    返回 (题目, 命中候选数)；命中候选数进回放事件（工具输出），未命中返回 (None, 0)。
    """
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
                follow_ups=item["follow_ups"],
            ), len(candidates)
    return None, 0


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
            domain_label=DOMAIN_LABELS[domain],
            difficulty=state.difficulty,
            profile=state.candidate_profile or "（候选人未提供项目背景）",
        )}],
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
    """项目深挖题（PRD §4.1 高阶架构设计题）：结合候选人项目经历由 LLM 定制。

    P1-M4.6-C 泛化：项目深挖前置、按轮出题——轮次号供 LLM 换切入点避免重复，
    难度随 state.difficulty（不再固定 L3）。
    """
    generated = await llm.chat_json(
        [{"role": "system", "content": ASK_SCENARIO_TEMPLATE.format(
            project_round=state.answered_count + 1,
            difficulty=state.difficulty,
            profile=state.candidate_profile or "（候选人未提供项目经历，出一道通用的架构设计题）")}],
        schema=GeneratedQuestion,
        temperature=0.7,
    )
    return QuestionRecord(
        text=generated.text,
        domain="project",  # 项目深挖题单列，不参与知识域统计（aggregate 口径）
        topic=generated.topic,
        difficulty=state.difficulty,
        key_points=generated.key_points,
        from_bank=False,
        question_type="scenario",  # 题型标识（COUNTED_QUESTION_TYPES 计入轮次）
    )
