#!/usr/bin/env python
"""多 worker 验证（P4-E4 验收项）：两个**独立进程**跨 worker resume 同一场。

形态说明：这是 compose 里 `uvicorn --workers 2` 的等价验证——
两个进程各有自己的 graph / saver / 连接池，**状态只在 PG checkpointer 里共享**。

跑法（先起 PG 与 Redis）：
    docker compose up -d postgres redis
    uv run python scripts/smoke_multiworker.py \
        --pg-url postgresql://marda:marda@127.0.0.1:5432/marda_checkpoints \
        --redis-url redis://127.0.0.1:6379/0

流程：
1. worker A 开场（跑 intro → 停在第一个 interrupt）；
2. worker B（**另一个进程**）接手同一 thread_id 继续答 → 验证它能读到 A 的状态；
3. 打印两个进程的 PID，证明确实换了进程。

全程用 FakeLLM + 假检索：不花钱、不打真模型。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "backend"))
sys.path.insert(0, str(REPO_ROOT / "backend" / "tests" / "fixtures"))

from app.graph.checkpointer import compatible_loop_factory  # noqa: E402


def _fake_env() -> None:
    """假 LLM + 假检索 + 假 key（不碰真模型、不花钱）。"""
    os.environ.setdefault("DEEPSEEK_API_KEY", "smoke-dummy-key")
    os.environ.setdefault("SILICONFLOW_API_KEY", "smoke-dummy-key")
    os.environ.setdefault("JWT_SECRET", "smoke-secret-0123456789abcdef0123456789")

    from app import llm
    from app.tools import question_search
    from fake_llm import FakeLLMClient

    llm._get_client = lambda: FakeLLMClient()  # type: ignore[assignment]

    async def _search(*, domain, difficulty, exclude_ids=None, k=3, user_id=None):
        return []

    async def _reference_answers(question_ids):
        return {}

    question_search.search_questions = _search
    question_search.fetch_reference_answers = _reference_answers


async def _worker(pg_url: str, thread_id: str, *, worker: str, resume: str | None) -> dict:
    """跑一个 worker：resume=None 就是开场，否则接着答一句。返回本次观察到的状态摘要。"""
    import os

    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
    from langgraph.types import Command
    from psycopg_pool import AsyncConnectionPool

    from app.graph.graph import build_graph, run_config
    from app.graph.serde import make_serde
    from app.graph.state import InterviewState

    pool = AsyncConnectionPool(
        pg_url, min_size=1, max_size=2, kwargs={"autocommit": True}, open=False
    )
    await pool.open(wait=True)
    saver = AsyncPostgresSaver(pool, serde=make_serde())
    await saver.setup()
    graph = build_graph(checkpointer=saver)
    config = run_config(thread_id, 10)

    if resume is None:
        await graph.ainvoke(
            InterviewState(
                interview_id=thread_id, position="Agent 工程师", question_count=10
            ),
            config=config,
        )
    else:
        await graph.ainvoke(Command(resume=resume), config=config)

    snapshot = await graph.aget_state(config)
    values = snapshot.values or {}
    result = {
        "worker": worker,
        "pid": os.getpid(),
        "phase": values.get("phase"),
        "answered_count": values.get("answered_count"),
        "chat_history": len(values.get("chat_history") or []),
        "status": values.get("status"),
    }
    await pool.close()
    return result


def _child(args, role: str, thread_id: str) -> dict:
    """起一个**独立进程**跑一个 worker（compose `--workers 2` 的等价形态）。"""
    import subprocess

    cmd = [
        sys.executable, str(Path(__file__).resolve()),
        "--pg-url", args.pg_url,
        "--thread", thread_id,
        "--role", role,
    ]
    if args.redis_url:
        cmd += ["--redis-url", args.redis_url]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
    for line in proc.stdout.splitlines():
        if line.startswith("RESULT "):
            return json.loads(line[len("RESULT "):])
    print(proc.stdout[-2000:], proc.stderr[-2000:])
    raise SystemExit(f"worker {role} 子进程失败（exit={proc.returncode}）")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pg-url", required=True)
    parser.add_argument("--redis-url", default="")
    parser.add_argument("--thread", default="")
    parser.add_argument("--role", choices=["A", "B"], default="")
    args = parser.parse_args()

    _fake_env()

    # 子进程模式：一个 worker 干自己的活，打印 RESULT 行
    if args.role:
        with asyncio.Runner(loop_factory=compatible_loop_factory()) as runner:
            result = runner.run(
                _worker(
                    args.pg_url,
                    args.thread,
                    worker=args.role,
                    resume=None if args.role == "A" else "我是应届生，做过 Agent 与 RAG 项目。",
                )
            )
        print("RESULT " + json.dumps(result, ensure_ascii=False))
        return 0

    # 主进程：依次起两个**独立进程**（PID 必须不同）
    import uuid

    thread_id = args.thread or f"mw-{uuid.uuid4().hex[:8]}"
    a = _child(args, "A", thread_id)
    print(f"worker A（PID {a['pid']}）开场：{json.dumps(a, ensure_ascii=False)}")
    b = _child(args, "B", thread_id)
    print(f"worker B（PID {b['pid']}）接手：{json.dumps(b, ensure_ascii=False)}")

    # 判据：① 两个 PID 不同（确实是两个进程）；② B 必须读到 A 留下的状态
    ok = b["pid"] != a["pid"] and b["chat_history"] > a["chat_history"]
    print("\n[OK] 跨进程 resume 成立" if ok else "\n[FAIL] 跨进程 resume 不成立")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
