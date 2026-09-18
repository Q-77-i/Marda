from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.interviews import router as interviews_router
from app.config import get_settings
from app.service import Service


@asynccontextmanager
async def lifespan(app: FastAPI):
    """service 单例：checkpointer 连接 + 编译图，随进程起停。"""
    service = Service(get_settings())
    await service.start()
    app.state.service = service
    yield
    await service.close()


app = FastAPI(title="Marda 码达 API", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(interviews_router)


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok"}
