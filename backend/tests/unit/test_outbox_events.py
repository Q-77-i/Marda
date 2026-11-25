"""P5-J5 T1：报告 payload → report_summary / 事件信封（纯函数）。

摘要派生是**服务端唯一实现**：前端 `lib/learning.ts` 的 `buildReportSummary` 是展示副本，
smoke 脚本改为复用本模块。两边用例必须同口径：
- 行为面没有可学习的技术域 → 空短板（不是把维度名塞进 weak_domains）；
- 短板缺 `domain_scores` 分数 → 丢掉该域，不按 0 分猜；
- 最多 5 个域；`advice` 截断到契约的 200 字；
- 整场未评分 → 不产出摘要、不发事件（缺数据 ≠ 0 分）。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

from app.outbox.events import build_interview_finalized
from app.outbox.summary import summarize_report

CONTRACTS = Path(__file__).resolve().parents[3] / "docs" / "p5" / "contracts"
FINISHED_AT = datetime(2026, 11, 20, 12, 0, 0, tzinfo=timezone.utc)


def _report(**overrides):
    report = {
        "interview_id": "iv-1",
        "position": "Agent",
        "interview_type": "tech",
        "scores": {
            "technical_depth": 3.5,
            "fundamentals": 3.0,
            "project_experience": 3.2,
            "communication": 3.6,
            "problem_solving": 3.1,
        },
        "overall": 3.28,
        "domain_scores": {"rag": 2.8, "memory": 3.1, "tool-use": 3.6},
        "weaknesses": ["rag", "memory"],
        "study_advice": [
            {"domain": "rag", "advice": "优先补混合检索与重排的取舍。"},
            {"domain": "memory", "advice": "补会话记忆的压缩策略。"},
        ],
    }
    report.update(overrides)
    return report


def _event(report=None, *, interview_id="iv-1", user_id="u1"):
    return build_interview_finalized(
        interview_id=interview_id,
        user_id=user_id,
        report=report or _report(),
        finished_at=FINISHED_AT,
    )


def _validate(schema_name: str, instance) -> None:
    schema = json.loads((CONTRACTS / schema_name).read_text(encoding="utf-8"))
    errors = list(
        Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(instance)
    )
    assert not errors, errors


# ---------- 摘要派生 ----------


def test_技术面摘要按短板顺序带出分数与建议():
    assert summarize_report(_report()) == {
        "overall": 3.28,
        "weak_domains": [
            {"domain": "rag", "score": 2.8, "advice": "优先补混合检索与重排的取舍。"},
            {"domain": "memory", "score": 3.1, "advice": "补会话记忆的压缩策略。"},
        ],
    }


def test_缺分数的短板不按零分猜():
    summary = summarize_report(_report(domain_scores={"rag": 2.8}))

    assert [item["domain"] for item in summary["weak_domains"]] == ["rag"]


def test_短板最多保留五个():
    domains = [
        "agent-architecture",
        "rag",
        "memory",
        "tool-use",
        "planning-reasoning",
        "engineering-observability",
    ]
    summary = summarize_report(
        _report(
            domain_scores={domain: 3.0 for domain in domains},
            weaknesses=domains,
            study_advice=[],
        )
    )

    assert [item["domain"] for item in summary["weak_domains"]] == domains[:5]


def test_行为面短板为空数组不把维度名塞进去():
    summary = summarize_report(
        _report(
            interview_type="behavioral",
            domain_scores={},
            weaknesses=["communication", "career_stability"],
            overall=3.2,
        )
    )

    assert summary == {"overall": 3.2, "weak_domains": []}


def test_不符合契约域名的短板被丢掉():
    summary = summarize_report(
        _report(
            domain_scores={"logic_structure": 2.0, "rag": 2.8},
            weaknesses=["logic_structure", "rag"],
            study_advice=[],
        )
    )

    assert [item["domain"] for item in summary["weak_domains"]] == ["rag"]


def test_整场未评分不产出摘要():
    assert (
        summarize_report(
            _report(scores={}, overall=None, domain_scores={}, weaknesses=[])
        )
        is None
    )


def test_缺overall时按已有维度均值兜底():
    summary = summarize_report(
        _report(overall=None, scores={"technical_depth": 4.0, "fundamentals": 3.0})
    )

    assert summary["overall"] == 3.5


def test_建议超长截断到二百字():
    summary = summarize_report(
        _report(study_advice=[{"domain": "rag", "advice": "学" * 260}])
    )

    assert len(summary["weak_domains"][0]["advice"]) == 200


# ---------- 事件信封 ----------


def test_事件信封与载荷通过契约_schema():
    event = _event()

    _validate("event-envelope.schema.json", event)
    _validate("interview-finalized.schema.json", event["payload"])


def test_报告_id_与场次一致且信封字段固定():
    event = _event()

    assert event["type"] == "interview.finalized"
    assert event["version"] == 1
    assert event["producer"] == "agent"
    assert event["payload"]["report_id"] == "iv-1"
    assert event["payload"]["finished_at"] == "2026-11-20T12:00:00Z"
    assert "trace_id" not in event  # J8 接 OTel 后再补


def test_同一场次事件_id稳定():
    assert _event()["event_id"] == _event()["event_id"]
    assert _event()["event_id"] != _event(interview_id="iv-2")["event_id"]


def test_没有归属用户不发事件():
    assert _event(user_id="") is None


def test_未评分不发事件():
    assert (
        _event(
            _report(scores={}, overall=None, domain_scores={}, weaknesses=[])
        )
        is None
    )


def test_行为面事件带空短板():
    event = _event(
        _report(
            interview_type="behavioral",
            domain_scores={},
            weaknesses=["communication"],
            overall=3.2,
        )
    )

    assert event["payload"]["report_summary"] == {
        "overall": 3.2,
        "weak_domains": [],
    }
    _validate("event-envelope.schema.json", event)
    _validate("interview-finalized.schema.json", event["payload"])


# ---------- 通知事件契约（Java 生产、worker 消费）----------


def test_通知事件示例通过契约_schema():
    example = json.loads(
        (CONTRACTS / "examples" / "notification-requested.json").read_text(encoding="utf-8")
    )

    _validate("event-envelope.schema.json", example)
    _validate("notification-requested.schema.json", example["payload"])
