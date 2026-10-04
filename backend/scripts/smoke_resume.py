"""简历链路真实链路 smoke（P2-M11 FR-28，手动跑，不进 pytest）。

验的是单测打不到的四件事：
1. **真链路闭环**：上传简历 → 解析 → 创建带 resume_id → 开场白「已看过简历」→ 引擎跑到报告；
2. **简历真的影响了出题**（机械判据，不靠眼看）：合成简历里埋**只存在于简历**的标识串，
   开场白与项目深挖题的文案必须引用它——「预填 candidate_profile」不是纸面声明；
3. **解析失败如实报错并指路粘贴**：真 pypdf 对非 PDF 字节报 400（出路写在 detail 里）；
4. **引用计数清理**：删除场次后，没人再引用的简历行一并消失。

用法：cd backend && uv run python scripts/smoke_resume.py

隔离（同 smoke_api / smoke_vision）：业务库 / checkpointer / 上传目录全部落 /tmp，
题库表从正式库拷贝一份（引擎要真出题），不碰正式数据。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # backend/

# 必须先于 app.config 首次 get_settings 注入临时路径（lru_cache）
_TMP_DIR = Path(tempfile.mkdtemp(prefix="marda-resume-smoke-"))
os.environ["DB_PATH"] = str(_TMP_DIR / "marda.sqlite3")
os.environ["CHECKPOINT_DB_PATH"] = str(_TMP_DIR / "checkpoints.sqlite3")
os.environ["UPLOAD_DIR"] = str(_TMP_DIR / "uploads")

_REAL_DB = Path(__file__).resolve().parents[2] / "data" / "marda.sqlite3"
with sqlite3.connect(os.environ["DB_PATH"]) as _dst:
    _dst.execute("ATTACH DATABASE ? AS real", (str(_REAL_DB),))
    _dst.execute("CREATE TABLE questions AS SELECT * FROM real.questions")
    _dst.execute("CREATE TABLE question_sources AS SELECT * FROM real.question_sources")
    _dst.execute("DETACH DATABASE real")

import httpx  # noqa: E402

PORT = 8767
BASE = f"http://127.0.0.1:{PORT}"
SMOKE_USER = "smoke_resume"
SMOKE_PASSWORD = "smoke-resume-123"

# 简历里埋的标识串（**只存在于简历**）：项目名一个、技术点一个——面试官文案引用它们
# 才证明简历内容真的进了出题上下文（与 vision smoke 的图内标识同一路数）
PROJECT_MARKER = "Zx9ResumeProject"
TECH_MARKER = "Fx7RolloutScheduler"
RESUME_TOKENS = (PROJECT_MARKER, TECH_MARKER)

RESUME_MD = f"""# 张三的简历

## 基本信息
2026 届计算机硕士，方向：Agent / RAG 系统。

## 项目经历
- {PROJECT_MARKER} 智能问答系统：主导检索链路与重排，基于 BGE-M3 + RRF 把召回率提升 18%。
- 代码助手小工具：实现工具调用与 {TECH_MARKER} 调度器，支持长任务断点续跑。

