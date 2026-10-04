"""出题节点（SPEC §4.4）：选域 → 检索 → 放宽难度 → LLM 兜底 → 面试官口吻提问。

降级链（CLAUDE.md）：题库（payload 过滤）→ 难度 ±1 → LLM 生成（from_bank=False 不入库）。
行为面（P1-M11）：不分域、不分难度，按行为题整池随机 → 池空 LLM 生成兜底。
"""

from __future__ import annotations

import asyncio
import logging

from app import llm
from app.agents import fallbacks
from app.agents.prompts import (
    ASKED_BEHAVIORAL_EMPTY,
    ASKED_BEHAVIORAL_HEADER,
    ASKED_PROJECT_EMPTY,
    ASKED_PROJECT_HEADER,
    ASK_BANK_TEMPLATE,
    ASK_GENERATE_TEMPLATE,
    ASK_SCENARIO_TEMPLATE,
    BEHAVIORAL_ASK_GENERATE_TEMPLATE,
    persona_for,
)
from app.agents.schemas import GeneratedQuestion
from app.config import get_settings
from app.domain import (
    BEHAVIORAL_DOMAIN,
    DOMAIN_LABELS,
    INTERVIEW_BEHAVIORAL,
    PROJECT_DOMAIN,
    QUESTION_TYPE_BEHAVIORAL,
)
from app.graph.rules import degrade, stream
from app.graph.rules.difficulty import DIFFICULTY_ORDER
from app.graph.rules.quota import pick_domain
from app.graph.rules.transition import buffer_line, transition_line
from app.graph.state import InterviewState, Phase, QuestionRecord, TraceEvent, add_history, add_trace
from app.tools import images as image_store
from app.tools import question_search

logger = logging.getLogger(__name__)


async def _last_answer_attachment(state: InterviewState) -> list[dict]:
    """上一轮问答的图附件（P2-M6）：出题结合图内容。

    **只给 LLM 现场生成题目的路径用**（项目深挖 / 兜底生成）——题库题的题面来自题库，
    出题官对它只做口吻改写、改不了考察点，带图是噪声（PRD FR-26 的「出题结合图内容」
    落在生成路径上）。取上一题的图（最近 N 张），无图返回空列表（不多做任何 IO）。
    """
    if not state.answered_questions:
        return []
    last = state.answered_questions[-1]
    if not last.image_ids:
        return []
    parts = await asyncio.to_thread(
        image_store.load_image_parts, get_settings().upload_dir, state.interview_id, last.image_ids
    )
    if not parts:
        return []
    return [image_store.attachment_message(parts, image_store.NOTE_ASK)]


async def ask_node(state: InterviewState) -> dict:
    try:
        if state.interview_type == INTERVIEW_BEHAVIORAL:
            question, hits = await _pick_behavioral(state)
            if question is None:
                question, hits = await _generate_behavioral(state), 0
        elif state.phase is Phase.TECH_BASE:
            question, hits = await _pick_from_bank(state)
            if question is None:
                question, hits = await _generate_tech(state), 0
        else:
            # PROJECT 阶段与首题（WARMUP 之后）：项目深挖题（P1-M4.6-C 前置）
            question, hits = await _generate_scenario(state), 0
    except llm.LLMError as exc:
        degrade.reraise_if_content(exc)  # 内容类不降级（见 degrade 模块）
        # 降级链最后一环（P2-M9）：题库没命中 + 生成也不可用 → 内置兜底题。
        # 不做「重试一次」——断的是整条 LLM 链路，重试只是白等；出题不能停。
        logger.warning("出题失败，改用内置兜底题（P2-M9 降级）：%s", exc)
        degrade.mark(state, degrade.QUESTION_FALLBACK)
        question, hits = fallbacks.fallback_question(state), 0
    # 人味层（P1-M4.7-D）：答错缓冲独立成条（它回应的是上一题），衔接语与题目同一条消息。
    # P2-M4：两条消息都走流式通道——分片先于 delta 到达，纯代码的那条若不发分片，
    # 前端就只能插在流式消息之后，用户会看到顺序倒过来。
    buffer = buffer_line(state)
    if buffer:
        stream.begin(buffer)
        add_history(state, "assistant", buffer)
    preamble = transition_line(state, question)
    try:
        text = await stream.speak(
            [{"role": "system", "content": ASK_BANK_TEMPLATE.format(
                persona=persona_for(state.interview_type),
                question=question.text,
                profile=state.candidate_profile or "（候选人未提供项目背景）",
            )}],
            preamble=preamble,
            purpose="ask",  # 成本归因（P2-M10）
        )
    except llm.LLMError as exc:
        degrade.reraise_if_content(exc)  # 内容类不降级（见 degrade 模块）
        # 口吻改写不可用 → 直接发原题面（题面来自题库/内置兜底题，本就不需要 LLM）
        logger.warning("出题文案生成失败，直接发原题面（P2-M9 降级）：%s", exc)
        degrade.mark(state, degrade.SCRIPT_FALLBACK)
        text = await stream.speak_fallback(preamble, question.text)
    add_history(state, "assistant", text)
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
        "degraded_reasons": state.degraded_reasons,
    }
    # 首次出题（WARMUP 之后）：进入问答段（行为面单段 / 技术面先项目深挖）
    if state.phase not in (Phase.TECH_BASE, Phase.PROJECT, Phase.BEHAVIORAL):
        updates["phase"] = (
            Phase.BEHAVIORAL
            if state.interview_type == INTERVIEW_BEHAVIORAL
            else Phase.PROJECT
        )
    return updates


