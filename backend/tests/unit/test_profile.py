"""能力档案聚合单测（FR-19）：多场报告 → 曲线数据与短板变化。

纯函数层：数据源是已落库的报告 payload（SPEC §4.6），本层不重算分数、不落库。
"""

from __future__ import annotations

from app.tools.profile import build_profile

DIMS = {
    "technical_depth": 4.0,
    "fundamentals": 4.0,
    "project_experience": 4.0,
    "communication": 4.0,
    "problem_solving": 4.0,
}


def _row(
    interview_id: str,
    *,
    started_at: str,
    overall: float | None = None,
    scores: dict | None = None,
    domain_scores: dict | None = None,
    weaknesses: list[str] | None = None,
    difficulty: str = "adaptive",
    position: str = "Agent/AI 工程师",
    question_count: int = 10,
    answered_count: int = 10,
) -> dict:
    """一行 `db.list_reports` 输出（payload 已 json.loads）。"""
    payload = {
        "position": position,
        "scores": dict(scores if scores is not None else DIMS),
        "domain_scores": dict(domain_scores if domain_scores is not None else {"rag": 4.0}),
        "weaknesses": list(weaknesses or []),
        "question_count": question_count,
        "answered_count": answered_count,
    }
    if overall is not None:
        payload["overall"] = overall
    return {
        "interview_id": interview_id,
        "payload": payload,
        "position": position,
        "difficulty": difficulty,
        "started_at": started_at,
    }


def test_空输入给零态():
    profile = build_profile([])

    assert profile["sessions"] == []
    assert profile["weakness_changes"] == []
    assert profile["summary"] == {
        "session_count": 0,
        "average_overall": 0.0,
        "best": None,
        "worst": None,
        "latest_delta": None,
    }


def test_单场有会话无曲线无变化():
    profile = build_profile([_row("iv-1", started_at="2026-09-01T10:00:00Z", overall=4.0)])

    assert [s["interview_id"] for s in profile["sessions"]] == ["iv-1"]
    assert profile["sessions"][0]["started_at"] == "2026-09-01T10:00:00Z"
    assert profile["summary"]["session_count"] == 1
    assert profile["summary"]["latest_delta"] is None
    # 首场无从比较：不产出变化条目（前端也不该显示「较上场」）
    assert profile["weakness_changes"] == []


def test_总分优先取payload存储值():
    """新 payload 带 overall（报告端算的那个值）→ 直接用，曲线与报告页显示同一个数。"""
    row = _row("iv-1", started_at="2026-09-01T10:00:00Z", overall=3.9)

    assert build_profile([row])["sessions"][0]["overall"] == 3.9


def test_老payload无总分时现算五维均值():
    """FR-19 之前落库的报告没有 overall 字段 → 用同一口径现算，不落回 0。"""
    row = _row(
        "iv-1",
        started_at="2026-09-01T10:00:00Z",
        scores={"technical_depth": 5, "fundamentals": 4, "project_experience": 4,
                "communication": 3, "problem_solving": 4},
    )

    assert build_profile([row])["sessions"][0]["overall"] == 4.0


def test_scores缺维按零计():
    row = _row("iv-1", started_at="2026-09-01T10:00:00Z", scores={"technical_depth": 5})

    session = build_profile([row])["sessions"][0]
    assert session["overall"] == 1.0  # 5 ÷ 5 维
    assert session["scores"]["fundamentals"] == 0.0


def test_域得分原样带出不补域():
    """一场只考部分域（tech_quota 按权重分配）→ 档案里也只有考过的域，不补零。"""
    row = _row(
        "iv-1",
        started_at="2026-09-01T10:00:00Z",
        domain_scores={"rag": 4.5, "memory": 3.0},
    )

    assert build_profile([row])["sessions"][0]["domain_scores"] == {"rag": 4.5, "memory": 3.0}


def test_会话顺序按入参保序():
    """db 层已按 started_at 升序取；本层不重排（曲线从左到右 = 时间从早到晚）。"""
    rows = [
        _row("iv-1", started_at="2026-09-01T10:00:00Z"),
        _row("iv-2", started_at="2026-09-05T10:00:00Z"),
        _row("iv-3", started_at="2026-09-09T10:00:00Z"),
    ]

    assert [s["interview_id"] for s in build_profile(rows)["sessions"]] == ["iv-1", "iv-2", "iv-3"]


def test_短板变化三态():
    rows = [
        _row("iv-1", started_at="2026-09-01T10:00:00Z", weaknesses=["rag", "memory"]),
        _row("iv-2", started_at="2026-09-05T10:00:00Z", weaknesses=["rag", "tool-use"]),
        _row("iv-3", started_at="2026-09-09T10:00:00Z", weaknesses=["tool-use"]),
    ]

    changes = build_profile(rows)["weakness_changes"]

    assert [c["interview_id"] for c in changes] == ["iv-2", "iv-3"]  # 首场不产出
    assert changes[0]["new"] == ["tool-use"]        # 上场不是、本场是
    assert changes[0]["persistent"] == ["rag"]      # 两场都是
    assert changes[0]["resolved"] == ["memory"]     # 上场是、本场不是
    assert changes[0]["started_at"] == "2026-09-05T10:00:00Z"
    assert changes[1] == {
        "interview_id": "iv-3",
        "started_at": "2026-09-09T10:00:00Z",
        "new": [],
        "persistent": ["tool-use"],
        "resolved": ["rag"],
    }


def test_变化条目域名字典序():
    """同一态多个域 → 字典序，跨场展示顺序稳定（不随 payload 里 weaknesses 的排列漂）。"""
    rows = [
        _row("iv-1", started_at="2026-09-01T10:00:00Z", weaknesses=["tool-use"]),
        _row("iv-2", started_at="2026-09-05T10:00:00Z", weaknesses=["rag", "memory", "tool-use"]),
    ]

    assert build_profile(rows)["weakness_changes"][0]["new"] == ["memory", "rag"]


def test_汇总均分与最高最低场():
    rows = [
        _row("iv-1", started_at="2026-09-01T10:00:00Z", overall=3.0),
        _row("iv-2", started_at="2026-09-05T10:00:00Z", overall=4.5),
        _row("iv-3", started_at="2026-09-09T10:00:00Z", overall=3.6),
    ]

    summary = build_profile(rows)["summary"]

    assert summary["session_count"] == 3
    assert summary["average_overall"] == 3.7  # (3.0 + 4.5 + 3.6) / 3
    assert summary["best"] == {"interview_id": "iv-2", "overall": 4.5}
    assert summary["worst"] == {"interview_id": "iv-1", "overall": 3.0}
    assert summary["latest_delta"] == {"from": 4.5, "to": 3.6, "delta": -0.9}


def test_最高最低并列取最早场():
    rows = [
        _row("iv-1", started_at="2026-09-01T10:00:00Z", overall=4.0),
        _row("iv-2", started_at="2026-09-05T10:00:00Z", overall=4.0),
    ]

    summary = build_profile(rows)["summary"]

    assert summary["best"]["interview_id"] == "iv-1"
    assert summary["worst"]["interview_id"] == "iv-1"


def test_缺字段的老payload不炸():
    """FR-25/FR-19 之前的历史 payload：scores/domain_scores/weaknesses 可能整体缺失。"""
    row = {"interview_id": "iv-1", "payload": {}, "position": "", "difficulty": "",
           "started_at": "2026-09-01T10:00:00Z"}

    session = build_profile([row])["sessions"][0]

    assert session["overall"] == 0.0
    assert session["scores"] == dict.fromkeys(DIMS, 0.0)
    assert session["domain_scores"] == {}
    assert session["weaknesses"] == []
