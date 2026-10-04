"""报告节点（SPEC §4.6）：纯代码聚合 + LLM 文字部分，写入 state.report 并结束。"""

from __future__ import annotations

import logging

from app import llm
from app.agents.prompts import CLOSING_REMARK_TEMPLATE, REPORT_TEMPLATE, persona_for
from app.agents.schemas import ReportLLM
from app.config import get_settings
from app.domain import INTERVIEW_BEHAVIORAL
from app.graph.rules import degrade, stream
from app.graph.rules.aggregate import (
    aggregate_scores,
    build_per_question_comments,
    dims_for,
    dims_payload,
    normalize_advice_domain,
    report_domain_options,
)
from app.graph.state import (
    InterviewState,
    Phase,
    QuestionRecord,
    TraceEvent,
    add_history,
    add_trace,
)
from app.tools import question_search

logger = logging.getLogger(__name__)


async def report_node(state: InterviewState) -> dict:
    behavioral = state.interview_type == INTERVIEW_BEHAVIORAL
    aggregation = aggregate_scores(state.answered_questions, interview_type=state.interview_type)
    llm_part = await _report_text(state, behavioral=behavioral)
    reference_answers = await _load_reference_answers(state.answered_questions)
    scored = [q for q in state.answered_questions if q.score]
    unscored_count = len(state.answered_questions) - len(scored)
    report = {
        "interview_id": state.interview_id,
        "position": state.position,
        # 会话类型（P1-M11 D4）：能力档案按本字段排除行为面场次（SPEC 显式写明），
        # 前端/PDF 也按它决定渲染哪套维度、要不要画知识域
        "interview_type": state.interview_type,
        # 维度表（key + 中文标签，顺序即展示顺序）：行为面与技术面各一套，
        # 前端与 PDF 都消费这里而不是硬编维度表
        "dims": dims_payload(state.interview_type),
        # 降级交代（P2-M9）：本场是否发生过降级、降级了什么、有几题未评分。
        # 报告页/PDF 据此横幅提示；能力档案据此排除「全未评分」的场次
        "degraded": bool(state.degraded_reasons),
        "degraded_reasons": list(state.degraded_reasons),
        "unscored_count": unscored_count,
        # 全未评分时不落 0 分（缺数据 ≠ 0 分，同 M10「缺场不补零」口径）——
        # 空结构与 0 分在消费侧是两件事，报告页/PDF/档案都按空渲染
        "scores": aggregation["scores"] if scored else {},
        "domain_scores": aggregation["domain_scores"] if scored else {},
        "weaknesses": aggregation["weaknesses"] if scored else [],
        "answered_count": state.answered_count,
        "question_count": state.question_count,
        "total_comment": llm_part.total_comment if llm_part else "",
        # 点评文字来自 LLM，元信息/回答/评分（题库 id / domain / 题干 / 复盘字段）由后端带出
        "per_question_comments": build_per_question_comments(
            state.answered_questions,
            [c.comment for c in llm_part.per_question_comments] if llm_part else [],
            reference_answers,
        ),
        # 建议域宽容归一（P2-M2）：LLM 填标签也能配回 id，学习推荐据此把建议挂到分组上；
        # 未知值保留原文（绝不抛错——一个建议字段不值得炸掉整场报告）
        "study_advice": [
            {"domain": normalize_advice_domain(a.domain), "advice": a.advice}
            for a in llm_part.study_advice
        ] if llm_part else [],
    }
    if scored:
        # 总分（P1-M10 D1）：落进 payload 供报告页/PDF/能力档案共用，避免三处各算一遍
        report["overall"] = aggregation["overall"]
    # 结束陈词（P1-M4.7-D）：模板不带任何输入——结构上就说不出分数与短板（红线另写死在 prompt）
    # 陈词是装饰、报告是产物：这一句失败不能把整份报告（和整场结束）一起拖垮，降级为不追加
    try:
        remark = await stream.speak(
            [{"role": "system", "content": CLOSING_REMARK_TEMPLATE.format(
                persona=persona_for(state.interview_type),
            )}]
        )
    except llm.LLMError as exc:
        logger.warning("结束陈词生成失败，跳过：%s", exc)
    else:
        add_history(state, "assistant", remark)
    # 回放证据（FR-21）：收尾事件（不属任何轮次）
    add_trace(state, TraceEvent.REPORT, {
        "answered_count": state.answered_count,
        "question_count": state.question_count,
        "weaknesses": report["weaknesses"],
    })
    return {
        "report": report,
        "status": "finished",
        "phase": Phase.FINISHED,
        "trace_log": state.trace_log,
        "chat_history": state.chat_history,
        "degraded_reasons": state.degraded_reasons,
    }


async def _report_text(state: InterviewState, *, behavioral: bool) -> ReportLLM | None:
    """报告文字部分（降级链 P2-M9）：v4-pro → flash → **确定性内容**（返回 None）。

    分数与逐题记录都是纯代码聚合，本来就不依赖 LLM——报告官只写总评/点评/建议。
    它写不出来时报告照样出：这三段留空，`degraded_reasons` 如实交代。
    """
    messages = [{"role": "system", "content": REPORT_TEMPLATE.format(
        axis="能力维度" if behavioral else "知识域",
        domain_options=report_domain_options(state.interview_type),  # P2-M2：建议域给合法清单
        records=_format_records(state.answered_questions, behavioral=behavioral))}]
    try:
        return await llm.chat_json(
            messages,
            schema=ReportLLM,
            temperature=0.3,
            model=get_settings().deepseek_pro_model,  # 深度档（SPEC §3：报告用 v4-pro）
        )
    except llm.LLMError as exc:
        degrade.reraise_if_content(exc)  # 内容类不降级（见 degrade 模块）
        logger.warning("报告（v4-pro）生成失败，降级 flash（P2-M9）：%s", exc)
    try:
        part = await llm.chat_json(messages, schema=ReportLLM, temperature=0.3)  # 缺省 = flash
    except llm.LLMError as exc:
        degrade.reraise_if_content(exc)  # 内容类不降级（见 degrade 模块）
        logger.warning("报告（flash）也失败，输出确定性内容（P2-M9 降级）：%s", exc)
        degrade.mark(state, degrade.REPORT_FALLBACK)
        return None
    degrade.mark(state, degrade.REPORT_MODEL_FALLBACK)
    return part


async def _load_reference_answers(questions: list[QuestionRecord]) -> dict[str, str]:
    """题库题参考答案（复盘展示，FR-25）；生成题/场景题无 id，不查库。"""
    return await question_search.fetch_reference_answers(
        [q.question_id for q in questions if q.question_id]
    )


def _format_records(questions: list[QuestionRecord], *, behavioral: bool = False) -> str:
    """逐题记录文本（报告官输入）；维度按会话类型取（行为面五维 vs 技术面五维）。"""
    dims = tuple(dims_for(INTERVIEW_BEHAVIORAL if behavioral else "tech"))
    lines = []
    for index, q in enumerate(questions, 1):
        dims_text = ""
        if q.score:
            dims_text = "；五维 " + "/".join(str(getattr(q.score, dim)) for dim in dims)
        lines.append(
            f"第{index}题（{q.domain}/{q.topic}，难度 {q.difficulty}）：{q.text}\n"
            f"回答：{q.answer or '（未作答）'}{dims_text}\n"
            f"点评：{q.score.comment if q.score else ''}"
        )
    return "\n\n".join(lines)
