"""报告 PDF 导出单测（FR-18）：雷达 SVG 几何 + PDF 渲染与中文读回。

「中文无乱码」的验收口径落在 **pypdf 读回断言**上：导出的 PDF 里文字是真文本（可选中、
可检索），字体缺字与编码错误会以 U+FFFD 或缺字形式在读回文本里现形；截图式 PDF 没有这个判据。
"""

from __future__ import annotations

import io
import math
import re

import pytest
from pypdf import PdfReader

from app.graph.rules.aggregate import (
    BEHAVIORAL_DIMENSION_LABELS,
    BEHAVIORAL_DIMS,
    DIMENSION_LABELS,
    FIVE_DIMS,
)
from app.report_pdf import format_created_at, radar_svg, render_report_pdf

SIZE = 260  # 与 radar_svg 默认值一致（几何断言按它算圆心）


def _payload(**overrides) -> dict:
    """一份字段齐全的报告 payload（契约见 SPEC §4.6）。"""
    payload = {
        "position": "Agent/AI 工程师",
        "scores": {
            "technical_depth": 4, "fundamentals": 3, "project_experience": 4,
            "communication": 3, "problem_solving": 4,
        },
        "domain_scores": {"rag": 4.0, "memory": 2.0},
        "weaknesses": ["memory"],
        "answered_count": 2,
        "question_count": 2,
        "total_comment": "总评：基础扎实，记忆机制一节需要补。",
        "per_question_comments": [
            {
                "index": 1, "number": 1, "question_id": "q_rag", "question_type": "tech",
                "domain": "rag", "text": "请说说 RAG 的检索与重排如何配合？",
                "comment": "回答结构清晰。",
                "candidate_answer": "先召回再重排。\n\n【追问补充】重排用交叉编码器。",
                "score": {
                    "technical_depth": 4, "fundamentals": 3, "project_experience": 4,
                    "communication": 3, "problem_solving": 4,
                },
                "covered_key_points": ["召回", "重排"], "missed_key_points": ["评估"],
                "reference_answer": "参考答案正文标记XYZ",
            },
            {
                "index": 2, "number": 2, "question_id": None, "question_type": "scenario",
                "domain": "project", "text": "讲讲你项目里的记忆设计。",
                "comment": "细节不足。", "candidate_answer": "用了滑动窗口。",
                "score": {
                    "technical_depth": 2, "fundamentals": 3, "project_experience": 3,
                    "communication": 4, "problem_solving": 3,
                },
                "covered_key_points": [], "missed_key_points": ["长期记忆"],
                "reference_answer": None,
            },
        ],
        "study_advice": [{"domain": "memory", "advice": "补齐长期记忆与压缩策略。"}],
    }
    payload.update(overrides)
    return payload


def _points(svg: str, cls: str) -> list[tuple[float, float]]:
    """取指定 class 的 polygon 顶点（如 radar-data / radar-ring）。"""
    match = re.search(rf'class="{cls}" points="([^"]+)"', svg)
    assert match, f"未找到 class={cls} 的 polygon：{svg[:200]}"
    return [tuple(float(v) for v in pair.split(",")) for pair in match.group(1).split()]


def _pdf_text(data: bytes) -> str:
    """PDF 全文（去空白：CJK 逐字排版会在字间插空格，比对前一律剥掉）。"""
    reader = PdfReader(io.BytesIO(data))
    raw = "\n".join(page.extract_text() or "" for page in reader.pages)
    return re.sub(r"\s+", "", raw)


# ---- 雷达 SVG（纯几何，不依赖渲染引擎） ----

def test_雷达图含五个维度标签与五点数据多边形():
    svg = radar_svg({dim: 3 for dim in FIVE_DIMS})

    assert len(_points(svg, "radar-data")) == 5
    for label in DIMENSION_LABELS.values():
        assert label in svg


def test_雷达图数据点半径随分数线性缩放():
    """0 分落在圆心、满分贴外圈、半分落在外圈一半处——顺带钉死 0–5 的刻度映射。"""
    cx = cy = SIZE / 2
    full = _points(radar_svg({dim: 5 for dim in FIVE_DIMS}), "radar-data")
    radius = cy - full[0][1]  # 顶点（角度 -90°）到圆心的距离

    def _distances(scores: dict[str, float]) -> list[float]:
        return [math.hypot(x - cx, y - cy) for x, y in _points(radar_svg(scores), "radar-data")]

    assert _distances({dim: 0 for dim in FIVE_DIMS}) == pytest.approx([0] * 5, abs=0.1)
    assert _distances({dim: 2.5 for dim in FIVE_DIMS}) == pytest.approx([radius / 2] * 5, abs=0.1)
    assert _distances({dim: 5 for dim in FIVE_DIMS}) == pytest.approx([radius] * 5, abs=0.1)


