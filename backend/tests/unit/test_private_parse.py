"""私有题模板解析（FR-13）：正常路径 + 逐题报错 + 编码/PDF 分派。"""

from __future__ import annotations

import io

import pytest

from app.tools import private_parse

TPL = """# 我的面试题

【题目】Redis 的持久化机制有哪几种？
【答案】RDB 与 AOF 两种：
```python
save 900 1
```
AOF 记录写命令。
【关键点】RDB 快照；AOF 日志；混合持久化
【追问】AOF 重写怎么触发？
【主题】存储与缓存

【题目】MCP 是什么？
【答案】Model Context Protocol，Anthropic 提出的工具协议。
"""


def test_解析两条题目并保留多行答案():
    records, errors = private_parse.parse_template(TPL)
    assert errors == []
    assert [r["text"] for r in records] == [
        "Redis 的持久化机制有哪几种？", "MCP 是什么？",
    ]
    first = records[0]
    # 答案保留换行与代码块（不能因为折行处理把代码吃掉）
    assert "```python" in first["answer"] and "AOF 记录写命令。" in first["answer"]
    assert first["key_points"] == ["RDB 快照", "AOF 日志", "混合持久化"]
    assert first["follow_ups"] == ["AOF 重写怎么触发？"]
    assert first["topic"] == "存储与缓存"
    # 未写【主题】的题落默认值
    assert records[1]["topic"] == private_parse.DEFAULT_TOPIC
    assert records[1]["key_points"] == [] and records[1]["follow_ups"] == []


def test_无标记时给出模板提示而不是空成功():
    records, errors = private_parse.parse_template("就是一段普通笔记\n没有任何标记")
    assert records == []
    assert len(errors) == 1
    assert "【题目】" in errors[0]["reason"]


def test_缺答案的题进错误清单且不拖累其他题():
    text = "【题目】没答案的题\n【题目】有答案的题\n【答案】完整参考答案内容。"
    records, errors = private_parse.parse_template(text)
    assert [r["text"] for r in records] == ["有答案的题"]
    assert len(errors) == 1
    assert errors[0]["question"] == "没答案的题"
    assert "【答案】" in errors[0]["reason"]


def test_占位答案按管道同一口径判不合格():
    """与语料管道共用 answer_substance：`xx` 这类占位不算答案（P1-M5 口径）。"""
    records, errors = private_parse.parse_template("【题目】占位题\n【答案】xx")
    assert records == []
    assert "过短" in errors[0]["reason"]


def test_空题干的题报错():
    records, errors = private_parse.parse_template("【题目】\n【答案】有答案但没有题干内容")
    assert records == []
    assert errors[0]["reason"] == "【题目】内容为空"


def test_孤立标记报错不静默():
    text = "【答案】先出现了答案\n【题目】正常的题\n【答案】正常答案内容。"
    records, errors = private_parse.parse_template(text)
    assert len(records) == 1
    assert "之前" in errors[0]["reason"]


def test_关键点支持项目符号与去重():
    text = (
        "【题目】题\n【答案】答案内容够长。\n"
        "【关键点】\n- 要点一\n- 要点二\n要点一\n* 要点三\n"
    )
    records, _ = private_parse.parse_template(text)
    assert records[0]["key_points"] == ["要点一", "要点二", "要点三"]


def test_题干跨行折叠成一行():
    """PDF 抽文本的硬换行不该把一道题拆成多行。"""
    records, _ = private_parse.parse_template("【题目】第一行\n第二行\n【答案】答案内容够长。")
    assert records[0]["text"] == "第一行 第二行"


def test_首个标记之前的前言被忽略():
    records, errors = private_parse.parse_template("前言一段\n\n【题目】题\n【答案】答案内容够长。")
    assert errors == [] and len(records) == 1


def test_decode_text支持utf8与gbk():
    assert private_parse.decode_text("中文".encode("utf-8-sig")) == "中文"
    assert private_parse.decode_text("中文".encode("gbk")) == "中文"


def test_parse_upload按后缀分派pdf(monkeypatch):
    """PDF 路径只验证「抽文本后走同一解析器」——pypdf 的真实抽取在集成测试覆盖。"""
    monkeypatch.setattr(private_parse, "extract_pdf_text", lambda data: "【题目】PDF题\n【答案】PDF 抽出的答案内容。")
    records, errors = private_parse.parse_upload("题库.PDF", b"ignored")
    assert errors == [] and records[0]["text"] == "PDF题"


def test_parse_upload非pdf走文本解码():
    records, _ = private_parse.parse_upload("题.md", "【题目】题\n【答案】答案内容够长。".encode())
    assert len(records) == 1


def _minimal_pdf(text: str) -> bytes:
    """生成一份结构合法的最小单页 PDF（含 xref 表，pypdf 才肯读）。"""
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
    bodies = [
        b"<</Type/Catalog/Pages 2 0 R>>",
        b"<</Type/Pages/Kids[3 0 R]/Count 1>>",
        b"<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]"
        b"/Resources<</Font<</F1 5 0 R>>>>/Contents 4 0 R>>",
        b"<</Length " + str(len(stream)).encode() + b">>stream\n" + stream + b"\nendstream",
        b"<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for index, body in enumerate(bodies, start=1):
        offsets.append(len(out))
        out += f"{index} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(bodies) + 1}\n".encode() + b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += f"trailer\n<</Size {len(bodies) + 1}/Root 1 0 R>>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)


def test_extract_pdf_text对真实pdf抽文本():
    """真实 PDF 走一遍 pypdf（自造最小 PDF，不引第三方构造库）。"""
    assert "Hello Marda" in private_parse.extract_pdf_text(_minimal_pdf("Hello Marda"))


def test_不认识的编码报错可读():
    with pytest.raises(ValueError, match="编码"):
        private_parse.decode_text(b"\xff\xfe\x00\x01\x02\x03\xff\xff\xfe")


def test_解析结果与io无关的纯函数():
    """同样输入两次解析结果一致（供幂等入库依赖）。"""
    assert private_parse.parse_template(TPL) == private_parse.parse_template(TPL)
    assert io.StringIO(TPL).read() == TPL