## 技能
Python、LangGraph、RAG、Qdrant
"""


def _sse_events(response) -> list[dict]:
    """逐行解析 SSE（httpx 同步 client，脚本够用）。"""
    events, current = [], {}
    for line in response.iter_lines():
        line = line.rstrip("\r")
        if not line:
            if current:
                events.append(current)
                current = {}
            continue
        if line.startswith("event: "):
            current["event"] = line[len("event: "):]
        elif line.startswith("data: "):
            current["data"] = json.loads(line[len("data: "):])
    if current:
        events.append(current)
    return events


def _replies(events: list[dict]) -> str:
    return "\n".join(e["data"]["text"] for e in events if e.get("event") == "delta")


def _phase_of(events: list[dict]) -> str | None:
    phases = [e["data"].get("phase") for e in events if e.get("event") == "meta"]
    return phases[-1] if phases else None


def wait_ready(timeout: float = 40.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if httpx.get(f"{BASE}/healthz", timeout=2).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.4)
    raise RuntimeError("uvicorn 未就绪")


def main() -> None:
    proc = subprocess.Popen(
        ["uv", "run", "uvicorn", "app.main:app", "--port", str(PORT), "--log-level", "warning"],
        cwd=Path(__file__).resolve().parents[1],
    )
    try:
        wait_ready()
        with httpx.Client(timeout=180) as client:
            r = client.post(
                f"{BASE}/api/auth/register",
                json={"username": SMOKE_USER, "password": SMOKE_PASSWORD},
            )
            assert r.status_code == 201, r.text
            client.headers["Authorization"] = f"Bearer {r.json()['token']}"

            # ── 1. 解析失败如实报错（真 pypdf 对非 PDF 字节）──────────────
            bad = client.post(
                f"{BASE}/api/resumes",
                files={"file": ("简历.pdf", "这不是一份 PDF".encode(), "application/pdf")},
            )
            assert bad.status_code == 400, f"{bad.status_code} {bad.text}"
            assert "粘贴文本" in bad.json()["detail"], bad.json()
            print(f"解析失败如实报错 OK：{bad.json()['detail']}")

            # ── 2. 粘贴文本走同一端点（同一抽取管线）────────────────────
            pasted = client.post(
                f"{BASE}/api/resumes", data={"text": "我是李四，做过检索系统与调度器。"}
            )
            assert pasted.status_code == 201, pasted.text
            assert pasted.json()["filename"] == "粘贴文本"
            print(f"粘贴文本 OK：读到 {len(pasted.json()['projects'])} 段项目经历")

            # ── 3. 上传简历 → 结构化结果（含埋点标识）───────────────────
            r = client.post(
                f"{BASE}/api/resumes",
                files={"file": ("简历.md", RESUME_MD.encode(), "text/markdown")},
            )
            assert r.status_code == 201, r.text
            body = r.json()
            resume_id = body["resume_id"]
            joined = " ".join(body["projects"])
            assert PROJECT_MARKER in joined, f"抽到的项目里没有埋点标识：{body['projects']}"
            assert "text" not in body, "接口不应回原文"
            print(f"简历解析 OK：{body['filename']} · {len(body['projects'])} 段项目"
                  f" · {len(body['skills'])} 项技能 · {body['chars']} 字")

            # ── 4. 创建带简历的场次：开场白必须提到已看过简历 ──────────────
            with client.stream(
                "POST", f"{BASE}/api/interviews",
                json={
                    "position": "Agent/AI 工程师", "question_count": 2,
                    "resume_id": resume_id,
                },
            ) as resp:
                assert resp.status_code == 200, resp.text
                events = _sse_events(resp)
            interview_id = events[0]["data"]["interview_id"]
            opening = _replies(events)
            assert "简历" in opening, f"开场白没有提到简历：{opening}"
            print(f"场次 {interview_id[:8]} 开场 OK：{opening[:80].replace(chr(10), ' ')}…")

            # 落库核对：resume_id 记在场次行上（引用计数的依据）
            with sqlite3.connect(os.environ["DB_PATH"]) as conn:
                stored = conn.execute(
                    "SELECT resume_id FROM interviews WHERE id=?", (interview_id,)
                ).fetchone()[0]
            assert stored == resume_id, f"场次行没记 resume_id：{stored}"

            # ── 5. 自我介绍 → 项目题：文案必须引用简历里的埋点标识 ─────────
            with client.stream(
                "POST", f"{BASE}/api/interviews/{interview_id}/messages",
                json={"content": "我是应届生，在实验室做过检索相关的系统。"},
            ) as resp:
                assert resp.status_code == 200, resp.text
                first = _sse_events(resp)
            assert not [e for e in first if e.get("event") == "error"], first
            first_text = opening + "\n" + _replies(first)
            hit = [t for t in RESUME_TOKENS if t in first_text]
            assert hit, (
                f"面试官文案没有引用简历里的任何标识 {RESUME_TOKENS}——"
                f"简历可能没进出题上下文：{first_text[:300]}"
            )
            print(f"✅ 出题文案引用了简历标识：{hit}")

            # ── 6. 按引擎节奏答到收尾段 → 报告 ───────────────────────────
            phase = ""
            for _ in range(12):  # 追问轮数由真实 LLM 决策（相位驱动，不写死节奏）
                with client.stream(
                    "POST", f"{BASE}/api/interviews/{interview_id}/messages",
                    json={"content": "补充：结合项目里的取舍展开说明。"},
                ) as resp:
                    events = _sse_events(resp)
                phase = _phase_of(events) or phase
                if phase == "closing":
                    break
            assert phase == "closing", f"未推进到收尾段（phase={phase}）"
            # 收尾反问限 2 个（CLOSING_QUESTION_LIMIT）：第二个之后才收尾出报告
            for question in ("你们团队的技术栈是什么？", "应届生进去主要做什么方向？"):
                with client.stream(
                    "POST", f"{BASE}/api/interviews/{interview_id}/messages",
                    json={"content": question},
                ) as resp:
                    _sse_events(resp)
            report = client.get(f"{BASE}/api/interviews/{interview_id}/report")
            assert report.status_code == 200, report.text
            payload = report.json()["report"]
            print(f"报告 OK：总分 {payload.get('overall')} · {len(payload['per_question_comments'])} 题")

            # ── 7. 引用计数清理：删场次 → 简历行一并消失 ─────────────────
            assert client.delete(f"{BASE}/api/interviews/{interview_id}").status_code == 204
            with sqlite3.connect(os.environ["DB_PATH"]) as conn:
                left = conn.execute(
                    "SELECT COUNT(*) FROM resumes WHERE id=?", (resume_id,)
                ).fetchone()[0]
            assert left == 0, "删除场次后没人引用的简历行仍在（引用计数清理失效）"
            print("删除场次 OK：简历行已按引用计数清理")

        print("\n✅ 简历链路 smoke 全过（解析失败指路 · 粘贴文本 · 解析 · 开场提到简历"
              " · 出题引用简历标识 · 报告 · 引用计数清理）")
    finally:
        proc.terminate()
        proc.wait(timeout=10)
        shutil.rmtree(_TMP_DIR, ignore_errors=True)


if __name__ == "__main__":
    main()
