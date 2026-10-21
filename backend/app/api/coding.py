"""代码工作台路由（M15）：随机算法题 + 结构化代码点评 + 无状态追问。

**独立于面试状态机**：不读写 interview、不进 checkpointer、不落库、不进报告。
题源固定为公共 `algorithms` 域（只存不考），`answer/key_points` 只进点评 prompt，
不出 API（随机题走安全投影）。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app import llm
from app.agents.coding import followup_messages, review_messages
from app.agents.schemas import CodeReview
from app.api.auth import get_current_user
from app.config import get_settings
from app.tools import bank_query

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/coding", tags=["coding"], dependencies=[Depends(get_current_user)])

CodingLanguage = Literal["python", "java", "cpp", "javascript"]
MAX_CODE_CHARS = 8000
MAX_HISTORY = 10
MAX_HISTORY_CHARS = 2000

LLM_UNAVAILABLE = "代码点评服务暂时不可用，请稍后重试"


class CodingReviewRequest(BaseModel):
    question_id: str = Field(min_length=1, max_length=64)
    language: CodingLanguage
    code: str = Field(min_length=1, max_length=MAX_CODE_CHARS)


class CodingTurn(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=MAX_HISTORY_CHARS)


class CodingFollowupRequest(CodingReviewRequest):
    review: CodeReview
    message: str = Field(min_length=1, max_length=2000)
    history: list[CodingTurn] = Field(default_factory=list, max_length=MAX_HISTORY)


def _question_or_404(question_id: str) -> dict:
    """取题（含内部参考）；不存在/非算法/私有/归档一律 404（不泄露存在性）。"""
    question = bank_query.get_coding_question_for_review(get_settings().db_path, question_id)
    if question is None:
        raise HTTPException(status_code=404, detail="题目不存在或不可用")
    return question


@router.get("/questions/random")
async def random_question():
    """随机一道公共算法题；只返回题干/主题/难度。"""
    question = await asyncio.to_thread(
        bank_query.random_coding_question, get_settings().db_path
    )
    if question is None:
        raise HTTPException(status_code=404, detail="题库暂无可用算法题")
    return question


@router.post("/reviews")
async def review_code(req: CodingReviewRequest):
    """结构化代码点评：问题清单 + 具体反例 + 复杂度判断 + 一个追问。"""
    if not req.code.strip():
        raise HTTPException(status_code=400, detail="代码不能为空")
    question = await asyncio.to_thread(_question_or_404, req.question_id)
    try:
        review = await llm.chat_json(
            review_messages(
                question=question["question"],
                language=req.language,
                code=req.code,
                reference=question.get("answer") or "",
                key_points=question.get("key_points") or [],
            ),
            schema=CodeReview,
            temperature=0.3,
            max_tokens=3000,
            purpose="coding_review",
        )
    except llm.LLMError as exc:
        logger.warning("代码点评失败（M15）：%s", exc)
        raise HTTPException(status_code=503, detail=LLM_UNAVAILABLE) from exc
    return {"question_id": req.question_id, "language": req.language, "review": review}


@router.post("/followups")
async def followup(req: CodingFollowupRequest):
    """无状态追问：每次带题目、代码、上次点评与最近历史。"""
    if not req.code.strip():
        raise HTTPException(status_code=400, detail="代码不能为空")
    question = await asyncio.to_thread(_question_or_404, req.question_id)
    try:
        answer = await llm.chat(
            followup_messages(
                question=question["question"],
                language=req.language,
                code=req.code,
                review=req.review,
                message=req.message,
                history=[turn.model_dump() for turn in req.history],
                reference=question.get("answer") or "",
                key_points=question.get("key_points") or [],
            ),
            max_tokens=1200,
            temperature=0.5,
            purpose="coding_followup",
        )
    except llm.LLMError as exc:
        logger.warning("代码追问失败（M15）：%s", exc)
        raise HTTPException(status_code=503, detail=LLM_UNAVAILABLE) from exc
    return {"answer": answer}
