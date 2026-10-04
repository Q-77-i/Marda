"""集成测试共享 fixture：ASGI 全链路（FakeLLM + fake 检索 + tmp 库 + 已登录客户端）。

只挂集成层——单测（tests/unit）零依赖，不被注入无谓 fixture。
模块内自有同名 fixture（如 test_graph_flow._test_env）按 pytest 就近原则覆盖本文件。
"""

from __future__ import annotations

import httpx
import pytest

from app import llm
from app.main import app
from app.tools import question_search
from fake_llm import FakeLLMClient

TEST_USER = {"username": "alice", "password": "secret123"}
TEST_JWT_SECRET = "test-secret-0123456789abcdef0123456789"  # ≥32 字节（config 下限）


@pytest.fixture(autouse=True)
def _test_env(monkeypatch, tmp_path):
    """注入假密钥与 tmp 库路径（get_settings 需要；同 T4 模式）。"""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setenv("SILICONFLOW_API_KEY", "test-key")
    monkeypatch.setenv("JWT_SECRET", TEST_JWT_SECRET)
    monkeypatch.setenv("DB_PATH", str(tmp_path / "marda.sqlite3"))
    monkeypatch.setenv("CHECKPOINT_DB_PATH", str(tmp_path / "ckpt.sqlite3"))
    # 图片通道（P2-M6）：上传目录也必须进 tmp——碰真库目录会污染 data/uploads
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path / "uploads"))
    llm.get_settings.cache_clear()
    yield
    llm.get_settings.cache_clear()


def question_bank() -> dict:
    """两域最小题库（fake 检索按 (domain, difficulty) 查表；difficulty=None = 整域）。"""

    def _item(qid: str, domain: str, difficulty: str = "L1") -> dict:
        return {
            "question_id": qid, "question": f"{domain} 方向的题目", "answer": "参考答案",
            "key_points": ["k1"], "follow_ups": [], "domain": domain,
            "topic": "测试主题", "difficulty": difficulty, "company": None, "round": "一面",
        }

    return {
        ("agent-architecture", "L1"): [_item("q_arch", "agent-architecture")],
        ("rag", "L1"): [_item("q_rag", "rag")],
        # 行为面（P1-M11）：整池抽取（difficulty=None）；两道便于验证「越问越少」
        ("behavioral", "L1"): [_item("q_beh1", "behavioral"), _item("q_beh2", "behavioral", "L2")],
    }


@pytest.fixture
def install_search(monkeypatch):
    def _install(bank: dict | None = None):
        items = [i for group in (bank or {}).values() for i in group]

        async def _search(*, domain, difficulty, exclude_ids=None, k=3, user_id=None):
            # difficulty=None = 不限难度（P1-M11 行为面整池检索）
            return [i for i in items if i["domain"] == domain
                    and (difficulty is None or i["difficulty"] == difficulty)
                    and i["question_id"] not in (exclude_ids or [])][:k]

        async def _reference_answers(question_ids):
            return {i["question_id"]: i["answer"] for i in items if i["question_id"] in question_ids}

        monkeypatch.setattr(question_search, "search_questions", _search)
        monkeypatch.setattr(question_search, "fetch_reference_answers", _reference_answers)

    return _install


@pytest.fixture
def fake_llm(monkeypatch):
    """共享的 FakeLLM 实例（挂在 `_get_client` 上）：用例可断言 `fake.calls`。

    P2-M6 起独立成 fixture：图附件断言要看「节点实际发出去的消息列表」，
    原先 inline 构造的实例用例够不着。
    """
    fake = FakeLLMClient()
    monkeypatch.setattr(llm, "_get_client", lambda: fake)
    return fake


@pytest.fixture
def upload_dir():
    """本用例的上传目录（随 _test_env 的 UPLOAD_DIR 走，绝不碰 data/uploads）。"""
    return llm.get_settings().upload_dir


@pytest.fixture
async def anon_client(fake_llm, install_search):
    """未登录客户端（鉴权用例用）；lifespan 内管理 service 起停。"""
    install_search(question_bank())
    transport = httpx.ASGITransport(app=app)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            yield c


@pytest.fixture
def register_user():
    """注册账号并返回 token（注册响应契约：201 + {token, username}）。"""

    async def _register(client, username: str = TEST_USER["username"],
                        password: str = TEST_USER["password"]) -> str:
        r = await client.post("/api/auth/register", json={"username": username, "password": password})
        assert r.status_code == 201, r.text
        return r.json()["token"]

    return _register


@pytest.fixture
async def login_as(anon_client, register_user):
    """按用户名造一个已登录客户端（隔离用例需要 A/B 两个账号）。"""
    clients: list[httpx.AsyncClient] = []

    async def _login_as(username: str, password: str = TEST_USER["password"]) -> httpx.AsyncClient:
        c = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")
        token = await register_user(c, username=username, password=password)
        c.headers["Authorization"] = f"Bearer {token}"
        clients.append(c)
        return c

    yield _login_as
    for c in clients:
        await c.aclose()


@pytest.fixture
async def client(login_as):
    """已登录客户端（历史用例沿用此名：Authorization 由 fixture 注入，用例零改动）。"""
    return await login_as(TEST_USER["username"])
