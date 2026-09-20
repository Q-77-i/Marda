"""T5 smoke：真实 DeepSeek + Qdrant，走 HTTP API（本地 uvicorn）跑一场 2 轮短面试。

用法：cd backend && uv run python scripts/smoke_api.py

流程：起 uvicorn 子进程（8765 端口）→ healthz 就绪 → POST 创建（SSE 开场）→
循环 POST 消息到 done → GET 报告 + 会话恢复 + 历史列表，验证落库。
依赖：.env（DEEPSEEK_API_KEY）；Qdrant 容器可选——检索不可用时出题走 LLM 生成降级。

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

from app import db
from app.config import get_settings

PORT = 8765
BASE = f"http://127.0.0.1:{PORT}"

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
            print("逐题点评（轮次语义 number/question_type）:")
            for item in report["per_question_comments"]:
                assert item["number"] is not None, "计入轮次的题型必须有序号"
                print(f"  第{item['number']}轮 [{item['question_type']}/{item['domain']}] "
                      f"{item['comment'][:30]}…")

            # 会话恢复 + 历史列表
            r = await client.get(f"{BASE}/api/interviews/{interview_id}")
            session = r.json()
            assert session["report_ready"] is True
            assert len(session["chat_history"]) > 0
            r = await client.get(f"{BASE}/api/interviews")
            rows = r.json()
            assert any(x["id"] == interview_id and x["status"] == "finished" for x in rows)
            print(f"会话恢复 OK（{len(session['chat_history'])} 条消息），历史列表 {len(rows)} 场")

        # 业务库落库验证（answers 行数 = 2 技术 + 1 场景）
        with __import__("sqlite3").connect(get_settings().db_path) as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM answers WHERE interview_id=?", (interview_id,)
            ).fetchone()[0]
        row = db.get_interview(get_settings().db_path, interview_id)
        print(f"落库 OK：answers {count} 行，interviews status={row['status']}")
        print("\nsmoke 完成 ✓")
    finally:
        proc.terminate()
        proc.wait(timeout=10)


if __name__ == "__main__":
    asyncio.run(main())
