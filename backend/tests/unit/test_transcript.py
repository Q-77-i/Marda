"""语音转写智能校对单测（P2-M13 追加）：提示构造 / 空文本 / 失败语义。"""

from __future__ import annotations

import pytest

from app.tools import transcript


async def test_校对提示带问题与技术术语(monkeypatch):
    captured: dict = {}

    async def fake(messages, *, schema, **kwargs):
        captured["messages"] = messages
        return schema(text="先召回再筛")

    monkeypatch.setattr(transcript.llm, "chat_json", fake)
    result = await transcript.correct_text("先兆回再塞", "问题")

    assert result == "先召回再筛"
    joined = "\n".join(item["content"] for item in captured["messages"])
    assert "先兆回再塞" in joined
    assert "先召回再筛" in joined  # 技术术语提示
    assert "面试问题：问题" in joined


async def test_空文本原样返回且不调模型(monkeypatch):
    called = False

    async def fake(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("空文本不应调用模型")

    monkeypatch.setattr(transcript.llm, "chat_json", fake)
    assert await transcript.correct_text("   ") == "   "
    assert called is False


async def test_模型失败抛统一错误(monkeypatch):
    async def fake(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(transcript.llm, "chat_json", fake)
    with pytest.raises(transcript.TranscriptError):
        await transcript.correct_text("先兆回再塞")
