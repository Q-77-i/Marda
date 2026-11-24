"""J6-C：Redis 客户端工厂（Sentinel / 单机）的解析与分派。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.redis_client import (
    database_from_url,
    make_redis_client,
    parse_sentinel_nodes,
)


def test_解析哨兵节点_默认端口():
    assert parse_sentinel_nodes("sentinel-1:26379, sentinel-2") == [
        ("sentinel-1", 26379),
        ("sentinel-2", 26379),
    ]


def test_解析空节点报错():
    with pytest.raises(ValueError):
        parse_sentinel_nodes(" , ")


def test_从URL取逻辑库():
    assert database_from_url("redis://redis:6379/0") == 0
    assert database_from_url("redis://redis:6379/1") == 1
    assert database_from_url("redis://redis:6379") == 0


def test_sentinel_模式选master并带db(monkeypatch):
    calls: dict = {}

    class FakeSentinel:
        def __init__(self, nodes, **kwargs):
            calls["nodes"] = nodes
            calls["sentinel_kwargs"] = kwargs

        def master_for(self, master, **kwargs):
            calls["master"] = master
            calls["master_kwargs"] = kwargs
            return object()

    monkeypatch.setattr("redis.asyncio.sentinel.Sentinel", FakeSentinel)
    settings = SimpleNamespace(
        redis_sentinel_nodes="sentinel-1:26379,sentinel-2:26379",
        redis_sentinel_master="marda-master",
    )
    client = make_redis_client(
        settings, url="redis://redis:6379/1", socket_timeout=0.2
    )
    assert client is not None
    assert calls["nodes"] == [("sentinel-1", 26379), ("sentinel-2", 26379)]
    assert calls["master"] == "marda-master"
    assert calls["master_kwargs"]["db"] == 1
    assert calls["master_kwargs"]["socket_timeout"] == 0.2


async def test_单机模式退回URL():
    settings = SimpleNamespace(
        redis_sentinel_nodes="", redis_sentinel_master="marda-master"
    )
    client = make_redis_client(settings, url="redis://127.0.0.1:6379/0")
    try:
        assert client.connection_pool.connection_kwargs["db"] == 0
    finally:
        await client.aclose()
