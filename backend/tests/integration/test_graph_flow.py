"""图集成测试（SPEC §10 #5，PRD 验收 3/4）：FakeLLM + fake 检索 + tmp checkpointer。

覆盖：五阶段顺序、追问三条路径、结束指令 60% 门槛、难度降档、
题库未命中降级、checkpoint 续面。全部离线，不打真实 API、不依赖 Qdrant。
"""

from __future__ import annotations

import pytest
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.types import Command

from app import llm
from app.graph.graph import build_graph, run_config
from app.graph.state import InterviewState
from app.tools import question_search
from fake_llm import DEFAULT_SCORE, FakeLLMClient


@pytest.fixture(autouse=True)
def _test_env(monkeypatch):
    """不依赖仓库 .env：注入假密钥（get_settings 需要）。"""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setenv("SILICONFLOW_API_KEY", "test-key")
    llm.get_settings.cache_clear()
    yield
    llm.get_settings.cache_clear()


@pytest.fixture
def install_llm(monkeypatch):
    """注入 FakeLLMClient（经 app.llm._get_client，与 test_llm.py 同模式）。"""

    def _install(**kwargs) -> FakeLLMClient:
        client = FakeLLMClient(**kwargs)
        monkeypatch.setattr(llm, "_get_client", lambda: client)
        return client

    return _install


@pytest.fixture
def install_search(monkeypatch):
    """注入 fake 题库检索：按 (domain, difficulty) 查表，记录调用序列。"""

    def _install(bank: dict | None = None):
        calls: list[tuple] = []

        async def _search(*, domain, difficulty, exclude_ids=None, k=3):
            calls.append((domain, difficulty))
            items = (bank or {}).get((domain, difficulty), [])
            return [i for i in items if i["question_id"] not in (exclude_ids or [])][:k]

        monkeypatch.setattr(question_search, "search_questions", _search)
        return _search, calls

    return _install


def _bank(*domains: str, difficulty: str = "L1") -> dict:
    """为若干域×难度造题库（每组合 1 题）。"""
    return {
        (domain, difficulty): [{
            "question_id": f"q_{domain}",
            "question": f"{domain} 方向的题目",
            "answer": "参考答案",
            "key_points": ["k1", "k2"],
            "follow_ups": [],
            "domain": domain,
            "topic": "测试主题",
            "difficulty": difficulty,
            "company": None,
            "round": "一面",
        }]
        for domain in domains
    }


@pytest.fixture
async def graph_env(tmp_path):
    """管理 checkpointer 生命周期：build 工厂 + teardown 退出全部上下文。

    langgraph-checkpoint-sqlite 3.x 的 from_conn_string 返回 async context
    manager（退出即关连接），这里手动 __aenter__ 持有到测试结束。
    """
    contexts = []
    db_path = tmp_path / "ckpt.sqlite3"

    async def _build(question_count: int = 2, difficulty: str = "L1"):
        import aiosqlite

        from app.graph.graph import make_serde

        conn = await aiosqlite.connect(str(db_path))
        saver = AsyncSqliteSaver(conn, serde=make_serde())
        contexts.append(conn)
        graph = build_graph(checkpointer=saver)
        state = InterviewState(
            interview_id="iv-1",
            position="Agent/AI 工程师",
            question_count=question_count,
            difficulty=difficulty,
        )
        return graph, conn, run_config("iv-1", question_count), state, db_path

    yield _build
    for conn in reversed(contexts):
        try:
            await conn.close()
        except Exception:
            pass  # 测试内已显式关闭的连接二次关闭，忽略


async def _run(graph, config, input_value) -> dict:
    """跑图一轮直到 interrupt 暂停或结束，返回 state 快照（全 dict，便于断言）。"""
    async for _ in graph.astream(input_value, config=config):
        pass
    snapshot = await graph.aget_state(config)
    return _plain(snapshot.values)


def _plain(value):
    """递归转纯 dict：LangGraph 反序列化回的是嵌套 Pydantic 模型/枚举实例。"""
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


