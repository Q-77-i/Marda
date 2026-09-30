"""API 集成测试（SPEC §10 #6）：httpx 走 ASGI 全链路，FakeLLM + fake 检索 + tmp 库。

覆盖：创建/消息 SSE 事件序、完整一场落库（answers/reports/interviews）、
会话恢复、历史列表、错误路径（404/400/409/422/LLM 失败 SSE error）。全部离线。

共享 fixture 见 tests/integration/conftest.py（client 已带登录态）；
SSE 响应由 httpx 逐行解析（不引第三方解析库）；心跳 ping=15s 测试内不会触发。
"""

from __future__ import annotations

import io
import json
import re
import sqlite3
from types import SimpleNamespace

import pytest
from pypdf import PdfReader

from app import db, llm
from app.config import get_settings
from app.graph.state import FOLLOWUP_ANSWER_MARKER
from fake_llm import DEFAULT_CLOSING, FakeLLMClient

# 一场 2 轮面试的完整轮次（轮次语义：2 轮 = 1 项目深挖 + 1 技术；
# 每题首答达标 → 深挖（P1-M4.5）→ 深挖补充换题 → 2 反问 → 报告）
TURNS = ["我是应届生，做过 RAG 项目", "第一题回答……", "第一题深挖补充……",
         "第二题回答……", "第二题深挖补充……", "请问团队技术栈？", "晋升路径？"]


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
    assert q == []  # 首题为项目深挖题（生成题无 question_id → 不发 question 事件）
    assert any(e["event"] == "delta" for e in events)
    assert events[-1]["event"] == "meta"
    assert events[-1]["data"]["phase"] == "project"  # P1-M4.6-C：项目深挖前置


async def test_完整一场落库与报告(client):
    interview_id, _ = await _create(client)
    questions = []
    for turn in TURNS:
        events = await _send(client, interview_id, turn)
        questions += [e for e in events if e["event"] == "question"]
    # 每道新题只发一次 question 事件（项目深挖题无 question_id 不发；追问/评分重传不算新题）
    assert [q["data"] for q in questions] == [
        {"index": 2, "question_id": "q_arch", "domain": "agent-architecture", "difficulty": "L1"},
    ]
    done = [e for e in events if e["event"] == "done"]
    assert len(done) == 1
    assert done[0]["data"] == {"interview_id": interview_id, "report_ready": True}
    # answers 落库：1 项目深挖题 + 1 技术题
    rows = _db_rows(f"SELECT * FROM answers WHERE interview_id='{interview_id}' ORDER BY id")
    assert len(rows) == 2
    # 报告落库（SPEC §8：结束后一次写入）
    report = db.get_report(get_settings().db_path, interview_id)
    assert report["payload"]["answered_count"] == 2
    assert set(report["payload"]["scores"]) == {
        "technical_depth", "fundamentals", "project_experience", "communication", "problem_solving",
    }
    # FR-25 复盘字段（SPEC §4.6）：题库题附参考答案、我的回答（首答 + 深挖补充分段）、五维、关键点对比
    comments = report["payload"]["per_question_comments"]
    assert len(comments) == 2
    assert comments[0]["question_id"] is None  # 项目深挖题（前置，无权威答案）
    assert comments[0]["candidate_answer"] == f"{TURNS[1]}\n\n{FOLLOWUP_ANSWER_MARKER}{TURNS[2]}"
    assert comments[0]["reference_answer"] is None
    assert comments[0]["score"]["fundamentals"] == 4
    assert comments[0]["covered_key_points"] == ["k1", "k2"]
    assert comments[0]["missed_key_points"] == []
    assert comments[1]["question_id"] == "q_arch"  # 技术题
    assert comments[1]["candidate_answer"] == f"{TURNS[3]}\n\n{FOLLOWUP_ANSWER_MARKER}{TURNS[4]}"
    assert comments[1]["reference_answer"] == "参考答案"
    # interviews 表收尾
    row = db.get_interview(get_settings().db_path, interview_id)
    assert row["status"] == "finished"
    assert row["ended_at"]
    # 报告接口：复盘字段经 JSON 序列化后仍完整（score 为标量 dict，不能是 Pydantic 对象）
    r = await client.get(f"/api/interviews/{interview_id}/report")
    assert r.status_code == 200
    payload = r.json()["report"]
    assert payload["answered_count"] == 2
    assert payload["per_question_comments"][0]["reference_answer"] is None
    assert payload["per_question_comments"][1]["reference_answer"] == "参考答案"
    assert payload["per_question_comments"][1]["score"]["technical_depth"] == 4
    # 结束陈词收尾（P1-M4.7-D），已结束场次不再附重连问候
    assert (await client.get(f"/api/interviews/{interview_id}?reconnect=true")).json()["chat_history"][-1][
        "content"
    ] == DEFAULT_CLOSING