def test_雷达图超范围分数截断在零与满分之间():
    """分数异常时宁可截断也不画出界（1–5 的整数值是常态，越界只可能是脏数据）。"""
    cx = cy = SIZE / 2
    over = _points(radar_svg({dim: 9 for dim in FIVE_DIMS}), "radar-data")
    under = _points(radar_svg({dim: -2 for dim in FIVE_DIMS}), "radar-data")
    full = _points(radar_svg({dim: 5 for dim in FIVE_DIMS}), "radar-data")

    assert over == full
    assert under == pytest.approx([(cx, cy)] * 5, abs=0.1)


def test_雷达图顶点顺序与维度顺序一致():
    """第 0 个顶点（正上方）是技术深度——顺序错了整张图会张冠李戴。"""
    scores = {dim: 0 for dim in FIVE_DIMS}
    scores[FIVE_DIMS[0]] = 5
    points = _points(radar_svg(scores), "radar-data")

    assert points[0][1] < SIZE / 2 - 1  # 顶点朝上
    assert points[1:] == pytest.approx([(SIZE / 2, SIZE / 2)] * 4, abs=0.1)


# ---- PDF 渲染 ----

def test_PDF可读回且中文不是乱码():
    data = render_report_pdf(_payload(), created_at="2026-09-30T06:23:45.123456+00:00")

    assert data[:5] == b"%PDF-"
    text = _pdf_text(data)
    assert "�" not in text  # 缺字/编码错误才会出现替换字符
    # 报告主体各段都在：头部、雷达维度、域均分、短板、总评、逐题、建议
    assert "Agent/AI工程师" in text
    assert "技术深度" in text and "问题解决" in text
    assert "总评：基础扎实，记忆机制一节需要补。" in text
    assert "请说说RAG的检索与重排如何配合？" in text
    assert "先召回再重排。" in text and "追问补充" in text
    # 关键点覆盖：已覆盖与漏掉的分列（标签在，条目也在）
    assert "覆盖的关键点" in text and "漏掉的关键点" in text
    assert "召回" in text and "重排" in text and "评估" in text
    assert "长期记忆" in text
    assert "补齐长期记忆与压缩策略。" in text
    assert "码达" in text


def test_题库题附参考答案_生成题无参考段():
    with_ref = _pdf_text(render_report_pdf(_payload()))
    assert "参考答案正文标记XYZ" in with_ref

    # 两道题都没有参考答案（生成题/项目深挖）→ 整份不出现「参考答案」小节标题
    payload = _payload()
    for item in payload["per_question_comments"]:
        item["reference_answer"] = None
    assert "参考答案" not in _pdf_text(render_report_pdf(payload))


def _font_names(data: bytes) -> set[str]:
    """PDF 里用到的全部字体名（Type0 的字体信息在 DescendantFonts 上）。"""
    reader = PdfReader(io.BytesIO(data))
    names = set()
    for page in reader.pages:
        for _, ref in (page.get("/Resources", {}).get("/Font") or {}).items():
            names.add(str(ref.get_object().get("/BaseFont")))
    return names


def test_PDF只用随镜像分发的字体_不混进本机字体():
    """导出的 PDF 必须只内嵌**随环境分发的**字体（Noto），不能落到本机字体。

    回归的是一次真实事故（2026-09-30 用户报「浏览器打开乱码、WPS 正常」）：字体栈当时只写在
    `body` 上，而 `@page` 页边距框与 SVG `<text>` **不继承 body 的 font-family**，各自落到
    系统默认字体（macOS 上是苹方 / 宋体 / Hiragino）；weasyprint 嵌的**苹方子集**，macOS 的
    PDFKit 系（Safari / 预览 / Quick Look）渲染不出来 → 整页缺字。而 Chrome、WPS 会回退到
    系统已装的同名字体，**肉眼看是好的**——所以这条只能断言字体名，读文本、看渲染图都拦不住。

    本机没有 Noto 时这条会红：装 `brew install --cask font-noto-sans-cjk-sc`（或直接跑容器）。
    """
    names = _font_names(render_report_pdf(_payload(), created_at="2026-09-30T06:23:45+00:00"))

    assert names, "PDF 里没有任何字体，渲染一定是空的"
    intruders = sorted(n for n in names if "Noto" not in n)
    assert not intruders, (
        f"PDF 混进了非随环境分发的字体：{intruders}——"
        f"macOS 上这些字体的子集在 Safari/预览里会缺字；"
        f"装 font-noto-sans-cjk-sc，或检查字体栈是否漏了某个渲染上下文（@page / SVG）"
    )


