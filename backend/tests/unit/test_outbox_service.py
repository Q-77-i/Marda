"""P5-J5 T2：落库时把收尾事件交给 db.finalize_interview（开关 / 构建失败语义）。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app import cache
from app.service import Service


def _report() -> dict:
    return {
        "interview_id": "iv-1",
        "position": "Agent",
        "interview_type": "tech",
        "scores": {"technical_depth": 3.0},
        "overall": 3.0,
        "domain_scores": {"rag": 2.5},
        "weaknesses": ["rag"],
        "study_advice": [],
    }


def _service(enabled: bool) -> Service:
    settings = SimpleNamespace(
        outbox_enabled=enabled,
        business_db="/tmp/marda-outbox-test.sqlite3",  # finalize 被替换，不会真写
    )
    return Service(settings, cache_client=cache.NoopCache())


async def _persist_capture(monkeypatch, service: Service) -> dict:
    captured: dict = {}

    def _finalize(target, interview_id, records, report, *, event=None):
        captured["event"] = event

    monkeypatch.setattr("app.service.db.finalize_interview", _finalize)
    await service._persist(  # noqa: SLF001 —— 测的就是落库接缝
        "iv-1",
        {"user_id": "u1", "report": _report(), "answered_questions": []},
    )
    return captured


@pytest.mark.asyncio
async def test_开关打开时把事件带进落库(monkeypatch):
    captured = await _persist_capture(monkeypatch, _service(enabled=True))

    assert captured["event"]["type"] == "interview.finalized"
    assert captured["event"]["payload"]["user_id"] == "u1"


@pytest.mark.asyncio
async def test_开关关闭时不带事件(monkeypatch):
    captured = await _persist_capture(monkeypatch, _service(enabled=False))

    assert captured["event"] is None


@pytest.mark.asyncio
async def test_事件构建失败不阻断落库(monkeypatch):
    def _boom(**_):
        raise RuntimeError("builder bug")

    monkeypatch.setattr("app.service.build_interview_finalized", _boom)
    captured = await _persist_capture(monkeypatch, _service(enabled=True))

    assert captured["event"] is None
