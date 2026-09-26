"""知识域配额（纯代码，SPEC §4.3）：largest remainder 按权重分配题量 + 同域成块选域。

余数并列时按权重表出现顺序取（dict 有序 + sorted 稳定），
同一输入永远同一配额（可解释、可回放）。
"""

from __future__ import annotations

from collections import Counter

from app.domain import DOMAIN_WEIGHTS, project_count
from app.graph.state import InterviewState


def allocate_quota(total: int, weights: dict[str, float]) -> dict[str, int]:
    """把 total 道题按 weights 权重分配到各域，配额之和恒等于 total。

    调用方保证权重和 = 1 且 total 非负（阶段 1 只有 DOMAIN_WEIGHTS 一个来源）。
    """
    base = {domain: int(total * weight) for domain, weight in weights.items()}
    remainder = total - sum(base.values())
    # 小数部分降序；并列时 sorted 稳定 → 保持权重表出现顺序
    order = sorted(weights, key=lambda d: (total * weights[d]) % 1, reverse=True)
    for domain in order[:remainder]:
        base[domain] += 1
    return base


def _tech_quota(state: InterviewState) -> dict[str, int]:
    """技术题总配额（单一来源）：轮次 − 项目深挖题数（question_count 是全场轮次语义）。

    max(…, 0) 兜底存量 checkpoint（旧数据可能 question_count=1）。
    """
    return allocate_quota(
        max(state.question_count - project_count(state.question_count), 0), DOMAIN_WEIGHTS
    )


def remaining_quota(state: InterviewState) -> dict[str, int]:
    """剩余配额 = 总配额 − 已出题域计数（含正在答的当前题）。"""
    base = _tech_quota(state)
    # 用 Counter 统计已经回答过的题目中，每个领域出现了多少次。
    used = Counter(q.domain for q in state.answered_questions)
    if state.current_question and state.current_question.domain in base:
        used[state.current_question.domain] += 1
    return {domain: base[domain] - used[domain] for domain in base}


def pick_domain(state: InterviewState) -> str:
    """出题选域（P1-M4.7-D2 同域成块）：当前域配额未尽 → 继续同域；块尽 → 切剩余最多的域。

    块序完全由 allocate_quota 决定（未改动）→ 同题量的场次域分布与块序逐位一致，
    成块只改「题与题的先后」，报告聚合在收尾一次性算、与题序无关（跨场次可比性）。

    粘性用**真实已答数**判断（不含当前题），与 remaining_quota 的「当前题也占额」口径
    不同：后者回答「这一域还剩几道」，前者回答「这一域还能不能接着出」。
    平局按权重表顺序；配额耗尽时兜底取权重表首域——正常流程答满即进反问，不会走到。
    """
    base = _tech_quota(state)
    used = Counter(q.domain for q in state.answered_questions)
    current = state.current_question
    if current and current.domain in base and used[current.domain] < base[current.domain]:
        return current.domain
    remaining = {domain: base[domain] - used[domain] for domain in base}
    if max(remaining.values()) <= 0:
        return next(iter(DOMAIN_WEIGHTS))
    return max(remaining, key=lambda d: remaining[d])

