"""报告节点（SPEC §4.6）：纯代码聚合 + LLM 文字部分，写入 state.report 并结束。"""

from __future__ import annotations

from app import llm
from app.agents.prompts import REPORT_TEMPLATE
from app.agents.schemas import ReportLLM
from app.config import get_settings
from app.graph.rules.aggregate import aggregate_scores, build_per_question_comments
from app.graph.state import InterviewState, Phase, QuestionRecord
from app.tools import question_search


async def report_node(state: InterviewState) -> dict:
    aggregation = aggregate_scores(state.answered_questions)
    llm_part = await llm.chat_json(
        [{"role": "system", "content": REPORT_TEMPLATE.format(
            records=_format_records(state.answered_questions))}],
        schema=ReportLLM,
        temperature=0.3,
        model=get_settings().deepseek_pro_model,  # 深度档（SPEC §3：报告用 v4-pro）
    )
    reference_answers = await _load_reference_answers(state.answered_questions)
    report = {
        "interview_id": state.interview_id,
        "position": state.position,
        "scores": aggregation["scores"],
        "domain_scores": aggregation["domain_scores"],
        "weaknesses": aggregation["weaknesses"],
        "answered_count": state.answered_count,
        "question_count": state.question_count,
        "total_comment": llm_part.total_comment,
        # 点评文字来自 LLM，元信息/回答/评分（题库 id / domain / 题干 / 复盘字段）由后端带出
        "per_question_comments": build_per_question_comments(
            state.answered_questions,
            [c.comment for c in llm_part.per_question_comments],
            reference_answers,
        ),
        "study_advice": [a.model_dump() for a in llm_part.study_advice],
    }
    return {"report": report, "status": "finished", "phase": Phase.FINISHED}


async def _load_reference_answers(questions: list[QuestionRecord]) -> dict[str, str]:
    """题库题参考答案（复盘展示，FR-25）；生成题/场景题无 id，不查库。"""
    return await question_search.fetch_reference_answers(
        [q.question_id for q in questions if q.question_id]
    )


def _format_records(questions: list[QuestionRecord]) -> str:
    """逐题记录文本（报告官输入）。"""
    lines = []
    for index, q in enumerate(questions, 1):
        dims = ""
        if q.score:
            dims = (
                f"；五维 {q.score.technical_depth}/{q.score.fundamentals}/"
                f"{q.score.project_experience}/{q.score.communication}/{q.score.problem_solving}"
            )
        lines.append(
            f"第{index}题（{q.domain}/{q.topic}，难度 {q.difficulty}）：{q.text}\n"
            f"回答：{q.answer or '（未作答）'}{dims}\n"
            f"点评：{q.score.comment if q.score else ''}"
        )
    return "\n\n".join(lines)