def test_旧payload缺新字段也能导出():
    """FR-25 之前的报告 payload 没有复盘字段——导出不能炸，缺什么略什么。"""
    legacy = {
        "position": "Agent/AI 工程师",
        "scores": {dim: 3 for dim in FIVE_DIMS},
        "domain_scores": {"rag": 3.0},
        "weaknesses": [],
        "answered_count": 1,
        "question_count": 3,
        "total_comment": "总评。",
    }
    data = render_report_pdf(legacy)

    assert data[:5] == b"%PDF-"
    assert "总评。" in _pdf_text(data)


def test_生成时间按东八区渲染():
    """报告落库是 UTC；PDF 是给人看的文档，按产品的东八区钟点渲染（同页面的本地时间）。"""
    assert format_created_at("2026-09-30T06:23:45.123456+00:00") == "2026-09-30 14:23"
    assert format_created_at("2026-09-30T22:05:00+00:00") == "2026-10-01 06:05"
    # 非法时间不抛——报告照常导出，只是不显示时间
    assert format_created_at("") == ""
    assert format_created_at("不是时间") == ""


# ---- 行为面报告（P1-M11）----


def _behavioral_payload() -> dict:
    """行为面报告 payload：行为面五维、无知识域、类型字段齐（契约同 SPEC §4.6）。"""
    return {
        "interview_type": "behavioral",
        "position": "Agent/AI 工程师",
        "scores": {
            "communication": 4, "logic_structure": 3, "project_experience": 4,
            "values_motivation": 2, "career_stability": 5,
        },
        "domain_scores": {},
        "weaknesses": ["values_motivation"],
        "answered_count": 1,
        "question_count": 1,
        "total_comment": "总评：讲述清晰，动机一节需要更具体。",
        "per_question_comments": [
            {
                "index": 1, "number": 1, "question_id": None,
                "question_type": "behavioral", "domain": "behavioral",
                "text": "讲讲你最有成就感的一段经历。",
                "comment": "结构不错。", "candidate_answer": "我在实习里做了一个检索服务。",
                "score": {
                    "communication": 4, "logic_structure": 3, "project_experience": 4,
                    "values_motivation": 2, "career_stability": 5,
                },
                "covered_key_points": ["背景清晰"], "missed_key_points": ["量化结果"],
                "reference_answer": None,
            },
        ],
        "study_advice": [{"domain": "价值观与动机", "advice": "把动机和岗位方向对上。"}],
    }


def test_行为面雷达图用行为面维度与标签():
    svg = radar_svg(_behavioral_payload()["scores"], dims=BEHAVIORAL_DIMS)

    assert len(_points(svg, "radar-data")) == 5
    for label in BEHAVIORAL_DIMENSION_LABELS.values():
        assert label in svg
    assert "技术深度" not in svg


def test_行为面PDF无知识域段且短板叫维度():
    payload = _behavioral_payload()

    text = _pdf_text(render_report_pdf(payload, "2026-10-01T10:00:00+00:00"))

    assert "行为面" in text or "价值观与动机" in text
    assert "短板维度" in text
    assert "知识域均分" not in text
    assert "技术深度" not in text
    # 逐题复盘里的评分也是行为面维度
    assert "职业稳定性" in text


def test_行为面PDF只用随镜像分发的字体():
    """同一约束对行为面报告同样成立（字体栈按渲染上下文逐处声明，P1-M8 教训）。"""
    data = render_report_pdf(_behavioral_payload(), "2026-10-01T10:00:00+00:00")
    reader = PdfReader(io.BytesIO(data))
    fonts: set[str] = set()
    for page in reader.pages:
        resources = page.get("/Resources") or {}
        for name, ref in (resources.get("/Font") or {}).items():
            obj = ref.get_object()
            fonts.add(str(obj.get("/BaseFont", "")))
            for descendant in obj.get("/DescendantFonts", []) or []:
                fonts.add(str(descendant.get_object().get("/BaseFont", "")))
    assert fonts, "PDF 里没有字体信息"
    assert all("Noto" in name or "SourceHan" in name for name in fonts), fonts


def test_学习建议域key渲染成中文标签():
    """P2-M2：报告节点把建议域归一成 key（values_motivation）→ PDF 标签优先查维度表。"""
    payload = _behavioral_payload()
    payload["study_advice"] = [{"domain": "values_motivation", "advice": "把动机和岗位方向对上。"}]

    text = _pdf_text(render_report_pdf(payload, "2026-10-01T10:00:00+00:00"))

    assert "价值观与动机" in text
    assert "values_motivation" not in text
