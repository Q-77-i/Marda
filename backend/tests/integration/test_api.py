"""API 集成测试（SPEC §10 #6）：httpx 走 ASGI 全链路，FakeLLM + fake 检索 + tmp 库。

覆盖：创建/消息 SSE 事件序、完整一场落库（answers/reports/interviews）、
会话恢复、历史列表、错误路径（404/400/409/422/LLM 失败 SSE error）。全部离线。

SSE 响应由 httpx 逐行解析（不引第三方解析库）；心跳 ping=15s 测试内不会触发。
"""

from __future__ import annotations

import json
import sqlite3
from types import SimpleNamespace

import httpx
import pytest

from app import db, llm
from app.config import get_settings
from app.main import app
from app.tools import question_search
from fake_llm import FakeLLMClient

# 一场 2 轮面试的完整轮次（轮次语义：2 轮 = 1 技术 + 1 场景 → 2 反问 → 报告）
TURNS = ["我是应届生，做过 RAG 项目", "第一题回答……",
         "场景题方案是……", "请问团队技术栈？", "晋升路径？"]


def _bank() -> dict:
    def _item(qid: str, domain: str) -> dict:
        return {
            "question_id": qid, "question": f"{domain} 方向的题目", "answer": "参考答案",
            "key_points": ["k1"], "follow_ups": [], "domain": domain,
            "topic": "测试主题", "difficulty": "L1", "company": None, "round": "一面",
        }

    return {
        ("agent-architecture", "L1"): [_item("q_arch", "agent-architecture")],
        ("rag", "L1"): [_item("q_rag", "rag")],
    }


@pytest.fixture(autouse=True)
def _test_env(monkeypatch, tmp_path):
    """注入假密钥与 tmp 库路径（get_settings 需要；同 T4 模式）。"""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setenv("SILICONFLOW_API_KEY", "test-key")
    monkeypatch.setenv("DB_PATH", str(tmp_path / "marda.sqlite3"))
    monkeypatch.setenv("CHECKPOINT_DB_PATH", str(tmp_path / "ckpt.sqlite3"))
    llm.get_settings.cache_clear()
    yield
    llm.get_settings.cache_clear()


@pytest.fixture
def install_search(monkeypatch):
    def _install(bank: dict | None = None):
        items = [i for group in (bank or {}).values() for i in group]

        async def _search(*, domain, difficulty, exclude_ids=None, k=3):
            return [i for i in items if i["domain"] == domain and i["difficulty"] == difficulty
                    and i["question_id"] not in (exclude_ids or [])][:k]

        async def _reference_answers(question_ids):
            return {i["question_id"]: i["answer"] for i in items if i["question_id"] in question_ids}

        monkeypatch.setattr(question_search, "search_questions", _search)
        monkeypatch.setattr(question_search, "fetch_reference_answers", _reference_answers)

    return _install


@pytest.fixture
async def client(monkeypatch, install_search):
    """app 生命周期内（lifespan_context 管理 service 起停）的 ASGI httpx 客户端。"""
    monkeypatch.setattr(llm, "_get_client", lambda: FakeLLMClient())
    install_search(_bank())
    transport = httpx.ASGITransport(app=app)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            yield c


async def _events(response) -> list[dict]:
    """逐行解析 SSE 流为事件列表（心跳注释记为 {"comment": ...}）。"""
    out, current = [], {}
    async for line in response.aiter_lines():
        line = line.rstrip("\r")
        if not line:
            if current:
                out.append(current)
                current = {}
            continue
        if line.startswith(": "):
            out.append({"comment": line[2:]})
        elif line.startswith("event: "):
            current["event"] = line[len("event: "):]
        elif line.startswith("data: "):
            current["data"] = json.loads(line[len("data: "):])
    if current:
        out.append(current)
    return out


async def _create(client, question_count: int = 2) -> tuple[str, list[dict]]:
    async with client.stream(
        "POST", "/api/interviews",
        json={"position": "Agent/AI 工程师", "question_count": question_count},
    ) as r:
        assert r.status_code == 200
        events = await _events(r)
    return events[0]["data"]["interview_id"], events


async def _send(client, interview_id: str, content: str) -> list[dict]:
    async with client.stream(
        "POST", f"/api/interviews/{interview_id}/messages", json={"content": content},
    ) as r:
        assert r.status_code == 200
        return await _events(r)


def _db_rows(sql: str) -> list:
    with sqlite3.connect(get_settings().db_path) as conn:
        return conn.execute(sql).fetchall()


async def test_创建面试SSE事件序与响应头(client):
    interview_id, events = await _create(client)
    # 首事件 meta 携带 interview_id（前端据此定位场次）
    assert events[0]["event"] == "meta"
    assert events[0]["data"] == {
        "interview_id": interview_id, "phase": "intro",
        "answered_count": 0, "question_count": 2,
    }
    # 开场文案 delta + 收尾 meta（phase → warmup）
    assert any(e["event"] == "delta" and e["data"]["text"] for e in events)
    assert events[-1]["event"] == "meta"
    assert events[-1]["data"]["phase"] == "warmup"
    assert events[-1]["data"]["interview_id"] == interview_id


