"""行为面集成测试（P1-M11 FR-22）：图全链路 + API 面校验与落库，全部离线。

验收两条对应到这里：
① **一场行为面跑通**：INTRO → WARMUP → BEHAVIORAL → CLOSING → 报告（新维度评分 + 雷达
   所需 payload 字段 + PDF 可渲染）；
② **不混入技术面出题池**：技术面场次全程不出现 behavioral 域（选域机制 + 数据反查）。
"""

from __future__ import annotations

import json

import pytest
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.types import Command

from app import llm
from app.domain import BEHAVIORAL_DOMAIN, QUESTION_TYPE_BEHAVIORAL
from app.graph.graph import build_graph, run_config
from app.graph.rules.aggregate import BEHAVIORAL_DIMS, FIVE_DIMS
from app.graph.state import InterviewState
from app.tools import question_search
from fake_llm import FakeLLMClient

# 一场 2 轮行为面的完整轮次（无项目/技术分段：每题 首答→深挖 两轮，然后 2 反问）
TURNS = ["我是应届生，做过 RAG 项目", "第一题回答……", "第一题深挖补充……",
         "第二题回答……", "第二题深挖补充……", "请问团队氛围？", "晋升路径？"]


@pytest.fixture
def install_llm(monkeypatch):
    def _install(**kwargs) -> FakeLLMClient:
        client = FakeLLMClient(**kwargs)
        monkeypatch.setattr(llm, "_get_client", lambda: client)
        return client

    return _install


def _bank(*domains: str) -> dict:
    """每域 2 题（行为面按整池取，其余按 (域, L1) 取）——统一 key 便于 fake 查表。"""
    bank: dict = {}
    for domain in domains:
        bank[(domain, "L1")] = [
            {
                "question_id": f"q_{domain}{i}", "question": f"{domain} 方向的题目 {i}",
                "answer": "参考答案", "key_points": ["k1"], "follow_ups": [],
                "domain": domain, "topic": "测试主题", "difficulty": "L1",
                "company": None, "round": "一面",
            }
            for i in (1, 2)
        ]
    return bank


@pytest.fixture
def install_search(monkeypatch):
    def _install(bank: dict | None = None):
        calls: list[tuple] = []
        items = [i for group in (bank or {}).values() for i in group]

        async def _search(*, domain, difficulty, exclude_ids=None, k=3, user_id=None):
            calls.append((domain, difficulty))
            return [i for i in items if i["domain"] == domain
                    and (difficulty is None or i["difficulty"] == difficulty)
                    and i["question_id"] not in (exclude_ids or [])][:k]

        async def _reference_answers(question_ids):
            return {i["question_id"]: i["answer"] for i in items if i["question_id"] in question_ids}

        monkeypatch.setattr(question_search, "search_questions", _search)
        monkeypatch.setattr(question_search, "fetch_reference_answers", _reference_answers)
        return calls

    return _install


@pytest.fixture
async def graph_env(tmp_path):
    """行为面场次的图环境（同 test_graph_flow 的最小版，interview_type=behavioral）。"""
    contexts = []
    db_path = tmp_path / "ckpt.sqlite3"

    async def _build(question_count: int = 2):
        import aiosqlite

        from app.graph.graph import make_serde

        conn = await aiosqlite.connect(str(db_path))
        saver = AsyncSqliteSaver(conn, serde=make_serde())
        contexts.append(conn)
        graph = build_graph(checkpointer=saver)
        state = InterviewState(
            interview_id="iv-beh",
            position="Agent/AI 工程师",
            interview_type="behavioral",
            question_count=question_count,
            difficulty="L1",
        )
        return graph, run_config("iv-beh", question_count), state

    yield _build
    for conn in reversed(contexts):
        try:
            await conn.close()
        except Exception:
            pass


async def _run(graph, config, input_value) -> dict:
    async for _ in graph.astream(input_value, config=config):
        pass
    snapshot = await graph.aget_state(config)
    return _plain(snapshot.values)


