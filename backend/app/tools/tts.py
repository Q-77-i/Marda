"""语音合成（TTS，P2-M5 FR-24）：edge-tts 包装（三档降级的第一档）。

选型理由（P2-M5 决策④）：免费、音色自然；探针实测宿主机与 api 容器都能连上微软端点
（2026-10-02：45072B / 63 片 / 首字节 2.09s / 整段 2.61s，两侧数字一致）。不可用时的降级
由**前端**承担（浏览器 speechSynthesis → 纯文字），后端只负责：说得出来就说，说不出来
给一条可展示的中文错误。

为什么独立端点而不是骑在面试 SSE 流上（决策①）：失败隔离（TTS 挂了不污染面试流）、
不把合成耗时算进面试流的生命周期、报告页/回放页将来可复用同一条通道。
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import edge_tts

# 单次播报上限：面试官单条消息远短于此（实测最长 ~200 字）。超长截断而不是报错——
# TTS 是展示层，用户正在面试，不能因为一段长文案就整条播报失败
MAX_TEXT = 1000


class TtsError(Exception):
    """合成失败（网络/上游拒绝）。message 可直接展示。"""


def normalize_text(text: str) -> str:
    """清洗播报文本：去首尾空白 + 上限截断（空格与换行不影响朗读，交给 edge-tts）。"""
    return (text or "").strip()[:MAX_TEXT]


async def synthesize(text: str, *, voice: str) -> AsyncIterator[bytes]:
    """逐块产出 MP3 字节（edge-tts 的原生分片，前端整段落盘成 blob 播放）。

    失败统一抛 TtsError：路由层在**首块**上做错误映射，能给出正确的 HTTP 状态码；
    中途断流只能截断（响应头已发出）。
    """
    payload = normalize_text(text)
    if not payload:
        raise TtsError("没有可播报的文本")
    try:
        communicate = edge_tts.Communicate(payload, voice)
        got_audio = False
        async for chunk in communicate.stream():
            if chunk.get("type") == "audio" and chunk.get("data"):
                got_audio = True
                yield chunk["data"]
        if not got_audio:
            raise TtsError("语音合成没有返回音频")
    except TtsError:
        raise
    except Exception as exc:  # noqa: BLE001 —— 上游异常类型不可枚举（403/连接/协议）
        raise TtsError(f"语音合成失败：{type(exc).__name__}") from exc
