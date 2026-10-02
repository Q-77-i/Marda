"""报告 PDF 导出（FR-18）：jinja2 模板 + weasyprint 渲染，雷达图以内联 SVG 绘制。

为什么在服务端渲染：中文文档排版交给 HTML/CSS 引擎，导出的是**真文本 PDF**——文字可选中、
可检索，「中文无乱码」才有可跑判据（pypdf 读回断言，见 tests/unit/test_report_pdf.py）；
浏览器截图再拼 PDF 的路线三者皆无。

数据来源是报告 payload（SPEC §4.6）本身，本模块只做展示转换、不重算任何分数：
页面显示什么，PDF 就显示什么。
"""

from __future__ import annotations

import math
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

from app.domain import DOMAIN_LABELS, INTERVIEW_BEHAVIORAL, INTERVIEW_TECH
from app.graph.rules.aggregate import (
    BEHAVIORAL_DIMENSION_LABELS,
    DIMENSION_LABELS,
    FIVE_DIMS,
    dims_for,
    overall_score,
)
from app.graph.state import FOLLOWUP_ANSWER_MARKER


def _ensure_native_libs() -> None:
    """macOS 宿主机：Homebrew 的 glib/pango 不在 dyld 默认搜索路径里，weasyprint 的
    cffi `dlopen("libgobject-2.0-0")` 会直接抛 OSError（报错看着像坏环境，其实是搜索路径）。

    补救只需一条搜索路径，且必须在 import weasyprint 之前生效：ctypes 的 macholib 搜索读的是
    **os.environ**（每次 dlopen 现读，不是进程启动时锁死的 dyld 变量），所以运行时补也来得及。
    容器（Debian + apt 装的 libpango）与 Linux CI 走不到这里，纯 no-op。
    """
    if sys.platform != "darwin":
        return
    for prefix in ("/opt/homebrew/lib", "/usr/local/lib"):
        if not Path(prefix, "libgobject-2.0.0.dylib").exists():
            continue
        current = os.environ.get("DYLD_FALLBACK_LIBRARY_PATH", "")
        if prefix not in current.split(":"):
            os.environ["DYLD_FALLBACK_LIBRARY_PATH"] = f"{prefix}:{current}".rstrip(":")
        return


_ensure_native_libs()

import weasyprint  # noqa: E402  —— 必须晚于 _ensure_native_libs（见其 docstring）
from jinja2 import Environment, FileSystemLoader  # noqa: E402

TEMPLATES_DIR = Path(__file__).parent / "templates"

# autoescape 必须显式打开：候选人回答与 LLM 文案都是自由文本，直出 HTML 会被
# 当成标签解析（`<b>` 少个字母就是一处注入）。雷达 SVG 是我们自己拼的，模板里 |safe 放行。
_env = Environment(loader=FileSystemLoader(TEMPLATES_DIR), autoescape=True)

# 报告是给人看的文档、产品面向国内校招场景：时间按东八区钟点渲染，与页面的本地时间一致
# （落库的 created_at 是 UTC，见 db._now）。多时区是阶段 3 之后的事。
_REPORT_TZ = timezone(timedelta(hours=8))

# 中文字体栈（模板 CSS 与 SVG 文本共用，故提到这里做单一来源）。
#
# **必须显式落到每个渲染上下文，不能只写在 body 上**：`@page` 页边距框与 SVG `<text>`
# 不继承 body 的 font-family，会各自落到系统默认字体（macOS 上是苹方/宋体）——那样导出的
# PDF 里就混进了**本机才有**的字体，而 macOS 的 PDFKit 系（Safari / 预览 / Quick Look）
# 渲染不了 weasyprint 嵌的苹方子集，表现为整片缺字/乱码（WPS、Chrome 会回退到系统字体，
# 所以看着正常，问题极难察觉）。容器里恰好只有 Noto，才不会暴露。
FONT_STACK = (
    "'Noto Sans CJK SC', 'Source Han Sans SC', 'PingFang SC', "
    "'Hiragino Sans GB', 'Microsoft YaHei', sans-serif"
)

