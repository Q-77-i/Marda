"""resume 载荷解析（P2-M6）：无图仍是裸字符串、带图是 {content, images}，两种都要接住。"""

from __future__ import annotations

from app.graph.graph import parse_resume


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
