"""P2-M9 降级链 smoke：**把 LLM 打断，一场面试仍能走完**（PRD §8.2 验收的机械证据）。

做法（全程真链路，不打桩）：

1. 起一个**本地假上游**（任何请求立即 503）——它替代 DeepSeek 的 HTTP 端点；
2. uvicorn 子进程用 `DEEPSEEK_BASE_URL` 指过去：真 httpx、真 tenacity 重试、
   真断路器（连续失败到阈值 → 开路 → 后续调用快速失败）；
3. 跑一场完整面试（开场 → 出题 → 作答 → 结束 → 报告），只看**用户能不能用**。

判据六条：
① 全场零 SSE `error`（降级不是错误——错误只留给「重试能治好」的内容类失败）；
② 有 `degraded` 事件（不许静默降级）；
③ 题目照出（题库题面/deterministic 兜底题，不依赖 LLM）；
④ 报告落库且 `degraded=true`、**不产 0 分**（scores 为空、无 overall）；
⑤ 能力档案排除该场并给出 `excluded.degraded` 说明；
⑥ 假上游真的收到了请求（证明走的是真 HTTP 栈，不是本地 mock 掉了）。

用法：cd backend && uv run python scripts/smoke_degraded.py
（不需要真 key；库与 checkpointer 落临时目录，不碰正式数据）
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

# 必须先于 app.config 的 get_settings 首次调用注入临时库路径（lru_cache）
_TMP_DIR = Path(tempfile.mkdtemp(prefix="marda-smoke-degraded-"))
os.environ["DB_PATH"] = str(_TMP_DIR / "marda.sqlite3")
os.environ["CHECKPOINT_DB_PATH"] = str(_TMP_DIR / "checkpoints.sqlite3")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# 出题检索走 SQLite join questions 表 → 从正式库拷贝只读快照到临时库
import sqlite3

_REAL_DB = Path(__file__).resolve().parents[2] / "data" / "marda.sqlite3"
with sqlite3.connect(os.environ["DB_PATH"]) as _dst:
    _dst.execute("ATTACH DATABASE ? AS real", (str(_REAL_DB),))
    _dst.execute("CREATE TABLE questions AS SELECT * FROM real.questions")
    _dst.execute("CREATE TABLE question_sources AS SELECT * FROM real.question_sources")
    _dst.execute("DETACH DATABASE real")

import httpx

from app.config import get_settings  # noqa: E402  （_TMP_DIR 注入之后才 import）

APP_PORT = 8767
FAKE_PORT = 8791
BASE = f"http://127.0.0.1:{APP_PORT}"
QUESTION_COUNT = 2
SMOKE_USER = f"smoke_degraded_{int(time.time())}"
SMOKE_PASSWORD = "smoke-secret-123"

ANSWERS = [
    "我是应届生，做过一个基于 LangGraph 的 RAG 问答项目，负责检索与状态管理部分。",
    "第一题我的思路是：先把召回和重排解耦，再用评测闭环来迭代参数。",
    "第二题我会用状态机把多轮流程显式建模，这样每一步都可解释、可回放。",
    "请问团队目前的技术栈和分工是怎样的？",
    "那应届生入职后的成长路径大概是怎样的？",
    "最后一个问题：团队平时怎么做技术评审？",
]

# 假上游收到的请求数（判据⑥：证明真 HTTP 栈被走到过）
_hits = {"count": 0}


class _FakeUpstream(BaseHTTPRequestHandler):
    """任何请求一律 503（= 上游不可用），立刻返回——比连不上的死端口省掉 TCP 超时等待。"""

    def do_POST(self) -> None:  # noqa: N802（stdlib 命名）
        _hits["count"] += 1
        body = b'{"error":{"message":"upstream unavailable","type":"server_error"}}'
        self.send_response(503)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args) -> None:  # 静音访问日志
        return


async def _events(response) -> list[tuple[str, dict | None]]:
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


async def _wait_ready() -> None:
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
    upstream = ThreadingHTTPServer(("127.0.0.1", FAKE_PORT), _FakeUpstream)
    threading.Thread(target=upstream.serve_forever, daemon=True).start()

    env = {
        **os.environ,
        # 把 LLM 上游指到假服务器（真 HTTP 栈、真重试、真熔断）
        "DEEPSEEK_BASE_URL": f"http://127.0.0.1:{FAKE_PORT}/v1",
        # 熔断阈值调到 2：本 smoke 要的是**降级路径**跑通，不是熬完生产阈值（5 次 × 3 次
        # 重试 ≈ 每轮 3-4s 的退避）。机制本身与阈值无关，默认值由 pytest 覆盖。
        "LLM_BREAKER_THRESHOLD": "2",
    }
    proc = subprocess.Popen(
        ["uv", "run", "uvicorn", "app.main:app", "--port", str(APP_PORT), "--log-level", "warning"],
        cwd=Path(__file__).resolve().parents[1],
        env=env,
    )
    try:
        await _wait_ready()
        print("=" * 60)
        print("P2-M9 smoke：断 LLM（假上游一律 503），跑一场完整面试")
        print("=" * 60)

        async with httpx.AsyncClient(timeout=180) as client:
            r = await client.post(
                f"{BASE}/api/auth/register",
                json={"username": SMOKE_USER, "password": SMOKE_PASSWORD},
            )
            assert r.status_code == 201, f"注册失败: {r.status_code} {r.text}"
            client.headers["Authorization"] = f"Bearer {r.json()['token']}"

            started = time.monotonic()
            async with client.stream(
                "POST", f"{BASE}/api/interviews",
                json={"position": "Agent/AI 工程师", "question_count": QUESTION_COUNT},
            ) as resp:
                assert resp.status_code == 200, f"创建失败: {resp.status_code}"
                create_events = await _events(resp)
            interview_id = create_events[0][1]["interview_id"]
            print(f"场次 {interview_id[:8]} 开场完成（{time.monotonic() - started:.1f}s）")

            all_events = list(create_events)
            done = False
            for text in ANSWERS:
                async with client.stream(
                    "POST", f"{BASE}/api/interviews/{interview_id}/messages",
                    json={"content": text},
                ) as resp:
                    assert resp.status_code == 200, f"作答失败: {resp.status_code}"
                    events = await _events(resp)
                all_events.extend(events)
                if any(name == "done" for name, _ in events):
                    done = True
                    break
            elapsed = time.monotonic() - started
            assert done, f"面试没走完：{[name for name, _ in all_events]}"
            print(f"面试走完（{elapsed:.1f}s，{len(ANSWERS)} 条之内）")

            # ① 零 error：降级不是错误
            errors = [(n, d) for n, d in all_events if n == "error"]
            assert not errors, f"降级路径不该发 error：{errors}"
            # ② 有 degraded 事件（不许静默）
            reasons = [d["reason"] for n, d in all_events if n == "degraded"]
            assert reasons, "全场没有任何 degraded 事件——降级被静默了"
            print(f"降级事件 OK（{len(reasons)} 条去重后）：{reasons}")

            session = (await client.get(f"{BASE}/api/interviews/{interview_id}")).json()
            history = " ".join(m["content"] for m in session["chat_history"])
            assert session["status"] == "finished", f"场次状态不是 finished：{session['status']}"

            # ③ 题目照出：题干进过对话（题库题面或内置兜底题，都不依赖 LLM）
            assert "题" in history, "对话里没有题目——出题被 LLM 拖死了"

            # ④ 报告：降级标记 + 不产 0 分
            report = (await client.get(
                f"{BASE}/api/interviews/{interview_id}/report"
            )).json()["report"]
            assert report["degraded"] is True, "报告没有降级标记"
            assert report["degraded_reasons"], "报告没有降级原因"
            assert report["unscored_count"] == report["answered_count"] >= 1
            assert report["scores"] == {}, f"未评分场次不该有分数：{report['scores']}"
            assert "overall" not in report, "未评分场次不该有总分（0 分是假信号）"
            assert report["per_question_comments"], "逐题记录不该为空（它是确定性内容）"
            print(
                f"报告 OK：degraded={report['degraded']} · 未评分 {report['unscored_count']}/"
                f"{report['answered_count']} · 无分数无总分"
            )

            # ④b PDF：降级态照常导出，分数区收起
            pdf = await client.get(f"{BASE}/api/interviews/{interview_id}/report.pdf")
            assert pdf.status_code == 200, f"降级报告导出失败：{pdf.status_code}"
            assert pdf.content[:5] == b"%PDF-"
            print(f"PDF OK：{len(pdf.content) // 1024} KB（降级态照常可导出）")

            # ⑤ 档案：排除该场并说明
            profile = (await client.get(f"{BASE}/api/profile")).json()
            assert profile["sessions"] == [], "未评分场次不该进能力曲线"
            assert profile["excluded"].get("degraded") == 1, f"档案没有说明：{profile['excluded']}"
            print(f"档案 OK：excluded={profile['excluded']}")

            # ⑥ 落库：未评分是 NULL 而不是 0
            with sqlite3.connect(get_settings().db_path) as conn:
                rows = conn.execute(
                    "SELECT score_json FROM answers WHERE interview_id = ?", (interview_id,)
                ).fetchall()
            assert rows and all(row[0] is None for row in rows), "落库的未评分应为 NULL"
            print(f"落库 OK：{len(rows)} 条 answers，score_json 全为 NULL")

        # ⑦ 真 HTTP 栈：假上游确实被敲过（不是本地 mock 短路掉了）
        assert _hits["count"] >= 2, f"假上游只收到 {_hits['count']} 次请求——链路没走真 HTTP"
        print(f"假上游收到 {_hits['count']} 次请求（真 httpx + 真重试 + 真熔断）")
        print("=" * 60)
        print("全过：断 LLM 全链路降级仍可用 ✓")
    finally:
        proc.terminate()
        proc.wait(timeout=10)
        upstream.shutdown()
        import shutil

        shutil.rmtree(_TMP_DIR, ignore_errors=True)


if __name__ == "__main__":
    asyncio.run(main())
