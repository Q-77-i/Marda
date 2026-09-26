"""面试路由（SPEC §7 API 契约）：六个端点 + SSE 流。

全部端点需登录（FR-23）：未带有效 token 401；他人场次由 service 按「不存在」404 处理。
SSE 心跳与注释由 EventSourceResponse 自带 ping 机制承担（15s，SPEC §7）；
流开始前的输入校验（401/404/409/400）在 route 层完成，保证 4xx 以普通 JSON 返回。
"""

from __future__ import annotations

import asyncio
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sse_starlette import EventSourceResponse
from sse_starlette.sse import ServerSentEvent

from app import db
from app.api.auth import get_current_user
from app.config import get_settings
from app.service import InterviewFinishedError, InterviewNotFoundError

router = APIRouter(
    prefix="/api/interviews", tags=["interviews"], dependencies=[Depends(get_current_user)]
)

SSE_HEADERS = {"X-Accel-Buffering": "no"}  # SPEC §7：关闭代理缓冲


class CreateRequest(BaseModel):
    position: str = Field(min_length=1, max_length=100)
    # 轮次语义（T7a-R1）：全场问答轮次，≥2（1 轮 = 0 技术 + 1 场景无意义）
    question_count: int = Field(default=10, ge=2, le=20)


class MessageRequest(BaseModel):
    content: str = Field(min_length=1, max_length=4000)


@router.post("")
async def create_interview(
    req: CreateRequest, request: Request, user: dict = Depends(get_current_user)
):
    service = request.app.state.service
    interview_id = uuid.uuid4().hex
    await asyncio.to_thread(
        db.create_interview,
        get_settings().db_path,
        interview_id=interview_id,
        position=req.position,
        question_count=req.question_count,
        user_id=user["id"],
    )
    return EventSourceResponse(
        service.start_interview(interview_id, req.position, req.question_count, user_id=user["id"]),
        headers=SSE_HEADERS,
        ping=15,
        ping_message_factory=lambda: ServerSentEvent(comment="ping"),
    )


@router.post("/{interview_id}/messages")
async def send_message(
    interview_id: str, req: MessageRequest, request: Request, user: dict = Depends(get_current_user)
):
    service = request.app.state.service
    if not req.content.strip():
        raise HTTPException(status_code=400, detail="消息内容不能为空")
    try:
        await service.check_can_send(interview_id, user["id"])
    except InterviewNotFoundError as exc:
        raise HTTPException(status_code=404, detail="面试不存在") from exc
    except InterviewFinishedError as exc:
        raise HTTPException(status_code=409, detail="面试已结束") from exc
    return EventSourceResponse(
        service.send_message(interview_id, req.content.strip(), user["id"]),
        headers=SSE_HEADERS,
        ping=15,
        ping_message_factory=lambda: ServerSentEvent(comment="ping"),
    )


@router.get("/{interview_id}")
async def get_interview(
    interview_id: str,
    request: Request,
    user: dict = Depends(get_current_user),
    reconnect: bool = False,
):
    """会话恢复数据（SPEC §7）。reconnect=true：答题中的场次附一句重连问候（P1-M4.7-D）。"""
    service = request.app.state.service
    try:
        return await service.get_session(interview_id, user["id"], reconnect=reconnect)
    except InterviewNotFoundError as exc:
        raise HTTPException(status_code=404, detail="面试不存在") from exc


@router.get("/{interview_id}/report")
async def get_interview_report(
    interview_id: str, request: Request, user: dict = Depends(get_current_user)
):
    service = request.app.state.service
    try:
        row = await service.get_report(interview_id, user["id"])
    except InterviewNotFoundError as exc:  # 不存在与越权同为 404（不泄露存在性）
        raise HTTPException(status_code=404, detail="报告不存在或面试未结束") from exc
    if row is None:
        raise HTTPException(status_code=404, detail="报告不存在或面试未结束")
    return {"interview_id": interview_id, "report": row["payload"], "created_at": row["created_at"]}


@router.get("/{interview_id}/trace")
async def get_interview_trace(
    interview_id: str, request: Request, user: dict = Depends(get_current_user)
):
    """决策回放事件流（FR-21）：未结束的场次同样可查。"""
    service = request.app.state.service
    try:
        return await service.get_trace(interview_id, user["id"])
    except InterviewNotFoundError as exc:
        raise HTTPException(status_code=404, detail="面试不存在") from exc


@router.get("")
async def list_interviews(request: Request, user: dict = Depends(get_current_user)):
    return await request.app.state.service.list_interviews(user["id"])


@router.delete("/{interview_id}", status_code=204)
async def delete_interview(
    interview_id: str, request: Request, user: dict = Depends(get_current_user)
):
    """物理删除场次（T7a-R1）：业务库三表 + checkpointer 线程，不可恢复。"""
    service = request.app.state.service
    try:
        await service.delete_interview(interview_id, user["id"])
    except InterviewNotFoundError as exc:
        raise HTTPException(status_code=404, detail="面试不存在") from exc
