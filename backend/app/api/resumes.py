"""简历路由（P2-M11 / FR-28）：解析一份简历，返回结构化结果。

两段式（同图片通道）：`POST /api/resumes` 先解析出 resume_id，创建面试时带 resume_id。
**只回结构化结果、不回原文**——创建页据此显示「读到 N 段项目经历」（解析错要当场看得见），
原文留在库里供重新解析。

文件与粘贴文本走**同一端点**（同一抽取管线）：粘贴文本是扫描版 / 图片版简历的兜底；
PDF 抽不出文字时明确报错并指路粘贴框，绝不静默退化成「没传简历」。

归属口径同 M7：简历是用户级资产，他人简历一律「不存在」（404，不泄露存在性）。
"""

from __future__ import annotations

import asyncio
import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from app import db, llm
from app.api.auth import get_current_user
from app.config import get_settings
from app.tools import resumes

router = APIRouter(
    prefix="/api/resumes", tags=["resumes"], dependencies=[Depends(get_current_user)]
)

PASTE_FILENAME = "粘贴文本"  # 落库的 filename：粘贴无文件名，给它一个诚实的来源标记


@router.post("", status_code=201)
async def create_resume(
    file: UploadFile | None = File(default=None),
    text: str | None = Form(default=None),
    user: dict = Depends(get_current_user),
):
    """解析简历（文件或粘贴文本，二选一），返回 `{resume_id, projects, skills, chars}`。"""
    if file is not None and not file.filename:
        file = None  # 浏览器空文件框会带一个空文件部分：按「没传文件」处理
    if (file is None) == (text is None):
        raise HTTPException(status_code=400, detail="请二选一：上传简历文件（.pdf / .md / .txt），或粘贴简历文本")

    if file is not None:
        filename = file.filename or ""
        if not filename.lower().endswith(resumes.UPLOAD_SUFFIXES):
            raise HTTPException(status_code=400, detail="仅支持 .pdf / .md / .txt 文件")
        # 先看声明大小再读内容：大文件不先进内存（同私有题库上传）
        if file.size is not None and file.size > resumes.MAX_RESUME_BYTES:
            raise HTTPException(status_code=413, detail="文件过大（上限 2MB）")
        data = await file.read()
        if len(data) > resumes.MAX_RESUME_BYTES:
            raise HTTPException(status_code=413, detail="文件过大（上限 2MB）")
        try:
            raw = await asyncio.to_thread(resumes.extract_text, filename, data)
        except resumes.ResumeError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    else:
        filename, raw = PASTE_FILENAME, text or ""

    try:
        content = resumes.prepare_text(raw)  # 空文本 / 超长在这里拦下
    except resumes.ResumeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    try:
        parsed = await resumes.parse_resume(content)
    except llm.LLMError as exc:
        # 抽取失败要给出路（重试 / 改粘贴），不是一句「服务异常」
        raise HTTPException(
            status_code=502, detail="简历解析失败（模型暂不可用），请重试，或改用粘贴文本"
        ) from exc

    resume_id = f"r_{uuid.uuid4().hex[:12]}"
    db_path = get_settings().db_path
    # 先清旧的孤儿（解析了却没建场次的），再存新的——顺序反了会把刚存的行也清掉
    await asyncio.to_thread(db.prune_unreferenced_resumes, db_path, user_id=user["id"])
    await asyncio.to_thread(
        db.save_resume,
        db_path,
        resume_id=resume_id,
        user_id=user["id"],
        filename=filename,
        text=content,
        parsed=parsed.model_dump(),
    )
    return {
        "resume_id": resume_id,
        "filename": filename,
        "projects": parsed.projects,
        "skills": parsed.skills,
        "chars": len(content),
    }