async def _pick_behavioral(state: InterviewState) -> tuple[QuestionRecord | None, int]:
    """行为题整池随机（P1-M11）：不分域（只有一个域）、不分难度（L1-L3 是技术语义，D3）。

    私有题不参与：私有库不开放行为面域（D7），`difficulty=None` 的检索在
    question_search 里本就跳过私有候选。
    """
    candidates = await question_search.search_questions(
        domain=BEHAVIORAL_DOMAIN,
        difficulty=None,
        exclude_ids=state.asked_ids,
        k=3,
        user_id=state.user_id or None,
    )
    if not candidates:
        return None, 0
    item = candidates[0]
    return QuestionRecord(
        question_id=item["question_id"],
        text=item["question"],
        domain=item["domain"],
        topic=item["topic"],
        difficulty=item["difficulty"],  # 只作记录（报告不展示、不参与出题，D3）
        key_points=item["key_points"],
        follow_ups=item["follow_ups"],
        question_type=QUESTION_TYPE_BEHAVIORAL,
    ), len(candidates)


async def _pick_from_bank(state: InterviewState) -> tuple[QuestionRecord | None, int]:
    """配额选域 + 难度放宽检索（原难度 → ±1，保底 L1 封顶 L3）。

    返回 (题目, 命中候选数)；命中候选数进回放事件（工具输出），未命中返回 (None, 0)。
    """
    domain = pick_domain(state)
    for difficulty in _relax(state.difficulty):
        candidates = await question_search.search_questions(
            domain=domain, difficulty=difficulty, exclude_ids=state.asked_ids, k=3,
            # 私有题混入（P1-M7）：state.user_id 为空 = 只用公共题库
            user_id=state.user_id or None,
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
        )}, *await _last_answer_attachment(state)],
        schema=GeneratedQuestion,
        temperature=0.7,
        purpose="ask",  # 成本归因（P2-M10）
    )
    return QuestionRecord(
        text=generated.text,
        domain=domain,
        topic=generated.topic,
        difficulty=state.difficulty,
        key_points=generated.key_points,
        from_bank=False,
    )


def _asked_project_block(state: InterviewState) -> str:
    """已问过的项目题原文（P1-M4.7 后续）：出题官每轮都是新调用，不喂前情就只会
    套同一个开头——「换一个切入点」得先让它看得见前面问过什么。"""
    texts = [q.text for q in state.answered_questions if q.domain == PROJECT_DOMAIN]
    if not texts:
        return ASKED_PROJECT_EMPTY
    return ASKED_PROJECT_HEADER + "\n".join(f"{i}. {t}" for i, t in enumerate(texts, 1))


async def _generate_scenario(state: InterviewState) -> QuestionRecord:
    """项目深挖题（PRD §4.1 高阶架构设计题）：结合候选人项目经历由 LLM 定制。

    P1-M4.6-C 泛化：项目深挖前置、按轮出题——轮次号供 LLM 换切入点避免重复，
    难度随 state.difficulty（不再固定 L3）。
    """
    generated = await llm.chat_json(
        [{"role": "system", "content": ASK_SCENARIO_TEMPLATE.format(
            project_round=state.answered_count + 1,
            difficulty=state.difficulty,
            profile=state.candidate_profile or "（候选人未提供项目经历，出一道通用的架构设计题）",
            asked=_asked_project_block(state))}, *await _last_answer_attachment(state)],
        schema=GeneratedQuestion,
        temperature=0.7,
        purpose="ask",  # 成本归因（P2-M10）
    )
    return QuestionRecord(
        text=generated.text,
        domain=PROJECT_DOMAIN,  # 项目深挖题单列，不参与知识域统计（aggregate 口径）
        topic=generated.topic,
        difficulty=state.difficulty,
        key_points=generated.key_points,
        from_bank=False,
        question_type="scenario",  # 题型标识（COUNTED_QUESTION_TYPES 计入轮次）
    )


def _asked_behavioral_block(state: InterviewState) -> str:
    """已问过的行为题原文（P1-M11）：与项目题同款——出题官不看见措辞就会换汤不换药。"""
    texts = [q.text for q in state.answered_questions if q.question_type == QUESTION_TYPE_BEHAVIORAL]
    if not texts:
        return ASKED_BEHAVIORAL_EMPTY
    return ASKED_BEHAVIORAL_HEADER + "\n".join(f"{i}. {t}" for i, t in enumerate(texts, 1))


async def _generate_behavioral(state: InterviewState) -> QuestionRecord:
    """行为题库耗尽 → LLM 按同标准现场生成（P1-M11 D1 兜底，不入正式库）。"""
    generated = await llm.chat_json(
        [{"role": "system", "content": BEHAVIORAL_ASK_GENERATE_TEMPLATE.format(
            profile=state.candidate_profile or "（候选人未提供项目经历，出一道通用的行为面题目）",
            asked=_asked_behavioral_block(state))}, *await _last_answer_attachment(state)],
        schema=GeneratedQuestion,
        temperature=0.7,
        purpose="ask",  # 成本归因（P2-M10）
    )
    return QuestionRecord(
        text=generated.text,
        domain=BEHAVIORAL_DOMAIN,
        topic=generated.topic,
        difficulty=state.difficulty,  # 死数据（D3）：只作记录，不参与出题与展示
        key_points=generated.key_points,
        from_bank=False,
        question_type=QUESTION_TYPE_BEHAVIORAL,
    )