async def test_响应头带禁缓冲(client):
    async with client.stream(
        "POST", "/api/interviews", json={"position": "x", "question_count": 2},
    ) as r:
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/event-stream")
        assert r.headers["x-accel-buffering"] == "no"
        async for _ in r.aiter_lines():
            pass  # 排空流


async def test_消息流出题事件与阶段推进(client):
    interview_id, _ = await _create(client)
    events = await _send(client, interview_id, TURNS[0])
    q = [e for e in events if e["event"] == "question"]
    assert len(q) == 1
    assert q[0]["data"] == {
        "index": 1, "question_id": "q_arch", "domain": "agent-architecture", "difficulty": "L1",
    }
    assert any(e["event"] == "delta" for e in events)
    assert events[-1]["event"] == "meta"
    assert events[-1]["data"]["phase"] == "tech_base"


async def test_完整一场落库与报告(client):
    interview_id, _ = await _create(client)
    questions = []
    for turn in TURNS:
        events = await _send(client, interview_id, turn)
        questions += [e for e in events if e["event"] == "question"]
    # 每道新题只发一次 question 事件（追问/评分重传 current_question 不算新题）
    assert [q["data"] for q in questions] == [
        {"index": 1, "question_id": "q_arch", "domain": "agent-architecture", "difficulty": "L1"},
    ]
    done = [e for e in events if e["event"] == "done"]
    assert len(done) == 1
    assert done[0]["data"] == {"interview_id": interview_id, "report_ready": True}
    # answers 落库：1 技术题 + 1 场景题
    rows = _db_rows(f"SELECT * FROM answers WHERE interview_id='{interview_id}' ORDER BY id")
    assert len(rows) == 2
    # 报告落库（SPEC §8：结束后一次写入）
    report = db.get_report(get_settings().db_path, interview_id)
    assert report["payload"]["answered_count"] == 2
    assert set(report["payload"]["scores"]) == {
        "technical_depth", "fundamentals", "project_experience", "communication", "problem_solving",
    }
    # FR-25 复盘字段（SPEC §4.6）：题库题附参考答案、我的回答、五维、关键点对比
    comments = report["payload"]["per_question_comments"]
    assert len(comments) == 2
    assert comments[0]["question_id"] == "q_arch"
    assert comments[0]["candidate_answer"] == TURNS[1]
    assert comments[0]["reference_answer"] == "参考答案"
    assert comments[0]["score"]["fundamentals"] == 4
    assert comments[0]["covered_key_points"] == ["k1", "k2"]
    assert comments[0]["missed_key_points"] == []
    assert comments[1]["question_id"] is None  # 场景题
    assert comments[1]["candidate_answer"] == TURNS[2]
    assert comments[1]["reference_answer"] is None
    # interviews 表收尾
    row = db.get_interview(get_settings().db_path, interview_id)
    assert row["status"] == "finished"
    assert row["ended_at"]
    # 报告接口：复盘字段经 JSON 序列化后仍完整（score 为标量 dict，不能是 Pydantic 对象）
    r = await client.get(f"/api/interviews/{interview_id}/report")
    assert r.status_code == 200
    payload = r.json()["report"]
    assert payload["answered_count"] == 2
    assert payload["per_question_comments"][0]["reference_answer"] == "参考答案"
    assert payload["per_question_comments"][0]["score"]["technical_depth"] == 4


async def test_会话状态恢复(client):
    interview_id, _ = await _create(client)
    await _send(client, interview_id, TURNS[0])
    r = await client.get(f"/api/interviews/{interview_id}")
    assert r.status_code == 200
    data = r.json()
    assert data["interview_id"] == interview_id
    assert data["phase"] == "tech_base"
    assert data["answered_count"] == 0
    assert data["question_count"] == 2
    assert data["status"] == "running"
    assert data["report_ready"] is False
    assert any(m["role"] == "assistant" for m in data["chat_history"])