_MAX_SCORE = 5.0
_RING_FRACTIONS = (1.0, 0.8, 0.6, 0.4, 0.2)  # 外圈先画，内圈压在上面
# 半径与标签半径：标签必须落在画布内。**不靠 viewBox 留白**——打印引擎对 <text> 不套用
# viewBox 变换（五边形按 viewBox 缩放、文字却按原始坐标摆），留白会被吃掉半截字，
# 故画布与坐标系 1:1，靠比例把标签收进框内（左右各留 ~15px，CJK 四字标签宽 ~40px）。
_RADIUS_RATIO = 0.30
_LABEL_RADIUS_RATIO = 1.28
# 颜色写成 SVG 呈现属性而不是 CSS：打印引擎的 SVG 渲染器只认属性，
# 走 CSS 的 fill-opacity 会被忽略（实心多边形糊住网格线）。
_RING_COLOR = "#e3e7ea"
_DATA_FILL = "#dbe6f1"
_DATA_STROKE = "#056a9d"
_LABEL_COLOR = "#6a7280"

# 题型展示名（与前端 constants.QUESTION_TYPE_LABELS 一致；tech 走编号、不入表，
# 未知题型显示原值不猜）
_QUESTION_TYPE_LABELS = {"scenario": "项目深挖"}


def _clamp(value: float) -> float:
    """分数截断到 0–5：越界只可能是脏数据，宁可贴边也不画出框。"""
    return min(max(float(value or 0), 0.0), _MAX_SCORE)


def _vertex(cx: float, cy: float, radius: float, index: int, fraction: float, sides: int = 5) -> tuple[float, float]:
    """第 index 个轴（0 = 正上方，顺时针）上距圆心 radius×fraction 的点，保留一位小数定死输出。"""
    angle = math.radians(-90 + (360 / sides) * index)
    return (
        round(cx + radius * fraction * math.cos(angle), 1),
        round(cy + radius * fraction * math.sin(angle), 1),
    )


def _points_attr(points: list[tuple[float, float]]) -> str:
    return " ".join(f"{x},{y}" for x, y in points)


def radar_svg(scores: dict, *, size: int = 260, dims: tuple[str, ...] = FIVE_DIMS) -> str:
    """能力雷达图（内联 SVG）。dims 决定顶点数与标签（技术面/行为面各一套，P1-M11）。"""
    sides = len(dims)
    cx = cy = size / 2
    radius = size * _RADIUS_RATIO
    rings = [
        f'<polygon class="radar-ring" points="'
        f'{_points_attr([_vertex(cx, cy, radius, i, frac, sides) for i in range(sides)])}" '
        f'fill="none" stroke="{_RING_COLOR}" stroke-width="1"/>'
        for frac in _RING_FRACTIONS
    ]
    axes = [
        f'<line class="radar-axis" x1="{cx}" y1="{cy}" x2="{x}" y2="{y}" '
        f'stroke="{_RING_COLOR}" stroke-width="1"/>'
        for x, y in (_vertex(cx, cy, radius, i, 1.0, sides) for i in range(sides))
    ]
    data = _points_attr([
        _vertex(cx, cy, radius, i, _clamp(scores.get(dim, 0)) / _MAX_SCORE, sides)
        for i, dim in enumerate(dims)
    ])
    labels = []
    for index, dim in enumerate(dims):
        x, y = _vertex(cx, cy, radius, index, _LABEL_RADIUS_RATIO, sides)
        dy = -4 if index == 0 else 4  # 正上方那顶点往上抬，余者压到顶点下方
        labels.append(
            f'<text class="radar-label" x="{x}" y="{round(y + dy, 1)}" text-anchor="middle" '
            f'font-family="{FONT_STACK}" font-size="10" '
            f'fill="{_LABEL_COLOR}">{dims_label(dim)}</text>'
        )
    return (
        f'<svg class="radar" width="{size}" height="{size}" viewBox="0 0 {size} {size}" '
        f'xmlns="http://www.w3.org/2000/svg">'
        f'{"".join(rings)}{"".join(axes)}'
        f'<polygon class="radar-data" points="{data}" fill="{_DATA_FILL}" '
        f'stroke="{_DATA_STROKE}" stroke-width="1.6"/>'
        f'{"".join(labels)}</svg>'
    )


