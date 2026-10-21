"""表达复盘纯规则单测（M16）：合并/派生语速/场次汇总/PDF 时长文案。"""

from __future__ import annotations

import json

from app.graph.rules.speech import (
    SPEECH_NOTE,
    derive_speech,
    expression_summary,
    format_duration_cn,
    merge_speech,
)

RAW = {
    "duration_ms": 42000,
    "chars": 180,
    "pause_count": 2,
    "pause_ms": 5000,
    "filler_count": 3,
    "segments": 1,
}


def test_合并_多轮累加():
    later = {
        "duration_ms": 18000, "chars": 60, "pause_count": 1,
        "pause_ms": 2500, "filler_count": 1, "segments": 1,
    }

    assert merge_speech(RAW, later) == {
        "duration_ms": 60000,
        "chars": 240,
        "pause_count": 3,
        "pause_ms": 7500,
        "filler_count": 4,
        "segments": 2,
    }


def test_合并_空侧原样返回():
    assert merge_speech(None, RAW) == RAW
    assert merge_speech(RAW, None) == RAW
    assert merge_speech(None, None) is None


def test_合并_脏值按零():
    assert merge_speech(None, {**RAW, "chars": "180", "pause_count": -1})["chars"] == 0
    assert merge_speech(None, {**RAW, "pause_count": True})["pause_count"] == 0


def test_派生语速():
    item = derive_speech(RAW)

    assert item is not None
    assert item["chars_per_min"] == 257.1
    assert item["pause_ms"] == 5000


def test_派生语速_零时长不除零():
    item = derive_speech({**RAW, "duration_ms": 0})
    assert item is not None and item["chars_per_min"] is None
    assert derive_speech(None) is None


def test_场次汇总_无语音返回None():
    assert expression_summary([None, None]) is None
    assert expression_summary([]) is None


def test_场次汇总_只统计有语音的题():
    summary = expression_summary([RAW, None, {**RAW, "duration_ms": 18000, "chars": 60}])

    assert summary is not None
    assert summary["spoken_question_count"] == 2
    assert summary["duration_ms"] == 60000
    assert summary["chars"] == 240
    assert summary["chars_per_min"] == 240.0
    assert summary["segments"] == 2
    assert summary["note"] == SPEECH_NOTE


def test_场次汇总可JSON序列化():
    summary = expression_summary([RAW])
    assert json.loads(json.dumps(summary, ensure_ascii=False))["chars"] == 180


def test_时长文案():
    assert format_duration_cn(130000) == "2 分 10 秒"
    assert format_duration_cn(35000) == "35 秒"
    assert format_duration_cn(0) == "0 秒"
