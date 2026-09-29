"""私有题库路由（FR-13，SPEC §7）：上传 / 列表 / 编辑归档。

全部端点需登录，且**只作用于本人私有题**——归属条件由 bank_private 的每个查询保证
（user_id 必传），跨用户访问按「不存在」404 处理，不泄露存在性（同 M2 口径）。

上传是**部分成功**语义：合法的题目照常入库，不合法的进 errors 报告，整份文件不因
一条坏题而回滚（用户传的是自己整理的笔记，全有或全无只会让人反复试）。
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field, field_validator, model_validator

from app.api.auth import get_current_user
from app.config import get_settings
from app.domain import ENABLED_DOMAINS
from app.tools import bank_private, bank_query, private_parse
from app.tools.question_text import MIN_ANSWER_CHARS, answer_substance

router = APIRouter(
    prefix="/api/bank/private", tags=["bank-private"], dependencies=[Depends(get_current_user)]
)

MAX_UPLOAD_BYTES = 2 * 1024 * 1024  # 2MB：个人笔记量级足够，挡住误传的大文件
Difficulty = Literal["L1", "L2", "L3"]


class QuestionPatch(BaseModel):
    """编辑/归档（归档 = status: draft，复用既有状态枚举，前端映射成「已归档」文案）。"""

    question: str | None = Field(default=None, min_length=1, max_length=500)
    answer: str | None = None
    key_points: list[str] | None = None
    follow_ups: list[str] | None = None
    topic: str | None = Field(default=None, max_length=100)
    domain: str | None = None
    difficulty: Difficulty | None = None
    status: Literal["enabled", "draft"] | None = None

    @model_validator(mode="after")
    def _at_least_one(self):
        if all(getattr(self, name) is None for name in type(self).model_fields):
            raise ValueError("至少需要一个待更新字段")
        return self

    @field_validator("answer")
    @classmethod
    def _answer_usable(cls, value: str | None) -> str | None:
        """编辑后的答案仍须可用：与上传同一口径（空答案的题在面试里给不出参考）。"""
        if value is not None and answer_substance(value) < MIN_ANSWER_CHARS:
            raise ValueError(f"参考答案过短（不足 {MIN_ANSWER_CHARS} 个实质字符）")
        return value

    @field_validator("domain")
    @classmethod
    def _known_domain(cls, value: str | None) -> str | None:
        if value is not None and value not in ENABLED_DOMAINS:
            raise ValueError("未知知识域")
        return value


def _require_question(db_path: Path, user_id: str, question_id: str) -> dict:
    """取本人私有题；不存在或非本人一律 404（不泄露存在性）。"""
    item = bank_private.get_question(db_path, user_id=user_id, question_id=question_id)
    if item is None:
        raise HTTPException(status_code=404, detail="题目不存在")
    return item


async def _attach_sources(db_path: Path, items: list[dict]) -> list[dict]:
    return await asyncio.to_thread(bank_query.attach_sources, db_path, items)


@router.post("/upload")
async def upload(
    file: UploadFile = File(...),
    domain: str = Form(...),
    difficulty: Difficulty = Form("L2"),
    user: dict = Depends(get_current_user),
):
    """上传 md/txt/pdf → 解析入库，返回导入/重复/失败三份明细。"""
    filename = file.filename or ""
    if not filename.lower().endswith(private_parse.UPLOAD_SUFFIXES):
        raise HTTPException(status_code=400, detail="仅支持 .md / .txt / .pdf 文件")
    if domain not in ENABLED_DOMAINS:
        raise HTTPException(status_code=400, detail="未知知识域")
    # 先看声明大小再读内容：大文件不先进内存
    if file.size is not None and file.size > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="文件过大（上限 2MB）")
    data = await file.read()
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="文件过大（上限 2MB）")

    try:
        records, errors = await asyncio.to_thread(private_parse.parse_upload, filename, data)
    except ValueError as exc:  # 编码无法识别 / PDF 读取失败等可预期错误
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    db_path = get_settings().db_path
    for record in records:  # 域与难度来自表单的批量默认值，导入后仍可逐题改
        record["domain"] = domain
        record["difficulty"] = difficulty
    result = await asyncio.to_thread(
        bank_private.insert_questions,
        db_path,
        user_id=user["id"],
        records=records,
        source_detail=filename,
    )
    return {"parsed": len(records) + len(errors), "errors": errors, **result}


@router.get("/questions")
async def list_questions(
    status: Literal["enabled", "draft"] | None = None,
    domain: str | None = None,
    difficulty: Difficulty | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=bank_private.PAGE_SIZE, ge=1, le=bank_private.MAX_PAGE_SIZE),
    user: dict = Depends(get_current_user),
):
    """本人私有题分页（含已归档，默认全出），items 附来源明细。"""
    db_path = get_settings().db_path
    items, total = await asyncio.to_thread(
        bank_private.list_questions,
        db_path,
        user_id=user["id"],
        status=status,
        domain=domain,
        difficulty=difficulty,
        page=page,
        page_size=page_size,
    )
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": await _attach_sources(db_path, items),
    }


@router.patch("/questions/{question_id}")
async def patch_question(
    question_id: str, req: QuestionPatch, user: dict = Depends(get_current_user)
):
    """编辑字段或归档/恢复本人私有题。"""
    db_path = get_settings().db_path
    _require_question(db_path, user["id"], question_id)
    await asyncio.to_thread(
        bank_private.update_question,
        db_path,
        user_id=user["id"],
        question_id=question_id,
        fields=req.model_dump(exclude_none=True),
    )
    item = _require_question(db_path, user["id"], question_id)
    return (await _attach_sources(db_path, [item]))[0]