async def test_长场次不漏发且回放完整(client, monkeypatch):
    """T8-R1 回归：对话历史不截断。

    两个消费者都要求完整：回放（GET /interviews/{id}）要含开场，SSE delta 靠
    「本次长度 − 上次长度」找新增——一旦从头部截断，差分恒为空 → 面试官文案漏发。
    本用例跑 10 轮 + 大量追问（消息数远超旧的 24 条上限），逐条核对。
    """
    low = {
        "technical_depth": 1, "fundamentals": 1, "project_experience": 1,
        "communication": 1, "problem_solving": 1,
        "covered_key_points": [], "missed_key_points": ["k1"],
        "error_flag": False, "comment": "继续追问",
    }
    monkeypatch.setattr(llm, "_get_client", lambda: FakeLLMClient(score=low))
    interview_id, events = await _create(client, question_count=10)
    deltas = sum(1 for e in events if e.get("event") == "delta")

    for turn in range(40):
        events = await _send(client, interview_id, f"第 {turn} 轮回答……")
        deltas += sum(1 for e in events if e.get("event") == "delta")
        if any(e.get("event") == "done" for e in events):
            break
    else:
        pytest.fail("40 轮内未跑完，用例前提失效")

    r = await client.get(f"/api/interviews/{interview_id}")
    assert r.status_code == 200
    history = r.json()["chat_history"]
    assistant = [m for m in history if m["role"] == "assistant"]
    assert history[0]["role"] == "assistant" and history[0]["content"], "开场白丢了 → 回放不完整"
    assert deltas == len(assistant), f"面试官文案 {len(assistant)} 条，只发到前端 {deltas} 条"
    assert len(history) > 24, "长场次对话历史被截断（本场消息数必定超出旧上限）"


async def test_历史列表倒序(client):
    id1, _ = await _create(client)
    id2, _ = await _create(client)
    r = await client.get("/api/interviews")
    assert r.status_code == 200
    rows = r.json()
    ids = [x["id"] for x in rows]
    assert ids.index(id2) < ids.index(id1)  # 新场次在前
    assert rows[0]["status"] == "running"


async def test_不存在404(client):
    r = await client.get("/api/interviews/nope")
    assert r.status_code == 404
    r = await client.get("/api/interviews/nope/report")
    assert r.status_code == 404
    async with client.stream(
        "POST", "/api/interviews/nope/messages", json={"content": "hi"},
    ) as r:
        assert r.status_code == 404


async def test_空消息400(client):
    interview_id, _ = await _create(client)
    r = await client.post(f"/api/interviews/{interview_id}/messages", json={"content": "   "})
    assert r.status_code == 400


async def test_题量越界422(client):
    r = await client.post("/api/interviews", json={"position": "x", "question_count": 99})
    assert r.status_code == 422
    # 轮次语义：1 轮 = 0 技术 + 1 场景没有意义，最小 2
    r = await client.post("/api/interviews", json={"position": "x", "question_count": 1})
    assert r.status_code == 422


async def test_删除场次_三表与接口全部清除(client):
    interview_id, _ = await _create(client)
    for turn in TURNS:
        await _send(client, interview_id, turn)  # 完整一场（有 answers/report）

    r = await client.delete(f"/api/interviews/{interview_id}")
    assert r.status_code == 204
    # 三表物理删除
    assert _db_rows(f"SELECT * FROM interviews WHERE id='{interview_id}'") == []
    assert _db_rows(f"SELECT * FROM answers WHERE interview_id='{interview_id}'") == []
    assert _db_rows(f"SELECT * FROM reports WHERE interview_id='{interview_id}'") == []
    # checkpointer 线程已删：会话/报告/发消息全部 404
    assert (await client.get(f"/api/interviews/{interview_id}")).status_code == 404
    assert (await client.get(f"/api/interviews/{interview_id}/report")).status_code == 404
    async with client.stream(
        "POST", f"/api/interviews/{interview_id}/messages", json={"content": "hi"},
    ) as r:
        assert r.status_code == 404
    # 历史列表不再包含
    rows = (await client.get("/api/interviews")).json()
    assert all(x["id"] != interview_id for x in rows)


async def test_删除进行中场次也允许(client):
    interview_id, _ = await _create(client)
    await _send(client, interview_id, TURNS[0])

    r = await client.delete(f"/api/interviews/{interview_id}")
    assert r.status_code == 204
    assert (await client.get(f"/api/interviews/{interview_id}")).status_code == 404


async def test_删除不存在404(client):
    assert (await client.delete("/api/interviews/nope")).status_code == 404


async def test_未结束查报告404(client):
    interview_id, _ = await _create(client)
    r = await client.get(f"/api/interviews/{interview_id}/report")
    assert r.status_code == 404


async def test_已结束再发消息409(client):
    interview_id, _ = await _create(client)
    for turn in TURNS:
        await _send(client, interview_id, turn)
    r = await client.post(f"/api/interviews/{interview_id}/messages", json={"content": "再聊聊"})
    assert r.status_code == 409


async def test_LLM失败发error事件(client, monkeypatch):
    interview_id, _ = await _create(client)

    class _Boom:
        @property
        def chat(self):
            class _Completions:
                async def create(self, **kwargs):
                    raise RuntimeError("boom")

            return SimpleNamespace(completions=_Completions())

    monkeypatch.setattr(llm, "_get_client", lambda: _Boom())
    events = await _send(client, interview_id, TURNS[0])
    errs = [e for e in events if e["event"] == "error"]
    assert len(errs) == 1
    assert errs[0]["data"]["code"] == "llm_error"
    assert errs[0]["data"]["retryable"] is False
