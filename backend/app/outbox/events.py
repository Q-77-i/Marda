"""事件信封构建（P5-J5）：`interview.finalized`。

契约：docs/p5/contracts/event-envelope.schema.json + interview-finalized.schema.json。
`event_id` 由场次确定性派生：`retry_persist` / 重复 finalize 用 `ON CONFLICT DO NOTHING`
就不会造重复事件（outbox 的唯一键是第二道闸）。
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any

from app.outbox.summary import summarize_report

EVENT_TYPE = "interview.finalized"
EVENT_VERSION = 1
# 固定命名空间：**不要改**——改了会让同一场次算出不同的 event_id。
EVENT_NAMESPACE = uuid.UUID("6ba7b811-9dad-11d1-80b4-00c04fd430c8")
_INTERVIEW_TYPES = {"tech", "behavioral"}
_MAX_POSITION_CHARS = 64  # 契约 maxLength


def event_id_for(interview_id: str, event_type: str = EVENT_TYPE) -> str:
    """同一场次 + 同一事件类型 → 稳定 UUID（uuid5）。"""
    return str(uuid.uuid5(EVENT_NAMESPACE, f"{event_type}:{interview_id}"))


def _rfc3339(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def build_interview_finalized(
    *,
    interview_id: str,
    user_id: str,
    report: Mapping[str, Any],
    finished_at: datetime,
    occurred_at: datetime | None = None,
) -> dict[str, Any] | None:
    """构建 `interview.finalized` 信封；没有归属用户 / 没有可用摘要时返回 None。

    `occurred_at` 与 `finished_at` 分开：线上 finalize 两者≈同一时刻；回填工具用
    `finished_at = interviews.ended_at`、`occurred_at = now`（事件何时发生 vs 报告何时生成）。
    """
    if not interview_id or not user_id:
        return None
    summary = summarize_report(report)
    if summary is None:
        return None

    interview_type = report.get("interview_type") or "tech"
    if interview_type not in _INTERVIEW_TYPES:
        interview_type = "tech"
    position = (report.get("position") or "Agent").strip()[: _MAX_POSITION_CHARS]
    if not position:
        position = "Agent"

    return {
        "event_id": event_id_for(interview_id),
        "type": EVENT_TYPE,
        "version": EVENT_VERSION,
        "occurred_at": _rfc3339(occurred_at or datetime.now(timezone.utc)),
        "producer": "agent",
        "payload": {
            "interview_id": interview_id,
            "user_id": user_id,
            "position": position,
            "interview_type": interview_type,
            "finished_at": _rfc3339(finished_at),
            "report_id": interview_id,  # reports.id = interview_id（1:1）
            "report_summary": summary,
        },
    }
