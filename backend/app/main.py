import logging
import os
import socket
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from opentelemetry import trace as otel_trace

from app import logging_setup, metrics, tracing
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

# J8：容器里切 JSON 日志（默认文本，测试与本地开发不受影响）。
logging_setup.configure()

# 实例标识（J6-A）：多实例演练时证明「请求真的落到了不同实例」。
# 只做诊断，不是 API 契约；可用 INSTANCE_HEADER_ENABLED=false 关掉。
INSTANCE_ID = f"{socket.gethostname()}:{os.getpid()}"


@app.middleware("http")
async def _instance_header(request: Request, call_next):
    response = await call_next(request)
    if get_settings().instance_header_enabled:
        response.headers["X-Marda-Instance"] = INSTANCE_ID
    return response


@app.middleware("http")
async def _metrics_middleware(request: Request, call_next):
    """J8：按路由模板记录 HTTP 请求与耗时（低基数；SSE 只计到响应头）。

    访问日志也在这里出：uvicorn 自带的 access logger 在中间件之外，
    拿不到 request_id / trace_id 上下文。
    """
    started = time.perf_counter()
    status = 500
    try:
        response = await call_next(request)
        status = response.status_code
        return response
    finally:
        route = request.scope.get("route")
        path = getattr(route, "path", None) or "unmatched"
        duration = time.perf_counter() - started
        metrics.observe_http(request.method, path, status, duration)
        logging.getLogger("app.access").info(
            "access method=%s path=%s route=%s status=%s duration_ms=%.1f client=%s",
            request.method,
            request.url.path,
            path,
            status,
            duration * 1000,
            request.client.host if request.client else "",
        )


@app.middleware("http")
async def _request_context(request: Request, call_next):
    """J8：request_id / trace_id 进日志上下文（最外层，覆盖下游与指标中间件）。"""
    request_id = request.headers.get("X-Request-Id") or uuid.uuid4().hex
    # 优先 OTel 当前 span（tracing 启用时 OTel middleware 在最外层）；
    # 否则退回 traceparent 头 / 本地随机 id（只用于日志串联）。
    span_context = otel_trace.get_current_span().get_span_context()
    trace_id = (
        format(span_context.trace_id, "032x")
        if span_context.is_valid
        else logging_setup.parse_trace_id(request.headers.get("traceparent"))
        or uuid.uuid4().hex
    )
    tokens = (
        logging_setup.request_id_var.set(request_id),
        logging_setup.trace_id_var.set(trace_id),
        logging_setup.instance_var.set(INSTANCE_ID),
    )
    try:
        response = await call_next(request)
        response.headers["X-Request-Id"] = request_id
        return response
    finally:
        logging_setup.instance_var.reset(tokens[2])
        logging_setup.trace_id_var.reset(tokens[1])
        logging_setup.request_id_var.reset(tokens[0])


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


@app.get("/metrics")
def metrics_endpoint() -> Response:
    """Prometheus 指标（J8）：仅容器网络可达，不鉴权、不走网关外部路由。"""
    return Response(metrics.render_latest(), media_type=metrics.content_type)


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


# J8-B：最后添加 = 最外层——span 覆盖所有自定义中间件，日志才能取到 trace_id。
# OTLP 未配置时为空操作；配置后由 collector 做 tail sampling。
tracing.configure(app)
