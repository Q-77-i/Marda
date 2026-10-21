"""表达复盘（M16 FR-30）：语音原始计数 → 派生语速 / 场次汇总。

纯代码、可单测。前端只上报**原始计数**（时长/字数/长停顿/口头禅），
语速与场次汇总只在这里算一次——报告页、PDF 与以后的能力档案同一口径。
"""

from __future__ import annotations

from typing import Any

SPEECH_FIELDS = (
    "duration_ms",
    "chars",
    "pause_count",
    "pause_ms",
    "filler_count",
    "segments",
)

SPEECH_NOTE = (
    "语速与停顿由录音音量与 ASR 转写估算：ASR 可能漏掉语气词；"
    "长停顿按 ≥2 秒的内部静音统计（不含开头/结尾）；文字作答不计入。"
)


def _value(raw: dict[str, Any], key: str) -> int:
    """取一个非负整数；脏值按 0（不因一个指标字段炸掉整份报告）。"""
    value = raw.get(key, 0)
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def _sum_fields(sources: list[dict[str, Any]]) -> dict[str, int]:
    return {key: sum(_value(source, key) for source in sources) for key in SPEECH_FIELDS}


def merge_speech(previous: dict | None, current: dict | None) -> dict | None:
    """同一题多轮作答的语音计数累加；任一侧为空返回另一侧。"""
    if not current:
        return previous
    if not previous:
        return _sum_fields([current])
    return {
        key: _value(previous, key) + _value(current, key) for key in SPEECH_FIELDS
    }


def derive_speech(raw: dict | None) -> dict | None:
    """单题指标：补上 chars_per_min（时长为 0 → None，不除零）。"""
    if not raw:
        return None
    item = _sum_fields([raw])
    duration = item["duration_ms"]
    item["chars_per_min"] = (
        round(item["chars"] / duration * 60_000, 1) if duration > 0 else None
    )
    return item


def expression_summary(speeches: list[dict | None]) -> dict | None:
    """整场汇总；本场没有任何语音作答返回 None（报告不落空壳，缺数据 ≠ 说 0 秒）。"""
    present = [speech for speech in speeches if speech]
    if not present:
        return None
    totals = _sum_fields(present)
    duration = totals["duration_ms"]
    return {
        "spoken_question_count": len(present),
        "duration_ms": totals["duration_ms"],
        "chars": totals["chars"],
        "chars_per_min": (
            round(totals["chars"] / duration * 60_000, 1) if duration > 0 else None
        ),
        "pause_count": totals["pause_count"],
        "pause_ms": totals["pause_ms"],
        "filler_count": totals["filler_count"],
        "segments": totals["segments"],
        "note": SPEECH_NOTE,
    }


def format_duration_cn(ms: int) -> str:
    """PDF 用的中文时长：`2 分 10 秒` / `35 秒`。"""
    total = max(0, round(ms / 1000))
    minutes, seconds = divmod(total, 60)
    return f"{minutes} 分 {seconds} 秒" if minutes else f"{seconds} 秒"
