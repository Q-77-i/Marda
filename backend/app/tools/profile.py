"""能力档案（FR-19 / SPEC §4.8）：多场报告 → 得分曲线与短板变化。

数据源 = 已落库的报告 payload（§4.6），与学习推荐（FR-20）同一口径：**不重算分数、
不落库、零 LLM 调用**——报告与 PDF 因此零回归，档案永远跟着报告走。

本模块只做「多场之间的比较」：单场的分数、短板由报告聚合（rules/aggregate）决定，
这里不重新定义任何评分口径（总分走 `overall_score` 同一个函数）。
"""

from __future__ import annotations

from app.graph.rules.aggregate import FIVE_DIMS, overall_score


def build_profile(rows: list[dict]) -> dict:
    """报告行（`db.list_reports` 输出，按 started_at 升序）→ 档案 payload。

    升序即曲线的从左到右 = 时间从早到晚；排序由 db 层保证，本层不重排。

    「域有洞」是常态而非异常：一场只考部分域（`tech_quota` 按权重分配题量），
    没考的域在该场的 `domain_scores` 里**根本没有键**——前端据此断线，不补零
    （补零会凭空造出一个「该场该域得 0 分」的低谷，那是假信号）。
    """
    sessions = [_session(row) for row in rows]
    return {
        "sessions": sessions,
        "summary": _summary(sessions),
        "weakness_changes": _weakness_changes(sessions),
    }


def _session(row: dict) -> dict:
    payload = row.get("payload") or {}
    raw_scores = payload.get("scores") or {}
    scores = {dim: float(raw_scores.get(dim, 0) or 0) for dim in FIVE_DIMS}
    # 总分：新 payload 直接取报告端算好的值（报告页显示的就是它）；老 payload 无该字段
    # → 用同一函数现算（口径单一来源，见 aggregate.overall_score）。
    stored = payload.get("overall")
    return {
        "interview_id": row["interview_id"],
        "position": row.get("position") or payload.get("position") or "",
        "difficulty": row.get("difficulty") or "",
        "question_count": payload.get("question_count") or 0,
        "answered_count": payload.get("answered_count") or 0,
        "started_at": row.get("started_at") or "",
        "overall": float(stored) if stored is not None else overall_score(scores),
        "scores": scores,
        "domain_scores": {
            domain: float(value) for domain, value in (payload.get("domain_scores") or {}).items()
        },
        "weaknesses": list(payload.get("weaknesses") or []),
    }


def _summary(sessions: list[dict]) -> dict:
    """整体概览：场次数、均分、最高/最低场、最近一场的变化。"""
    if not sessions:
        return {
            "session_count": 0,
            "average_overall": 0.0,
            "best": None,
            "worst": None,
            "latest_delta": None,
        }
    overalls = [session["overall"] for session in sessions]
    best = max(sessions, key=lambda session: session["overall"])  # 并列取最早（max 取首个最大）
    worst = min(sessions, key=lambda session: session["overall"])
    return {
        "session_count": len(sessions),
        "average_overall": round(sum(overalls) / len(overalls), 2),
        "best": {"interview_id": best["interview_id"], "overall": best["overall"]},
        "worst": {"interview_id": worst["interview_id"], "overall": worst["overall"]},
        "latest_delta": _latest_delta(sessions),
    }


def _latest_delta(sessions: list[dict]) -> dict | None:
    """最近一场相对上一场的总分变化；只有一场时无从比较 → None。"""
    if len(sessions) < 2:
        return None
    previous, latest = sessions[-2]["overall"], sessions[-1]["overall"]
    return {"from": previous, "to": latest, "delta": round(latest - previous, 2)}


def _weakness_changes(sessions: list[dict]) -> list[dict]:
    """逐场对**上一场**比短板的三种走向（首场无从比较，不产出条目）。

    - `new`：上场不是短板、本场是 → 新暴露的窟窿
    - `persistent`：两场都是 → 老问题没解决
    - `resolved`：上场是、本场不是 → 补上了（可能是真提升，也可能只是这场没考到该域，
      展示口径上按「本场不再是短板」如实呈现，不替用户下结论）

    三个列表都按字典序：跨场展示顺序稳定，不随 payload 里 weaknesses 的排列漂。
    """
    changes = []
    for previous, current in zip(sessions, sessions[1:]):
        before, now = set(previous["weaknesses"]), set(current["weaknesses"])
        changes.append({
            "interview_id": current["interview_id"],
            "started_at": current["started_at"],
            "new": sorted(now - before),
            "persistent": sorted(now & before),
            "resolved": sorted(before - now),
        })
    return changes
