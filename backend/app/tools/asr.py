"""豆包流式语音识别（火山 v3 sauc 二进制协议，P2-M5 FR-24）。

分工（与 P1 的「确定性逻辑用代码写死」一致）：
- **纯函数**：帧构造 / 响应解析——离线可单测。帧格式已由探针在真服务端验证过
  （2026-10-02：服务端正确解析了配置帧与音频帧，回了结构化错误帧 `code 45000010`
  ——说明帧头布局、gzip、payload 长度、错误帧解析全对，只差一把语音控制台的 key）；
- **AsrSession**：一次录音 = 一条上游连接（连 → 配置帧 → 喂 PCM → 末帧 → 收结果），
  后台 reader 把 partial / final / error 放进事件队列，由 transport 层（api/voice.py）
  转发给浏览器。**音频只在内存里过一遍**：不落盘、不落库、不写日志（红线，P2 规划）。

上游协议要点（v3）：
- 4 字节头：`version<<4|header_size` / `message_type<<4|flags` / `serialization<<4|compression` / 保留；
- 配置帧：message_type=0001 + 4 字节大端 payload 长度 + gzip(JSON)；
- 音频帧：message_type=0010 + flags=0001（正序号）或 0010（**末帧，负序号**）+ 序号 + 长度 + gzip(PCM)；
- 服务端：message_type=1001 正常（flags & 0b0010 = 末包结果）、1111 错误（4 字节 code + 长度 + 文本）。
"""

from __future__ import annotations

import asyncio
import gzip
import json
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

PROTOCOL_VERSION = 0b0001
HEADER_SIZE = 0b0001

CLIENT_FULL_REQUEST = 0b0001
CLIENT_AUDIO_ONLY_REQUEST = 0b0010
SERVER_FULL_RESPONSE = 0b1001
SERVER_ACK = 0b1011
SERVER_ERROR_RESPONSE = 0b1111

NO_SEQUENCE = 0b0000
POS_SEQUENCE = 0b0001
NEG_SEQUENCE = 0b0010
NEG_SEQUENCE_1 = 0b0011

JSON_SER = 0b0001
NO_SER = 0b0000
GZIP = 0b0001

# 前端把 Float32 降采样到 16k 单声道；每帧 100ms（3200 字节）——切得太碎上游处理开销大，
# 太粗则部分转写迟迟不更新（实测 200ms 一片也是官方示例的取值，这里取 100ms 更跟手）
AUDIO_FORMAT = {"format": "pcm", "codec": "raw", "rate": 16000, "bits": 16, "channel": 1}
FRAME_BYTES = 3200

FINISH_TIMEOUT = 8.0  # 发完末帧后等 final 的上限；超时用最后一次 partial 兜底（不吞已识别文本）


class AsrError(Exception):
    """语音识别失败（上游错误帧 / 连接问题）。message 是可直接展示的中文。"""

    def __init__(self, message: str, *, code: int | None = None) -> None:
        super().__init__(message)
        self.code = code


def _header(message_type: int, flags: int, ser: int = JSON_SER, comp: int = GZIP) -> bytes:
    return bytes([
        (PROTOCOL_VERSION << 4) | HEADER_SIZE,
        (message_type << 4) | flags,
        (ser << 4) | comp,
        0x00,
    ])


def build_full_request(payload: dict[str, Any]) -> bytes:
    """配置帧（连接后第一帧）：gzip 的 JSON，字段与火山文档一致。"""
    body = gzip.compress(json.dumps(payload, ensure_ascii=False).encode())
    return _header(CLIENT_FULL_REQUEST, NO_SEQUENCE) + len(body).to_bytes(4, "big") + body


def build_audio_frame(pcm: bytes, seq: int, *, last: bool = False) -> bytes:
    """音频帧。`seq` 只在普通帧里出现——**末帧的 flags=0010 表示「不带序号字段」**。

    实测教训（2026-10-02，两种错法都撞过）：
    - 音频序号从 1 起 → `autoAssignedSequence (2) mismatch sequence in request (1)`
      （配置帧占 1 号，音频必须从 2 起）；
    - 末帧按「负序号」写（header + (-seq) + size + body）→ 上游把负序号当成了 body 长度
      （`declared body size ... expected=4294967218`）；**末帧就是没有序号字段**。
    末帧 payload 可以为空（音频已经发完），上游收到后吐 flags=1011 的末包结果。
    """
    body = gzip.compress(pcm)
    if last:
        return (
            _header(CLIENT_AUDIO_ONLY_REQUEST, NEG_SEQUENCE, NO_SER)
            + len(body).to_bytes(4, "big")
            + body
        )
    return (
        _header(CLIENT_AUDIO_ONLY_REQUEST, POS_SEQUENCE, NO_SER)
        + seq.to_bytes(4, "big", signed=True)
        + len(body).to_bytes(4, "big")
        + body
    )


