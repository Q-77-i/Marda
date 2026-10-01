"""question_search 单测：fake qdrant client 注入，覆盖过滤/排除/join/随机上限。

search_questions 编排层打 fake；fetch_by_ids 的真实 SQL/JSON 逻辑用 tmp SQLite 验证。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.tools import question_search


def _payload(qid: str) -> dict:
    return {
        "question_id": qid,
        "domain": "rag",
        "topic": "检索",
        "difficulty": "L1",
        "company": None,
        "round": "一面",
    }


class FakeScroll:
    """记录 scroll 入参并返回预置 payload 点。"""

    def __init__(self, payloads: list[dict]) -> None:
        self._payloads = payloads
        self.kwargs: dict | None = None

    async def scroll(self, **kwargs):
        self.kwargs = kwargs
        return [SimpleNamespace(payload=p) for p in self._payloads], None


@pytest.fixture(autouse=True)
def _test_env(monkeypatch):
    """不依赖仓库 .env（get_settings 需要假密钥）。"""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setenv("SILICONFLOW_API_KEY", "test-key")
    question_search.get_settings.cache_clear()
    yield
    question_search.get_settings.cache_clear()


@pytest.fixture
def install(monkeypatch):
    """注入 fake qdrant client 与 fake SQLite 查询，返回 (scroll 记录器, fetch 调用记录)。"""

    def _install(payloads: list[dict], rows: list[dict]) -> tuple[FakeScroll, dict]:
        scroll = FakeScroll(payloads)
        calls: dict = {}

        def fake_fetch(db_path, ids):
            calls["ids"] = ids
            return rows

        monkeypatch.setattr(question_search, "get_qdrant_client", lambda: SimpleNamespace(scroll=scroll.scroll))
        monkeypatch.setattr(question_search, "fetch_by_ids", fake_fetch)
        return scroll, calls

    return _install


async def test_过滤条件正确传递(install):
    scroll, _ = install([], [])

    await question_search.search_questions(domain="rag", difficulty="L2")

    kwargs = scroll.kwargs
    assert kwargs["collection_name"] == "questions"
    must = kwargs["scroll_filter"].must
    assert {c.key: c.match.value for c in must} == {"domain": "rag", "difficulty": "L2"}


async def test_排除已问题目_join完整题目(install):
    rows = [{"question_id": "q1"}, {"question_id": "q2"}]
    _, calls = install([_payload("q1"), _payload("q2"), _payload("q3")], rows)

    result = await question_search.search_questions(domain="rag", difficulty="L1", exclude_ids=["q3"], k=10)

    assert calls["ids"] == ["q1", "q2"]  # 排除 q3 后才查 SQLite
    assert sorted(r["question_id"] for r in result) == ["q1", "q2"]


async def test_返回不超过k条(install):
    rows = [{"question_id": f"q{i}"} for i in range(5)]
    install([_payload(f"q{i}") for i in range(5)], rows)

    result = await question_search.search_questions(domain="rag", difficulty="L1", k=2)

    assert len(result) == 2


async def test_qdrant无命中不查sqlite(install):
    _, calls = install([], [])

    result = await question_search.search_questions(domain="rag", difficulty="L1")

    assert result == []
    assert "ids" not in calls


async def test_payload缺question_id的脏数据跳过(install):
    _, calls = install([{"domain": "rag", "difficulty": "L1"}, _payload("q1")], [])

    await question_search.search_questions(domain="rag", difficulty="L1")

    assert calls["ids"] == ["q1"]


@pytest.fixture
def db(tmp_path: Path) -> Path:
    """tmp SQLite：1 条 enabled + 1 条 draft。"""
    path = tmp_path / "test.sqlite3"
    with sqlite3.connect(path) as conn:
        conn.execute(
            "CREATE TABLE questions (id TEXT PRIMARY KEY, question TEXT, answer TEXT,"
            " key_points JSON, follow_ups JSON, domain TEXT, topic TEXT, difficulty TEXT,"
            " company TEXT, round TEXT, source TEXT, status TEXT DEFAULT 'enabled')"
        )
        conn.execute(
            "INSERT INTO questions VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            ("q1", "题目一", "答案一", '["k1", "k2"]', '["f1"]', "rag", "检索", "L1", None, "一面", "个人题库", "enabled"),
        )
        conn.execute(
            "INSERT INTO questions VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            ("q2", "草稿题", "", '["k"]', "[]", "rag", "检索", "L1", None, None, None, "draft"),
        )
    return path


def test_fetch_字段映射与JSON解析(db):
    rows = question_search.fetch_by_ids(db, ["q1"])

    assert rows[0]["question_id"] == "q1"
    assert rows[0]["question"] == "题目一"
    assert rows[0]["key_points"] == ["k1", "k2"]
    assert rows[0]["follow_ups"] == ["f1"]
    assert rows[0]["source"] == "个人题库"  # 主源（学习推荐的来源列表靠它排首位）


def test_fetch_draft不入结果(db):
    assert len(question_search.fetch_by_ids(db, ["q1", "q2"])) == 1


def test_fetch_不存在id返回空(db):
    assert question_search.fetch_by_ids(db, ["nope"]) == []


# ---- 私有题混入（P1-M7 FR-13）----


@pytest.fixture
def private_env(monkeypatch, tmp_path):
    """指向 tmp 库（私有候选走真 SQL），并注入 fake qdrant。

    search_questions 用 get_settings().db_path 查私有题，故这里把 DB_PATH 指到 tmp 库。
    """
    import bank_fixture

    from app.config import get_settings

    path = tmp_path / "marda.sqlite3"
    bank_fixture.create_tables(path)
    monkeypatch.setenv("DB_PATH", str(path))
    get_settings.cache_clear()
    yield path
    get_settings.cache_clear()


def _public(qid: str) -> dict:
    return {"question_id": qid, "question": f"{qid} 题干", "domain": "rag", "difficulty": "L1"}


def _insert_private(path, user_id: str, text: str, *, difficulty: str = "L1") -> str:
    from app.tools import bank_private

    bank_private.insert_questions(
        path, user_id=user_id,
        records=[{"text": text, "answer": "参考答案内容。", "key_points": [], "follow_ups": [],
                  "topic": "个人上传", "domain": "rag", "difficulty": difficulty}],
        source_detail="t.md",
    )
    return bank_private.private_id(user_id, text)


async def test_私有题混入候选池(private_env, install):
    install([_payload("q1")], [_public("q1")])
    pid = _insert_private(private_env, "u1", "我的私有题？")

    result = await question_search.search_questions(
        domain="rag", difficulty="L1", k=10, user_id="u1"
    )

    assert {r["question_id"] for r in result} == {"q1", pid}
    assert next(r for r in result if r["question_id"] == pid)["question"] == "我的私有题？"


async def test_只并入本人的私有题(private_env, install):
    install([_payload("q1")], [_public("q1")])
    _insert_private(private_env, "u2", "别人的私有题？")

    result = await question_search.search_questions(
        domain="rag", difficulty="L1", k=10, user_id="u1"
    )

    assert {r["question_id"] for r in result} == {"q1"}


async def test_私有题同样排除已问(private_env, install):
    install([], [])
    pid = _insert_private(private_env, "u1", "已问过的私有题？")

    result = await question_search.search_questions(
        domain="rag", difficulty="L1", k=10, user_id="u1", exclude_ids=[pid]
    )

    assert result == []


async def test_私有题按域与难度过滤(private_env, install):
    install([], [])
    _insert_private(private_env, "u1", "L2 的私有题？", difficulty="L2")

    assert await question_search.search_questions(
        domain="rag", difficulty="L1", k=10, user_id="u1"
    ) == []
    assert len(await question_search.search_questions(
        domain="rag", difficulty="L2", k=10, user_id="u1"
    )) == 1


async def test_公共题全无命中时私有题仍可出(private_env, install):
    """私有题是独立的候选来源：Qdrant 空命中不该连坐（M7 之前这里返回 []）。"""
    install([], [])
    pid = _insert_private(private_env, "u1", "题库没有时的私有题？")

    result = await question_search.search_questions(
        domain="rag", difficulty="L1", k=10, user_id="u1"
    )

    assert [r["question_id"] for r in result] == [pid]


async def test_不传user_id时行为与接入前一致(private_env, install):
    _, calls = install([_payload("q1")], [_public("q1")])
    _insert_private(private_env, "u1", "不该被看到的私有题？")

    result = await question_search.search_questions(domain="rag", difficulty="L1", k=10)

    assert [r["question_id"] for r in result] == ["q1"]
    assert calls["ids"] == ["q1"]


# ---- 不限难度（P1-M11：行为面整池检索） ----


async def test_不限难度时过滤条件只有域(install):
    scroll, _ = install([], [])

    await question_search.search_questions(domain="behavioral", difficulty=None)

    must = scroll.kwargs["scroll_filter"].must
    assert {c.key: c.match.value for c in must} == {"domain": "behavioral"}


async def test_不限难度时不并入私有题(install, monkeypatch):
    """私有库不开放行为面域（D7）：difficulty=None 路径直接跳过私有候选，也不该被调用。"""
    calls: list = []

    def fake_private(*args, **kwargs):
        calls.append(kwargs)
        return []

    monkeypatch.setattr(question_search.bank_private, "search_candidates", fake_private)
    install([_payload("q1")], [{"question_id": "q1"}])

    await question_search.search_questions(domain="behavioral", difficulty=None, user_id="u1")

    assert calls == []