async def test_五阶段完整流程(install_llm, install_search, graph_env):
    install_llm()
    install_search(_bank("agent-architecture", "rag"))
    graph, _, config, state, _ = await graph_env(question_count=2)

    # INTRO：新会话直达开场并暂停
    values = await _run(graph, config, state)
    assert values["phase"] == "warmup"
    assert values["chat_history"][-1]["role"] == "assistant"

    # WARMUP：自我介绍 → 提炼 + 出第 1 题（配额：agent-architecture 优先）
    values = await _run(graph, config, Command(resume="我是应届生，做过 RAG 项目"))
    assert values["phase"] == "tech_base"
    assert values["candidate_profile"]
    assert values["current_question"]["domain"] == "agent-architecture"
    assert values["current_question"]["from_bank"] is True

    # 技术题作答 → 答满（2 轮 = 1 技术 + 1 场景）→ PROJECT 场景题
    values = await _run(graph, config, Command(resume="我的答案是……"))
    assert values["answered_count"] == 1
    assert values["phase"] == "project"
    assert values["current_question"]["domain"] == "project"
    assert values["current_question"]["from_bank"] is False
    assert values["current_question"]["question_type"] == "scenario"

    # 场景题作答 → CLOSING 反问邀请
    values = await _run(graph, config, Command(resume="我的场景题方案是……"))
    assert values["phase"] == "closing"
    assert values["closing_question_count"] == 0

    # 反问 1 次 → 继续等
    values = await _run(graph, config, Command(resume="请问团队用什么技术栈？"))
    assert values["closing_question_count"] == 1
    assert values["status"] == "running"

    # 反问 2 次 → 报告生成并结束
    values = await _run(graph, config, Command(resume="再问一个：晋升路径？"))
    assert values["status"] == "finished"
    report = values["report"]
    assert report["answered_count"] == 2  # 1 技术 + 1 场景
    assert set(report["scores"]) == {
        "technical_depth", "fundamentals", "project_experience", "communication", "problem_solving"
    }
    assert report["weaknesses"] == ["agent-architecture"]
    assert report["total_comment"]
    # 逐题点评：条数恒等于作答数，元信息来自真实记录（fake 里 LLM 自编的 "q1" 不许泄漏）
    comments = report["per_question_comments"]
    assert len(comments) == report["answered_count"] == 2
    assert [c["index"] for c in comments] == [1, 2]
    assert all(c["question_id"] != "q1" for c in comments)
    assert comments[-1]["domain"] == "project"  # 场景题
    assert all(c["text"] for c in comments)
    # 题型语义：技术题/场景题都计入轮次，按序编号
    assert [c["question_type"] for c in comments] == ["tech", "scenario"]
    assert [c["number"] for c in comments] == [1, 2]


async def test_错误触发澄清追问_重评覆盖最终记录(install_llm, install_search, graph_env):
    scores = iter([
        {**DEFAULT_SCORE, "error_flag": True},  # 首次评分：有明确错误 → CLARIFY
        {**DEFAULT_SCORE, "error_flag": False},  # 重评：补充后澄清 → NEXT
    ])
    install_llm(score=lambda: next(scores))
    install_search(_bank("agent-architecture", "rag"))
    graph, _, config, state, _ = await graph_env(question_count=3)  # 2 技术 + 1 场景

    await _run(graph, config, state)
    await _run(graph, config, Command(resume="我是应届生"))

    # 作答 → 澄清追问
    values = await _run(graph, config, Command(resume="回答有矛盾……"))
    question = values["current_question"]
    assert question["follow_up_count"] == 1
    assert question["clarify_used"] == 1
    assert question["followup_log"]  # 追问文案已记录
    assert values["answered_count"] == 1

    # 补充作答 → 重评通过 → 换题
    values = await _run(graph, config, Command(resume="我补充解释一下……"))
    assert values["answered_count"] == 1  # 重评不重复计数
    assert len(values["answered_questions"]) == 1
    assert values["answered_questions"][0]["score"]["error_flag"] is False  # 最终记录为重评结果
    assert values["current_question"]["domain"] == "rag"  # 已换到第 2 题


async def test_遗漏追问_两次用满后换题(install_llm, install_search, graph_env):
    missed = {**DEFAULT_SCORE, "covered_key_points": ["k1"], "missed_key_points": ["k2"]}  # 50% < 70%
    install_llm(score=lambda: dict(missed))
    install_search(_bank("agent-architecture", "rag"))
    graph, _, config, state, _ = await graph_env(question_count=3)  # 2 技术 + 1 场景

    await _run(graph, config, state)
    await _run(graph, config, Command(resume="我是应届生"))
    await _run(graph, config, Command(resume="第一题回答……"))  # 遗漏 → MISSING 1
    values = await _run(graph, config, Command(resume="补充一些……"))  # 仍遗漏 → MISSING 2
    assert values["current_question"]["missing_used"] == 2
    assert values["current_question"]["follow_up_count"] == 2

    values = await _run(graph, config, Command(resume="再补充……"))  # 上限已满 → 换题
    assert values["answered_count"] == 1
    assert values["current_question"]["domain"] == "rag"