@dataclass
class ServerFrame:
    """上游一帧。payload 是解压后的 JSON（正常响应）；错误帧填 code/message。"""

    message_type: int
    flags: int = 0
    seq: int | None = None
    payload: dict | None = None
    code: int | None = None
    message: str = ""

    @property
    def text(self) -> str:
        """累计识别文本（v3 的 result.text 是「整段音频到目前为止」的结果，前端按替换处理）。"""
        result = (self.payload or {}).get("result") or {}
        text = result.get("text")
        return text if isinstance(text, str) else ""

    @property
    def is_final(self) -> bool:
        """末包结果（flags 带负序号位）。"""
        return bool(self.flags & NEG_SEQUENCE)


def parse_server_frame(raw: bytes) -> ServerFrame:
    """解析上游帧（长度保护：畸形帧抛 AsrError 而不是 IndexError）。"""
    if len(raw) < 4:
        raise AsrError("语音服务返回了无法解析的数据")
    header_size = raw[0] & 0x0F
    frame = ServerFrame(message_type=raw[1] >> 4, flags=raw[1] & 0x0F)
    compression = raw[2] & 0x0F
    payload = raw[header_size * 4:]
    if frame.flags & 0x01:  # 带序号
        frame.seq = int.from_bytes(payload[:4], "big", signed=True)
        payload = payload[4:]
    if frame.message_type == SERVER_ERROR_RESPONSE:
        frame.code = int.from_bytes(payload[:4], "big")
        size = int.from_bytes(payload[4:8], "big")
        body = payload[8:8 + size]
        if compression == GZIP:
            body = gzip.decompress(body)
        frame.message = body.decode("utf-8", "replace")
        return frame
    if frame.message_type in (SERVER_FULL_RESPONSE, SERVER_ACK):
        size = int.from_bytes(payload[:4], "big")
        body = payload[4:4 + size]
        if compression == GZIP:
            body = gzip.decompress(body)
        try:
            data = json.loads(body)
        except ValueError as exc:
            raise AsrError("语音服务返回了无法解析的数据") from exc
        frame.payload = data if isinstance(data, dict) else None
    return frame


def request_payload(uid: str) -> dict[str, Any]:
    """配置帧内容：PCM16k 单声道 + 标点/ITN（面试答题需要标点，否则大段无标点影响可读性）。"""
    return {
        "user": {"uid": uid},
        "audio": dict(AUDIO_FORMAT),
        "request": {
            "model_name": "bigmodel",
            "enable_itn": True,
            "enable_punc": True,
            "show_utterances": True,
        },
    }


ConnectFn = Callable[..., Any]


@dataclass
class AsrEvent:
    """转发给浏览器的事件（transport 层直接映射成 WS 文本帧）。"""

    type: str  # partial | final | error
    text: str = ""
    message: str = ""
    code: int | None = None


