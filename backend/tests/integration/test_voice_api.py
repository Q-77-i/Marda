"""语音路由集成测试（P2-M5 FR-24）：WS 中继（FakeUpstream）+ TTS 端点。

WS 用 starlette TestClient（浏览器侧的真实形状：拿不到自定义请求头，token 只能走 query）。
上游一律注入假连接——真火山是计费服务，真链路交给 smoke_voice.py。
"""

from __future__ import annotations

import pytest
from asr_fake import ScriptedConn, header_of, make_connect, server_response
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.api import voice as voice_api
from app.main import app
from app.tools import asr, tts


def token_of(client) -> str:
    return client.headers["Authorization"].removeprefix("Bearer ")


def install_session(monkeypatch, conn=None, *, api_key: str = "k") -> None:
    """把路由里的会话工厂换成假上游（真链路以外的一切编排照旧跑）。"""

    def _make(settings, uid: str) -> asr.AsrSession:
        return asr.AsrSession(
            url="wss://fake/asr",
            api_key=api_key,
            resource_id="volc.seedasr.sauc.duration",
            uid=uid,
            connect=make_connect(conn) if conn is not None else None,
        )

    monkeypatch.setattr(voice_api, "_make_session", _make)


# ---------- ASR WebSocket ----------


async def test_ws_asr_转写流程_配置帧_音频帧_末帧_与事件序(monkeypatch, client):
    conn = ScriptedConn({
        1: [  # 配置帧发出后：上游开始吐累计转写
            server_response({"result": {"text": "你好"}}),
            server_response({"result": {"text": "你好，世界"}}),
        ],
        2: [  # 首个音频帧后：末包结果
            server_response({"result": {"text": "你好，世界。"}}, flags=asr.NEG_SEQUENCE_1),
        ],
    })
    install_session(monkeypatch, conn)

    with TestClient(app) as tc:
        with tc.websocket_connect(f"/api/asr?token={token_of(client)}") as ws:
            ws.send_bytes(b"\x00\x01" * 1600)
            ws.send_text('{"type":"stop"}')
            events = [ws.receive_json() for _ in range(3)]

    assert [(e["type"], e["text"]) for e in events] == [
        ("partial", "你好"), ("partial", "你好，世界"), ("final", "你好，世界。"),
    ]
    # 上行：配置帧在前、音频帧在后、末帧（flags 标记、无序号字段）收尾
    assert header_of(conn.sent[0])[0] == asr.CLIENT_FULL_REQUEST
    assert header_of(conn.sent[1])[0] == asr.CLIENT_AUDIO_ONLY_REQUEST
    assert int.from_bytes(conn.sent[1][4:8], "big", signed=True) == 2  # 配置帧占 1 号
    assert header_of(conn.sent[-1])[1] == asr.NEG_SEQUENCE  # 末帧无序号字段
    assert conn.closed is True


async def test_ws_asr_未配置key_回可展示文案而不是静默失败(monkeypatch, client):
    install_session(monkeypatch, None, api_key="")

    with TestClient(app) as tc:
        with tc.websocket_connect(f"/api/asr?token={token_of(client)}") as ws:
            message = ws.receive_json()

    assert message["type"] == "error"
    assert "VOLCANO_SPEECH_API_KEY" in message["message"]


async def test_ws_asr_上游连不上_回错误事件并正常关闭(monkeypatch, client):
    def _make(settings, uid: str) -> asr.AsrSession:
        async def _boom(url: str, **kwargs):
            raise OSError("network unreachable")

        return asr.AsrSession(url="wss://fake", api_key="k", resource_id="r", uid=uid, connect=_boom)

    monkeypatch.setattr(voice_api, "_make_session", _make)

    with TestClient(app) as tc:
        with tc.websocket_connect(f"/api/asr?token={token_of(client)}") as ws:
            message = ws.receive_json()

    assert message["type"] == "error"
    assert "语音服务连接失败" in message["message"]


async def test_ws_asr_未登录_4401关闭(anon_client):
    with TestClient(app) as tc:
        with pytest.raises(WebSocketDisconnect) as exc:
            with tc.websocket_connect("/api/asr?token=bad-token") as ws:
                ws.receive_json()
    assert exc.value.code == voice_api.WS_UNAUTHORIZED


# ---------- TTS 端点 ----------


class FakeCommunicate:
    def __init__(self, text: str, voice: str) -> None:
        self.text = text
        self.voice = voice

    async def stream(self):
        for piece in (b"ID3\x01", b"\x02\x03"):
            yield {"type": "audio", "data": piece}


@pytest.fixture
def install_tts(monkeypatch):
    monkeypatch.setattr(tts.edge_tts, "Communicate", FakeCommunicate)


async def test_tts_返回_mp3_流(install_tts, client):
    r = await client.post("/api/tts", json={"text": " 你好，请介绍一下你自己 "})
    assert r.status_code == 200
    assert r.headers["content-type"] == "audio/mpeg"
    assert r.content == b"ID3\x01\x02\x03"


async def test_tts_空白文本_422(install_tts, client):
    r = await client.post("/api/tts", json={"text": "   "})
    assert r.status_code == 422
    assert "没有可播报的文本" in r.json()["detail"]


async def test_tts_上游失败_502_且文案可展示(monkeypatch, client):
    class Broken:
        def __init__(self, text: str, voice: str) -> None: ...

        async def stream(self):  # 真 edge-tts 的 stream 是异步生成器，替身保持同形状
            raise RuntimeError("403 handshake")
            yield b""  # pragma: no cover —— 只为让本方法是异步生成器

    monkeypatch.setattr(tts.edge_tts, "Communicate", Broken)
    r = await client.post("/api/tts", json={"text": "你好"})
    assert r.status_code == 502
    assert "语音合成失败" in r.json()["detail"]


async def test_tts_未登录_401(install_tts, anon_client):
    r = await anon_client.post("/api/tts", json={"text": "你好"})
    assert r.status_code == 401
