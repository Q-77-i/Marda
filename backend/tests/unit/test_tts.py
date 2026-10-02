"""TTS 包装单测（P2-M5 FR-24）：文本清洗 + edge-tts 分片透传 + 失败映射。

不打真微软端点（网络依赖，交给 smoke 真链路验证）。
"""

from __future__ import annotations

import pytest

from app.tools import tts


class FakeCommunicate:
    """edge_tts.Communicate 的最小替身：按脚本产出分片。"""

    script: list[dict] = []
    raises: Exception | None = None
    seen: list[tuple[str, str]] = []

    def __init__(self, text: str, voice: str) -> None:
        FakeCommunicate.seen.append((text, voice))

    async def stream(self):
        if FakeCommunicate.raises is not None:
            raise FakeCommunicate.raises
        for chunk in FakeCommunicate.script:
            yield chunk


@pytest.fixture(autouse=True)
def _fake_edge(monkeypatch):
    FakeCommunicate.script = []
    FakeCommunicate.raises = None
    FakeCommunicate.seen = []
    monkeypatch.setattr(tts.edge_tts, "Communicate", FakeCommunicate)
    yield


async def collect(text: str, voice: str = "zh-CN-XiaoxiaoNeural") -> bytes:
    return b"".join([chunk async for chunk in tts.synthesize(text, voice=voice)])


def test_清洗文本_去首尾空白与截断():
    assert tts.normalize_text("  你好  ") == "你好"
    assert tts.normalize_text("") == ""
    assert len(tts.normalize_text("字" * 5000)) == tts.MAX_TEXT


async def test_只透传音频分片_忽略其它类型():
    FakeCommunicate.script = [
        {"type": "audio", "data": b"ID3\x01"},
        {"type": "WordBoundary", "offset": 100},  # 字幕时间轴：与播放无关，不该混进音频
        {"type": "audio", "data": b"\x02\x03"},
    ]
    assert await collect("你好") == b"ID3\x01\x02\x03"
    assert FakeCommunicate.seen == [("你好", "zh-CN-XiaoxiaoNeural")]


async def test_空白文本直接报错_不打上游():
    with pytest.raises(tts.TtsError):
        await collect("   ")
    assert FakeCommunicate.seen == []


async def test_没有音频分片算失败():
    FakeCommunicate.script = [{"type": "WordBoundary", "offset": 1}]
    with pytest.raises(tts.TtsError):
        await collect("你好")


async def test_上游异常转成可展示文案():
    FakeCommunicate.raises = RuntimeError("403 handshake failed")
    with pytest.raises(tts.TtsError) as err:
        await collect("你好")
    assert "语音合成失败" in str(err.value)
    assert "403" not in str(err.value)  # 内部细节不进用户可见文案
