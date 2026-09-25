"""追问决策（纯代码，PRD §4.2 / SPEC §4.3）。

口径（2026-09-16 拍板）：覆盖率 < 阈值才追问遗漏（PRD §4.2），
SPEC §4.3 伪代码「有遗漏即追问」已同步修订为阈值口径。
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel

from app.graph.state import ScoreItem


class Decision(str, Enum):
    CLARIFY = "clarify"  # 追问澄清（回答有明确错误）
    MISSING = "missing"  # 追问遗漏关键点
    NEXT = "next"  # 换题


class Reason(str, Enum):
    """决策原因（P1-M4）：回放展示「为什么追问 / 为什么换题」。

    原因码是机器语义，中文文案由前端映射（同 domain/question_type 口径：后端定义、前端只消费）。
    """

    ERROR_FLAG = "error_flag"  # 回答有明确错误 → 澄清追问
    COVERAGE_LOW = "coverage_low"  # 关键点覆盖率 < 阈值 → 追问遗漏
    TOTAL_LIMIT = "total_limit"  # 单题追问达总上限 → 换题
    CLARIFY_LIMIT = "clarify_limit"  # 澄清已用过且无其他可追问项 → 换题
    MISSING_LIMIT = "missing_limit"  # 遗漏追问达上限 → 换题
    COVERAGE_OK = "coverage_ok"  # 覆盖完整、无错误 → 换题


class FollowUpRules(BaseModel):
    """PRD §4.2 上限：澄清 1 次 / 遗漏 2 次 / 单题总追问 3 次。"""

    clarify_limit: int = 1
    missing_limit: int = 2
    total_limit: int = 3
    coverage_threshold: float = 0.7


def explain_decision(
    score: ScoreItem,
    *,
    follow_up_count: int,
    clarify_used: int,
    missing_used: int,
    rules: FollowUpRules | None = None,
) -> tuple[Decision, Reason]:
    """按 PRD §4.2 规则表决策，并给出原因码（P1-M4 回放用）。

    **决策的唯一实现**：decide_follow_up 与条件边都走它，保证回放展示的原因
    与实际发生的转移永远同源（原因分支必须与决策分支一一对应，不许另起判断）。
    """
    rules = rules or FollowUpRules()
    if follow_up_count >= rules.total_limit:
        return Decision.NEXT, Reason.TOTAL_LIMIT
    if score.error_flag and clarify_used < rules.clarify_limit:
        return Decision.CLARIFY, Reason.ERROR_FLAG
    if (
        score.missed_key_points
        and score.coverage < rules.coverage_threshold
        and missing_used < rules.missing_limit
    ):
        return Decision.MISSING, Reason.COVERAGE_LOW
    # 以下都返回 NEXT（与上方 fallthrough 同结果），仅用于区分换题原因
    if score.error_flag and clarify_used >= rules.clarify_limit:
        return Decision.NEXT, Reason.CLARIFY_LIMIT
    if score.missed_key_points and score.coverage < rules.coverage_threshold:
        return Decision.NEXT, Reason.MISSING_LIMIT
    return Decision.NEXT, Reason.COVERAGE_OK


def decide_follow_up(
    score: ScoreItem,
    *,
    follow_up_count: int,
    clarify_used: int,
    missing_used: int,
    rules: FollowUpRules | None = None,
) -> Decision:
    """按 PRD §4.2 规则表决策：总上限 → 澄清 → 遗漏阈值 → 换题。"""
    decision, _ = explain_decision(
        score,
        follow_up_count=follow_up_count,
        clarify_used=clarify_used,
        missing_used=missing_used,
        rules=rules,
    )
    return decision