async def test_提前结束未达门槛被挽留后继续(install_llm, install_search, graph_env):
    install_llm()
    install_search(_bank("agent-architecture", "rag", "planning-reasoning"))
    graph, _, config, state, _ = await graph_env(question_count=3)

    await _run(graph, config, state)
    await _run(graph, config, Command(resume="我是应届生"))
    await _run(graph, config, Command(resume="第一题回答……"))  # answered=1

    # 1/3 < 60%（门槛 2）→ 挽留，面试继续
    values = await _run(graph, config, Command(resume="结束面试"))
    assert values["status"] == "running"
    assert values["phase"] == "tech_base"
    assert values["answered_count"] == 1
    assert values["current_question"]["domain"] == "rag"  # 当前题不变

    # 正常作答继续
    values = await _run(graph, config, Command(resume="第二题回答……"))
    assert values["answered_count"] == 2


async def test_达标后结束指令直接进报告(install_llm, install_search, graph_env):
    install_llm()
    install_search(_bank("agent-architecture", "rag", "planning-reasoning"))
    graph, _, config, state, _ = await graph_env(question_count=3)

    await _run(graph, config, state)
    await _run(graph, config, Command(resume="我是应届生"))
    await _run(graph, config, Command(resume="第一题回答……"))
    await _run(graph, config, Command(resume="第二题回答……"))  # answered=2 ≥ 门槛

    values = await _run(graph, config, Command(resume="结束面试"))
    assert values["status"] == "finished"
    assert values["report"]["answered_count"] == 2


async def test_checkpoint续面_重建图后状态一致(install_llm, install_search, graph_env):
    install_llm()
    install_search(_bank("agent-architecture", "rag"))
    graph, saver, config, state, db_path = await graph_env(question_count=3)  # 2 技术 + 1 场景

    await _run(graph, config, state)
    await _run(graph, config, Command(resume="我是应届生"))
    values = await _run(graph, config, Command(resume="第一题回答……"))  # 停在第 2 题

    # 断线快照
    assert values["answered_count"] == 1
    history_len = len(values["chat_history"])
    asked_ids = list(values["asked_ids"])
    await saver.close()  # 模拟服务重启、连接断开

    # 重建 saver 与 graph（同 checkpoint 文件、同 thread_id），恢复续面
    import aiosqlite

    from app.graph.graph import make_serde

    conn2 = await aiosqlite.connect(str(db_path))
    saver2 = AsyncSqliteSaver(conn2, serde=make_serde())
    graph2 = build_graph(checkpointer=saver2)

    values = await _run(graph2, config, Command(resume="第二题回答……"))
    assert values["answered_count"] == 2
    assert values["phase"] == "project"  # 2/2 技术轮答满 → 场景题
    assert len(values["chat_history"]) > history_len  # 历史连续
    assert len(values["asked_ids"]) == len(asked_ids)  # 场景题无 id，asked_ids 无重复
    await conn2.close()


async def test_连差两次难度降档(install_llm, install_search, graph_env):
    low = {
        **DEFAULT_SCORE,
        "technical_depth": 1, "fundamentals": 1, "project_experience": 1,
        "communication": 1, "problem_solving": 1,
    }
    install_llm(score=lambda: dict(low))
    install_search(_bank("agent-architecture", "rag", difficulty="L2"))
    graph, _, config, state, _ = await graph_env(question_count=3, difficulty="L2")  # 2 技术 + 1 场景

    await _run(graph, config, state)
    await _run(graph, config, Command(resume="我是应届生"))
    await _run(graph, config, Command(resume="第一题回答……"))  # bad=1
    values = await _run(graph, config, Command(resume="第二题回答……"))  # bad=2 → 降档

    assert values["difficulty"] == "L1"


async def test_题库未命中走LLM生成并放宽难度(install_llm, install_search, graph_env):
    client = install_llm()
    _, calls = install_search(None)  # 空题库
    graph, _, config, state, _ = await graph_env(question_count=2)

    await _run(graph, config, state)
    values = await _run(graph, config, Command(resume="我是应届生"))

    assert calls == [("agent-architecture", "L1"), ("agent-architecture", "L2")]  # 原难度 → 放宽 +1
    question = values["current_question"]
    assert question["from_bank"] is False
    assert question["question_id"] is None
    assert question["text"] == "请设计一个带工具调用的 Agent 系统"  # DEFAULT_GENERATED
    assert values["asked_ids"] == []  # 生成题不入 asked_ids
    assert client.calls  # 出题官调用发生过