def dims_label(dim: str) -> str:
    """维度中文标签（两套表同源 aggregate）：技术面五维 ∪ 行为面五维。"""
    return DIMENSION_LABELS.get(dim) or BEHAVIORAL_DIMENSION_LABELS.get(dim, dim)


def _parse_utc(iso: str) -> datetime | None:
    """UTC ISO 串 → 带时区的 datetime；解析不了给 None（报告照常导出，只是不显示时间）。"""
    try:
        moment = datetime.fromisoformat(iso)
    except (TypeError, ValueError):
        return None
    return moment.replace(tzinfo=timezone.utc) if moment.tzinfo is None else moment


def format_created_at(iso: str) -> str:
    """UTC ISO 串 → 东八区 "YYYY-MM-DD HH:mm"。"""
    moment = _parse_utc(iso)
    return moment.astimezone(_REPORT_TZ).strftime("%Y-%m-%d %H:%M") if moment else ""


def _file_stamp(iso: str) -> str:
    """UTC ISO 串 → 东八区 ``YYYYMMDD-HHmm``（文件名用，与 format_created_at 同一时区口径）。"""
    moment = _parse_utc(iso)
    return moment.astimezone(_REPORT_TZ).strftime("%Y%m%d-%H%M") if moment else ""


def _split_answer(answer: str | None) -> list[dict]:
    """「我的回答」按追问标记分段（同前端 splitAnswerSegments，FR-25 复盘卡口径）。"""
    text = (answer or "").strip()
    if not text:
        return []
    segments = []
    for index, part in enumerate(text.split(FOLLOWUP_ANSWER_MARKER)):
        part = part.strip()
        if part:
            segments.append({"label": "首答" if index == 0 else f"追问补充 {index}", "text": part})
    return segments


def _question_label(item: dict, index: int) -> str:
    """逐题标题：题型语义由后端定义（T7a），未知题型显示原值不猜（同前端 commentLabels）。"""
    number = item.get("number")
    if isinstance(number, int):
        question_type = item.get("question_type")
        suffix = ""
        if question_type and question_type != "tech":
            suffix = f" · {_QUESTION_TYPE_LABELS.get(question_type, question_type)}"
        return f"第 {number} 题{suffix}"
    if item.get("question_type"):
        return _QUESTION_TYPE_LABELS.get(item["question_type"], item["question_type"])
    return f"第 {index + 1} 题"


def _advice_label(domain: str | None, dims: tuple[str, ...]) -> str:
    """学习建议的域标签（P2-M2）：建议域可能是**评分维度 key**（行为面，报告节点归一成 key）
    或**知识域 id**（技术面），先查维度表再查域表，都没有则原样——与前端同序。"""
    key = domain or ""
    if key in dims:
        return dims_label(key)
    return DOMAIN_LABELS.get(key, key)


def _question_context(item: dict, index: int, dims: tuple[str, ...] = FIVE_DIMS) -> dict:
    score = item.get("score") or {}
    return {
        "label": _question_label(item, index),
        "domain": DOMAIN_LABELS.get(item.get("domain") or "", item.get("domain") or ""),
        "text": item.get("text") or "",
        "segments": _split_answer(item.get("candidate_answer")),
        "dimensions": [
            {"label": dims_label(dim), "value": score.get(dim)} for dim in dims
        ],
        "has_score": bool(score),
        "covered": item.get("covered_key_points") or [],
        "missed": item.get("missed_key_points") or [],
        "comment": item.get("comment") or "",
        "reference_answer": item.get("reference_answer") or "",
    }


