"""代码工作台的消息构造（M15）。

生产路由与单测共用这一个入口（同 judge_messages 的纪律）：prompt 模板一改，
两边不会一个量新口径、一个量旧口径。
"""

from __future__ import annotations

import json
from typing import Any

from app.agents.prompts import CODE_FOLLOWUP_TEMPLATE, CODE_REVIEW_TEMPLATE
from app.agents.schemas import CodeReview

MAX_HISTORY = 8
"""追问时带上的最近对话条数（再早的对当前问题没有增量，只会白烧 token）。"""


def _reference_block(reference: str, key_points: list[str] | tuple[str, ...]) -> str:
    """内部参考要点：参考答案 + 关键点；都没有时给「（无）」，不让模板留空。"""
    parts: list[str] = []
    if reference.strip():
        parts.append(reference.strip())
    if key_points:
        parts.append("关键点：\n" + "\n".join(f"- {point}" for point in key_points))
    return "\n\n".join(parts) or "（无）"


def _history_block(history: list[dict[str, Any]]) -> str:
    """最近 N 条对话（只取最近的，避免把整段历史塞进 prompt）。"""
    rows = history[-MAX_HISTORY:]
    if not rows:
        return "（无）"
    return "\n".join(
        f"{'候选人' if row.get('role') == 'user' else '辅导官'}：{row.get('content', '')}"
        for row in rows
    )


def _review_text(review: CodeReview | dict[str, Any]) -> str:
    if isinstance(review, CodeReview):
        return review.model_dump_json(indent=2)
    return json.dumps(review, ensure_ascii=False, indent=2)


def review_messages(
    *,
    question: str,
    language: str,
    code: str,
    reference: str = "",
    key_points: list[str] | tuple[str, ...] = (),
) -> list[dict[str, str]]:
    """代码点评的 system 消息（题干 + 内部参考 + 候选代码）。"""
    return [{
        "role": "system",
        "content": CODE_REVIEW_TEMPLATE.format(
            question=question,
            language=language,
            reference=_reference_block(reference, key_points),
            code=code,
        ),
    }]


def followup_messages(
    *,
    question: str,
    language: str,
    code: str,
    review: CodeReview | dict[str, Any],
    message: str,
    history: list[dict[str, Any]] | None = None,
    reference: str = "",
    key_points: list[str] | tuple[str, ...] = (),
) -> list[dict[str, str]]:
    """代码追问的 system 消息（带上次点评与最近历史）。"""
    return [{
        "role": "system",
        "content": CODE_FOLLOWUP_TEMPLATE.format(
            question=question,
            language=language,
            code=code,
            review=_review_text(review),
            history=_history_block(history or []),
            message=message,
        ),
    }]
