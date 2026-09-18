"""知识域配额（纯代码，SPEC §4.3）：largest remainder 按权重分配题量。

余数并列时按权重表出现顺序取（dict 有序 + sorted 稳定），
同一输入永远同一配额（可解释、可回放）。
"""

from __future__ import annotations

from collections import Counter

from app.domain import DOMAIN_WEIGHTS
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


def remaining_quota(state: InterviewState) -> dict[str, int]:
    """剩余配额 = 总配额 − 已出题域计数（含正在答的当前题）。"""
    # 总配额
    base = allocate_quota(state.question_count, DOMAIN_WEIGHTS)
    # 用 Counter 统计已经回答过的题目中，每个领域出现了多少次。
    used = Counter(q.domain for q in state.answered_questions)
    if state.current_question and state.current_question.domain in base:
        used[state.current_question.domain] += 1
    return {domain: base[domain] - used[domain] for domain in base}


def pick_domain(state: InterviewState) -> str:
    """出题选域：剩余配额最多的域，平局按权重表顺序（SPEC §4.4）。

    配额耗尽时兜底取权重表首域——正常流程答满即进场景题，不会走到。
    """
    remaining = remaining_quota(state)
    if max(remaining.values()) <= 0:
        return next(iter(DOMAIN_WEIGHTS))
    return max(remaining, key=lambda d: remaining[d])

