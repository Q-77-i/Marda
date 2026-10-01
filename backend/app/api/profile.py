"""能力档案路由（FR-19 / SPEC §7）。

登录用户级、**无场次参数**：档案看的是「我的全部场次」，隔离由 service 层的
user_id 过滤承担，因此没有 404/越权面（对照面试各端点的 owner 校验）。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from app.api.auth import get_current_user

router = APIRouter(prefix="/api/profile", tags=["profile"], dependencies=[Depends(get_current_user)])


@router.get("")
async def get_profile(request: Request, user: dict = Depends(get_current_user)):
    """多场得分曲线与短板变化（FR-19）。

    数据源 = 该用户已落库的报告 payload：不重算分数、不落库、零 LLM 调用；
    没有场次时返回零态结构（不是 404）——「还没有数据」是正常状态，前端据 session_count
    渲染空态 + 引导，而不是当成错误。
    """
    return await request.app.state.service.get_profile(user["id"])