async def test_会话状态恢复(client):
    interview_id, _ = await _create(client)
    await _send(client, interview_id, TURNS[0])
    r = await client.get(f"/api/interviews/{interview_id}")
    assert r.status_code == 200
    data = r.json()
    assert data["interview_id"] == interview_id
    assert data["phase"] == "project"  # P1-M4.6-C：项目深挖前置
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
    # checkpointer 线程已删：会话/报告/导出/发消息全部 404
    assert (await client.get(f"/api/interviews/{interview_id}")).status_code == 404
    assert (await client.get(f"/api/interviews/{interview_id}/report")).status_code == 404
    assert (await client.get(f"/api/interviews/{interview_id}/report.pdf")).status_code == 404
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


def _pdf_text(data: bytes) -> str:
    """PDF 全文去空白（CJK 逐字排版会插空格，比对前一律剥掉）。"""
    reader = PdfReader(io.BytesIO(data))
    return re.sub(r"\s+", "", "\n".join(page.extract_text() or "" for page in reader.pages))


async def test_导出报告PDF(client):
    """FR-18：导出真文本 PDF（中文可读回），内容与报告接口同源。"""
    interview_id, _ = await _create(client)
    for turn in TURNS:
        await _send(client, interview_id, turn)

    r = await client.get(f"/api/interviews/{interview_id}/report.pdf")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/pdf"
    assert r.headers["content-disposition"].startswith("attachment;")
    assert "filename*=UTF-8''" in r.headers["content-disposition"]  # 中文名走 RFC 5987 编码
    assert r.content[:5] == b"%PDF-"

    # 报告主体来自同一份 payload：总评/逐题点评/学习建议/五维标签都在，且无缺字
    text = _pdf_text(r.content)
    assert "�" not in text
    assert "Agent/AI工程师" in text
    assert "整体表现良好" in text and "回答到位" in text and "深入检索" in text
    assert "技术深度" in text and "问题解决" in text
    assert "参考答案" in text  # 题库题（q_arch）带参考答案
    assert "码达" in text


async def test_未结束与不存在导出PDF404(client):
    interview_id, _ = await _create(client)
    assert (await client.get(f"/api/interviews/{interview_id}/report.pdf")).status_code == 404
    assert (await client.get("/api/interviews/nope/report.pdf")).status_code == 404


async def test_导出他人场次报告PDF404(login_as):
    """越权与不存在同为 404（不泄露场次存在性）——PDF 端点沿用报告端点的归属校验。"""
    alice = await login_as("alice", "secret123")
    bob = await login_as("bob", "secret123")
    interview_id, _ = await _create(alice)
    for turn in TURNS:
        await _send(alice, interview_id, turn)

    assert (await alice.get(f"/api/interviews/{interview_id}/report.pdf")).status_code == 200
    assert (await bob.get(f"/api/interviews/{interview_id}/report.pdf")).status_code == 404


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


async def _session(client, interview_id: str) -> dict:
    r = await client.get(f"/api/interviews/{interview_id}")
    assert r.status_code == 200
    return r.json()


def _user_messages(session: dict) -> list[dict]:
    return [m for m in session["chat_history"] if m["role"] == "user"]


