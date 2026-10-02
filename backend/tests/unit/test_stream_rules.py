"""graph/rules/stream.py 单测（P2-M4）：面试官消息的流式透传，离线无图。

两条契约钉死在这里：
1. 事件形状 {type: message_start|message_delta, text}——SSE 事件名是 transport 的事，
   由 service 映射，节点侧不出现（M5 语音事件接进来时也不必改节点）；
2. **分片拼接 == 节点落 chat_history 的那条消息**——前端「收终稿替换累积文本」的对账
   全靠它，漂了就是用户看见文字跳变。
"""

from __future__ import annotations

import pytest

from app.graph.rules import stream


class _Capture:
    def __init__(self) -> None:
        self.payloads: list[dict] = []

    def __call__(self, payload: dict) -> None:
        self.payloads.append(payload)


@pytest.fixture
def capture(monkeypatch) -> _Capture:
    cap = _Capture()
    monkeypatch.setattr(stream, "get_stream_writer", lambda: cap)
    return cap


def _fake_chat(pieces: list[str], text: str):
    """替身 llm.chat：逐块调 on_delta，返回全文（与真实现的契约一致）。"""

    async def _chat(messages, *, on_delta=None, **kwargs):
        for piece in pieces:
            if on_delta is not None:
                on_delta(piece)
        return text

    return _chat


def test_begin_只发消息开始(capture):
    stream.begin()

    assert capture.payloads == [{"type": "message_start"}]


def test_begin_带纯代码文案时作为首块发出(capture):
    """模板衔接语/元数据追问：一条消息 + 一整块（无 LLM）。"""
    stream.begin("好，我们换个方向。")

    assert capture.payloads == [
        {"type": "message_start"},
        {"type": "message_delta", "text": "好，我们换个方向。"},
    ]


async def test_speak_透传分片并返回含前缀的全文(capture, monkeypatch):
    monkeypatch.setattr(stream.llm, "chat", _fake_chat(["甲", "乙"], "甲乙"))

    text = await stream.speak([{"role": "system", "content": "x"}], preamble="前缀：")

    assert text == "前缀：甲乙"
    assert capture.payloads == [
        {"type": "message_start"},
        {"type": "message_delta", "text": "前缀："},
        {"type": "message_delta", "text": "甲"},
        {"type": "message_delta", "text": "乙"},
    ]


async def test_speak_分片拼接等于返回全文(capture, monkeypatch):
    monkeypatch.setattr(stream.llm, "chat", _fake_chat(["春", "夏", "秋"], "春夏秋"))

    text = await stream.speak([{"role": "system", "content": "x"}], preamble="四季：")

    emitted = "".join(p["text"] for p in capture.payloads if p["type"] == "message_delta")
    assert emitted == text


async def test_speak_不传前缀时首块就是LLM正文(capture, monkeypatch):
    monkeypatch.setattr(stream.llm, "chat", _fake_chat(["你好"], "你好"))

    assert await stream.speak([{"role": "system", "content": "x"}]) == "你好"
    assert capture.payloads == [
        {"type": "message_start"},
        {"type": "message_delta", "text": "你好"},
    ]


async def test_speak_透传max_tokens等参数(capture, monkeypatch):
    seen: dict = {}

    async def _chat(messages, *, on_delta=None, **kwargs):
        seen.update(kwargs)
        return "文案"

    monkeypatch.setattr(stream.llm, "chat", _chat)

    await stream.speak([{"role": "system", "content": "x"}], max_tokens=64, temperature=0.2)

    assert seen == {"max_tokens": 64, "temperature": 0.2}


async def test_图外调用退化为空操作(monkeypatch):
    """不在图里跑（单测直调节点 / evals / 脚本）：不许炸。

    `get_stream_writer()` 在图外抛 `RuntimeError: Called get_config outside of a runnable
    context`（不是静默 no-op，实测），故 stream.py 内部必须把这条吞掉。
    """
    monkeypatch.setattr(stream.llm, "chat", _fake_chat(["甲"], "甲"))

    stream.begin("纯代码文案")  # 走真 get_stream_writer → 守卫吞掉
    assert await stream.speak([{"role": "system", "content": "x"}]) == "甲"
