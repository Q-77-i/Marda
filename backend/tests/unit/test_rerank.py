"""rerank 客户端单测：httpx MockTransport 注入，覆盖请求形状/响应解析/校验/失败上抛。

不打真实 SiliconFlow（计费 API，交给探针真调验证）。
"""

from __future__ import annotations

import json

import httpx
import pytest

from app.tools import rerank


@pytest.fixture(autouse=True)
def _test_env(monkeypatch):
    """不依赖仓库 .env（get_settings 需要假密钥）。"""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setenv("SILICONFLOW_API_KEY", "test-key")
    monkeypatch.setenv("JWT_SECRET", "t" * 32)
    rerank.get_settings.cache_clear()
    yield
    rerank.get_settings.cache_clear()


def _service(
    sink: list[tuple[httpx.Request, dict]],
    *,
    results: list[dict] | None = None,
    status: int = 200,
) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if status != 200:
            return httpx.Response(status)
        body = {"id": "rerank-x", "results": results or []}
        sink.append((request, body))
        return httpx.Response(200, json=body)

    return httpx.MockTransport(handler)


async def test_请求形状():
    sink: list = []
    client = rerank.rerank(
        "query 文本", ["doc1", "doc2"], top_n=2, base_url="http://sf:8000/v1", api_key="k123", transport=_service(sink, results=[{"index": 1, "relevance_score": 0.9}, {"index": 0, "relevance_score": 0.5}]),
    )
    out = await client

    request = sink[0][0]
    assert request.method == "POST"
    assert request.url.path == "/v1/rerank"
    assert request.headers["authorization"] == "Bearer k123"
    assert request.headers["content-type"] == "application/json"
    body = json.loads(request.content)
    assert body == {
        "model": rerank.RERANK_MODEL,
        "query": "query 文本",
        "documents": ["doc1", "doc2"],
        "top_n": 2,
        "return_documents": False,
    }
    assert out == [(1, 0.9), (0, 0.5)]  # 返回顺序与响应一致（服务端已按分降序）


async def test_top_n为None不传该字段():
    sink: list = []
    client = rerank.rerank(
        "q", ["d"], base_url="http://sf:8000/v1", api_key="k", transport=_service(sink, results=[{"index": 0, "relevance_score": 0.1}]),
    )
    await client

    body = json.loads(sink[0][0].content)
    assert "top_n" not in body


async def test_默认取配置的_base_url与密钥():
    sink: list = []
    client = rerank.rerank(
        "q", ["d"], transport=_service(sink, results=[{"index": 0, "relevance_score": 0.1}]),
    )
    await client

    request = sink[0][0]
    assert request.url.host == "api.siliconflow.cn"
    assert request.headers["authorization"] == "Bearer test-key"


async def test_index越界报错():
    client = rerank.rerank("q", ["d1", "d2"], base_url="http://sf", api_key="k", transport=_service([], results=[{"index": 2, "relevance_score": 0.9}]))
    with pytest.raises(RuntimeError, match="index"):
        await client


async def test_index重复报错():
    client = rerank.rerank("q", ["d1", "d2"], base_url="http://sf", api_key="k", transport=_service([], results=[{"index": 1, "relevance_score": 0.9}, {"index": 1, "relevance_score": 0.8}]))
    with pytest.raises(RuntimeError, match="index"):
        await client


async def test_score非数值报错():
    client = rerank.rerank("q", ["d1"], base_url="http://sf", api_key="k", transport=_service([], results=[{"index": 0, "relevance_score": "high"}]))
    with pytest.raises(RuntimeError, match="score"):
        await client


async def test_results缺失或空报错():
    client = rerank.rerank("q", ["d1"], base_url="http://sf", api_key="k", transport=_service([], results=[]))
    with pytest.raises(RuntimeError, match="rerank"):
        await client


async def test_http错误上抛():
    client = rerank.rerank("q", ["d1"], base_url="http://sf", api_key="k", transport=_service([], status=500))
    with pytest.raises(httpx.HTTPStatusError):
        await client


async def test_空documents不发请求():
    sink: list = []
    client = rerank.rerank("q", [], base_url="http://sf", api_key="k", transport=_service(sink))
    assert await client == []
    assert sink == []
