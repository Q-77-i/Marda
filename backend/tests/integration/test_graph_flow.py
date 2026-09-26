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
from app.graph.state import FOLLOWUP_ANSWER_MARKER, InterviewState
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
    """注入 fake 题库检索：按 (domain, difficulty) 查表，记录调用序列。

    报告节点的参考答案查询（FR-25）同源 fake，保证整场流程离线、不碰真实题库。
    """

    def _install(bank: dict | None = None):
        calls: list[tuple] = []
        items = [i for group in (bank or {}).values() for i in group]

        async def _search(*, domain, difficulty, exclude_ids=None, k=3):
            calls.append((domain, difficulty))
            return [i for i in items if i["domain"] == domain and i["difficulty"] == difficulty
                    and i["question_id"] not in (exclude_ids or [])][:k]

        async def _reference_answers(question_ids):
            return {i["question_id"]: i["answer"] for i in items if i["question_id"] in question_ids}

        monkeypatch.setattr(question_search, "search_questions", _search)
        monkeypatch.setattr(question_search, "fetch_reference_answers", _reference_answers)
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

    # 技术题作答 → 覆盖达标 → 深挖追问（P1-M4.5：答得好也往边界追，不换题）
    values = await _run(graph, config, Command(resume="我的答案是……"))
    assert values["answered_count"] == 1
    assert values["phase"] == "tech_base"
    assert values["current_question"]["follow_up_count"] == 1
    assert values["current_question"]["deepen_used"] == 1

    # 深挖补充 → 重评达标、深挖额度用尽 → 换题 → 答满（2 轮 = 1 技术 + 1 场景）→ PROJECT 场景题
    values = await _run(graph, config, Command(resume="深挖补充……"))
    assert values["answered_count"] == 1
    assert values["phase"] == "project"
    assert values["current_question"]["domain"] == "project"
    assert values["current_question"]["from_bank"] is False
    assert values["current_question"]["question_type"] == "scenario"

    # 场景题作答 → 同样深挖（统一生效，不特判；from_bank=False → LLM 现场生成深挖）
    values = await _run(graph, config, Command(resume="我的场景题方案是……"))
    assert values["phase"] == "project"
    assert values["current_question"]["deepen_used"] == 1

    # 场景题深挖补充 → CLOSING 反问邀请
    values = await _run(graph, config, Command(resume="场景深挖补充……"))
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
    # FR-25 复盘扩展：回答带出（首答 + 追问补充分段）、题库题附参考答案全文、场景题无权威答案
    assert comments[0]["candidate_answer"] == f"我的答案是……\n\n{FOLLOWUP_ANSWER_MARKER}深挖补充……"
    assert comments[0]["reference_answer"] == "参考答案"
    assert comments[1]["candidate_answer"] == f"我的场景题方案是……\n\n{FOLLOWUP_ANSWER_MARKER}场景深挖补充……"
    assert comments[1]["reference_answer"] is None
    assert comments[0]["score"]["technical_depth"] == 4


async def test_追问轮回答保留首答并带标记(install_llm, install_search, graph_env):
    """SPEC §4.1：candidate_answer 含追问轮——首答保留 + 【追问补充】标记追加（FR-25 复盘分段依据）。"""
    missed = {**DEFAULT_SCORE, "covered_key_points": ["k1"], "missed_key_points": ["k2"]}  # 50% < 70%
    scores = iter([missed, DEFAULT_SCORE])
    install_llm(score=lambda: next(scores))
    install_search(_bank("agent-architecture", "rag"))
    graph, _, config, state, _ = await graph_env(question_count=3)  # 2 技术 + 1 场景

    await _run(graph, config, state)
    await _run(graph, config, Command(resume="我是应届生"))
    values = await _run(graph, config, Command(resume="首答内容……"))
    assert values["current_question"]["follow_up_count"] == 1  # 遗漏 → 追问

    values = await _run(graph, config, Command(resume="追问补充内容……"))

    record = values["answered_questions"][0]
    assert record["answer"] == f"首答内容……\n\n{FOLLOWUP_ANSWER_MARKER}追问补充内容……"
    assert record["score"]["covered_key_points"] == ["k1", "k2"]  # 最终记录为重评结果


