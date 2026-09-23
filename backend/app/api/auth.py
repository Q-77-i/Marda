"""账号路由（FR-23，SPEC §7）：注册 / 登录 / 当前用户 + 鉴权依赖。

鉴权口径：Bearer JWT（HS256）。未登录或 token 失效统一 401；
跨用户访问他人资源在业务侧按「不存在」404 处理（不泄露存在性，见 service）。
"""

from __future__ import annotations

import asyncio
import uuid
from functools import lru_cache

from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field, field_validator

from app import db, security
from app.config import get_settings

router = APIRouter(prefix="/api/auth", tags=["auth"])

_bearer = HTTPBearer(auto_error=False)  # auto_error=False：401 由本模块统一给（默认是 403）

USERNAME_PATTERN = r"^[A-Za-z0-9_]+$"


class Credentials(BaseModel):
    """注册/登录共用：用户名统一小写（大小写不敏感，与 users.username 的 UNIQUE 口径一致）。"""

    username: str = Field(min_length=3, max_length=32, pattern=USERNAME_PATTERN)
    password: str = Field(min_length=6, max_length=72)

    @field_validator("username", mode="before")
    @classmethod
    def _normalize(cls, value: object) -> object:
        return value.strip().lower() if isinstance(value, str) else value


def _unauthorized() -> HTTPException:
    return HTTPException(
        status_code=401, detail="未登录或登录已过期", headers={"WWW-Authenticate": "Bearer"}
    )


@lru_cache
def _dummy_hash() -> str:
    """登录时序均衡用的哈希（首次调用算一次并缓存）。"""
    return security.hash_password("timing-equalizer")


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> dict:
    """鉴权依赖（面试路由全端点挂载）：token → users 行；用户被删同样按未登录处理。"""
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise _unauthorized()
    user_id = security.decode_token(credentials.credentials, get_settings().jwt_secret)
    if user_id is None:
        raise _unauthorized()
    row = await asyncio.to_thread(db.get_user, get_settings().db_path, user_id)
    if row is None:
        raise _unauthorized()
    return {"id": row["id"], "username": row["username"]}


@router.post("/register", status_code=201)
async def register(req: Credentials) -> dict:
    user_id = uuid.uuid4().hex
    created = await asyncio.to_thread(
        db.create_user,
        get_settings().db_path,
        user_id=user_id,
        username=req.username,
        password_hash=security.hash_password(req.password),
    )
    if not created:
        raise HTTPException(status_code=409, detail="用户名已存在")
    # 阶段 1 的历史场次无归属：首个注册账号认领（后续账号注册时已无 NULL 行，认领 0 条）
    await asyncio.to_thread(db.claim_orphan_interviews, get_settings().db_path, user_id)
    return {
        "token": security.encode_token(user_id, get_settings().jwt_secret),
        "username": req.username,
    }


@router.post("/login")
async def login(req: Credentials) -> dict:
    row = await asyncio.to_thread(db.get_user_by_username, get_settings().db_path, req.username)
    # 恒定时序：账号不存在也走一次同参数 scrypt —— 文案一致但耗时不同同样会泄露账号是否注册
    if not security.verify_password(
        req.password, row["password_hash"] if row else _dummy_hash()
    ) or row is None:
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    return {
        "token": security.encode_token(row["id"], get_settings().jwt_secret),
        "username": row["username"],
    }


@router.get("/me")
async def me(user: dict = Depends(get_current_user)) -> dict:
    return user
