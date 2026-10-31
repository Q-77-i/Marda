"""checkpoint 序列化单测（P4-E1）：zlib 压缩 + 新旧 type 双读。

三条断言各自对应一个真实风险：
1. **round-trip 不改语义**——压缩是存储层的事，反序列化出来的对象必须逐字相同；
2. **旧数据可读**——库里有升级前写的 `msgpack` 行，新代码必须照样读出来
   （不兼容 = 老场次全废，比膨胀更糟）；
3. **确实压下来了**——不压就没有意义（这是 P4-E1 的验收指标）。
"""

from __future__ import annotations

from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

from app.graph.serde import ZlibJsonPlusSerializer, make_serde


def _payload(n: int = 200) -> dict:
    """一份像样的 state 载荷：有嵌套、有重复文本（真实快照里 trace_log 就是重复结构）。"""
    return {
        "phase": "question",
        "answered_count": 3,
        "chat_history": [
            {"role": "assistant", "content": "介绍一下你的项目经历" * 8},
            {"role": "user", "content": "我做了一个 Agent 面试系统" * 8},
        ],
        "trace_log": [{"node": "judge", "detail": {"score": 4, "comment": "答得不错" * 6}} for _ in range(n)],
    }


def test_压缩后仍能还原出同样的对象():
    serde = make_serde()
    obj = _payload()
    type_, blob = serde.dumps_typed(obj)
    assert type_.endswith("+zlib")
    assert serde.loads_typed((type_, blob)) == obj


def test_旧的msgpack行照样能读():
    """升级前写进库的行（type=msgpack）必须能被新序列化器读出来。"""
    old = JsonPlusSerializer()
    obj = _payload(5)
    type_, blob = old.dumps_typed(obj)
    assert type_ == "msgpack"
    assert make_serde().loads_typed((type_, blob)) == obj


def test_压缩确实压下来了():
    serde = make_serde()
    obj = _payload()
    type_, blob = serde.dumps_typed(obj)
    _, raw = JsonPlusSerializer().dumps_typed(obj)
    assert len(blob) < len(raw) / 2  # 至少 2 倍（真实快照约 3 倍）
    assert serde.loads_typed((type_, blob)) == obj


def test_空值与字节不压缩():
    """null / bytes 压了反而变大，保持原样。"""
    serde = make_serde()
    assert serde.dumps_typed(None)[0] == "null"
    assert serde.dumps_typed(b"abc")[0] == "bytes"


def test_解压失败要报错而不是静默返回垃圾():
    """数据损坏要**响亮**地失败——静默返回空 state 会让面试凭空「失忆」。"""
    import pytest

    serde = make_serde()
    with pytest.raises(Exception):
        serde.loads_typed(("msgpack+zlib", b"not-zlib-data"))
