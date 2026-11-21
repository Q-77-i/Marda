"""报告 payload → 事件 `report_summary`（P5-J5 服务端唯一实现）。

前端 `frontend/lib/learning.ts::buildReportSummary` 是展示副本（手动建计划的补偿入口）；
两侧用例必须同口径，改这里时同步改那边：

- 行为面没有可学习的技术域 → `weak_domains: []`（**不能**把 `logic_structure` 这类
  评分维度塞进去：它们带下划线，不符合事件契约的 `^[a-z][a-z0-9-]*$`）；
- 短板缺 `domain_scores` 分数 → 丢掉该域，不按 0 分猜；
- 最多 5 个域（与事件 schema 的 maxItems 一致）；
- `advice` 截断到契约的 200 字；
- 整场未评分（没有 overall、也没有任何维度分）→ `None`：缺数据 ≠ 0 分，不发事件。
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

MAX_WEAK_DOMAINS = 5
MAX_ADVICE_CHARS = 200
# 与 docs/p5/contracts/notification-requested.schema.json / Java WeakDomainRequest 同一模式
_DOMAIN_PATTERN = re.compile(r"^[a-z][a-z0-9-]*$")


def _number(value: Any) -> float | None:
    """只接受真数字；bool 是 int 的子类，不能让它混进来。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _overall(report: Mapping[str, Any]) -> float | None:
    """总分：优先 payload 的 `overall`；缺省时按已有维度等权均值兜底（同前端口径）。"""
    overall = _number(report.get("overall"))
    if overall is not None:
        return round(overall, 2)
    scores = report.get("scores") or {}
    values = [_number(value) for value in scores.values()]
    present = [value for value in values if value is not None]
    if not present:
        return None
    return round(sum(present) / len(present), 2)


def summarize_report(report: Mapping[str, Any]) -> dict[str, Any] | None:
    """报告 payload → `{overall, weak_domains[]}`；没有可用分数时返回 None。"""
    overall = _overall(report)
    if overall is None:
        return None

    if report.get("interview_type") == "behavioral":
        return {"overall": overall, "weak_domains": []}

    scores = report.get("domain_scores") or {}
    advice = {
        item.get("domain"): item.get("advice")
        for item in (report.get("study_advice") or [])
        if isinstance(item, Mapping)
    }

    weak_domains: list[dict[str, Any]] = []
    for domain in report.get("weaknesses") or []:
        if not isinstance(domain, str) or not _DOMAIN_PATTERN.fullmatch(domain):
            continue
        score = _number(scores.get(domain))
        if score is None:
            continue
        item: dict[str, Any] = {"domain": domain, "score": score}
        text = advice.get(domain)
        if isinstance(text, str) and text.strip():
            item["advice"] = text.strip()[:MAX_ADVICE_CHARS]
        weak_domains.append(item)
        if len(weak_domains) == MAX_WEAK_DOMAINS:
            break

    return {"overall": overall, "weak_domains": weak_domains}