async def test_错误触发澄清追问_重评覆盖最终记录(install_llm, install_search, graph_env):
    scores = iter([
        {**DEFAULT_SCORE, "error_flag": True},  # 首次评分：有明确错误 → CLARIFY
        {**DEFAULT_SCORE, "error_flag": False},  # 重评：补充后澄清 → 达标 → DEEPEN
        {**DEFAULT_SCORE, "error_flag": False},  # 深挖补充重评 → 深挖用尽 → 换题
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

    # 补充作答 → 重评达标 → 深挖追问
    values = await _run(graph, config, Command(resume="我补充解释一下……"))
    assert values["answered_count"] == 1  # 重评不重复计数
    assert len(values["answered_questions"]) == 1
    assert values["answered_questions"][0]["score"]["error_flag"] is False  # 最终记录为重评结果
    assert values["current_question"]["deepen_used"] == 1

    # 深挖补充 → 重评 → 深挖用尽 → 换题
    values = await _run(graph, config, Command(resume="深挖补充……"))
    assert values["answered_count"] == 1
    assert values["current_question"]["domain"] == "rag"  # 已换到第 2 题


async def test_遗漏追问_逐次追问不同漏点_用满上限(install_llm, install_search, graph_env):
    """P1-M4.5-R1：同一 key_point 只追问一次——每轮只问未问过的漏点，问过的点不再重复。"""
    scores = iter([
        {**DEFAULT_SCORE, "covered_key_points": ["k1"], "missed_key_points": ["k2", "k3"]},
        {**DEFAULT_SCORE, "covered_key_points": ["k1", "k2"], "missed_key_points": ["k3", "k4"]},  # k2 补上，新漏点 k4
        {**DEFAULT_SCORE, "covered_key_points": ["k1"], "missed_key_points": ["k2", "k3", "k4"]},  # 跳回：漏点全已问过
    ])
    client = install_llm(score=lambda: next(scores))
    install_search(_bank("agent-architecture", "rag"))
    graph, _, config, state, _ = await graph_env(question_count=3)  # 2 技术 + 1 场景

    await _run(graph, config, state)
    await _run(graph, config, Command(resume="我是应届生"))
    await _run(graph, config, Command(resume="第一题回答……"))  # 遗漏 → MISSING 1（k2、k3）
    values = await _run(graph, config, Command(resume="补充一些……"))  # 新漏点 k4 → MISSING 2
    assert values["current_question"]["missing_used"] == 2
    assert values["current_question"]["asked_key_points"] == ["k2", "k3", "k4"]

    values = await _run(graph, config, Command(resume="再补充……"))  # 漏点全问过 → 换题（不再追问）
    assert values["answered_count"] == 1
    assert values["current_question"]["domain"] == "rag"
    # 追问文案只提未问过的漏点：k2/k3 只出现在第一轮，k4 只出现在第二轮
    missing_prompts = [c["system"] for c in client.calls if "回答未覆盖的方面" in c["system"]]
    assert len(missing_prompts) == 2
    assert "k2" in missing_prompts[0] and "k2" not in missing_prompts[1]  # 已问过的点不重复追问
    assert "k3" in missing_prompts[0] and "k3" not in missing_prompts[1]
    assert "k4" not in missing_prompts[0] and "k4" in missing_prompts[1]


async def test_覆盖率跳变不触发重复追问(install_llm, install_search, graph_env):
    """P1-M4.5-R1 实测回归：覆盖率 14%→86%→14% 上下跳（片段重评特征），
    追问只发生两次（遗漏一次 + 深挖一次），跳回后不再重复追问。"""
    k7 = {f"k{i}" for i in range(1, 8)}
    scores = iter([
        {**DEFAULT_SCORE, "covered_key_points": ["k1"], "missed_key_points": sorted(k7 - {"k1"})},  # 14%
        {**DEFAULT_SCORE, "covered_key_points": sorted(k7 - {"k7"}), "missed_key_points": ["k7"]},  # 86% → 深挖
        {**DEFAULT_SCORE, "covered_key_points": ["k1"], "missed_key_points": sorted(k7 - {"k1"})},  # 跳回 14%
    ])
    client = install_llm(score=lambda: next(scores))
    bank = _bank("agent-architecture", "rag")
    bank[("agent-architecture", "L1")][0]["key_points"] = sorted(k7)
    install_search(bank)
    graph, _, config, state, _ = await graph_env(question_count=2)

    await _run(graph, config, state)
    await _run(graph, config, Command(resume="我是应届生"))
    await _run(graph, config, Command(resume="首答……"))  # 14% → MISSING（k2..k7）
    values = await _run(graph, config, Command(resume="补充……"))  # 86% → DEEPEN
    assert values["current_question"]["deepen_used"] == 1

    values = await _run(graph, config, Command(resume="深挖补充……"))  # 跳回 14%：漏点全问过 → 换题
    followups = [e for e in values["trace_log"] if e["type"] == "followup"]
    assert [f["detail"]["decision"] for f in followups] == ["missing", "deepen"]
    advance = [e for e in values["trace_log"] if e["type"] == "advance"][-1]
    assert advance["detail"]["reason"] == "missing_asked"
    assert values["answered_questions"][0]["asked_key_points"] == sorted(k7 - {"k1"})  # 每个漏点只问一次
    missing_prompts = [c["system"] for c in client.calls if "回答未覆盖的方面" in c["system"]]
    assert len(missing_prompts) == 1  # 遗漏追问只有一轮：同一 key_point 不被重复追问


async def test_深挖追问_题库元数据直接发与生成题现场生成(install_llm, install_search, graph_env):
    """P1-M4.5 拍板：题库题深挖直接发 follow_ups 元数据（零 LLM 调用、可回放）；
    生成题（场景题 from_bank=False）深挖由 LLM 从问答上下文现场生成。"""
    bank = _bank("agent-architecture", "rag")
    bank[("agent-architecture", "L1")][0]["follow_ups"] = ["深挖追问：为什么选向量检索？"]
    client = install_llm()
    install_search(bank)
    graph, _, config, state, _ = await graph_env(question_count=2)

    await _run(graph, config, state)
    await _run(graph, config, Command(resume="我是应届生"))
    calls_before = len(client.calls)

    # 首答达标 → 深挖：题库题直接发元数据（该轮仅评分一次 LLM 调用）
    values = await _run(graph, config, Command(resume="我的答案是……"))
    assert values["current_question"]["deepen_used"] == 1
    assert values["current_question"]["follow_up_count"] == 1
    assert values["chat_history"][-1]["content"] == "深挖追问：为什么选向量检索？"
    followup = values["trace_log"][-1]
    assert followup["type"] == "followup"
    assert followup["detail"] == {
        "decision": "deepen", "reason": "deepen_ok",
        "text": "深挖追问：为什么选向量检索？",
    }
    assert len(client.calls) == calls_before + 1  # 深挖直发零新增 LLM 调用

    # 深挖补充 → 换题 → 场景题 → 首答 → 深挖走 LLM 现场生成（FOLLOWUP_DEEPEN_TEMPLATE）
    values = await _run(graph, config, Command(resume="因为混合检索……"))
    assert values["current_question"]["domain"] == "project"  # 已换到场景题
    calls_before = len(client.calls)
    values = await _run(graph, config, Command(resume="我的场景题方案是……"))
    assert values["current_question"]["deepen_used"] == 1
    assert len(client.calls) == calls_before + 2  # 评分 + 深挖文案各一次
    assert any("深挖" in call["system"] for call in client.calls[-2:])
    assert values["chat_history"][-1]["content"] == "面试官文案"  # FakeLLM 默认文案


async def test_出题接上下文_口吻层带profile且原题不漂移(install_llm, install_search, graph_env):
    """P1-M4.5-A 三条防漂移约束：口吻层带候选人背景适度改写题干，
    但 state 原题记录（question_id / key_points）恒为题库原值。"""
    client = install_llm()
    install_search(_bank("agent-architecture", "rag"))
    graph, _, config, state, _ = await graph_env(question_count=2)

    await _run(graph, config, state)
    values = await _run(graph, config, Command(resume="我是应届生，做过 RAG 项目"))

    # 口吻层 prompt 含候选人背景（profile 提炼自自我介绍）+ 禁改考察点约束
    ask_call = next(c for c in client.calls if "【题目】" in c["system"])
    assert "RAG" in ask_call["system"]
    assert "不得改变考察点" in ask_call["system"]
    # 原题记录不漂移：question_id / key_points 恒为题库原值
    question = values["current_question"]
    assert question["question_id"] == "q_agent-architecture"
    assert question["key_points"] == ["k1", "k2"]


async def test_提前结束未达门槛被挽留后继续(install_llm, install_search, graph_env):
    install_llm()
    install_search(_bank("agent-architecture", "rag", "planning-reasoning"))
    graph, _, config, state, _ = await graph_env(question_count=3)

    await _run(graph, config, state)
    await _run(graph, config, Command(resume="我是应届生"))
    await _run(graph, config, Command(resume="第一题回答……"))  # answered=1 → 深挖

    # 1/3 < 60%（门槛 2）→ 挽留，面试继续
    values = await _run(graph, config, Command(resume="结束面试"))
    assert values["status"] == "running"
    assert values["phase"] == "tech_base"
    assert values["answered_count"] == 1
    assert values["current_question"]["domain"] == "agent-architecture"  # 当前题不变
    # 回放证据（FR-21）：挽留事件挂当前轮次，门槛口径与 rules.end_quota 同源
    refused = [e for e in values["trace_log"] if e["type"] == "end_refused"]
    assert len(refused) == 1
    assert refused[0]["round"] == 2
    assert refused[0]["detail"] == {"answered_count": 1, "threshold": 2}

    # 深挖补充 → 换题 → 第 2 题
    values = await _run(graph, config, Command(resume="深挖补充……"))
    assert values["answered_count"] == 1
    assert values["current_question"]["domain"] == "rag"  # 已换到第 2 题

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
    await _run(graph, config, Command(resume="深挖补充……"))  # 换到第 2 题
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
    values = await _run(graph, config, Command(resume="第一题回答……"))  # 停在第 1 题深挖

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

    values = await _run(graph2, config, Command(resume="深挖补充……"))  # 重评 → 换题
    values = await _run(graph2, config, Command(resume="第二题回答……"))
    assert values["answered_count"] == 2
    values = await _run(graph2, config, Command(resume="深挖补充……"))  # 换题 → 场景题
    assert values["phase"] == "project"  # 2/2 技术轮答满 → 场景题
    assert len(values["chat_history"]) > history_len  # 历史连续
    assert values["asked_ids"] == [*asked_ids, "q_rag"]  # 场景题无 id，asked_ids 只增题库题
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
    await _run(graph, config, Command(resume="第一题回答……"))  # bad=1 → 深挖
    await _run(graph, config, Command(resume="深挖补充……"))  # 重评不记难度 → 换题
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
    # P1-M4.5-A：生成题也接候选人背景（出题官 prompt 含 profile）
    gen_call = next(c for c in client.calls if "出题官" in c["system"])
    assert "RAG" in gen_call["system"]
    assert client.calls  # 出题官调用发生过


async def test_决策回放事件流_逐轮证据完整(install_llm, install_search, graph_env):
    """FR-21（P1-M4）：出题工具输出 / 评分状态变化 / 追问原因 / 换题原因 全程留痕。

    事件流是回放页的唯一数据源，本用例逐条核对「输入 / 决策 / 工具输出 / 状态变化 / 换题原因」。
    覆盖 M4.5 深挖链路：遗漏追问 → 补充达标 → 深挖追问 → 深挖用尽换题。
    """
    missed = {**DEFAULT_SCORE, "covered_key_points": ["k1"], "missed_key_points": ["k2"]}  # 50% < 70%
    scores = iter([missed, DEFAULT_SCORE, DEFAULT_SCORE, DEFAULT_SCORE, DEFAULT_SCORE, DEFAULT_SCORE])
    client = install_llm(score=lambda: next(scores))
    install_search(_bank("agent-architecture", "rag"))
    graph, _, config, state, _ = await graph_env(question_count=2)  # 1 技术 + 1 场景

    await _run(graph, config, state)  # 开场
    await _run(graph, config, Command(resume="我是应届生"))  # 提炼 → 出第 1 题
    await _run(graph, config, Command(resume="首答……"))  # 评分 → 覆盖率低 → 遗漏追问
    await _run(graph, config, Command(resume="补充……"))  # 重评达标 → 深挖追问
    values = await _run(graph, config, Command(resume="深挖补充……"))  # 深挖用尽 → 换题 → 第 2 题（场景）

    events = values["trace_log"]
    assert [e["type"] for e in events] == [
        "ask", "judge", "followup", "judge", "followup", "judge", "advance", "ask"
    ]
    assert [e["round"] for e in events] == [1, 1, 1, 1, 1, 1, 1, 2]

    ask1, judge1, followup1, judge_re, followup2, judge_deepen, advance1, ask2 = (
        e["detail"] for e in events
    )
    # 出题：目标域/难度为输入，工具输出 = 检索命中（题库题带 question_id，生成题记 0）
    assert ask1["domain"] == "agent-architecture"
    assert ask1["difficulty"] == "L1"
    assert ask1["from_bank"] is True
    assert ask1["question_id"] == "q_agent-architecture"
    assert ask1["question"] == "agent-architecture 方向的题目"
    assert ask1["hits"] == 1
    # 评分：输入 = 本轮回答原文，输出 = 五维 + 覆盖率，状态变化 = 难度
    assert judge1["answer"] == "首答……"
    assert judge1["coverage"] == 0.5
    assert judge1["score"]["technical_depth"] == 4
    assert judge1["score"]["missed_key_points"] == ["k2"]
    assert judge1["difficulty"] == "L1"
    assert judge1["difficulty_changed"] is False
    # 追问决策：决策 + 原因 + 文案
    assert followup1["decision"] == "missing"
    assert followup1["reason"] == "coverage_low"
    assert followup1["text"]
    # 追问轮重评：同一轮次的第二条评分事件（回答为追问补充原文）
    assert judge_re["answer"] == "补充……"
    assert judge_re["coverage"] == 1.0
    # 深挖追问（P1-M4.5）：达标 → 深挖；题库题无 follow_ups 元数据 → LLM 现场生成
    assert followup2["decision"] == "deepen"
    assert followup2["reason"] == "deepen_ok"
    assert followup2["text"] == "面试官文案"
    assert judge_deepen["answer"] == "深挖补充……"
    # 换题：原因 + 阶段推进
    assert advance1["reason"] == "coverage_ok"
    assert advance1["phase"] == "project"
    # 场景题：LLM 生成，无检索命中
    assert ask2["from_bank"] is False
    assert ask2["question_id"] is None
    assert ask2["hits"] == 0
    assert ask2["domain"] == "project"

    # 走完余下流程：场景题首评 → 深挖（LLM 现场生成）→ 重评换题 → 反问 → 报告收尾
    await _run(graph, config, Command(resume="场景题方案……"))
    await _run(graph, config, Command(resume="场景深挖补充……"))
    await _run(graph, config, Command(resume="请问团队技术栈？"))
    values = await _run(graph, config, Command(resume="再问一个？"))

    tail = values["trace_log"][-3:]
    assert [e["type"] for e in tail] == ["judge", "advance", "report"]
    assert tail[1]["detail"]["phase"] == "closing"
    assert tail[2]["round"] is None  # 收尾事件不属任何轮次
    assert tail[2]["detail"]["answered_count"] == 2
    assert tail[2]["detail"]["weaknesses"] == ["agent-architecture"]
    # 场景题深挖走 LLM 现场生成（FOLLOWUP_DEEPEN_TEMPLATE 调用发生过）
    assert any("深挖" in call["system"] for call in client.calls)
    # 全程只增不改：报告生成后事件数 = 主段事件 + 收尾 5 件（场景首评/深挖/重评/换题/报告）
    assert len(values["trace_log"]) == len(events) + 5


async def test_决策回放_难度降档留痕(install_llm, install_search, graph_env):
    """状态变化证据：连续两题低分降档，评分事件记录档位与变化标记。"""
    low = {
        **DEFAULT_SCORE,
        "technical_depth": 1, "fundamentals": 1, "project_experience": 1,
        "communication": 1, "problem_solving": 1,
    }
    install_llm(score=lambda: dict(low))
    install_search(_bank("agent-architecture", "rag", difficulty="L2"))
    graph, _, config, state, _ = await graph_env(question_count=3, difficulty="L2")

    await _run(graph, config, state)
    await _run(graph, config, Command(resume="我是应届生"))
    await _run(graph, config, Command(resume="第一题回答……"))
    await _run(graph, config, Command(resume="深挖补充……"))  # 重评不记难度 → 换题
    values = await _run(graph, config, Command(resume="第二题回答……"))

    judges = [e for e in values["trace_log"] if e["type"] == "judge"]
    assert judges[-2]["detail"]["difficulty"] == "L2"  # 深挖重评：难度不变
    assert judges[-2]["detail"]["difficulty_changed"] is False
    assert judges[-1]["detail"]["difficulty"] == "L1"  # 第 2 题首评：连差两次 → 降档
    assert judges[-1]["detail"]["difficulty_changed"] is True

