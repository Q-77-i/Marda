"""图片通道路由（P2-M6 FR-26）：上传 / 取回。

两段式交互：先 `POST .../images` 拿 image_id，再随消息发 id 列表
（`POST .../messages {content, images}`）。这样重试语义「重发同一条消息」天然复用
同一批 id——不会重复落盘（multipart 消息请求的写法会在每次重试时重传文件）。

归属与状态口径与消息端点一致：不存在/他人场次一律 404（不泄露存在性）、已结束 409；
取回端点只校验归属（已结束场次仍要能看回放里的图）。
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, File, HTTPException, Request, Response, UploadFile

from app.api.auth import get_current_user
from app.config import get_settings
from app.service import InterviewFinishedError, InterviewNotFoundError
from app.tools import images

router = APIRouter(
    prefix="/api/interviews", tags=["images"], dependencies=[Depends(get_current_user)]
)

_EXT_TO_MIME = {"png": "image/png", "jpg": "image/jpeg", "webp": "image/webp"}


@router.post("/{interview_id}/images", status_code=201)
async def upload_image(
    interview_id: str,
    request: Request,
    file: UploadFile = File(...),
    user: dict = Depends(get_current_user),
):
    """上传一张面试图片（候选人的代码截图/架构图），返回 image_id。"""
    service = request.app.state.service
    try:
        await service.check_can_send(interview_id, user["id"])  # 404 / 409 与消息端点同口径
    except InterviewNotFoundError as exc:
        raise HTTPException(status_code=404, detail="面试不存在") from exc
    except InterviewFinishedError as exc:
        raise HTTPException(status_code=409, detail="面试已结束") from exc

    settings = get_settings()
    # 上限校验在路由层：save_image 保持单一职责（校验 + 落盘）。数量读目录是同步 IO，小目录开销可忽略
    if await asyncio.to_thread(images.count_images, settings.upload_dir, interview_id) >= (
        images.MAX_IMAGES_PER_INTERVIEW
    ):
        raise HTTPException(status_code=400, detail="本场面试的图片数量已达上限")
    data = await file.read()  # UploadFile 超过 1MB 即落临时文件，内存中只是这一份拷贝
    if len(data) > images.MAX_IMAGE_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"图片超过 {images.MAX_IMAGE_BYTES // (1024 * 1024)}MB 上限",
        )
    try:
        image_id = await asyncio.to_thread(
            images.save_image, settings.upload_dir, interview_id, data
        )
    except images.ImageError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"image_id": image_id}


@router.get("/{interview_id}/images/{image_id}")
async def get_image(
    interview_id: str,
    image_id: str,
    request: Request,
    user: dict = Depends(get_current_user),
):
    """取回一张图（面试页气泡与回放渲染用）；他人场次/不存在/坏 id 一律 404。"""
    service = request.app.state.service
    try:
        await service.require_owner(interview_id, user["id"])
    except InterviewNotFoundError as exc:
        raise HTTPException(status_code=404, detail="面试不存在") from exc
    path = await asyncio.to_thread(
        images.image_path, get_settings().upload_dir, interview_id, image_id
    )
    if path is None:
        raise HTTPException(status_code=404, detail="图片不存在")
    data = await asyncio.to_thread(path.read_bytes)
    return Response(
        content=data,
        media_type=_EXT_TO_MIME.get(path.suffix.lstrip("."), "application/octet-stream"),
        headers={
            "Cache-Control": "private, max-age=3600",  # 带鉴权的内容不许共享缓存
            "X-Content-Type-Options": "nosniff",
        },
    )