def _build_context(payload: dict, created_at: str) -> dict:
    scores = payload.get("scores") or {}
    interview_type = payload.get("interview_type") or INTERVIEW_TECH
    behavioral = interview_type == INTERVIEW_BEHAVIORAL
    dims = tuple(dims_for(interview_type))
    answered, total = payload.get("answered_count") or 0, payload.get("question_count") or 0
    weaknesses = set(payload.get("weaknesses") or [])
    # 总分走 aggregate.overall_score 单一来源（P1-M10 D1）：新 payload 直接取报告端算好的值，
    # 老 payload 用同一函数现算（_clamp 是对存量脏数据的防御，正常数据上是恒等）
    stored_overall = payload.get("overall")
    overall = (
        round(_clamp(stored_overall), 2)
        if stored_overall is not None
        else overall_score({dim: _clamp(scores.get(dim, 0)) for dim in dims}, dims)
    )
    return {
        "font_stack": FONT_STACK,
        "position": payload.get("position") or "",
        "overall": overall,
        "answered_count": answered,
        "question_count": total,
        "created_at": format_created_at(created_at),
        "radar": radar_svg(scores, dims=dims),
        "dimensions": [
            {"label": dims_label(dim), "value": round(_clamp(scores.get(dim, 0)), 2)}
            for dim in dims
        ],
        # 域得分按分值降序：短板一眼可见（页面是柱状图，PDF 用同宽的横条）。
        # 行为面没有知识域（整场一个域）→ domains 为空，模板相应收起右栏（P1-M11）
        "has_domains": bool(payload.get("domain_scores")),
        "domains": [
            {
                "label": DOMAIN_LABELS.get(domain, domain),
                "value": round(float(value or 0), 2),
                "percent": round(_clamp(value) / _MAX_SCORE * 100, 1),
                "weak": domain in weaknesses,
            }
            for domain, value in sorted(
                (payload.get("domain_scores") or {}).items(), key=lambda kv: -float(kv[1] or 0)
            )
        ],
        "weaknesses_label": "短板维度" if behavioral else "短板域",
        "weaknesses": (
            [dims_label(key) for key in payload.get("weaknesses") or []]
            if behavioral
            else [DOMAIN_LABELS.get(domain, domain) for domain in payload.get("weaknesses") or []]
        ),
        "total_comment": payload.get("total_comment") or "",
        "questions": [
            _question_context(item, index, dims)
            for index, item in enumerate(payload.get("per_question_comments") or [])
        ],
        "advice": [
            {
                "label": _advice_label(item.get("domain"), dims),
                "advice": item.get("advice") or "",
            }
            for item in payload.get("study_advice") or []
        ],
    }


def render_report_pdf(payload: dict, created_at: str = "") -> bytes:
    """报告 payload → PDF 字节（无状态、可重入：同一 payload 每次产出同一份文档）。"""
    template = _env.get_template("report.html.j2")
    html = template.render(**_build_context(payload, created_at))
    return weasyprint.HTML(string=html, base_url=str(TEMPLATES_DIR)).write_pdf()


def content_disposition(interview_id: str, created_at: str = "") -> str:
    """附件下载头。

    中文名走 RFC 5987（``filename*``），并另给一个纯 ASCII 的 ``filename`` 兜底：
    不认 ``filename*`` 的老客户端至少能存下文件，而不是把裸汉字塞进文件名（头字段只允许
    latin-1，裸塞还会让部分服务器直接抛错）。

    尾部带该场报告时间：同名文件只会被存成 ``-2.pdf``，名字里带上时间才分得清是哪一场。
    **浏览器实际落盘的名字由前端 `lib/download.ts` 决定**（blob 下载的 `link.download`
    覆盖本头），这里服务的是直接访问 URL 的客户端；两处形状保持一致。

    ⚠️ 本函数只拿到场次号与本场时间，拿不到岗位名（前端那份有），故第二段用场次号短码：
    ``码达-能力评估-<场次短码>-<YYYYMMDD-HHmm>``。
    """
    stamp = _file_stamp(created_at)
    stem = f"码达-能力评估-{interview_id[:8]}{f'-{stamp}' if stamp else ''}"
    return (
        f'attachment; filename="marda-report-{interview_id[:8]}.pdf"; '
        f"filename*=UTF-8''{quote(stem)}.pdf"
    )
