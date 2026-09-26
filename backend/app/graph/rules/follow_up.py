"""追问决策（纯代码，PRD §4.2 / SPEC §4.3）。

口径（2026-09-16 拍板）：覆盖率 < 阈值才追问遗漏（PRD §4.2），
SPEC §4.3 伪代码「有遗漏即追问」已同步修订为阈值口径。
密度口径（P1-M4.5-R1 拍板，实测 3 题 10 次追问后修订）：
- 优先级：澄清（不占池）→ 深挖（达标，不占池）→ 遗漏（占池）→ 换题；
- 同一 key_point 只追问一次（asked_key_points 集合，覆盖率跳变不触发重复追问）；
- 全场补救池 `remedy_budget(N) = max(3, ceil(N×0.7))`（5 题 4 次 / 10 题 7 次 / 15 题 11 次），
  澄清与深挖豁免（单题上限约束），remedy_used 从已答题计数派生。
"""

from __future__ import annotations

from enum import Enum
from math import ceil

from pydantic import BaseModel

from app.graph.state import InterviewState, ScoreItem


class Decision(str, Enum):
    CLARIFY = "clarify"  # 追问澄清（回答有明确错误，不占补救池）
    MISSING = "missing"  # 追问遗漏关键点（占补救池）
    DEEPEN = "deepen"  # 深挖追问（覆盖达标，不占补救池）
    NEXT = "next"  # 换题


class Reason(str, Enum):
    """决策原因（P1-M4）：回放展示「为什么追问 / 为什么换题」。

    原因码是机器语义，中文文案由前端映射（同 domain/question_type 口径：后端定义、前端只消费）。
    TOTAL_LIMIT 为 P0 遗留值（旧事件数据），不再产出。
    """

    ERROR_FLAG = "error_flag"  # 回答有明确错误 → 澄清追问
    COVERAGE_LOW = "coverage_low"  # 覆盖率 < 阈值且有未问过的漏点 → 追问遗漏
    DEEPEN_OK = "deepen_ok"  # 覆盖率达标且无错误 → 深挖追问
    TOTAL_LIMIT = "total_limit"  # 遗留（P0 单题总上限，已退役，不再产出）
    REMEDY_LIMIT = "remedy_limit"  # 全场补救池用尽 → 换题
    CLARIFY_LIMIT = "clarify_limit"  # 澄清已用过且错误仍在 → 换题
    MISSING_LIMIT = "missing_limit"  # 遗漏追问达单题上限 → 换题
    MISSING_ASKED = "missing_asked"  # 有遗漏但遗漏点均已追问过 → 换题
    COVERAGE_OK = "coverage_ok"  # 覆盖达标但深挖已用尽/不可用 → 换题


class FollowUpRules(BaseModel):
    """单题上限（PRD §4.2 + P1-M4.5）：澄清 1 / 遗漏 2 / 深挖 1。

    总量由全场补救池约束（见 remedy_budget），澄清与深挖豁免。
    """

    clarify_limit: int = 1
    missing_limit: int = 2
    deepen_limit: int = 1
    coverage_threshold: float = 0.7


def remedy_budget(question_count: int) -> int:
    """全场补救预算（P1-M4.5-R1）：max(3, ceil(N×0.7))。

    单一来源（仿 end_quota）：决策与回放展示共用，不各算一份。
    """
    return max(3, ceil(question_count * 0.7))


def remedy_used_total(state: InterviewState) -> int:
    """全场补救已用量：从已答题目的遗漏追问计数派生（含当前题，首评即入列）。

    零独立 state 字段——answered_questions 是权威，补救池消耗 = 各题 missing_used 之和。
    """
    return sum(q.missing_used for q in state.answered_questions)


def unasked_missed(score: ScoreItem, asked_key_points: list[str]) -> list[str]:
    """本轮遗漏中尚未追问过的点（P1-M4.5-R1：同一 key_point 只追问一次）。"""
    return [k for k in score.missed_key_points if k not in asked_key_points]


def explain_decision(
    score: ScoreItem,
    *,
    question_count: int,
    clarify_used: int,
    missing_used: int,
    deepen_used: int,
    remedy_used: int,
    asked_key_points: list[str],
    rules: FollowUpRules | None = None,
) -> tuple[Decision, Reason]:
    """按 PRD §4.2 规则表决策，并给出原因码（P1-M4 回放用）。

    **决策的唯一实现**：decide_follow_up 与条件边都走它，保证回放展示的原因
    与实际发生的转移永远同源（原因分支必须与决策分支一一对应，不许另起判断）。
    """
    rules = rules or FollowUpRules()
    # 澄清：不占池；用尽且错误仍在 → 换题（不深挖、不转遗漏）
    if score.error_flag and clarify_used < rules.clarify_limit:
        return Decision.CLARIFY, Reason.ERROR_FLAG
    if score.error_flag:
        return Decision.NEXT, Reason.CLARIFY_LIMIT
    # 深挖：覆盖达标且无错误（不占池，单题 1 次）
    if score.coverage >= rules.coverage_threshold:
        if deepen_used < rules.deepen_limit:
            return Decision.DEEPEN, Reason.DEEPEN_OK
        return Decision.NEXT, Reason.COVERAGE_OK
    # 遗漏：占池——只问没问过的漏点，单题上限 2 次，全场池子封顶
    unasked = unasked_missed(score, asked_key_points)
    if unasked and missing_used < rules.missing_limit:
        if remedy_used < remedy_budget(question_count):
            return Decision.MISSING, Reason.COVERAGE_LOW
        return Decision.NEXT, Reason.REMEDY_LIMIT
    if missing_used >= rules.missing_limit:
        return Decision.NEXT, Reason.MISSING_LIMIT
    return Decision.NEXT, Reason.MISSING_ASKED


def decide_follow_up(
    score: ScoreItem,
    *,
    question_count: int,
    clarify_used: int,
    missing_used: int,
    deepen_used: int,
    remedy_used: int,
    asked_key_points: list[str],
    rules: FollowUpRules | None = None,
) -> Decision:
    """薄封装：decision, _ = explain_decision(...)，决策与原因同源。"""
    decision, _ = explain_decision(
        score,
        question_count=question_count,
        clarify_used=clarify_used,
        missing_used=missing_used,
        deepen_used=deepen_used,
        remedy_used=remedy_used,
        asked_key_points=asked_key_points,
        rules=rules,
    )
    return decision
