"""语音转写智能校对（P2-M13 追加）：ASR final → flash 保守纠错。

为什么需要：流式 ASR 对技术术语的同音误识别（实测「先召回再筛→先兆回再塞、
静默→寂寞、候选位→穴位」）靠热词只能缓解；LLM 拿到**面试问题 + 术语提示 + 原文**
后能按语境修掉大部分，且不改写候选人观点。

边界（与「发送前可编辑」一致）：
- 只修同音字/术语/断句，不扩写、不总结、不评价；不确定保留原文；
- 校对是增强不是必需：失败由调用方静默保留原文；
- 前端只在用户**没有编辑过**输入框时替换（见 lib/voice.ts::applyCorrection）。
"""

from __future__ import annotations

from pydantic import BaseModel

from app import llm
from app.tools import asr

MAX_TEXT = 4000
MAX_QUESTION = 1200

SYSTEM_PROMPT = (
    "你是面试语音转写的校对员。候选人原话来自语音识别，可能有同音字、"
    "技术术语和断句错误。只修正这些明显错误，保持候选人的原意、信息量和口语风格；"
    "不要扩写、总结、评价或改变观点；不确定的地方保留原文。"
    "返回 JSON，字段 text 是修正后的全文。"
)


class CorrectedText(BaseModel):
    text: str


class TranscriptError(Exception):
    """校对失败（上游/校验）。message 可直接展示；前端通常会保留原文。"""


async def correct_text(text: str, question: str = "") -> str:
    """保守校对一段 ASR 转写；空文本原样返回（不调模型）。"""
    clean = (text or "").strip()[:MAX_TEXT]
    if not clean:
        return text
    prompt = (
        f"面试问题：{(question or '').strip()[:MAX_QUESTION] or '（未提供）'}\n"
        f"技术术语提示：{'、'.join(asr.TECH_HOTWORDS)}\n"
        f"语音识别原文：{clean}"
    )
    try:
        result = await llm.chat_json(
            [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            schema=CorrectedText,
            temperature=0.1,
            purpose="asr_correct",
        )
    except Exception as exc:  # noqa: BLE001 —— 上游异常类型不可枚举
        raise TranscriptError("智能纠正暂不可用") from exc
    corrected = (result.text or "").strip()
    return corrected or text
