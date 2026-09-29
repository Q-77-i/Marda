"""私有题上传解析（FR-13）：模板文本 → 结构化题目（md 与 PDF 抽文本同一入口）。

模板（每道题以【题目】开头，标记必须独立成行首；纯代码解析、零 LLM 调用）：

    【题目】Redis 的持久化机制有哪几种？
    【答案】RDB 与 AOF 两种……
    【关键点】RDB 快照；AOF 日志；混合持久化
    【追问】AOF 重写是怎么触发的？
    【主题】存储与缓存

**为什么不用 markdown 标题（`## `）作题目标记**：PDF 抽文本会丢掉 `##` 前缀，
两套规则会让同一份内容在 md 与 PDF 下解析出不同结果；【题目】在两种格式下都原样保留。

逐题校验、**部分成功照常入库**：不合法的那条进 errors 报告，不静默丢弃也不拖累整份文件。
答案的实质字符数判定与语料管道同源（question_text.answer_substance）。
"""

from __future__ import annotations

import io
import re
from typing import Final

from pypdf import PdfReader

from app.tools.question_text import MIN_ANSWER_CHARS, answer_substance

DEFAULT_TOPIC: Final = "个人上传"
PREVIEW_CHARS: Final = 40
UPLOAD_SUFFIXES: Final = (".md", ".markdown", ".txt", ".pdf")

# 标记必须出现在行首（允许前导空白）——正文里的「【关键点】」若顶格另起一行会被当标记，
# 这是模板的成文约定（模板里已写明），换来的是解析完全确定、可单测。
MARKER_RE = re.compile(r"^\s*【(题目|答案|关键点|追问|主题)】[ \t]*(.*)$")
# 关键点/追问的分隔：中文分号、英文分号、竖线、换行都认（用户手写形态不一）
_ITEM_SPLIT_RE = re.compile(r"[；;|\n]")
_BULLET_RE = re.compile(r"^\s*[-*·•]\s*")


def _preview(text: str) -> str:
    flat = " ".join(text.split())
    return flat[:PREVIEW_CHARS] + ("…" if len(flat) > PREVIEW_CHARS else "")


def _one_line(content: str) -> str:
    """题干/主题折叠成一行：PDF 硬换行不该把一道题拆成多行。"""
    return " ".join(part.strip() for part in content.splitlines() if part.strip())


def _items(content: str) -> list[str]:
    """关键点/追问：按分隔符拆条、剥项目符号、去重保序。"""
    seen: set[str] = set()
    items: list[str] = []
    for part in _ITEM_SPLIT_RE.split(content):
        item = _BULLET_RE.sub("", part).strip()
        if item and item not in seen:
            seen.add(item)
            items.append(item)
    return items


def _blocks(text: str) -> list[tuple[str, str]]:
    """切 (标记, 内容) 序列；内容 = 标记行剩余部分 + 其后到下一个标记之间的所有行。

    首个标记之前的行（文档标题、前言）忽略——它们不属于任何字段。
    """
    blocks: list[tuple[str, list[str]]] = []
    for line in text.splitlines():
        match = MARKER_RE.match(line)
        if match:
            blocks.append((match.group(1), [match.group(2)]))
        elif blocks:
            blocks[-1][1].append(line)
    return [(marker, "\n".join(lines)) for marker, lines in blocks]


def _finalize(record: dict, records: list[dict], errors: list[dict]) -> None:
    """校验并收集单条记录：不合法的进 errors，不中断后续题目。"""
    if not record["text"]:
        errors.append({"question": "", "reason": "【题目】内容为空"})
        return
    if not record["answer"]:
        errors.append({"question": _preview(record["text"]), "reason": "缺少【答案】（必填）"})
        return
    if answer_substance(record["answer"]) < MIN_ANSWER_CHARS:
        errors.append({
            "question": _preview(record["text"]),
            "reason": f"参考答案过短（不足 {MIN_ANSWER_CHARS} 个实质字符）",
        })
        return
    record["topic"] = record["topic"] or DEFAULT_TOPIC
    records.append(record)


def parse_template(text: str) -> tuple[list[dict], list[dict]]:
    """模板文本 → (可入库记录, 错误清单)。

    记录字段：text / answer / key_points / follow_ups / topic（域与难度由上传表单给，
    不来自文件——文件里写域等于让用户背内部枚举值）。
    """
    blocks = _blocks(text)
    if not any(marker == "题目" for marker, _ in blocks):
        return [], [{
            "question": "",
            "reason": "未找到【题目】标记——每道题请以【题目】开头（格式见页面上传区的「格式说明」）",
        }]
    records: list[dict] = []
    errors: list[dict] = []
    current: dict | None = None
    for marker, content in blocks:
        if marker == "题目":
            if current is not None:
                _finalize(current, records, errors)
            current = {
                "text": _one_line(content),
                "answer": "",
                "key_points": [],
                "follow_ups": [],
                "topic": "",
            }
        elif current is None:
            errors.append({
                "question": _preview(content),
                "reason": f"【{marker}】出现在首个【题目】之前",
            })
        elif marker == "答案":
            current["answer"] = content.strip()
        elif marker == "关键点":
            current["key_points"] = _items(content)
        elif marker == "追问":
            current["follow_ups"] = _items(content)
        else:  # 主题
            current["topic"] = _one_line(content)
    if current is not None:
        _finalize(current, records, errors)
    return records, errors


def extract_pdf_text(data: bytes) -> str:
    """PDF 抽文本（pypdf）。**仅文字版 PDF**：扫描件抽不出文本，解析结果为空 → 报错可见。"""
    reader = PdfReader(io.BytesIO(data))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def decode_text(data: bytes) -> str:
    """纯文本解码：UTF-8（含 BOM）优先，回退 GBK（Windows 导出的中文笔记常是 GBK）。"""
    for encoding in ("utf-8-sig", "gbk"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ValueError("文件编码无法识别（支持 UTF-8 / GBK）")


def parse_upload(filename: str, data: bytes) -> tuple[list[dict], list[dict]]:
    """按扩展名分派解析（API 已校验后缀，这里只做内容层面的分派）。"""
    if filename.lower().endswith(".pdf"):
        return parse_template(extract_pdf_text(data))
    return parse_template(decode_text(data))
