"""豆包流式 ASR 协议单测（P2-M5 FR-24）：帧构造/解析 round-trip + 会话编排（FakeUpstream）。

不打真火山（计费服务，交给探针与 smoke 真调验证）。帧格式的「真值」来自探针实测：
2026-10-02 服务端正确解析了本实现同构的配置帧/音频帧并回了结构化错误帧。
"""

from __future__ import annotations

import asyncio
import gzip
import json

import pytest
from asr_fake import (  # tests/ 根目录的共享替身（单测与集成共用）
    FakeConn,
    header_of,
    make_connect,
    payload_of,
    server_error,
    server_response,
)

from app.tools import asr


# ---------- 帧构造 ----------


def test_配置帧_头部与_gzip_JSON():
    frame = asr.build_full_request(asr.request_payload("u1"))
    assert frame[0] == 0x11  # version=1 header_size=1
    assert header_of(frame) == (asr.CLIENT_FULL_REQUEST, asr.NO_SEQUENCE, 0x11)
    body = json.loads(payload_of(frame, has_seq=False))
    assert body["audio"] == {
        "format": "pcm", "codec": "raw", "rate": 16000, "bits": 16, "channel": 1,
    }
    assert body["user"]["uid"] == "u1"
    assert body["request"]["enable_punc"] is True


def test_音频帧_普通帧带正序号_末帧不带序号字段():
    normal = asr.build_audio_frame(b"\x01\x02" * 100, 7)
    mt, flags, _ = header_of(normal)
    assert (mt, flags) == (asr.CLIENT_AUDIO_ONLY_REQUEST, asr.POS_SEQUENCE)
    assert int.from_bytes(normal[4:8], "big", signed=True) == 7
    assert payload_of(normal, has_seq=True) == b"\x01\x02" * 100

    # 末帧：header 之后**直接是长度**（没有序号字段）。写成负序号会被上游当成长度读
    # （实测 `declared body size ... expected=4294967218` = -78 的无符号解释）
    last = asr.build_audio_frame(b"", 8, last=True)
    mt, flags, _ = header_of(last)
    assert (mt, flags) == (asr.CLIENT_AUDIO_ONLY_REQUEST, asr.NEG_SEQUENCE)
    assert len(last) == 4 + 4 + len(gzip.compress(b""))
    assert payload_of(last, has_seq=False) == b""


# ---------- 响应解析 ----------


def test_解析正常响应帧():
    frame = asr.parse_server_frame(
        server_response({"result": {"text": "你好世界"}}, flags=0b0001, seq=3)
    )
    assert frame.message_type == asr.SERVER_FULL_RESPONSE
    assert frame.seq == 3
    assert frame.text == "你好世界"
    assert frame.is_final is False


def test_末包结果标记_is_final():
    frame = asr.parse_server_frame(
        server_response({"result": {"text": "整段结果"}}, flags=asr.NEG_SEQUENCE_1)
    )
    assert frame.is_final is True


def test_解析错误帧_code_与_message():
    frame = asr.parse_server_frame(server_error(45000010, "bad key"))
    assert frame.message_type == asr.SERVER_ERROR_RESPONSE
    assert frame.code == 45000010
    assert frame.message == "bad key"


def test_畸形帧抛_AsrError():
    with pytest.raises(asr.AsrError):
        asr.parse_server_frame(b"\x11")
    with pytest.raises(asr.AsrError):  # payload 不是 JSON
        body = gzip.compress(b"not-json")
        raw = bytes([0x11, asr.SERVER_FULL_RESPONSE << 4, 0x11, 0]) + len(body).to_bytes(4, "big") + body
        asr.parse_server_frame(raw)


class _FakeResponse:
    def __init__(self, status_code: int, body: bytes) -> None:
        self.status_code = status_code
        self.body = body


class _HandshakeRejected(Exception):
    def __init__(self, status_code: int, body: str) -> None:
        super().__init__("rejected")
        self.response = _FakeResponse(status_code, body.encode())


def test_握手失败文案_三种真实失败形态各给一句解决方向():
    # 探针里真撞到过的三种（长得很不一样，只报 InvalidStatus 等于糊成一件）
    forbidden = asr.handshake_error_text(
        _HandshakeRejected(403, '{"error":"[resource_id=volc.seedasr.sauc.duration] requested resource not granted"}')
    )
    assert "403" in forbidden and "开通" in forbidden

    unauthorized = asr.handshake_error_text(_HandshakeRejected(401, '{"error":"Invalid X-Api-Key"}'))
    assert "401" in unauthorized and "方舟 key 不通用" in unauthorized

    other = asr.handshake_error_text(RuntimeError("dns failed"))
    assert other == "RuntimeError"