async def test_流内失败打上stalled标记_重试后清除(client, monkeypatch):
    """P1-M4.7 后续：「重试」不能靠前端猜服务端死活（末条消息是不是 assistant 之类的
    外部特征会把报告节点失败误判成「已跑完」→ 面试永久卡死）。会话接口直接给判据。

    这里走真图：健康 → stalled=false（停在 pause 中断点）；节点抛异常 → true；
    重试（重发同一文本）跑通 → 回到 false。
    """
    interview_id, _ = await _create(client)
    assert (await _session(client, interview_id))["stalled"] is False  # 健康：停在中断点等作答

    real_chat = llm.chat
    flaky = {"fail": True}

    async def _chat(messages, **kwargs):
        if flaky["fail"] and "用面试官口吻" in messages[0]["content"]:
            raise llm.LLMError("模拟抖动")  # 只让 ask 节点那次失败
        return await real_chat(messages, **kwargs)

    monkeypatch.setattr(llm, "chat", _chat)
    events = await _send(client, interview_id, TURNS[0])
    assert [e for e in events if e["event"] == "error"], "错误事件是失败路径的前提"
    assert (await _session(client, interview_id))["stalled"] is True

    flaky["fail"] = False
    events = await _send(client, interview_id, TURNS[0])  # 重试 = 重发同一文本
    assert [e for e in events if e["event"] == "error"] == []
    assert (await _session(client, interview_id))["stalled"] is False


async def test_评分节点失败后重试_以原始回答计分且不重复计分(client, monkeypatch):
    """承重墙：答案已随 pause 落到 state.user_input，重发只是把失败的 judge「踢」起来。

    前端重试的重发与「服务端是否已入账」正交（它拿不到 judge 的成败），所以这条语义
    必须钉死：resume 值被丢弃、判的是 state 里那份原始回答、chat_history 只增一条。
    这里故意在重试时发一段**别的话**——若哪天 langgraph 改成把 resume 值喂给待跑节点，
    这条会立刻红：那时「重发同一文本」就不再安全，前端契约要跟着重写。
    """
    interview_id, _ = await _create(client)
    await _send(client, interview_id, TURNS[0])  # 提炼 → 出第 1 道题，停在中断点等作答

    real_json = llm.chat_json
    flaky = {"fail": True}

    async def _chat_json(messages, **kwargs):
        if flaky["fail"] and "评分官" in messages[0]["content"]:
            raise llm.LLMError("模拟评分抖动")
        return await real_json(messages, **kwargs)

    monkeypatch.setattr(llm, "chat_json", _chat_json)
    await _send(client, interview_id, TURNS[1])
    session = await _session(client, interview_id)
    assert session["stalled"] is True
    # judge 先调 LLM 再入账：它失败 = 这条回答还没进服务端的账（自我介绍那条是 profile 入的）
    assert [m["content"] for m in _user_messages(session)] == [TURNS[0]]

    flaky["fail"] = False
    await _send(client, interview_id, "重试时随手敲的别的话")
    session = await _session(client, interview_id)
    assert session["stalled"] is False
    assert [m["content"] for m in _user_messages(session)] == [TURNS[0], TURNS[1]]  # 判的是原始回答
    assert session["answered_count"] == 1  # 重发不重复计分


# ---- 决策回放接口（P1-M4 / FR-21）----


async def test_回放_整场事件流可查(client):
    """一次面试的决策证据按场次取回；未结束的场次也能看（回放不依赖报告）。"""
    interview_id, _ = await _create(client)
    r = await client.get(f"/api/interviews/{interview_id}/trace")
    assert r.status_code == 200
    data = r.json()
    assert data["events"] == []  # 仅开场，尚无决策
    assert data["status"] == "running"
    assert data["question_count"] == 2
    assert data["answered_count"] == 0
    assert data["position"] == "Agent/AI 工程师"

    for turn in TURNS:
        await _send(client, interview_id, turn)

    data = (await client.get(f"/api/interviews/{interview_id}/trace")).json()
    assert data["status"] == "finished"
    assert data["answered_count"] == 2
    types = [e["type"] for e in data["events"]]
    assert types[0] == "ask" and types[-1] == "report"
    # 逐轮归属：技术题 1 / 场景题 2，收尾事件无轮次
    assert [e["round"] for e in data["events"] if e["type"] == "ask"] == [1, 2]
    assert data["events"][-1]["round"] is None
    # 事件细节经 JSON 序列化后完整（score 为标量 dict，不是 Pydantic 对象）
    ask = data["events"][0]["detail"]
    assert ask["question_id"] is None and ask["from_bank"] is False  # 首题为项目深挖题
    judge = next(e for e in data["events"] if e["type"] == "judge")
    assert judge["detail"]["answer"] == TURNS[1]
    assert judge["detail"]["score"]["technical_depth"] == 4
    assert judge["detail"]["coverage"] == 1.0


