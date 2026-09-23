"""账号体系集成测试（FR-23，SPEC §7）：注册/登录/鉴权/多用户隔离。

验收口径（PRD §8.1）：未登录 401；A 看不到 B 的场次（全端点 404）；
历史孤儿场次归属首个注册账号。全部离线（FakeLLM + tmp 库）。
"""

from __future__ import annotations

import json
from datetime import timedelta

import pytest

from app import db, security
from app.config import get_settings


async def _events(response) -> list[dict]:
    """逐行解析 SSE 流（同 test_api 口径）。"""
    out, current = [], {}
    async for line in response.aiter_lines():
        line = line.rstrip("\r")
        if not line:
            if current:
                out.append(current)
                current = {}
            continue
        if line.startswith("event: "):
            current["event"] = line[len("event: "):]
        elif line.startswith("data: "):
            current["data"] = json.loads(line[len("data: "):])
    return out


async def _create(client, question_count: int = 2) -> str:
    """建一场面试（SSE 开场流跑完），返回 interview_id。"""
    async with client.stream(
        "POST", "/api/interviews",
        json={"position": "Agent/AI 工程师", "question_count": question_count},
    ) as r:
        assert r.status_code == 200, r.text
        events = await _events(r)
    return events[0]["data"]["interview_id"]


# ---------- 注册 / 登录 ----------


async def test_注册返回token且用户名小写存储(anon_client):
    r = await anon_client.post(
        "/api/auth/register", json={"username": "Alice", "password": "secret123"}
    )
    assert r.status_code == 201
    body = r.json()
    assert body["username"] == "alice"  # 统一小写：大小写不敏感（UNIQUE 默认敏感）
    assert body["token"]
    assert "password" not in body and "password_hash" not in body  # 不回显凭据


async def test_重名注册409且大小写不敏感(anon_client, register_user):
    await register_user(anon_client, username="alice")
    r = await anon_client.post(
        "/api/auth/register", json={"username": "ALICE", "password": "secret123"}
    )
    assert r.status_code == 409


@pytest.mark.parametrize(
    "payload",
    [
        {"username": "ab", "password": "secret123"},  # 用户名过短
        {"username": "a" * 33, "password": "secret123"},  # 用户名过长
        {"username": "bad name", "password": "secret123"},  # 非法字符
        {"username": "okname", "password": "12345"},  # 密码过短
    ],
)
async def test_注册参数校验422(anon_client, payload):
    r = await anon_client.post("/api/auth/register", json=payload)
    assert r.status_code == 422


async def test_登录成功_密码错误与账号不存在同为401(anon_client, register_user):
    await register_user(anon_client, username="alice")
    # 大小写不敏感：Alice 可登录 alice 账号
    r = await anon_client.post(
        "/api/auth/login", json={"username": "Alice", "password": "secret123"}
    )
    assert r.status_code == 200 and r.json()["token"]

    wrong = await anon_client.post(
        "/api/auth/login", json={"username": "alice", "password": "wrong123"}
    )
    nobody = await anon_client.post(
        "/api/auth/login", json={"username": "nobody", "password": "secret123"}
    )
    assert wrong.status_code == nobody.status_code == 401
    assert wrong.json()["detail"] == nobody.json()["detail"]  # 不泄露账号是否存在（文案）


async def test_账号不存在也走一次哈希比对(anon_client, monkeypatch):
    """时序均衡：不存在账号的路径不能提前返回（否则可用响应耗时探测账号是否注册）。

    计时断言在 CI 上必然抖，改为钉住行为——比对函数必须被调用且只调用一次。
    """
    calls: list[str] = []
    real = security.verify_password

    def _spy(password: str, stored: str) -> bool:
        calls.append(stored)
        return real(password, stored)

    monkeypatch.setattr(security, "verify_password", _spy)
    r = await anon_client.post(
        "/api/auth/login", json={"username": "nobody", "password": "secret123"}
    )
    assert r.status_code == 401
    assert len(calls) == 1, "不存在账号的登录路径跳过了哈希比对（时序泄漏）"


async def test_me需鉴权(anon_client, register_user):
    assert (await anon_client.get("/api/auth/me")).status_code == 401
    r = await anon_client.get("/api/auth/me", headers={"Authorization": "Bearer garbage"})
    assert r.status_code == 401
    token = await register_user(anon_client, username="alice")
    r = await anon_client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert r.json()["id"] and r.json()["username"] == "alice"


async def test_过期token401(anon_client):
    expired = security.encode_token("u-x", get_settings().jwt_secret, ttl=timedelta(seconds=-1))
    r = await anon_client.get("/api/auth/me", headers={"Authorization": f"Bearer {expired}"})
    assert r.status_code == 401


async def test_未登录访问面试接口全部401(anon_client):
    """六个端点（含两个 SSE）：鉴权在流开始前完成，401 以普通 JSON 返回。"""
    cases = [
        ("get", "/api/interviews", None),
        ("post", "/api/interviews", {"position": "x", "question_count": 2}),
        ("get", "/api/interviews/iv-1", None),
        ("post", "/api/interviews/iv-1/messages", {"content": "hi"}),
        ("get", "/api/interviews/iv-1/report", None),
        ("delete", "/api/interviews/iv-1", None),
    ]
    for method, url, body in cases:
        r = await getattr(anon_client, method)(url, **({"json": body} if body else {}))
        assert r.status_code == 401, f"{method.upper()} {url} 未拦截：{r.status_code}"


# ---------- 多用户隔离 ----------


async def test_A看不到B的场次(login_as):
    alice = await login_as("alice")
    bob = await login_as("bob")
    interview_id = await _create(alice)

    # B：列表 / 会话 / 报告 / 发消息 / 删除 —— 全部 404（同「不存在」，不泄露存在性）
    assert all(x["id"] != interview_id for x in (await bob.get("/api/interviews")).json())
    assert (await bob.get(f"/api/interviews/{interview_id}")).status_code == 404
    assert (await bob.get(f"/api/interviews/{interview_id}/report")).status_code == 404
    async with bob.stream(
        "POST", f"/api/interviews/{interview_id}/messages", json={"content": "hi"},
    ) as r:
        assert r.status_code == 404
    assert (await bob.delete(f"/api/interviews/{interview_id}")).status_code == 404

    # A 自己不受影响：B 的尝试未破坏场次
    assert (await alice.get(f"/api/interviews/{interview_id}")).status_code == 200
    assert any(x["id"] == interview_id for x in (await alice.get("/api/interviews")).json())


async def test_历史场次归属首个注册账号且不重复接管(anon_client, register_user):
    """孤儿场次（user_id 为 NULL 的历史数据）归首个注册账号，第二个账号不接管。"""
    db.create_interview(
        get_settings().db_path, interview_id="orphan", position="x", question_count=5
    )
    alice_token = await register_user(anon_client, username="alice")
    alice = (await anon_client.get(
        "/api/auth/me", headers={"Authorization": f"Bearer {alice_token}"}
    )).json()
    assert db.get_interview(get_settings().db_path, "orphan")["user_id"] == alice["id"]

    bob_token = await register_user(anon_client, username="bob")
    bob = (await anon_client.get(
        "/api/auth/me", headers={"Authorization": f"Bearer {bob_token}"}
    )).json()
    assert db.get_interview(get_settings().db_path, "orphan")["user_id"] == alice["id"] != bob["id"]