def test_空文本不产出_partial_但可作_final():
    frame = asr.parse_server_frame(server_response({"result": {}}))
    assert frame.text == ""


# ---------- 会话编排 ----------


def session(conn: FakeConn, sink: dict | None = None, **kw) -> asr.AsrSession:
    return asr.AsrSession(
        url="wss://example.invalid/asr",
        api_key="k",
        resource_id="volc.seedasr.sauc.duration",
        connect=make_connect(conn, sink),
        **kw,
    )


async def test_会话_正常流程_配置帧_音频帧_末帧_与事件():
    conn, sink = FakeConn(), {}
    s = session(conn, sink)
    await s.open()
    assert sink["url"] == "wss://example.invalid/asr"
    assert sink["additional_headers"]["X-Api-Key"] == "k"
    assert sink["additional_headers"]["X-Api-Resource-Id"] == "volc.seedasr.sauc.duration"
    assert "X-Api-Connect-Id" in sink["additional_headers"]
    assert header_of(conn.sent[0])[0] == asr.CLIENT_FULL_REQUEST

    await s.feed(b"\x00\x01" * 1600)
    await s.feed(b"\x00\x02" * 1600)
    assert [header_of(f)[0] for f in conn.sent[1:]] == [asr.CLIENT_AUDIO_ONLY_REQUEST] * 2
    # 音频从 2 起：配置帧占掉 1 号（上游按自增计数校验，实测原文见 build_audio_frame）
    assert [int.from_bytes(f[4:8], "big", signed=True) for f in conn.sent[1:]] == [2, 3]

    await conn.push(server_response({"result": {"text": "你好"}}))
    await conn.push(server_response({"result": {"text": "你好，世界"}}, flags=asr.NEG_SEQUENCE_1))
    final = await asyncio.wait_for(s.finish(), timeout=3)

    assert final.type == "final"
    assert final.text == "你好，世界"
    assert header_of(conn.sent[-1])[1] == asr.NEG_SEQUENCE  # 末帧：flags 标记 + 无序号字段
    events = []
    while not s.events.empty():
        events.append(s.events.get_nowait())
    assert [(e.type, e.text) for e in events] == [("partial", "你好"), ("final", "你好，世界")]
    await s.close()


async def test_会话_上游错误帧_转成_error_事件与中文文案():
    conn = FakeConn()
    s = session(conn)
    await s.open()
    await conn.push(server_error(45000010, "ark auth failed"))
    event = await asyncio.wait_for(s.events.get(), timeout=3)
    assert event.type == "error"
    assert "语音 API Key" in event.message  # 已知错误码给可操作的中文文案
    await s.close()


async def test_会话_上游沉默时_finish_用最后_partial_兜底():
    conn = FakeConn()
    s = session(conn, finish_timeout=0.2)
    await s.open()
    await conn.push(server_response({"result": {"text": "只说到一半"}}))
    await asyncio.sleep(0.05)
    final = await asyncio.wait_for(s.finish(), timeout=2)
    assert final.type == "final"
    assert final.text == "只说到一半"
    await s.close()


async def test_会话_上游中途断开转成_error_而不是干等():
    conn = FakeConn()
    s = session(conn)
    await s.open()
    await conn.eof()
    final = await asyncio.wait_for(s.finish(), timeout=2)
    assert final.text == ""  # 一个字都没有：final 为空由前端提示「没听清」
    events = []
    while not s.events.empty():
        events.append(s.events.get_nowait())
    assert [e.type for e in events] == ["error"]
    await s.close()


async def test_会话_未配置_key_直接给出可操作文案():
    s = asr.AsrSession(url="wss://x", api_key="", resource_id="r")
    with pytest.raises(asr.AsrError) as err:
        await s.open()
    assert "VOLCANO_SPEECH_API_KEY" in str(err.value)


async def test_会话_feed_在未连接时静默丢弃_不抛错():
    s = asr.AsrSession(url="wss://x", api_key="k", resource_id="r")
    await s.feed(b"\x00" * 100)  # 不该抛（音频是过程数据，丢一片不炸整场面试）