class AsrSession:
    """一次录音的识别会话：open → feed* → finish → close。

    连接函数注入（`connect`）以便集成测试塞 FakeUpstream——真链路与测试走同一段编排代码。
    """

    def __init__(
        self,
        *,
        url: str,
        api_key: str,
        resource_id: str,
        uid: str = "marda",
        connect: ConnectFn | None = None,
        finish_timeout: float = FINISH_TIMEOUT,
    ) -> None:
        self._url = url
        self._api_key = api_key
        self._resource_id = resource_id
        self._uid = uid
        self._connect = connect
        self._finish_timeout = finish_timeout
        self._ws: Any = None
        # 序号从 1 起、**配置帧就占掉 1 号**：上游按「自增计数」校验每一帧，
        # 音频帧从 1 开始会被拒（实测原文：`autoAssignedSequence (2) mismatch sequence in request (1)`）
        self._seq = 1
        self._last_text = ""
        self._final: AsrEvent | None = None
        self._reader: asyncio.Task | None = None
        self._closed = False
        # 事件队列**只有一个消费者**（transport 转发循环）；finish() 不看队列，
        # 只等 `_done`——两个消费者抢同一个队列会互相吃掉对方的事件
        self.events: asyncio.Queue[AsrEvent] = asyncio.Queue()
        self._done = asyncio.Event()

    async def open(self) -> None:
        if not self._api_key:
            raise AsrError("语音通道未配置（缺 VOLCANO_SPEECH_API_KEY），请用文字作答")
        connect = self._connect
        if connect is None:
            import websockets

            connect = websockets.connect
        headers = {
            "X-Api-Key": self._api_key,
            "X-Api-Resource-Id": self._resource_id,
            "X-Api-Connect-Id": uuid.uuid4().hex,
        }
        try:
            self._ws = await connect(self._url, additional_headers=headers, open_timeout=10)
        except Exception as exc:  # 建连失败：给用户可读文案，不暴露堆栈
            raise AsrError(f"语音服务连接失败：{handshake_error_text(exc)}") from exc
        await self._ws.send(build_full_request(request_payload(self._uid)))
        self._reader = asyncio.create_task(self._read_loop())

    async def feed(self, pcm: bytes) -> None:
        """喂一段 PCM（前端每 ~100ms 一片）。上游未就绪/已断开时静默丢弃本片——
        音频是过程数据，丢一片不该炸掉整场面试（用户可重录）。"""
        if self._ws is None or self._closed:
            return
        self._seq += 1
        try:
            await self._ws.send(build_audio_frame(pcm, self._seq))
        except Exception:  # noqa: BLE001 —— 上游断开由 reader 汇报，这里不重复报错
            pass

    async def finish(self, timeout: float | None = None) -> AsrEvent:
        """发末帧 → 等上游收尾（final 或错误）；超时用最后一次 partial 兜底，不丢已识别文本。

        返回的 final 事件**同时**已进过事件队列（别在 transport 里重复转发，队列才是唯一出口）。
        """
        if self._ws is not None and not self._closed:
            self._seq += 1
            try:
                await self._ws.send(build_audio_frame(b"", self._seq, last=True))
            except Exception:  # noqa: BLE001
                pass
        try:
            await asyncio.wait_for(self._done.wait(), timeout=timeout or self._finish_timeout)
        except asyncio.TimeoutError:
            if self._final is None:
                self._final = AsrEvent(type="final", text=self._last_text)
                await self.events.put(self._final)
        return self._final or AsrEvent(type="final", text=self._last_text)

    async def _emit(self, event: AsrEvent) -> None:
        """终态事件（final / error）同时记在 `_final`（给 finish()）与队列（给 transport）。"""
        if event.type in ("final", "error"):
            self._final = event
        await self.events.put(event)

    async def _read_loop(self) -> None:
        """后台读上游：partial / final / error 进队列。

        上游断开（无论正常还是异常）都算一种结束：`_done` 置位让 finish() 不再干等，
        没收到过终态时用最后一次 partial 兜底。
        """
        try:
            async for raw in self._ws:
                if isinstance(raw, str):  # 协议里没有文本帧，忽略
                    continue
                try:
                    frame = parse_server_frame(raw)
                except AsrError as exc:
                    await self._emit(AsrEvent(type="error", message=str(exc)))
                    return
                if frame.message_type == SERVER_ERROR_RESPONSE:
                    await self._emit(
                        AsrEvent(type="error", message=_error_text(frame), code=frame.code)
                    )
                    return
                text = frame.text
                if frame.is_final:  # 末包只出 final，不再补一条同文的 partial（前端不重复落框）
                    await self._emit(AsrEvent(type="final", text=text or self._last_text))
                    return
                if text and text != self._last_text:
                    self._last_text = text
                    await self.events.put(AsrEvent(type="partial", text=text))
            # 上游正常关闭却没给 final：别让用户对着空输入框发呆，明说重试
            if self._final is None and not self._closed:
                await self._emit(AsrEvent(type="error", message="语音服务连接中断，请重试"))
        except Exception:  # noqa: BLE001 —— 连接中断（含上游异常关闭）
            if self._final is None:
                await self._emit(AsrEvent(type="error", message="语音服务连接中断，请重试"))
        finally:
            if self._final is None:  # 兜底值只给 finish()，不再进队列（队列是转发出口）
                self._final = AsrEvent(type="final", text=self._last_text)
            self._done.set()

    async def close(self) -> None:
        self._closed = True
        if self._reader is not None:
            self._reader.cancel()
            try:
                await self._reader
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._reader = None
        ws, self._ws = self._ws, None
        if ws is not None:
            try:
                await ws.close()
            except Exception:  # noqa: BLE001
                pass


def handshake_error_text(exc: BaseException) -> str:
    """握手失败 → 可诊断文案：状态码 + 上游原因 + 一句「该去哪解决」。

    为什么值得单独做：探针里遇到过的三种失败**长得完全不同**——
    `401 Invalid X-Api-Key`（key 拿错产品线）、`403 requested resource not granted`
    （服务没开通）、`acquire failed ... call ark 401`（方舟 key 走到了语音网关）。
    只报 `InvalidStatus` 等于把三件事糊成一件，排查全靠猜。
    """
    response = getattr(exc, "response", None)
    status = getattr(response, "status_code", None)
    if status is None:
        return type(exc).__name__
    body = getattr(response, "body", b"") or b""
    reason = body.decode("utf-8", "replace")[:160].strip()
    try:
        payload = json.loads(reason)
        reason = str(payload.get("error") or payload.get("message") or reason)
    except ValueError:
        pass
    hint = ""
    if status == 403 and "not granted" in reason:
        hint = "（语音服务未开通：到豆包语音控制台「开通管理」开通对应模型）"
    elif status == 401:
        hint = "（检查 VOLCANO_SPEECH_API_KEY：须是豆包语音控制台签发的 key，方舟 key 不通用）"
    return f"HTTP {status} {reason}{hint}".strip()


def _error_text(frame: ServerFrame) -> str:
    """上游错误 → 可展示文案。已知码给「该去哪解决」，其余把上游原文带上（都是协议/权限问题，
    光看数字没法排查——`autoAssignedSequence mismatch` 这类原文才是线索）。"""
    if frame.code == 45000010:
        return "语音服务鉴权失败，请检查语音 API Key（VOLCANO_SPEECH_API_KEY）"
    detail = frame.message.strip()[:160]
    return f"语音识别失败（{frame.code}）：{detail}" if detail else f"语音识别失败（{frame.code}）"