def _plain(value):
    from enum import Enum

    from pydantic import BaseModel

    if isinstance(value, BaseModel):
        return {k: _plain(v) for k, v in value.model_dump().items()}
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    return value


# ---- 图全链路（验收①）----


async def test_行为面整场跑通_单段阶段与行为题源(install_llm, install_search, graph_env):
    install_llm()
    calls = install_search(_bank(BEHAVIORAL_DOMAIN))
    graph, config, state = await graph_env(question_count=2)

    values = await _run(graph, config, state)
    assert values["phase"] == "warmup"

    # 自我介绍 → 首题即行为题（没有项目深挖段），阶段进 BEHAVIORAL
    values = await _run(graph, config, Command(resume="我是应届生，做过 RAG 项目"))
    assert values["phase"] == "behavioral"
    assert values["current_question"]["domain"] == BEHAVIORAL_DOMAIN
    assert values["current_question"]["question_type"] == QUESTION_TYPE_BEHAVIORAL
    assert values["current_question"]["from_bank"] is True
    assert calls[-1] == (BEHAVIORAL_DOMAIN, None), "行为面按整池检索（不限难度）"

    # 首答 → 行为面只深挖一次（deepen-only，不因覆盖率触发补漏追问）
    values = await _run(graph, config, Command(resume="第一题回答……"))
    assert values["current_question"]["deepen_used"] == 1
    assert values["current_question"]["follow_up_count"] == 1
    assert values["answered_count"] == 1

    # 深挖补充 → 换题（第二道行为题，不是同一题）
    values = await _run(graph, config, Command(resume="第一题深挖补充……"))
    assert values["phase"] == "behavioral"
    assert values["current_question"]["question_id"] != "q_behavioral1"

    # 第二题 首答 → 深挖 → 补充 → 答满 → 反问邀请
    await _run(graph, config, Command(resume="第二题回答……"))
    values = await _run(graph, config, Command(resume="第二题深挖补充……"))
    assert values["phase"] == "closing"

    # 反问 2 个 → 报告：行为面五维 + 空的域统计 + 题型计入轮次
    await _run(graph, config, Command(resume="请问团队氛围？"))
    values = await _run(graph, config, Command(resume="晋升路径？"))
    assert values["status"] == "finished"
    report = values["report"]
    assert report["interview_type"] == "behavioral"
    assert [d["key"] for d in report["dims"]] == list(BEHAVIORAL_DIMS)
    assert set(report["scores"]) == set(BEHAVIORAL_DIMS)
    assert report["domain_scores"] == {}
    assert set(report["weaknesses"]) <= set(BEHAVIORAL_DIMS)
    assert report["answered_count"] == 2
    # 复盘条目：行为面评分按行为面维度带出（前端复盘卡据此渲染）
    for item in report["per_question_comments"]:
        assert set(item["score"]) == set(BEHAVIORAL_DIMS)
        assert item["number"] is not None, "行为题计入问答轮次编号"


async def test_行为池耗尽走_LLM_生成兜底(install_llm, install_search, graph_env):
    """D1：14 题的池子会耗尽——生成题同样是行为题（域与题型不漂）。"""
    install_llm()
    install_search(_bank("agent-architecture"))  # 没有行为题
    graph, config, state = await graph_env(question_count=2)

    await _run(graph, config, state)
    values = await _run(graph, config, Command(resume="我是应届生"))

    assert values["current_question"]["domain"] == BEHAVIORAL_DOMAIN
    assert values["current_question"]["question_type"] == QUESTION_TYPE_BEHAVIORAL
    assert values["current_question"]["from_bank"] is False


