"""resume 载荷解析（P2-M6）：无图仍是裸字符串、带图是 {content, images}，两种都要接住。"""

from __future__ import annotations

import pytest

from app.graph.graph import parse_resume, parse_speech


def test_裸字符串_纯文本路径逐字不变():
    assert parse_resume("我的回答") == ("我的回答", [])
    assert parse_resume("") == ("", [])
    assert parse_resume(None) == ("", [])


def test_字典载荷_带图():
    assert parse_resume({"content": "见截图", "images": ["a" * 32]}) == ("见截图", ["a" * 32])


def test_字典载荷_缺字段与脏值容错():
    assert parse_resume({"content": "只有文本"}) == ("只有文本", [])
    assert parse_resume({"images": ["a" * 32]}) == ("", ["a" * 32])
    # 非字符串项被丢弃（载荷只可能由服务层构造，这里是防御）
    assert parse_resume({"content": "x", "images": [1, "b" * 32]}) == ("x", ["b" * 32])


# ---- 语音指标（M16）：六个非负整数字段，形状不对就整份丢弃 ----

SPEECH = {
    "duration_ms": 42000,
    "chars": 180,
    "pause_count": 2,
    "pause_ms": 5000,
    "filler_count": 3,
    "segments": 1,
}


def test_语音载荷_合法():
    payload = {"content": "回答", "speech": SPEECH}
    assert parse_speech(payload) == SPEECH
    # 文本解析不受影响（parse_resume 的 2 元组契约不变）
    assert parse_resume(payload) == ("回答", [])


def test_语音载荷_缺省为None():
    assert parse_speech("回答") is None
    assert parse_speech({"content": "回答"}) is None
    assert parse_speech({"speech": None}) is None


@pytest.mark.parametrize("bad", [
    {k: v for k, v in SPEECH.items() if k != "segments"},   # 缺字段
    {**SPEECH, "chars": "180"},                              # 类型错
    {**SPEECH, "pause_count": True},                         # bool 不是 int
    {**SPEECH, "duration_ms": -1},                           # 负数
    {**SPEECH, "pause_ms": 99999},                           # 停顿超过总时长
    {**SPEECH, "pause_count": 3, "pause_ms": 5000},          # 3×2s > 5s
])
def test_语音载荷_脏值整份丢弃(bad):
    assert parse_speech({"content": "回答", "speech": bad}) is None
