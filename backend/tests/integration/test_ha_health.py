"""J6-A：readiness 与实例标识。

readiness 只查业务库 + checkpoint 库；Redis 挂了仍应 ready（缓存/协调故障降级，
不是「整个 API 不能服务」）。
"""

from __future__ import annotations

from app import db


async def test_readyz_正常时200(client):
    response = await client.get("/readyz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_healthz_带实例标识(client):
    """多实例演练靠这个头证明请求落到了不同实例；它是诊断信息，不是契约。"""
    response = await client.get("/healthz")
    assert response.headers["x-marda-instance"]
    assert ":" in response.headers["x-marda-instance"]


async def test_readyz_业务库不可用时503(client, monkeypatch):
    def _boom(target):
        raise RuntimeError("db down")

    monkeypatch.setattr(db, "ping", _boom)
    response = await client.get("/readyz")
    assert response.status_code == 503
    assert response.json() == {"status": "unready"}
