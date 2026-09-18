"""报告聚合（纯代码，SPEC §4.6）：五维均值 / 各域均分 / 短板定位。"""

from __future__ import annotations

from app.domain import DOMAIN_WEIGHTS
from app.graph.state import QuestionRecord

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
