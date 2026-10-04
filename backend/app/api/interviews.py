"""面试路由（SPEC §7 API 契约）：六个端点 + SSE 流。

全部端点需登录（FR-23）：未带有效 token 401；他人场次由 service 按「不存在」404 处理。
SSE 心跳与注释由 EventSourceResponse 自带 ping 机制承担（15s，SPEC §7）；
流开始前的输入校验（401/404/409/400）在 route 层完成，保证 4xx 以普通 JSON 返回。
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field, model_validator
from sse_starlette import EventSourceResponse
from sse_starlette.sse import ServerSentEvent

from app import db, report_pdf
from app.api.auth import get_current_user
from app.config import get_settings
from app.domain import BEHAVIORAL_MAX_QUESTIONS, INTERVIEW_BEHAVIORAL, INTERVIEW_TECH
from app.service import InterviewFinishedError, InterviewNotFoundError
from app.tools import images
from app.tools import recommend
from app.tools import resumes

router = APIRouter(
    prefix="/api/interviews", tags=["interviews"], dependencies=[Depends(get_current_user)]
)

SSE_HEADERS = {"X-Accel-Buffering": "no"}  # SPEC §7：关闭代理缓冲


class CreateRequest(BaseModel):
    position: str = Field(min_length=1, max_length=100)
    # 轮次语义（T7a-R1）：全场问答轮次，≥2（1 轮 = 0 技术 + 1 场景无意义）
    question_count: int = Field(default=10, ge=2, le=20)
    # 难度（P1-M6 FR-14）：adaptive=自适应（默认，从 L1 起升降）；L1/L2/L3=全场锁定该档
    difficulty: Literal["adaptive", "L1", "L2", "L3"] = "adaptive"
    # 会话类型（P1-M11 FR-22）：与技术岗位正交（position 两种类型同一个值）
    interview_type: Literal["tech", "behavioral"] = INTERVIEW_TECH
    # 简历（P2-M11 FR-28）：先经 POST /api/resumes 解析出的 id；缺省 = 没传简历
    # （出题侧按「无简历」走原路径，消息构造逐字一致——零回归是构造性的）
    resume_id: str | None = None

    @model_validator(mode="after")
    def _behavioral_question_limit(self) -> "CreateRequest":
        """行为面题量上限（P1-M11 D1）：题库该域只有十来道，15 题场会当场耗尽走 LLM 兜底。

        前端表单对行为面本就只给 5/10（更早一步拦住用户），这里是 API 面的兜底校验。
        """
        if (
            self.interview_type == INTERVIEW_BEHAVIORAL
            and self.question_count > BEHAVIORAL_MAX_QUESTIONS
        ):
            raise ValueError(f"行为面最多 {BEHAVIORAL_MAX_QUESTIONS} 题")
        return self


class MessageRequest(BaseModel):
    content: str = Field(min_length=1, max_length=4000)
    # 图片通道（P2-M6 FR-26）：随消息附带的 image_id 列表（先经 POST /images 上传）；
    # 文字仍必填——图是回答的补充证据，不单独成答（避免空回答进评分）
    images: list[str] = Field(default_factory=list, max_length=images.MAX_IMAGES_PER_MESSAGE)


@router.post("")
async def create_interview(
    req: CreateRequest, request: Request, user: dict = Depends(get_current_user)
):
    service = request.app.state.service
    interview_id = uuid.uuid4().hex
    db_path = get_settings().db_path
    # 简历（P2-M11 FR-28）：非本人 / 不存在一律 404（同 M7 口径，不泄露存在性）。
    # 解析结果转成 candidate_profile 文本预填——出题侧零改动就变具体。
    candidate_profile = ""
    if req.resume_id:
        resume = await asyncio.to_thread(
            db.get_resume, db_path, user_id=user["id"], resume_id=req.resume_id
        )
        if resume is None:
            raise HTTPException(status_code=404, detail="简历不存在")
        candidate_profile = resumes.format_profile(resume["parsed"])
    await asyncio.to_thread(
        db.create_interview,
        db_path,
        interview_id=interview_id,
        position=req.position,
        question_count=req.question_count,
        difficulty=req.difficulty,
        user_id=user["id"],
        interview_type=req.interview_type,
        resume_id=req.resume_id,
    )
    return EventSourceResponse(
        service.start_interview(
            interview_id, req.position, req.question_count, user_id=user["id"],
            difficulty=req.difficulty, interview_type=req.interview_type,
            candidate_profile=candidate_profile, resume_id=req.resume_id or "",
        ),
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
    if req.images:
        # 图必须已上传且属于本场（id 只从本场目录解析）：失效/伪造一律 400，不静默丢图
        settings = get_settings()
        for image_id in req.images:
            path = await asyncio.to_thread(
                images.image_path, settings.upload_dir, interview_id, image_id
            )
            if path is None:
                raise HTTPException(status_code=400, detail="图片不存在或已失效，请重新上传")
    return EventSourceResponse(
        service.send_message(interview_id, req.content.strip(), user["id"], images=req.images),
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


@router.get("/{interview_id}/report.pdf")
async def export_interview_report_pdf(
    interview_id: str, request: Request, user: dict = Depends(get_current_user)
):
    """报告导出 PDF（FR-18）：与报告端点同一份 payload、同一套 404 语义。

    每次现渲染、不落盘缓存——报告本身不可变，且渲染是纯 CPU 的确定性转换
    （阻塞主循环，故丢线程池）；LLM 那份 60s 的耗时预算属于报告生成，不在这一步。
    """
    service = request.app.state.service
    try:
        row = await service.get_report(interview_id, user["id"])
    except InterviewNotFoundError as exc:
        raise HTTPException(status_code=404, detail="报告不存在或面试未结束") from exc
    if row is None:
        raise HTTPException(status_code=404, detail="报告不存在或面试未结束")
    pdf = await asyncio.to_thread(report_pdf.render_report_pdf, row["payload"], row["created_at"])
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={
            "Content-Disposition": report_pdf.content_disposition(interview_id, row["created_at"])
        },
    )


@router.get("/{interview_id}/recommendations")
async def get_interview_recommendations(
    interview_id: str, request: Request, user: dict = Depends(get_current_user)
):
    """学习推荐（FR-20）：报告短板域 → 混合检索资料卡片。

    与报告端点同一 404 判据（未结束/不存在/越权），因为推荐读的就是报告 payload
    （weaknesses + 逐题漏点）；检索失败不吞——500 透传，前端给错误态 + 重试。

    行为面（P1-M11 D5）：推荐检索的是六大技术域，行为面场次没有可推的域——
    直接返回空分组，不做无效检索（前端也不渲染推荐卡）。
    """
    service = request.app.state.service
    try:
        row = await service.get_report(interview_id, user["id"])
    except InterviewNotFoundError as exc:
        raise HTTPException(status_code=404, detail="报告不存在或面试未结束") from exc
    if row is None:
        raise HTTPException(status_code=404, detail="报告不存在或面试未结束")
    payload = row["payload"]
    if payload.get("interview_type") == INTERVIEW_BEHAVIORAL:
        return {
            "interview_id": interview_id,
            "position": payload.get("position", ""),
            "groups": [],
        }
    groups = await recommend.recommend_for_report(payload)
    return {
        "interview_id": interview_id,
        "position": payload.get("position", ""),
        "groups": groups,
    }


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
