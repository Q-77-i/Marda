"""T5 smoke：真实 DeepSeek + Qdrant，走 HTTP API（本地 uvicorn）跑一场 2 轮短面试。

用法：cd backend && uv run python scripts/smoke_api.py

流程：起 uvicorn 子进程（8765 端口）→ healthz 就绪 → 注册账号（FR-23）→ 反向验证
未登录 401 → POST 创建（SSE 开场）→ 循环 POST 消息到 done → GET 报告 + 决策回放
（FR-21）+ 会话恢复 + 历史列表 → 核对场次归属，验证落库与用户隔离。
依赖：.env（DEEPSEEK_API_KEY / JWT_SECRET）；Qdrant 容器可选——检索不可用时出题走 LLM 生成降级。

隔离（T7a-R1）：业务库与 checkpointer 落 /tmp 临时文件，验证不污染正式数据
（题库 Qdrant 只读，不受影响）。
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# 必须先于 app.config 的 get_settings 首次调用注入临时库路径（lru_cache）
_TMP_DIR = Path(tempfile.mkdtemp(prefix="marda-smoke-"))
os.environ["DB_PATH"] = str(_TMP_DIR / "marda.sqlite3")
os.environ["CHECKPOINT_DB_PATH"] = str(_TMP_DIR / "checkpoints.sqlite3")

# 出题检索走 SQLite join questions 表 → 从正式库拷贝只读快照到临时库
import sqlite3

_REAL_DB = Path(__file__).resolve().parents[2] / "data" / "marda.sqlite3"
with sqlite3.connect(os.environ["DB_PATH"]) as _dst:
    _dst.execute("ATTACH DATABASE ? AS real", (str(_REAL_DB),))
    _dst.execute("CREATE TABLE questions AS SELECT * FROM real.questions")
    _dst.execute("DETACH DATABASE real")

import httpx

from app import db, observability
from app.config import get_settings

PORT = 8765
BASE = f"http://127.0.0.1:{PORT}"

# 账号（FR-23）：临时库每次全新，用时间戳保证用户名不撞（规则 [A-Za-z0-9_]，3-32）
SMOKE_USER = f"smoke_{int(time.time())}"
SMOKE_PASSWORD = "smoke-secret-123"

# 预置候选人回答（smoke 只验证链路，不验证回答质量；数量不足时循环喂通用作答）
CANDIDATE_ANSWERS = [
    "你好，我是应届生，主要做 Agent 和 RAG 方向。项目里用 LangGraph 搭过一个面试机器人，"
    "做过向量检索和工具调用，最近在看多智能体协作。",
    "我先说整体思路：这个问题我会从数据流和状态管理两个角度拆……（模拟作答）",
    "第二题我想想……核心是把检索和生成解耦，再加一层重排……（模拟作答）",
    "场景题我的方案是：入口做意图路由，主流程用状态机管编排，工具侧统一走适配层……（模拟作答）",
    "请问贵团队在 Agent 方向的技术栈和分工是怎样的？",
    "谢谢，最后问一个：应届生入职后一般怎么成长？",
]
FALLBACK_ANSWER = "好的，我再补充一点：整体上我会优先保证流程能跑通，再逐步加监控和降级……（模拟作答）"


async def _events(response) -> list[dict]:
    """解析 SSE 流为 (event, data) 列表。"""
    out, current = [], {}
    async for line in response.aiter_lines():
        line = line.rstrip("\r")
        if not line:
            if current:
                out.append((current["event"], current.get("data")))
                current = {}
            continue
        if line.startswith("event: "):
            current["event"] = line[len("event: "):]
        elif line.startswith("data: "):
            current["data"] = json.loads(line[len("data: "):])
    return out


async def wait_ready() -> None:
    for _ in range(60):
        try:
            async with httpx.AsyncClient(timeout=2) as client:
                if (await client.get(f"{BASE}/healthz")).status_code == 200:
                    return
        except httpx.HTTPError:
            pass
        await asyncio.sleep(0.5)
    raise RuntimeError("uvicorn 未在 30s 内就绪")


async def main() -> None:
    proc = subprocess.Popen(
        ["uv", "run", "uvicorn", "app.main:app", "--port", str(PORT), "--log-level", "warning"],
        cwd=Path(__file__).resolve().parents[1],
    )
    try:
        await wait_ready()
        print("=" * 60)
        print(f"T5 smoke：真实 DeepSeek + Qdrant，HTTP API 跑 2 轮短面试")
        print("=" * 60)

        async with httpx.AsyncClient(timeout=120) as client:
            # 账号（FR-23）：注册即登录，后续请求全部带 Bearer
            r = await client.post(
                f"{BASE}/api/auth/register",
                json={"username": SMOKE_USER, "password": SMOKE_PASSWORD},
            )
            assert r.status_code == 201, f"注册失败: {r.status_code} {r.text}"
            token = r.json()["token"]
            client.headers["Authorization"] = f"Bearer {token}"
            me = (await client.get(f"{BASE}/api/auth/me")).json()
            print(f"账号 OK：{SMOKE_USER}（id={me['id'][:8]}…）")
            # 反向验证：未登录必须被拦
            async with httpx.AsyncClient(timeout=30) as anon:
                for path in ("/api/interviews", "/api/auth/me"):
                    assert (await anon.get(f"{BASE}{path}")).status_code == 401, f"{path} 未拦截"
                assert (await anon.post(
                    f"{BASE}/api/interviews", json={"position": "x", "question_count": 2}
                )).status_code == 401
            print("未登录 401 OK（GET 列表 / GET me / POST 创建）")

            # 创建面试（SSE 开场）
            async with client.stream(
                "POST", f"{BASE}/api/interviews",
                json={"position": "Agent/AI 工程师", "question_count": 2},
            ) as r:
                assert r.status_code == 200, f"创建失败: {r.status_code}"
                events = await _events(r)
            interview_id = events[0][1]["interview_id"]
            print(f"interview_id={interview_id}")
            for name, data in events:
                if name == "delta":
                    print(f"[面试官] {data['text']}\n")

            # 逐轮发消息直到 done
            turn = 0
            done = False
            while not done and turn < 20:
                answer = CANDIDATE_ANSWERS[turn] if turn < len(CANDIDATE_ANSWERS) else FALLBACK_ANSWER
                async with client.stream(
                    "POST", f"{BASE}/api/interviews/{interview_id}/messages",
                    json={"content": answer},
                ) as r:
                    assert r.status_code == 200, f"消息失败: {r.status_code}"
                    events = await _events(r)
                for name, data in events:
                    if name == "delta":
                        print(f"[面试官] {data['text']}\n")
                    elif name == "question":
                        print(f"  >> 第 {data['index']} 题 [{data['domain']}/{data['difficulty']}]"
                              f" question_id={data['question_id']}")
                    elif name == "done":
                        print(f"  >> 面试结束 report_ready={data['report_ready']}")
                        done = True
                turn += 1
            assert done, f"{turn} 轮后仍未结束"

            # 报告落库 + 接口
            r = await client.get(f"{BASE}/api/interviews/{interview_id}/report")
            assert r.status_code == 200
            report = r.json()["report"]
            print("=" * 60)
            print("报告摘要")
            print("=" * 60)
            print("五维均值:", report["scores"])
            print("域均分:", report["domain_scores"])
            print("短板:", report["weaknesses"])
            print("总评:", report["total_comment"])
            for item in report["study_advice"]:
                print(f"  学习建议 - {item['domain']}: {item['advice']}")
            print("逐题复盘（FR-25：我的回答 / 五维 / 关键点 / 参考答案）:")
            for item in report["per_question_comments"]:
                assert item["number"] is not None, "计入轮次的题型必须有序号"
                # 复盘扩展：已答题必须带回答与五维（标量 dict，不能是 Pydantic 对象）
                assert item["candidate_answer"], f"第{item['number']}轮缺我的回答"
                assert set(item["score"]) == {
                    "technical_depth", "fundamentals", "project_experience",
                    "communication", "problem_solving",
                }, f"第{item['number']}轮五维不全：{item['score']}"
                assert isinstance(item["covered_key_points"], list)
                # 题库题附参考答案全文；生成题/场景题无权威答案 → null
                if item["question_id"]:
                    assert item["reference_answer"], f"题库题缺参考答案：{item['question_id']}"
                    ref = "有参考答案"
                else:
                    assert item["reference_answer"] is None, "场景题/生成题不该有参考答案"
                    ref = "无参考答案（生成题）"
                print(f"  第{item['number']}轮 [{item['question_type']}/{item['domain']}] "
                      f"五维均分={sum(item['score'].values()) / 5:.1f} {ref} "
                      f"覆盖{len(item['covered_key_points'])}/遗漏{len(item['missed_key_points'])}"
                      f" · {item['comment'][:24]}…")

            # 决策回放（FR-21）：整场事件流一次取回，逐轮证据自包含
            r = await client.get(f"{BASE}/api/interviews/{interview_id}/trace")
            assert r.status_code == 200, f"回放查询失败: {r.status_code}"
            trace = r.json()
            events = trace["events"]
            types = [e["type"] for e in events]
            asked = [e["round"] for e in events if e["type"] == "ask"]
            assert trace["status"] == "finished"
            assert trace["answered_count"] == trace["question_count"] == 2  # 本场 1 技术 + 1 场景
            assert types[0] == "ask" and types[-1] == "report", f"事件流首尾异常：{types}"
            assert asked == [1, 2], f"出题轮次应为 1..question_count：{asked}"
            assert set(types) <= {"ask", "judge", "followup", "advance", "end_refused", "report"}
            print("=" * 60)
            print("决策回放（FR-21）：逐轮事件流")
            print("=" * 60)
            for e in events:
                d = e["detail"]
                if e["type"] == "ask":
                    print(f"  第{e['round']}轮 ASK      [{d['domain']}/{d['difficulty']}/{d['question_type']}] "
                          f"{'题库' if d['from_bank'] else '生成'} 候选{d['hits']} · {d['question'][:26]}…")
                elif e["type"] == "judge":
                    five = {k: d["score"][k] for k in
                            ("technical_depth", "fundamentals", "project_experience",
                             "communication", "problem_solving")}
                    print(f"  第{e['round']}轮 JUDGE    覆盖率={d['coverage']} "
                          f"难度={d['difficulty']}{'(变)' if d['difficulty_changed'] else ''} "
                          f"五维={list(five.values())}")
                elif e["type"] == "followup":
                    print(f"  第{e['round']}轮 FOLLOWUP 决策={d['decision']} 原因={d['reason']} "
                          f"· {d['text'][:22]}…")
                elif e["type"] == "advance":
                    print(f"  第{e['round']}轮 ADVANCE  换题原因={d['reason']} 阶段={d['phase']}")
                elif e["type"] == "end_refused":
                    print(f"  第{e['round']}轮 END_REFUSED {d['answered_count']}/{d['threshold']} 未达门槛")
                else:
                    print(f"  REPORT  {d['answered_count']}/{d['question_count']} 短板={d['weaknesses']}")
            # 回放三要素（SPEC §7）：每题有序号、轮次单调、追问决策与原因同源
            rounds = [e["round"] for e in events if e["round"] is not None]
            assert rounds == sorted(rounds), f"轮次非单调：{rounds}"
            assert all(e["detail"] for e in events), "事件 detail 不得为空"
            print(f"回放 OK：{len(events)} 个事件 / 轮次 1-{max(rounds)}")

            # Langfuse（P1-M4）：按场次可查的 trace_id 可直接抄进控制台核对
            if observability.enabled():
                print(f"Langfuse 已启用：trace_id={observability.get_client().create_trace_id(seed=interview_id)}"
                      f"（控制台按 Sessions / session_id={interview_id} 查）")
                observability.flush()
            else:
                print("Langfuse 未配置（.env 缺 LANGFUSE_* key）→ 跳过云端核对")

            # 会话恢复 + 历史列表
            r = await client.get(f"{BASE}/api/interviews/{interview_id}")
            session = r.json()
            assert session["report_ready"] is True
            assert len(session["chat_history"]) > 0
            r = await client.get(f"{BASE}/api/interviews")
            rows = r.json()
            assert any(x["id"] == interview_id and x["status"] == "finished" for x in rows)
            print(f"会话恢复 OK（{len(session['chat_history'])} 条消息），历史列表 {len(rows)} 场")

        # 业务库落库验证（answers 行数 = 已答题数 = 全场轮次）
        with __import__("sqlite3").connect(get_settings().db_path) as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM answers WHERE interview_id=?", (interview_id,)
            ).fetchone()[0]
        assert count == trace["question_count"], f"落库 {count} 行 ≠ 轮次 {trace['question_count']}"
        row = db.get_interview(get_settings().db_path, interview_id)
        assert row["user_id"] == me["id"], "场次未归属到当前用户"
        print(f"落库 OK：answers {count} 行，interviews status={row['status']}，"
              f"归属 user_id={row['user_id'][:8]}…")
        print("\nsmoke 完成 ✓")
    finally:
        proc.terminate()
        proc.wait(timeout=10)


if __name__ == "__main__":
    asyncio.run(main())
