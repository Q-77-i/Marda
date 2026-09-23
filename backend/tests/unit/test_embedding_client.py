"""EmbeddingClient 单测：httpx MockTransport 注入，覆盖分批保序/键转换/形状校验。

不打真实嵌入服务（模型推理重，交给容器 smoke 验证）。
"""

from __future__ import annotations

import json
from urllib.parse import urlparse

import httpx
import pytest

from app.tools import embedding


@pytest.fixture(autouse=True)
def _test_env(monkeypatch):
    """不依赖仓库 .env（get_settings 需要假密钥）。"""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setenv("SILICONFLOW_API_KEY", "test-key")
    monkeypatch.setenv("JWT_SECRET", "t" * 32)
    embedding.get_settings.cache_clear()
    yield
    embedding.get_settings.cache_clear()


def _service(
    sink: list[list[str]],
    *,
    dim: int = 4,
    drop: int = 0,
    bad_dim: int | None = None,
    status: int = 200,
) -> httpx.MockTransport:
    """假嵌入服务：dense 首维取文本长度（便于验证保序），sparse 一个 token。"""

    def handler(request: httpx.Request) -> httpx.Response:
        if status != 200:
            return httpx.Response(status)
        texts = json.loads(request.content)["texts"]
        sink.append(texts)
        kept = texts[: len(texts) - drop]
        vectors = [[float(len(t))] * dim for t in kept]
        if bad_dim is not None and vectors:
            vectors[0] = vectors[0][:bad_dim]
        return httpx.Response(
            200,
            json={"dense": vectors, "sparse": [{str(100 + len(t)): 0.5} for t in kept], "dim": dim},
        )

    return httpx.MockTransport(handler)


async def test_分批保序():
    sink: list[list[str]] = []
    texts = [f"t{i}" for i in range(130)]  # 64 + 64 + 2
    client = embedding.EmbeddingClient("http://embed:8091", transport=_service(sink))

    out = await client.embed(texts)

    assert [len(batch) for batch in sink] == [64, 64, 2]
    assert len(out) == 130
    assert [e.dense[0] for e in out] == [float(len(t)) for t in texts]  # 顺序与输入一致


async def test_sparse_键转_int():
    client = embedding.EmbeddingClient("http://embed:8091", transport=_service([]))

    out = await client.embed(["abc"])

    assert out[0].sparse == {103: 0.5}
    assert all(isinstance(k, int) for k in out[0].sparse)


async def test_空输入不发请求():
    sink: list[list[str]] = []
    client = embedding.EmbeddingClient("http://embed:8091", transport=_service(sink))

    assert await client.embed([]) == []
    assert sink == []


async def test_条数不一致报错():
    client = embedding.EmbeddingClient("http://embed:8091", transport=_service([], drop=1))

    with pytest.raises(RuntimeError, match="期望 3 条"):
        await client.embed(["a", "b", "c"])


async def test_维度不一致报错():
    client = embedding.EmbeddingClient("http://embed:8091", transport=_service([], dim=4, bad_dim=3))

    with pytest.raises(RuntimeError, match="dense 维度 3"):
        await client.embed(["a"])


async def test_http_错误上抛():
    client = embedding.EmbeddingClient("http://embed:8091", transport=_service([], status=503))

    with pytest.raises(httpx.HTTPStatusError):
        await client.embed(["a"])


async def test_默认取配置且归一末尾斜杠():
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, json={"dense": [[1.0]], "sparse": [{"1": 1.0}], "dim": 1})

    client = embedding.EmbeddingClient("http://embed:8091/", transport=httpx.MockTransport(handler))
    await client.embed(["a"])
    assert seen == ["http://embed:8091/embed"]  # 无重复斜杠

    configured = embedding.EmbeddingClient(transport=httpx.MockTransport(handler))
    await configured.embed(["a"])
    host = urlparse(embedding.get_settings().embedding_url).netloc
    assert urlparse(seen[-1]).netloc == host
