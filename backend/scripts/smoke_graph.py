"""T4 smoke：真实 DeepSeek + Qdrant 跑一场 2 题短面试，验证全链路。

用法：cd backend && uv run python scripts/smoke_graph.py

依赖：.env（DEEPSEEK_API_KEY / SILICONFLOW_API_KEY）；Qdrant 容器 marda-qdrant
可选——检索不可用时出题走 LLM 生成降级（正好验证降级链，CLAUDE.md 降级原则）。
"""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import aiosqlite
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.types import Command

from app.config import get_settings
from app.graph.graph import build_graph, make_serde, run_config
from app.graph.state import InterviewState

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


async def run_to_pause(graph, config, input_value):
    """跑图直到 interrupt 暂停或结束，返回 state（Pydantic）。"""
    async for _ in graph.astream(input_value, config=config):
        pass
    return (await graph.aget_state(config)).values


async def main() -> None:
    settings = get_settings()
    question_count = 2
    interview_id = f"smoke-t4-{int(time.time())}"  # 每次新场次，避免 resume 旧状态

    conn = await aiosqlite.connect(str(settings.checkpoint_db_path))
    saver = AsyncSqliteSaver(conn, serde=make_serde())
    graph = build_graph(checkpointer=saver)
    config = run_config(interview_id, question_count)

    print("=" * 60)
    print(f"T4 smoke：真实 DeepSeek + Qdrant，{question_count} 题短面试")
    print(f"interview_id={interview_id}  qdrant={settings.qdrant_url}")
    print("=" * 60)

    try:
        values = await run_to_pause(graph, config, InterviewState(
            interview_id=interview_id,
            position="Agent/AI 工程师",
            question_count=question_count,
        ))
        assert values["phase"] == "warmup", "开场后应进入 WARMUP"

        for turn in range(20):  # 追问会占用轮次，循环喂答直到结束（上限 20 防死循环）
            if values["status"] == "finished":
                break
            answer = CANDIDATE_ANSWERS[turn] if turn < len(CANDIDATE_ANSWERS) else FALLBACK_ANSWER
            values = await run_to_pause(graph, config, Command(resume=answer))

        print("\n" + "=" * 60)
        print("完整对话回放")
        print("=" * 60)
        for entry in values["chat_history"]:
            role = "面试官" if entry["role"] == "assistant" else "候选人"
            print(f"[{role}] {entry['content']}\n")

        print("=" * 60)
        print("报告摘要")
        print("=" * 60)
        report = values["report"]
        answered = values["answered_questions"]
        bank_hits = sum(1 for q in answered if q.from_bank)
        print("status:", values["status"], "| answered:", values["answered_count"],
              f"| 题库命中: {bank_hits}/{len(answered)}")
        print("五维均值:", report["scores"])
        print("域均分:", report["domain_scores"])
        print("短板:", report["weaknesses"])
        print("总评:", report["total_comment"])
        for item in report["per_question_comments"]:
            print(f"  逐题点评 - {item['question_id'] or '(生成题)'}: {item['comment']}")
        for item in report["study_advice"]:
            print(f"  学习建议 - {item['domain']}: {item['advice']}")
        print("\nsmoke 完成 ✓")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
