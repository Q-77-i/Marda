"""简历解析（P2-M11 / FR-28）：文本抽取 → LLM 结构化 → candidate_profile 文本。

两段式（同图片通道）：先在创建页 `POST /api/resumes` 解析出 resume_id 与「读到 N 段项目
经历」，创建面试时带 resume_id；解析结果**预填 `state.candidate_profile`**——出题侧
（项目深挖题 / 行为面兜底生成题 / 口吻层）零改动就变具体（candidate_profile 是唯一
候选人插槽，被三处出题模板消费）。

口径：
- 接口**只回结构化结果、不回原文**；原文留在 `resumes.text` 供重新解析（也便于排查
  「怎么读成这样」）；
- 抽取走 flash + 关 thinking + json_object（`llm.chat_json` 的固化路径，CLAUDE.md 坑位 1）；
- 扫描版 / 图片版 PDF 抽不出文字 → 明确报错并**指路粘贴框**（不静默退化成「没传简历」）；
- 文本抽取复用私有题库上传的同一套解码 / PDF 抽文（`private_parse`），不另写一份。
"""

from __future__ import annotations

from typing import Final

from app import llm
from app.agents.prompts import RESUME_PARSE_TEMPLATE
from app.agents.schemas import ResumeExtraction
from app.tools.private_parse import decode_text, extract_pdf_text

UPLOAD_SUFFIXES: Final = (".pdf", ".md", ".markdown", ".txt")
MAX_RESUME_BYTES: Final = 2 * 1024 * 1024
# 上限按「够放下任何一份真实简历」定：简历量级几千字，5 万字符已远超（挡住误传的长文）。
MAX_RESUME_CHARS: Final = 50_000


class ResumeError(ValueError):
    """可预期的内容层面错误（后缀 / 编码 / 空文本等），路由转 400 并原样展示原因。"""


def extract_text(filename: str, data: bytes) -> str:
    """按后缀抽文本：.pdf 走 pypdf，其余按 UTF-8 / GBK 解码（与私有题库上传同一实现）。"""
    if filename.lower().endswith(".pdf"):
        try:
            text = extract_pdf_text(data)
        except Exception as exc:  # pypdf 的异常类型杂（PdfReadError / 加密 / 损坏 / 结构怪异）
            raise ResumeError("这份 PDF 读不出来（可能损坏或加密）——请改用粘贴文本") from exc
    else:
        try:
            text = decode_text(data)
        except ValueError as exc:
            raise ResumeError(str(exc)) from exc
    return text


def prepare_text(text: str) -> str:
    """归一待解析文本：去首尾空白；空 / 超长直接报错（不静默截断——截断等于悄悄丢经历）。"""
    stripped = text.strip()
    if not stripped:
        raise ResumeError("没有读到简历内容——扫描版 / 图片版 PDF 请改用粘贴文本")
    if len(stripped) > MAX_RESUME_CHARS:
        raise ResumeError(f"简历文本过长（上限 {MAX_RESUME_CHARS} 字符），请精简后再试")
    return stripped


async def parse_resume(text: str) -> ResumeExtraction:
    """LLM 结构化抽取。失败抛 `llm.LLMError`（路由转 502 并给「重试或改粘贴」的出路）。"""
    return await llm.chat_json(
        [{"role": "system", "content": RESUME_PARSE_TEMPLATE.format(content=text)}],
        schema=ResumeExtraction,
        temperature=0.2,
        purpose="resume",  # 成本归因（P2-M10 的调用命名口径）
    )


def format_profile(parsed: dict) -> str:
    """解析结果 → `candidate_profile` 文本。

    与 `profile_node` 的拼接口径同形（summary + 「；项目经历：」并列），这样出题模板拿到
    的 profile 无论来自简历还是自我介绍都是同一个形状。空解析返回空串——调用方据此
    决定要不要预填（预填空串等于没预填，但不报错：候选人简历里可能确实只有个人信息）。
    """
    parts: list[str] = []
    summary = str(parsed.get("summary") or "").strip()
    if summary:
        parts.append(summary)
    projects = [str(p).strip() for p in parsed.get("projects") or [] if str(p).strip()]
    if projects:
        parts.append("项目经历：" + "；".join(projects))
    skills = [str(s).strip() for s in parsed.get("skills") or [] if str(s).strip()]
    if skills:
        parts.append("技能：" + "、".join(skills))
    return "；".join(parts)
