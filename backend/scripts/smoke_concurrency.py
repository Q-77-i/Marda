#!/usr/bin/env python
"""并发冒烟（P4-E4 验收项）：fake 20–50 路并发，验证「并发下不重复计分 + 不互相踩」。

**全程离线、不花钱**：在进程内起 ASGI 应用 + FakeLLM，不连真模型、不连真 DB。

两个场景，都要跑：
1. **同场争用**（`--mode same`）：N 路并发打**同一场** →
   应当 1 个成功、N-1 个 409（会话锁），且**只有一条回答进了对话历史**（不重复计分）。
2. **多场并行**（`--mode many`）：M 场同时跑（每场自己的锁）→
   全部成功，报告延迟分位（p50 / p95 / max）与状态分布。

用法：
    uv run python scripts/smoke_concurrency.py --mode same  --concurrency 32
    uv run python scripts/smoke_concurrency.py --mode many  --sessions 8 --concurrency 4
    uv run python scripts/smoke_concurrency.py --mode both  --concurrency 50 --sessions 10

退出码：0 = 全部符合预期；非 0 = 有违反（会话锁没挡住 / 出现重复计分 / 有非预期错误）。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import statistics
import sys
import tempfile
import time
import uuid
from pathlib import Path

# Windows 控制台默认 GBK：中文没问题，但任何 emoji / 特殊符号会直接抛
# UnicodeEncodeError 把脚本结果盖掉。统一转 UTF-8（errors="replace" 兜底）。
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "backend"))
sys.path.insert(0, str(REPO_ROOT / "backend" / "tests" / "fixtures"))


def _setup_env(tmp: Path) -> None:
    """假密钥 + tmp 库路径（绝不能碰真库 / 真目录）。"""
    os.environ["DEEPSEEK_API_KEY"] = "smoke-dummy-key"
    os.environ["SILICONFLOW_API_KEY"] = "smoke-dummy-key"
    os.environ["JWT_SECRET"] = "smoke-secret-0123456789abcdef0123456789"
    os.environ["DB_PATH"] = str(tmp / "marda.sqlite3")
    os.environ["CHECKPOINT_DB_PATH"] = str(tmp / "ckpt.sqlite3")
    os.environ["UPLOAD_DIR"] = str(tmp / "uploads")
    # 限流在本脚本里**关掉**：这里要验的是会话锁与并发正确性，
    # 而 32 路并发本身就会撞上 per-user 限流（那是另一条闸门，有独立的 API 用例覆盖）。
    os.environ["USER_RATE_LIMIT_PER_MIN"] = "1000000"


def _install_fake_search() -> None:
    """假题库检索（不连 Qdrant）：两域各一题。

    没有这一步，出题会去打 Qdrant —— 离线跑不了（本脚本的定位就是「不花钱、不依赖外部」）。
    """
    from app.tools import question_search

    def _item(qid: str, domain: str) -> dict:
        return {
            "question_id": qid, "question": f"{domain} 方向的题目", "answer": "参考答案",
            "key_points": ["k1"], "follow_ups": [], "domain": domain,
            "topic": "测试主题", "difficulty": "L1", "company": None, "round": "一面",
        }

    items = [_item("q_arch", "agent-architecture"), _item("q_rag", "rag")]

    async def _search(*, domain, difficulty, exclude_ids=None, k=3, user_id=None):
        return [i for i in items if i["domain"] == domain
                and (difficulty is None or i["difficulty"] == difficulty)
                and i["question_id"] not in (exclude_ids or [])][:k]

    async def _reference_answers(question_ids):
        return {i["question_id"]: i["answer"] for i in items if i["question_id"] in question_ids}

    question_search.search_questions = _search
    question_search.fetch_reference_answers = _reference_answers


def _interview_id(sse_text: str) -> str:
    for line in sse_text.splitlines():
        if line.startswith("data: "):
            payload = json.loads(line[len("data: "):])
            if "interview_id" in payload:
                return payload["interview_id"]
    raise AssertionError(f"SSE 里没有 interview_id：{sse_text[:200]}")


def _percentiles(values: list[float]) -> dict[str, float]:
    if not values:
        return {"p50": 0.0, "p95": 0.0, "max": 0.0, "mean": 0.0}
    ordered = sorted(values)
    idx95 = min(len(ordered) - 1, int(len(ordered) * 0.95))
    return {
        "p50": round(statistics.median(ordered) * 1000),
        "p95": round(ordered[idx95] * 1000),
        "max": round(ordered[-1] * 1000),
        "mean": round(statistics.fmean(ordered) * 1000),
    }


async def _run_same(client, *, concurrency: int, question_count: int) -> dict:
    """同场争用：N 路并发打同一场。"""
    created = await client.post(
        "/api/interviews", json={"position": "Agent", "question_count": question_count}
    )
    interview_id = _interview_id(created.text)

    async def _send(i: int):
        started = time.perf_counter()
        response = await client.post(
            f"/api/interviews/{interview_id}/messages",
            json={"content": f"并发回答 {i}"},
        )
        return response.status_code, time.perf_counter() - started

    results = await asyncio.gather(*[_send(i) for i in range(concurrency)])
    codes = [code for code, _ in results]
    latencies = [dt for _, dt in results]

    session = await client.get(f"/api/interviews/{interview_id}")
    body = session.json()
    user_messages = [m for m in body.get("chat_history", []) if m.get("role") == "user"]

    return {
        "场景": "同场争用",
        "并发数": concurrency,
        "状态分布": {str(code): codes.count(code) for code in sorted(set(codes))},
        "延迟(ms)": _percentiles(latencies),
        "记入的候选人消息数": len(user_messages),
    }


async def _run_many(client, *, sessions: int, concurrency: int, question_count: int) -> dict:
    """多场并行：每场一场面试，同时开。"""
    errors: list[str] = []

    async def _session(index: int):
        started = time.perf_counter()
        created = await client.post(
            "/api/interviews",
            json={"position": f"岗位{index}", "question_count": question_count},
        )
        interview_id = _interview_id(created.text)
        # 每场并发发 1 条（每场只被一个请求打，锁不该挡自己）
        responses = await asyncio.gather(
            *[
                client.post(
                    f"/api/interviews/{interview_id}/messages",
                    json={"content": f"第 {i} 条"},
                )
                for i in range(concurrency)
            ]
        )
        codes = [r.status_code for r in responses]
        if codes.count(200) != 1:
            errors.append(f"{interview_id}: 状态分布异常 {codes}")
        return time.perf_counter() - started

    latencies = await asyncio.gather(*[_session(i) for i in range(sessions)])
    return {
        "场景": "多场并行",
        "场次数": sessions,
        "每场并发": concurrency,
        "延迟(ms)": _percentiles(list(latencies)),
        "异常": errors[:5] or "无",
    }


async def main_async(args) -> int:
    import httpx

    from app import llm
    from app.main import app
    from fake_llm import FakeLLMClient

    llm.get_settings.cache_clear()
    llm._get_client = lambda: FakeLLMClient()  # type: ignore[assignment]
    if not os.environ.get('SMOKE_NO_FAKE_SEARCH'):
        _install_fake_search()

    transport = httpx.ASGITransport(app=app)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=transport, base_url="http://smoke") as client:
            # 注册一个账号（并发场景共用一个用户），带上登录态
            registered = await client.post(
                "/api/auth/register",
                json={"username": f"smoke_{uuid.uuid4().hex[:8]}", "password": "secret123"},
            )
            token = registered.json().get("token", "")
            if not token:
                print("注册失败：", registered.text[:200])
                return 1
            client.headers["Authorization"] = f"Bearer {token}"
            results = []
            if args.mode in ("same", "both"):
                results.append(await _run_same(
                    client, concurrency=args.concurrency, question_count=args.questions
                ))
            if args.mode in ("many", "both"):
                results.append(await _run_many(
                    client,
                    sessions=args.sessions,
                    concurrency=args.same_session_concurrency,
                    question_count=args.questions,
                ))

    for result in results:
        print(json.dumps(result, ensure_ascii=False, indent=2))

    # 判据：同场争用必须 1 成功 + 其余 409，且只记入一条回答
    failures: list[str] = []
    for result in results:
        if result["场景"] == "同场争用":
            dist = result["状态分布"]
            if dist.get("200") != 1:
                failures.append(f"同场并发应当只有 1 个成功，实际 {dist}")
            if dist.get("409", 0) != result["并发数"] - 1:
                failures.append(f"其余应当全是 409，实际 {dist}")
            if result["记入的候选人消息数"] != 1:
                failures.append(
                    f"并发导致重复计分：记入了 {result['记入的候选人消息数']} 条回答"
                )
        elif result.get("异常") not in (None, "无"):
            failures.append(str(result["异常"]))

    if failures:
        print("\n 不符合预期：")
        for item in failures:
            print(f"  - {item}")
        return 1
    print("\n 符合预期：同场并发只有一条能进，其余 409；未出现重复计分。")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="并发冒烟（fake LLM，离线不花钱）")
    parser.add_argument("--mode", choices=["same", "many", "both"], default="both")
    parser.add_argument("--concurrency", type=int, default=32, help="同场争用的并发路数")
    parser.add_argument(
        "--same-session-concurrency", type=int, default=1, help="多场并行时每场的并发路数"
    )
    parser.add_argument("--sessions", type=int, default=8, help="多场并行的场次数")
    parser.add_argument("--questions", type=int, default=5, help="每场题量")
    args = parser.parse_args()

    # 用 mkdtemp 而不是 TemporaryDirectory：Windows 上 SQLite 文件在进程完全退出前
    # 删不掉（句柄还开着），自动清理会抛 PermissionError 盖掉真正的结果。
    tmp = tempfile.mkdtemp(prefix="marda-smoke-")
    _setup_env(Path(tmp))
    try:
        return asyncio.run(main_async(args))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
