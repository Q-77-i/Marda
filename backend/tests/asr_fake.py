"""ASR 测试替身（单测与集成共用）：假上游连接 + 服务端帧构造器。

真值来自探针（2026-10-02）：帧形状（头部字节、gzip、序号、末包负序号）在真服务端上验证过。
"""

from __future__ import annotations

import asyncio
import gzip
import json

from app.tools import asr


class FakeConn:
    """websockets 连接的最小形状：send / 异步迭代 / close。"""

    def __init__(self) -> None:
        self.sent: list[bytes] = []
        self.closed = False
        self._incoming: asyncio.Queue[bytes | None] = asyncio.Queue()

    async def send(self, data: bytes) -> None:
        self.sent.append(data)

    async def push(self, frame: bytes) -> None:
        await self._incoming.put(frame)

    async def eof(self) -> None:
        await self._incoming.put(None)

    def __aiter__(self) -> "FakeConn":
        return self

    async def __anext__(self) -> bytes:
        item = await self._incoming.get()
        if item is None:
            raise StopAsyncIteration
        return item

    async def close(self) -> None:
        self.closed = True
        await self._incoming.put(None)


class ScriptedConn(FakeConn):
    """按「第几次 send」触发回帧脚本：send 在应用的事件循环里被 await，压队列天然安全。

    键 = send 的序号（1 = 配置帧），值 = 该次 send 之后要推给客户端的服务端帧。
    """

    def __init__(self, script: dict[int, list[bytes]] | None = None) -> None:
        super().__init__()
        self._script = dict(script or {})

    async def send(self, data: bytes) -> None:
        await super().send(data)
        for frame in self._script.pop(len(self.sent), []):
            await self.push(frame)


def make_connect(conn: FakeConn, sink: dict | None = None):
    async def _connect(url: str, **kwargs):
        if sink is not None:
            sink.update({"url": url, **kwargs})
        return conn

    return _connect


def failing_connect(exc: Exception):
    async def _connect(url: str, **kwargs):
        raise exc

    return _connect


def server_response(payload: dict, *, flags: int = 0, seq: int | None = None) -> bytes:
    body = gzip.compress(json.dumps(payload, ensure_ascii=False).encode())
    head = bytes([
        (1 << 4) | 1,
        (asr.SERVER_FULL_RESPONSE << 4) | flags,
        (asr.JSON_SER << 4) | asr.GZIP,
        0,
    ])
    if flags & 0x01:
        head += (seq or 0).to_bytes(4, "big", signed=True)
    return head + len(body).to_bytes(4, "big") + body


def server_error(code: int, message: str) -> bytes:
    body = gzip.compress(message.encode())
    head = bytes([
        (1 << 4) | 1,
        (asr.SERVER_ERROR_RESPONSE << 4),
        (asr.JSON_SER << 4) | asr.GZIP,
        0,
    ])
    return head + code.to_bytes(4, "big") + len(body).to_bytes(4, "big") + body


def header_of(frame: bytes) -> tuple[int, int, int]:
    """(message_type, flags, serialization/compression 字节)。"""
    return frame[1] >> 4, frame[1] & 0x0F, frame[2]


def payload_of(frame: bytes, *, has_seq: bool) -> bytes:
    return gzip.decompress(frame[4 + (4 if has_seq else 0) + 4:])
