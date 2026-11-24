import os
import socket
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.auth import router as auth_router
from app.api.bank import router as bank_router
from app.api.bank_private import router as bank_private_router
from app.api.coding import router as coding_router
from app.api.images import router as images_router
from app.api.interviews import router as interviews_router
from app.api.profile import router as profile_router
from app.api.resumes import router as resumes_router
from app.api.voice import router as voice_router
from app.config import get_settings
from app.graph.checkpointer import ensure_compatible_event_loop
from app.service import Service

# Windows 上 PG checkpointer（官方 saver 是异步的）需要 Selector 事件循环。
# 必须在**事件循环被创建之前**调用——策略只对新建的 loop 生效，
# 而 uvicorn 是在 `asyncio.run` 之前 import 本模块的。Linux 不受影响（默认就是 Selector）。
ensure_compatible_event_loop()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """service 单例：checkpointer 连接 + 编译图，随进程起停。"""
    service = Service(get_settings())
    await service.start()
    app.state.service = service
    yield
    await service.close()


app = FastAPI(title="Marda 码达 API", version="0.1.0", lifespan=lifespan)

# 实例标识（J6-A）：多实例演练时证明「请求真的落到了不同实例」。
# 只做诊断，不是 API 契约；可用 INSTANCE_HEADER_ENABLED=false 关掉。
INSTANCE_ID = f"{socket.gethostname()}:{os.getpid()}"


@app.middleware("http")
async def _instance_header(request: Request, call_next):
    response = await call_next(request)
    if get_settings().instance_header_enabled:
        response.headers["X-Marda-Instance"] = INSTANCE_ID
    return response


app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router)
app.include_router(interviews_router)
app.include_router(bank_router)
app.include_router(bank_private_router)
app.include_router(profile_router)
app.include_router(voice_router)  # 语音（P2-M5 FR-24）：WS /api/asr + POST /api/tts
app.include_router(images_router)  # 图片通道（P2-M6 FR-26）：上传 / 取回面试截图
app.include_router(resumes_router)  # 简历（P2-M11 FR-28）：解析 → resume_id
app.include_router(coding_router)  # 代码工作台（M15）：随机算法题 / 点评 / 追问


@app.get("/healthz")
def healthz() -> dict:
    """liveness：进程活着就 200（与依赖无关，别把探针变成依赖检查）。"""
    return {"status": "ok"}


@app.get("/readyz")
async def readyz(request: Request):
    """readiness：业务库 + checkpoint 库都通才 200；否则 503（摘流用）。"""
    service = getattr(request.app.state, "service", None)
    if service is None or not await service.is_ready():
        return JSONResponse({"status": "unready"}, status_code=503)
    return {"status": "ok"}
