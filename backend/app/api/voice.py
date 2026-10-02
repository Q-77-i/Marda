"""语音路由（P2-M5 FR-24）：ASR WebSocket 中继 + TTS 端点。

两条通道与面试引擎**完全解耦**（PRD FR-24「引擎与模态解耦」）：
- `WS /api/asr`：浏览器 ⇄ 后端 ⇄ 火山豆包流式识别。上行二进制 = PCM 片段，
  文本帧 = 控制指令（`{"type": "stop"}`）；下行 JSON = partial / final / error。
  **音频不落盘不落库不写日志**（P2 红线：用户语音数据只在内存里过一遍）；
- `POST /api/tts`：面试官消息文本 → MP3（edge-tts）。前端在消息终稿结算后自行调用，
  失败只影响播报、不影响面试（三档降级见 tools/tts.py）。

鉴权（两处形态不同，各有原因）：
- TTS 走常规 Bearer（`authorizedFetch` 直接可用）；
- ASR 走 **query token**：浏览器 WebSocket API 不能自定义请求头，只能把 token 放 URL。
  代价是 token 会进 nginx access log（demo 接受，记在 SPEC §11）；失效一律 4401 关闭，
  前端复用既有 401 处置（弹确认框，答题中途不被直接踢走）。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app import db, security
from app.api.auth import get_current_user
from app.config import get_settings
from app.tools import asr, tts

router = APIRouter(prefix="/api", tags=["voice"])

# 关闭码：4401 = 未登录/登录过期（前端识这个码走既有 401 处置；4000-4999 是应用自定义段）
WS_UNAUTHORIZED = 4401
FORWARD_TIMEOUT = 2.0  # stop 之后等转发任务把 final 送出浏览器的上限


class TtsRequest(BaseModel):
    # 上限与 tools/tts.MAX_TEXT 对齐：超长在工具层截断，这里只挡明显异常的请求体
    text: str = Field(min_length=1, max_length=4000)


@router.post("/tts")
async def synthesize_speech(req: TtsRequest, user: dict = Depends(get_current_user)):
    """文本 → MP3（chunked）。首块先取出来做错误映射——否则响应头已发、502 变截断。"""
    text = tts.normalize_text(req.text)
    if not text:
        raise HTTPException(status_code=422, detail="没有可播报的文本")
    stream = tts.synthesize(text, voice=get_settings().tts_voice)
    try:
        first = await stream.__anext__()
    except StopAsyncIteration:
        raise HTTPException(status_code=502, detail="语音合成没有返回音频")
    except tts.TtsError as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    async def body():
        yield first
        try:
            async for chunk in stream:
                yield chunk
        except tts.TtsError:
            # 中途断流：响应头已发，只能截断（前端播到一半会自然结束）
            return

    return StreamingResponse(body(), media_type="audio/mpeg")


async def _ws_user(token: str) -> str | None:
    """query token → user_id（与 get_current_user 同口径：token 有效且用户还在）。"""
    if not token:
        return None
    user_id = security.decode_token(token, get_settings().jwt_secret)
    if user_id is None:
        return None
    row = await asyncio.to_thread(db.get_user, get_settings().db_path, user_id)
    return user_id if row else None


def _make_session(settings, uid: str) -> asr.AsrSession:
    """会话工厂（测试注入 FakeUpstream 的接缝，同 llm._get_client 的做法）。"""
    return asr.AsrSession(
        url=settings.volcano_asr_url,
        api_key=settings.volcano_speech_api_key,
        resource_id=settings.volcano_asr_resource_id,
        uid=uid,
    )


def _is_stop(raw: str) -> bool:
    try:
        payload = json.loads(raw)
    except ValueError:
        return False
    return isinstance(payload, dict) and payload.get("type") == "stop"


async def _forward(session: asr.AsrSession, ws: WebSocket) -> None:
    """会话事件 → 浏览器（唯一消费者；收到 final/error 即退出）。"""
    while True:
        event = await session.events.get()
        payload: dict[str, Any] = {"type": event.type, "text": event.text}
        if event.type == "error":
            payload["message"] = event.message
        with contextlib.suppress(Exception):  # 浏览器已断开：没人接就算了
            await ws.send_json(payload)
        if event.type in ("final", "error"):
            return


@router.websocket("/asr")
async def asr_stream(ws: WebSocket, token: str = Query(default="")) -> None:
    """一次录音 = 一条连接：连上游 → 收 PCM → stop → final → 关。"""
    user_id = await _ws_user(token)
    if user_id is None:
        # **必须先 accept 再 close**：ASGI 下未 accept 就关闭会变成 HTTP 403 响应，
        # 浏览器只看到 close code 1006（异常关闭），前端就认不出「登录过期」这一态了
        await ws.accept()
        await ws.close(code=WS_UNAUTHORIZED)
        return
    await ws.accept()

    session = _make_session(get_settings(), user_id)
    try:
        await session.open()
    except asr.AsrError as exc:
        # 未配置 key / 连不上上游：连上再说明原因（浏览器只有连上才收得到消息）
        await ws.send_json({"type": "error", "message": str(exc)})
        await ws.close()
        return

    forward = asyncio.create_task(_forward(session, ws))
    try:
        while True:
            message = await ws.receive()
            if message["type"] == "websocket.disconnect":
                break
            if (data := message.get("bytes")) is not None:
                await session.feed(data)
            elif (raw := message.get("text")) is not None and _is_stop(raw):
                await session.finish()  # 发末帧并等上游收尾
                # 等转发任务把 final 送出浏览器（它收到终态事件自己就退出）；超时/浏览器已断都不影响收尾
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(asyncio.shield(forward), timeout=FORWARD_TIMEOUT)
                break
    except WebSocketDisconnect:
        pass  # 浏览器直接断开（关标签页/取消录音）：不再补 final
    finally:
        forward.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):  # noqa: BLE001
            await forward
        await session.close()
        with contextlib.suppress(Exception):
            await ws.close()