async def test_回放_他人场次与不存在同为404(client, login_as):
    interview_id, _ = await _create(client)
    other = await login_as("bob")
    r = await other.get(f"/api/interviews/{interview_id}/trace")
    assert r.status_code == 404  # 不泄露场次存在性（FR-23 口径）
    assert (await client.get("/api/interviews/nope/trace")).status_code == 404


async def test_回放_删除场次后404(client):
    interview_id, _ = await _create(client)
    await _send(client, interview_id, TURNS[0])
    assert (await client.delete(f"/api/interviews/{interview_id}")).status_code == 204
    assert (await client.get(f"/api/interviews/{interview_id}/trace")).status_code == 404


async def test_回放_未登录401(anon_client):
    assert (await anon_client.get("/api/interviews/whatever/trace")).status_code == 401


# ---- 重连问候（P1-M4.7-D）----


async def test_重连问候附在响应里不落库(client):
    """?reconnect=true：答题中的场次在响应里附一句问候（重发当前题干作锚点）。

    只随本次响应返回、不落 checkpoint —— 连续刷新不堆叠，回放数据不受影响。
    """
    interview_id, _ = await _create(client)
    await _send(client, interview_id, TURNS[0])  # 自我介绍 → 进入项目深挖，当前题在手

    plain = (await client.get(f"/api/interviews/{interview_id}")).json()
    greeted = (await client.get(f"/api/interviews/{interview_id}?reconnect=true")).json()
    assert len(greeted["chat_history"]) == len(plain["chat_history"]) + 1
    last = greeted["chat_history"][-1]
    assert last["role"] == "assistant" and "欢迎回来" in last["content"]
    # 重发题干：问候里带当前题目全文（断线回来不用往上滚）
    trace = (await client.get(f"/api/interviews/{interview_id}/trace")).json()
    question = [e for e in trace["events"] if e["type"] == "ask"][-1]["detail"]["question"]
    assert question in last["content"]
    # 不落 checkpoint：不带参数仍是原样；连续两次带参数也不堆叠
    assert (await client.get(f"/api/interviews/{interview_id}")).json()["chat_history"] == plain["chat_history"]
    assert (await client.get(f"/api/interviews/{interview_id}?reconnect=true")).json()["chat_history"] == greeted["chat_history"]


async def test_重连问候_非答题阶段不加(client):
    """开场（等自我介绍）/反问阶段没有「刚才的题」可回去 → 静默恢复。"""
    interview_id, _ = await _create(client)
    plain = (await client.get(f"/api/interviews/{interview_id}")).json()
    again = (await client.get(f"/api/interviews/{interview_id}?reconnect=true")).json()
    assert again["chat_history"] == plain["chat_history"]


async def test_创建时选定难度_出题与落库一致(client):
    """P1-M6 FR-14：难度可固定——固定场次按该档出题，自适应场次仍从 L1 起。

    观察点用「项目深挖题」：它直接取 state.difficulty（题库题会走难度放宽，
    看不出锁定与否）。落库列存的是**用户的选择**（adaptive/L1/L2/L3）。
    """
    async def _ask_difficulty(difficulty: str) -> tuple[str, str]:
        async with client.stream("POST", "/api/interviews", json={
            "position": "Agent/AI 工程师", "question_count": 5, "difficulty": difficulty,
        }) as r:
            events = await _events(r)
        interview_id = events[0]["data"]["interview_id"]
        await _send(client, interview_id, TURNS[0])  # 自我介绍 → 出首题
        trace = (await client.get(f"/api/interviews/{interview_id}/trace")).json()
        first_ask = [e for e in trace["events"] if e["type"] == "ask"][0]
        stored = _db_rows(f"SELECT difficulty FROM interviews WHERE id='{interview_id}'")
        return first_ask["detail"]["difficulty"], stored[0][0]

    assert await _ask_difficulty("L3") == ("L3", "L3")
    assert await _ask_difficulty("L2") == ("L2", "L2")
    assert await _ask_difficulty("adaptive") == ("L1", "adaptive")  # 默认值同款


async def test_创建难度非法值422(client):
    r = await client.post("/api/interviews", json={
        "position": "Agent/AI 工程师", "question_count": 5, "difficulty": "L9",
    })
    assert r.status_code == 422
