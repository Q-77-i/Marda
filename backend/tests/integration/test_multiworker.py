"""多 worker 集成测试（P4-E4）：任意 worker 都能 resume 同一场 + 会话锁互斥。

形态说明：这里用 **SQLite checkpointer + 共享协调器** 模拟「两个 worker」——
两个 Service 实例 = 两个进程（各自独立的 graph / saver / 连接），
共享同一份 checkpoint 文件与同一个协调器。换成 PG + Redis 时，
代码路径完全一样（checkpointer.py / coordination.py 里分派），
差异只在「状态放在哪」，由 tests/integration/test_pg_*.py 覆盖。

三条断言：
1. worker B 能读到 worker A 跑出来的状态并接着往下走（**resume 的核心**）；
2. 同一场的并发请求，第二个拿不到锁（多 worker 下防重复计分的关键闸门）；
3. 锁释放后另一个 worker 能拿到（不会把场次永久卡死）。
"""

from __future__ import annotations

import asyncio

import pytest

from app import coordination, db, llm
from app.config import get_settings
from app.service import InterviewLockedError, Service
from app.tools import question_search
from fake_llm import FakeLLMClient


@pytest.fixture(autouse=True)
def _test_env(monkeypatch, tmp_path):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setenv("SILICONFLOW_API_KEY", "test-key")
    monkeypatch.setenv("JWT_SECRET", "test-secret-0123456789abcdef0123456789")
    monkeypatch.setenv("DB_PATH", str(tmp_path / "marda.sqlite3"))
    monkeypatch.setenv("CHECKPOINT_DB_PATH", str(tmp_path / "ckpt.sqlite3"))
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path / "uploads"))
    llm.get_settings.cache_clear()
    yield
    llm.get_settings.cache_clear()


@pytest.fixture
def fake_llm(monkeypatch):
    fake = FakeLLMClient()
    monkeypatch.setattr(llm, "_get_client", lambda: fake)
    return fake


@pytest.fixture
def fake_search(monkeypatch):
    """假检索（不连 Qdrant）：两域各一题。"""

    def _item(qid: str, domain: str) -> dict:
        return {
            "question_id": qid, "question": f"{domain} 方向的题目", "answer": "参考答案",
            "key_points": ["k1"], "follow_ups": [], "domain": domain,
            "topic": "测试主题", "difficulty": "L1", "company": None, "round": "一面",
        }

    items = [_item("q_arch", "agent-architecture"), _item("q_rag", "rag")]

    async def _search(*, domain, difficulty, exclude_ids=None, k=3, user_id=None):
        return [i for i in items if i["domain"] == domain
                and (difficulty is None or i["difficulty"] == difficulty)
                and i["question_id"] not in (exclude_ids or [])][:k]

    async def _reference_answers(question_ids):
        return {i["question_id"]: i["answer"] for i in items if i["question_id"] in question_ids}

    monkeypatch.setattr(question_search, "search_questions", _search)
    monkeypatch.setattr(question_search, "fetch_reference_answers", _reference_answers)


@pytest.fixture
def shared_coordinator(monkeypatch):
    """两个 worker 共用一个协调器（生产形态里就是同一个 Redis）。"""
    coordinator = coordination.MemoryCoordinator()
    monkeypatch.setattr(coordination, "get_coordinator", lambda settings=None: coordinator)
    return coordinator


async def _create_interview(interview_id: str = "iv-1", user_id: str = "u1") -> None:
    """业务库里的场次行（路由层做的事：归属校验的权威在这里）。"""
    await asyncio.to_thread(
        db.create_interview,
        get_settings().business_db,
        interview_id=interview_id,
        position="Agent",
        question_count=3,
        user_id=user_id,
    )


async def _service() -> Service:
    service = Service(get_settings())
    await service.start()
    return service


@pytest.fixture
async def workers(shared_coordinator):
    """两个「worker」：各自独立的 Service（= 各自独立的 graph + saver 连接）。"""
    a = await _service()
    b = await _service()
    yield a, b
    await a.close()
    await b.close()


async def test_另一个worker能读到同一场的状态(workers, fake_llm):
    a, b = workers
    await _create_interview()
    events = [event async for event in a.start_interview("iv-1", "Agent", 3, user_id="u1")]
    assert any(e["event"] == "meta" for e in events)

    session = await b.get_session("iv-1", "u1")
    assert session["interview_id"] == "iv-1"
    assert session["chat_history"], "worker B 应能看到 worker A 跑出来的对话历史"


async def test_另一个worker能接着往下走(workers, fake_llm, fake_search):
    """真正的 resume：worker B 接手 worker A 开的那一场。"""
    a, b = workers
    await _create_interview()
    async for _ in a.start_interview("iv-1", "Agent", 3, user_id="u1"):
        pass

    values = await b._current_values("iv-1")
    assert values["phase"], "worker B 应能读到 checkpoint 里的状态"

    # worker B 发一条消息：能续上（不报「面试不存在」）
    events = [event async for event in b.send_message("iv-1", "我是应届生", "u1")]
    assert any(e["event"] == "delta" for e in events)


async def test_同一场并发时第二个worker拿不到锁(workers, fake_llm):
    a, b = workers
    token = await a.acquire_session("iv-1")
    assert token
    with pytest.raises(InterviewLockedError):
        await b.acquire_session("iv-1")


async def test_锁释放后另一个worker能拿到(workers, fake_llm):
    a, b = workers
    token = await a.acquire_session("iv-1")
    await a.release_session("iv-1", token)
    assert await b.acquire_session("iv-1")
