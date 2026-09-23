"""报告聚合（纯代码，SPEC §4.6）：五维均值 / 各域均分 / 短板定位。"""

from __future__ import annotations

from app.domain import COUNTED_QUESTION_TYPES, DOMAIN_WEIGHTS
from app.graph.state import QuestionRecord, ScoreItem

FIVE_DIMS = ("technical_depth", "fundamentals", "project_experience", "communication", "problem_solving")


def aggregate_scores(questions: list[QuestionRecord]) -> dict:
    """聚合已答题目：五维等权均值、六大域均分、短板域。

    场景题 domain="project" 不在 DOMAIN_WEIGHTS，不参与域统计（单列于逐题点评）。
    """
    scored = [q for q in questions if q.score]
    scores: dict[str, float] = {}
    for dim in FIVE_DIMS:
        values = [getattr(q.score, dim) for q in scored]
        scores[dim] = round(sum(values) / len(values), 2) if values else 0.0
    by_domain: dict[str, list[float]] = {}
    for q in scored:
        if q.domain in DOMAIN_WEIGHTS:
            by_domain.setdefault(q.domain, []).append(q.score.mean)
    domain_scores = {
        domain: round(sum(values) / len(values), 2) for domain, values in sorted(by_domain.items())
    }
    return {"scores": scores, "domain_scores": domain_scores, "weaknesses": _weak_domains(domain_scores)}


def _weak_domains(domain_scores: dict[str, float]) -> list[str]:
    """短板 = 均分最低的 2 个域；第 3 个同分也带上（SPEC §4.6「2-3 个」）。"""
    if not domain_scores:
        return []
    ordered = sorted(domain_scores, key=domain_scores.get)  # 稳定：同分按域名字典序
    weak = ordered[:2]
    if len(ordered) > 2 and domain_scores[ordered[2]] == domain_scores[ordered[1]]:
        # 如果存在并列第二低分，就把并列的也选为弱项。
        weak.append(ordered[2])
    return weak


def build_per_question_comments(
    questions: list[QuestionRecord],
    comments: list[str],
    reference_answers: dict[str, str] | None = None,
) -> list[dict]:
    """逐题点评（SPEC §4.6）：元信息取真实作答记录，LLM 只提供点评文字。

    LLM 的 ``question_id`` 是它自编的序号（prompt 未定义该字段含义），不可信，故调用方
    只取 ``comment`` 文本、按位置与已答题目对齐。条数恒等于已答题目数（LLM 少给时用评分官
    点评兜底），保证「逐题点评条数」与「完成题量」一致。

    题型语义（T7a/T7a-R1）：每条带出 ``question_type`` 与 ``number``——计入问答轮次的
    题型（COUNTED_QUESTION_TYPES）按作答顺序编号（场景题计入轮次，编号为其轮次序号）；
    前端据此展示，不再按 domain 推断题型。

    复盘扩展（FR-25）：每条带出 ``candidate_answer``（含追问轮，前端按
    ``FOLLOWUP_ANSWER_MARKER`` 分段）、``score``（五维标量）、``covered_key_points`` /
    ``missed_key_points``、``reference_answer``（题库题参考答案全文；生成题/场景题
    ``question_id`` 为空 → None，前端不渲染——无权威答案硬编反而误导）。
    """
    reference_answers = reference_answers or {}
    number = 0
    rows = []
    for index, q in enumerate(questions, 1):
        if q.question_type in COUNTED_QUESTION_TYPES:
            number += 1
            number_value = number
        else:
            number_value = None
        rows.append({
            "index": index,
            "number": number_value,
            "question_id": q.question_id,
            "question_type": q.question_type,
            "domain": q.domain,
            "text": q.text,
            "comment": comments[index - 1] if index <= len(comments) else (q.score.comment if q.score else ""),
            "candidate_answer": q.answer,
            "score": _five_dims(q.score),
            "covered_key_points": list(q.score.covered_key_points) if q.score else [],
            "missed_key_points": list(q.score.missed_key_points) if q.score else [],
            # 题库查不到（已归档/生成题）同样为 None：宁可缺失也不编造参考
            "reference_answer": reference_answers.get(q.question_id) if q.question_id else None,
        })
    return rows


def _five_dims(score: ScoreItem | None) -> dict[str, int] | None:
    """五维标量 dict（复盘 payload 只带五维）。

    显式取标量而非 ``score.model_dump()``：payload 必须可 JSON 序列化，
    Pydantic 对象直接进 payload 会在报告接口序列化时炸（FR-25 复盘口径）。
    """
    if score is None:
        return None
    return {dim: getattr(score, dim) for dim in FIVE_DIMS}
