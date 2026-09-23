"""Marda 嵌入服务：本地 BGE-M3 → dense + sparse（M3 混合检索底座）。

独立容器（引擎与模型解耦）：调用方（ingest / hybrid_search）只认 POST /embed 契约，
换模型不动业务代码。模型进程内常驻，启动时加载一次。
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import Any

import numpy as np
from fastapi import FastAPI, HTTPException
from FlagEmbedding import BGEM3FlagModel
from pydantic import BaseModel, Field

MODEL_NAME = os.getenv("EMBEDDING_MODEL", "BAAI/bge-m3")
MAX_BATCH = 128  # 单请求文本数上限：CPU 一次推太多会顶爆内存
MAX_LENGTH = 512  # 题库 doc 最长约 300 token（题干 + 关键点），不截断
INNER_BATCH = 8

_state: dict[str, Any] = {}


@asynccontextmanager
async def lifespan(_: FastAPI):
    _state["model"] = BGEM3FlagModel(MODEL_NAME, use_fp16=False)  # CPU 上 fp16 反而更慢
    yield
    _state.clear()


app = FastAPI(title="Marda Embedding Service", version="0.1.0", lifespan=lifespan)


class EmbedRequest(BaseModel):
    texts: list[str] = Field(min_length=1)


class EmbedResponse(BaseModel):
    dense: list[list[float]]  # L2 归一（cosine 与内积同口径）
    sparse: list[dict[str, float]]  # token_id(str) → 权重，已剔零、按 token_id 升序
    dim: int


@app.get("/healthz")
def healthz() -> dict[str, Any]:
    return {"status": "ok", "model": MODEL_NAME, "loaded": "model" in _state}


@app.post("/embed", response_model=EmbedResponse)
def embed(request: EmbedRequest) -> EmbedResponse:
    """同步 def：CPU 推理阻塞，交 FastAPI 线程池跑，不堵事件循环（/healthz 仍可响应）。"""
    model = _state.get("model")
    if model is None:
        raise HTTPException(status_code=503, detail="模型未加载")
    if len(request.texts) > MAX_BATCH:
        raise HTTPException(status_code=413, detail=f"单请求文本数上限 {MAX_BATCH}")

    out = model.encode(
        request.texts,
        batch_size=INNER_BATCH,
        max_length=MAX_LENGTH,
        return_dense=True,
        return_sparse=True,
        return_colbert_vecs=False,
    )
    dense = np.asarray(out["dense_vecs"], dtype=np.float32)
    dense = dense / np.maximum(np.linalg.norm(dense, axis=1, keepdims=True), 1e-12)
    sparse = [
        {str(token): float(weight) for token, weight in sorted(weights.items()) if weight}
        for weights in out["lexical_weights"]
    ]
    return EmbedResponse(dense=dense.tolist(), sparse=sparse, dim=int(dense.shape[1]))
