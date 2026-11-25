"""Redis 客户端工厂（J6-C）：单机 URL 或 Sentinel 主从，调用方不感知。

为什么单独抽一份：协调层（锁/限流/LLM 槽）与缓存都连 Redis，但逻辑库不同
（/0 vs /1）；Sentinel 模式下两者都走同一个 master 名，只是 db 不同。
把节点解析、db 解析放在一处，避免「协调层支持 Sentinel、缓存不支持」的半套高可用。

失败语义不变：
- 连接不上就是连接不上；锁 fail closed、限流/缓存 fail open 由调用方决定，不在工厂里藏。
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from app.config import Settings


def parse_sentinel_nodes(raw: str) -> list[tuple[str, int]]:
    """`host:port,host:port` → [(host, port)]；空串应在上层判断，不传进来。"""
    nodes: list[tuple[str, int]] = []
    for part in raw.split(","):
        item = part.strip()
        if not item:
            continue
        host, sep, port = item.rpartition(":")
        if not sep:
            host, port = item, "26379"
        nodes.append((host, int(port)))
    if not nodes:
        raise ValueError("REDIS_SENTINEL_NODES 非空但没有可解析的节点")
    return nodes


def database_from_url(url: str) -> int:
    """从 `redis://host:6379/1` 取逻辑库编号（Sentinel master_for 需要显式 db）。"""
    path = urlparse(url).path.lstrip("/")
    return int(path) if path else 0


def make_redis_client(
    settings: Settings,
    *,
    url: str,
    decode_responses: bool = False,
    socket_timeout: float | None = None,
    socket_connect_timeout: float | None = None,
) -> Any:
    """按配置返回 Redis 客户端。

    `REDIS_SENTINEL_NODES` 非空 → Sentinel（master 名取 `REDIS_SENTINEL_MASTER`）；
    否则退回单机 `url`。逻辑库编号从 url 的 path 取，Sentinel 与单机语义一致。
    """
    if settings.redis_sentinel_nodes.strip():
        from redis.asyncio.sentinel import Sentinel

        kwargs: dict[str, Any] = {"decode_responses": decode_responses}
        if socket_timeout is not None:
            kwargs["socket_timeout"] = socket_timeout
        if socket_connect_timeout is not None:
            kwargs["socket_connect_timeout"] = socket_connect_timeout
        sentinel = Sentinel(
            parse_sentinel_nodes(settings.redis_sentinel_nodes),
            socket_timeout=socket_timeout,
            socket_connect_timeout=socket_connect_timeout,
        )
        return sentinel.master_for(
            settings.redis_sentinel_master,
            db=database_from_url(url),
            **kwargs,
        )

    from redis.asyncio import Redis

    kwargs = {"decode_responses": decode_responses}
    if socket_timeout is not None:
        kwargs["socket_timeout"] = socket_timeout
    if socket_connect_timeout is not None:
        kwargs["socket_connect_timeout"] = socket_connect_timeout
    return Redis.from_url(url, **kwargs)
