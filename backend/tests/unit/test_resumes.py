"""简历解析（P2-M11 FR-28）单测：文本抽取 / 归一 / 结构化调用 / profile 拼装。

真实 PDF 走 pypdf 的路径由 test_private_parse 覆盖（同一实现）；这里验的是
**分派与错误包装**——坏文件要变成「人话 + 出路」，而不是 pypdf 的内部异常。
"""

from __future__ import annotations

import pytest

from app import llm
from app.agents.schemas import ResumeExtraction
from app.tools import resumes


def test_后缀白名单():
    assert resumes.UPLOAD_SUFFIXES == (".pdf", ".md", ".markdown", ".txt")


def test_文本文件按_utf8_gbk_解码():
    assert "张三" in resumes.extract_text("简历.txt", "张三的简历。".encode("utf-8"))
    assert "张三" in resumes.extract_text("简历.md", "张三的简历。".encode("gbk"))


def test_pdf_走抽文本(monkeypatch):
    monkeypatch.setattr(resumes, "extract_pdf_text", lambda data: "从 PDF 抽出的文字")
    assert resumes.extract_text("简历.PDF", b"ignored") == "从 PDF 抽出的文字"


def test_坏_pdf_转成可读错误并指路粘贴():
    """pypdf 的异常类型杂（PdfReadError / 加密 / 结构怪异）——统一转 ResumeError。"""
    with pytest.raises(resumes.ResumeError, match="粘贴文本"):
        resumes.extract_text("简历.pdf", "这不是一份 PDF".encode())


def test_编码无法识别转成可读错误():
    with pytest.raises(resumes.ResumeError, match="编码"):
        resumes.extract_text("简历.txt", b"\xff\xff\xff")


def test_归一去掉首尾空白():
    assert resumes.prepare_text("  简历内容\n ") == "简历内容"


def test_空文本报错并指路粘贴():
    with pytest.raises(resumes.ResumeError, match="粘贴文本"):
        resumes.prepare_text("   \n  ")


def test_超长文本不静默截断():
    """截断等于悄悄丢经历——宁可报错让用户精简。"""
    with pytest.raises(resumes.ResumeError, match="过长"):
        resumes.prepare_text("字" * (resumes.MAX_RESUME_CHARS + 1))


async def test_parse_resume_走结构化调用(monkeypatch):
    captured = {}

    async def _fake_chat_json(messages, *, schema, **kwargs):
        captured.update(system=messages[0]["content"], schema=schema, purpose=kwargs.get("purpose"))
        return ResumeExtraction(summary="应届生", projects=["做过 X"], skills=["Python"])

    monkeypatch.setattr(llm, "chat_json", _fake_chat_json)
    parsed = await resumes.parse_resume("简历原文……")

    assert parsed.projects == ["做过 X"]
    assert captured["schema"] is ResumeExtraction
    assert captured["purpose"] == "resume"  # 成本归因（P2-M10 的调用命名口径）
    assert "简历原文……" in captured["system"]


def test_format_profile_与自我介绍口径同形():
    text = resumes.format_profile(
        {"summary": "应届生", "projects": ["A", "B"], "skills": ["Python", "RAG"]}
    )
    assert text == "应届生；项目经历：A；B；技能：Python、RAG"


def test_format_profile_空值与缺键都容错():
    assert resumes.format_profile({}) == ""
    assert resumes.format_profile({"summary": "  ", "projects": [], "skills": None}) == ""
    assert resumes.format_profile({"projects": ["  ", "A"]}) == "项目经历：A"