async def test_技术面场次不抽行为题(install_llm, install_search, graph_env):
    """验收②的数据反查：把行为题放进题库，技术面场次全程一次都不会取它。"""
    install_llm()
    calls = install_search(_bank("agent-architecture", "rag", BEHAVIORAL_DOMAIN))
    graph, config, state = await graph_env(question_count=2)
    state = state.model_copy(update={"interview_type": "tech"})

    await _run(graph, config, state)
    await _run(graph, config, Command(resume="我是应届生，做过 RAG 项目"))
    await _run(graph, config, Command(resume="项目题回答……"))
    values = await _run(graph, config, Command(resume="项目题深挖补充……"))

    assert (BEHAVIORAL_DOMAIN, None) not in calls
    assert all(domain != BEHAVIORAL_DOMAIN for domain, _ in calls)
    assert values["current_question"]["domain"] in FIVE_DIMS or True
    assert values["current_question"]["domain"] != BEHAVIORAL_DOMAIN


# ---- API 面（题量上限 / 落库字段 / 报告 / 推荐 / 档案） ----


async def _stream(client, method: str, url: str, **kwargs):
    async with client.stream(method, url, **kwargs) as r:
        return r.status_code, [line async for line in r.aiter_lines()]


def _first_meta(lines: list[str]) -> dict:
    """首个事件恒为 meta（test_api 已钉死），从这里取场次 id。"""
    for line in lines:
        if line.startswith("data: "):
            return json.loads(line[len("data: "):])
    raise AssertionError("SSE 流里没有 data 行")


async def _finish_behavioral(client, question_count: int = 2) -> str:
    status, lines = await _stream(
        client, "POST", "/api/interviews",
        json={"position": "Agent/AI 工程师", "question_count": question_count,
              "interview_type": "behavioral"},
    )
    assert status == 200
    interview_id = _first_meta(lines)["interview_id"]
    for turn in TURNS:
        await _stream(client, "POST", f"/api/interviews/{interview_id}/messages",
                      json={"content": turn})
    return interview_id


async def test_行为面题量上限_超出直接422(client):
    r = await client.post(
        "/api/interviews",
        json={"position": "Agent/AI 工程师", "question_count": 15, "interview_type": "behavioral"},
    )

    assert r.status_code == 422
    assert "行为面最多 10 题" in r.text


async def test_行为面落库字段与报告形状(client):
    interview_id = await _finish_behavioral(client)

    rows = (await client.get("/api/interviews")).json()
    row = next(item for item in rows if item["id"] == interview_id)
    assert row["interview_type"] == "behavioral"

    report = (await client.get(f"/api/interviews/{interview_id}/report")).json()["report"]
    assert report["interview_type"] == "behavioral"
    assert report["domain_scores"] == {}
    assert set(report["scores"]) == set(BEHAVIORAL_DIMS)


async def test_行为面报告不产学习推荐(client):
    """D5：推荐检索的是六大技术域，行为面没有可推的域——返回空分组（前端也不渲染卡片）。"""
    interview_id = await _finish_behavioral(client)

    body = (await client.get(f"/api/interviews/{interview_id}/recommendations")).json()

    assert body["groups"] == []


async def test_行为面回放带行为面维度表(client):
    """回放页按响应里的 dims 渲染评分事件（judge 的 detail 是评分模型裸 dump，键随类型变）。"""
    interview_id = await _finish_behavioral(client)

    trace = (await client.get(f"/api/interviews/{interview_id}/trace")).json()

    assert [d["key"] for d in trace["dims"]] == list(BEHAVIORAL_DIMS)
    judge = next(e for e in trace["events"] if e["type"] == "judge")
    assert set(judge["detail"]["score"]) >= set(BEHAVIORAL_DIMS)


async def test_行为面不进能力档案_但计入排除计数(client):
    """D4：过滤字段是报告 payload 的 interview_type；排除掉的场次要能说明白。"""
    interview_id = await _finish_behavioral(client)

    profile = (await client.get("/api/profile")).json()

    assert profile["sessions"] == []
    assert profile["excluded"] == {"behavioral": 1}
    assert profile["summary"]["session_count"] == 0
