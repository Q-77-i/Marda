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


class FollowUpRules(BaseModel):
    """PRD §4.2 上限：澄清 1 次 / 遗漏 2 次 / 单题总追问 3 次。"""

    clarify_limit: int = 1
    missing_limit: int = 2
    total_limit: int = 3
    coverage_threshold: float = 0.7


def decide_follow_up(
    score: ScoreItem,
    *,
    follow_up_count: int,
    clarify_used: int,
    missing_used: int,
    rules: FollowUpRules | None = None,
) -> Decision:
    """按 PRD §4.2 规则表决策：总上限 → 澄清 → 遗漏阈值 → 换题。"""
    rules = rules or FollowUpRules()
    if follow_up_count >= rules.total_limit:
        return Decision.NEXT
    if score.error_flag and clarify_used < rules.clarify_limit:
        return Decision.CLARIFY
    if (
        score.missed_key_points
        and score.coverage < rules.coverage_threshold
        and missing_used < rules.missing_limit
    ):
        return Decision.MISSING
    return Decision.NEXT
