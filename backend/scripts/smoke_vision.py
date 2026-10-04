"""图片通道真实链路 smoke（P2-M6 FR-26，手动跑，不进 pytest）。

验的是单测打不到的四件事：
1. **真链路闭环**：HTTP 上传截图 → 随消息发 image_id → 引擎跑到报告；
2. **图真的到了模型眼前**（机械判据，不靠眼看）：收尾反问「你能看看我这段代码吗」
   答案必须提到**只存在于图里**的标识串（Zx9KernelLoop / max_iterations / scratchpad）；
3. **Langfuse 记录形态**：含图调用的 input 是**媒体引用**（不落 base64），费用/token 可读回；
4. **清理闭环**：删除场次连同图片目录一起清掉。

用法：cd backend && uv run python scripts/smoke_vision.py [--image /path/to.png]
不给 --image 时用 headless Chrome 现渲染一张合成代码截图（图内埋标识串）；
没有 Chrome 就明确退出提示，不静默跳过。

隔离（同 smoke_api）：业务库/checkpointer/上传目录全部落 /tmp，不碰正式数据。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # backend/

# 必须先于 app.config 首次 get_settings 注入临时路径（lru_cache）
_TMP_DIR = Path(tempfile.mkdtemp(prefix="marda-vision-smoke-"))
os.environ["DB_PATH"] = str(_TMP_DIR / "marda.sqlite3")
os.environ["CHECKPOINT_DB_PATH"] = str(_TMP_DIR / "checkpoints.sqlite3")
os.environ["UPLOAD_DIR"] = str(_TMP_DIR / "uploads")

import sqlite3  # noqa: E402

_REAL_DB = Path(__file__).resolve().parents[2] / "data" / "marda.sqlite3"
with sqlite3.connect(os.environ["DB_PATH"]) as _dst:
    _dst.execute("ATTACH DATABASE ? AS real", (str(_REAL_DB),))
    _dst.execute("CREATE TABLE questions AS SELECT * FROM real.questions")
    _dst.execute("CREATE TABLE question_sources AS SELECT * FROM real.question_sources")
    _dst.execute("DETACH DATABASE real")

import httpx  # noqa: E402

from app import observability  # noqa: E402
from app.config import get_settings  # noqa: E402

PORT = 8766
BASE = f"http://127.0.0.1:{PORT}"
SMOKE_USER = "smoke_vision"
SMOKE_PASSWORD = "smoke-vision-123"

# 图内标识串（只有截图里有；断言面试官消息/收尾回答引用它 = 模型真的读了图）
MARKER = "Zx9KernelLoop"
IMAGE_TOKENS = (MARKER, "max_iterations", "scratchpad", "decide_action")

SHOT_HTML = f"""<!doctype html><html><head><meta charset="utf-8"><style>
body{{margin:0;background:#fff;font:14px/1.6 ui-monospace,Menlo,monospace;color:#1a1a1a}}
.win{{background:#f6f8fa;padding:10px 14px;border-bottom:1px solid #d0d7de;font:12px system-ui;color:#57606a}}
pre{{margin:16px}}
</style></head><body><div class="win">re-act-loop.py — marda-agent</div>
<pre># ReAct 循环
def {MARKER}(query, tools, max_iterations=5):
    scratchpad = []
    for i in range(max_iterations):
        thought = llm.think(query, scratchpad)
        action = llm.decide_action(thought, tools)
        if action.name == "finish":
            return action.args["answer"]
        obs = tools[action.name].run(**action.args)
        scratchpad.append((thought, action, obs))
    raise RuntimeError("超出迭代上限，未能收敛")</pre></body></html>"""


def _find_chrome() -> str | None:
    candidates = [
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        shutil.which("google-chrome"),
        shutil.which("chromium"),
        shutil.which("chromium-browser"),
    ]
    return next((c for c in candidates if c and Path(c).exists()), None)


def make_screenshot(out: Path) -> None:
    """合成代码截图（不依赖 PIL：headless Chrome 渲染 HTML）。"""
    chrome = _find_chrome()
    assert chrome, "未找到 Chrome/Chromium——请用 --image 传入一张既有截图"
    html = out.with_suffix(".html")
    html.write_text(SHOT_HTML, encoding="utf-8")
    subprocess.run(
        [chrome, "--headless", "--disable-gpu", f"--screenshot={out}",
         "--window-size=900,340", "--hide-scrollbars", f"file://{html}"],
        check=True, capture_output=True,
    )
    assert out.exists() and out.stat().st_size > 5000, "截图未生成"


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


class Session:
    """一场面试的 HTTP 会话（带 token 的 httpx client + 常用动作）。"""

    def __init__(self, client: httpx.Client, interview_id: str) -> None:
        self.client = client
        self.interview_id = interview_id

    def send(self, content: str, images: list[str] | None = None) -> list[dict]:
        body: dict = {"content": content}
        if images:
            body["images"] = images
        with self.client.stream(
            "POST", f"{BASE}/api/interviews/{self.interview_id}/messages", json=body
        ) as r:
            assert r.status_code == 200, f"{r.status_code} {r.text}"
            events = _sse_events(r)
        errors = [e for e in events if e.get("event") == "error"]
        assert not errors, f"SSE error：{errors}"
        return events

    @staticmethod
    def replies(events: list[dict]) -> str:
        return "\n".join(e["data"]["text"] for e in events if e.get("event") == "delta")

    @staticmethod
    def phase_of(events: list[dict]) -> str | None:
        """最后一次 meta 事件的相位（驱动流程用——追问轮数由真实 LLM 决策，脚本不假设节奏）。"""
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


def fetch_langfuse_inputs(interview_id: str) -> list[str]:
    """读回云端各观测的 input 文本（未配置 Langfuse 时返回空表）。"""
    if not observability.enabled():
        return []
    from langfuse import Langfuse

    settings = get_settings()
    client = Langfuse(
        public_key=settings.langfuse_public_key,
        secret_key=settings.langfuse_secret_key,
        base_url=settings.langfuse_base_url,
    )
    trace_id = client.create_trace_id(seed=interview_id)
    time.sleep(6)  # 上报是异步批量的（smoke_api 有重试轮询；这里睡一口再读，读不到就如实报）
    for _ in range(20):
        resp = client.api.observations.get_many(
            trace_id=trace_id, fields="core,basic,usage,model,io", limit=100
        )
        pages = resp.data
        if any(o.type == "GENERATION" for o in pages):
            return [str(getattr(o, "input", "")) for o in pages]
        time.sleep(2)
    return []


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", help="现成的截图路径（不给则用 headless Chrome 渲染）")
    parser.add_argument(
        "--keep", action="store_true",
        help="保留现场：跳过删除清理断言、不删临时目录（供 eval_judge_vision 审计脚本对拍）",
    )
    args = parser.parse_args()

    image_path = Path(args.image) if args.image else _TMP_DIR / "code_shot.png"
    if not args.image:
        make_screenshot(image_path)
    print(f"截图：{image_path}（{image_path.stat().st_size // 1024}KB，图内标识 {MARKER}）")

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

            with client.stream(
                "POST", f"{BASE}/api/interviews",
                json={"position": "Agent/AI 工程师", "question_count": 2},
            ) as resp:
                assert resp.status_code == 200
                events = _sse_events(resp)
            interview_id = events[0]["data"]["interview_id"]
            session = Session(client, interview_id)
            print(f"场次 {interview_id[:8]} 开场 OK")

            # ── 1. 上传 + 取回（字节一致）──────────────────────────────
            png = image_path.read_bytes()
            r = client.post(
                f"{BASE}/api/interviews/{interview_id}/images",
                files={"file": (image_path.name, png, "image/png")},
            )
            assert r.status_code == 201, r.text
            image_id = r.json()["image_id"]
            r = client.get(f"{BASE}/api/interviews/{interview_id}/images/{image_id}")
            assert r.status_code == 200 and r.content == png, "取回的图与上传不一致"
            print(f"上传/取回 OK（image_id={image_id[:8]}…，{len(png) // 1024}KB）")

            # ── 2. 走完问答段：第一题带图，按引擎节奏答到收尾段 ──────────
            session.send("我是应届生，做过 RAG 问答项目")
            replies = session.replies(session.send("第一题回答：见截图里的实现。", [image_id]))
            print(f"第一题（带图）回应：{replies[:120].replace(chr(10), ' ')}…")
            # 追问轮数由真实 LLM 决策（跑 1 次与跑 2 次的节奏就可能不同）——脚本按 meta
            # 相位驱动、不写死轮次；上限 12 轮是防呆（2 题场远用不到）
            phase = ""
            for _ in range(12):
                events = session.send("继续补充：结合项目里的取舍展开说明。")
                phase = Session.phase_of(events) or phase
                if phase == "closing":
                    break
            assert phase == "closing", f"未推进到收尾段（phase={phase}）"
            # 收尾反问带图：问题本身要求看代码 → 回答必然引用图内内容（机械判据）
            closing = session.replies(
                session.send("你能看看我这段代码有什么问题吗？", [image_id])
            )
            print(f"收尾反问（带图）回应：{closing[:160].replace(chr(10), ' ')}…")
            hit = [token for token in IMAGE_TOKENS if token in closing]
            assert hit, f"收尾回答没有引用图内任何标识 {IMAGE_TOKENS}——模型可能没看到图：{closing}"
            print(f"✅ 收尾回答引用了图内标识：{hit}")
            session.send("晋升路径是怎么样的？")

            # ── 3. 报告：逐题图片计数 ─────────────────────────────────
            report = client.get(f"{BASE}/api/interviews/{interview_id}/report").json()["report"]
            counts = [row.get("image_count", 0) for row in report["per_question_comments"]]
            assert counts and counts[0] == 1 and sum(counts) == 1, f"应只有第一题计 1 张截图：{counts}"
            print(f"报告 OK：逐题图片计数 {counts}，总评 {report['overall']}")

            # ── 4. Langfuse：图在 trace 里是媒体引用（不落 base64）──────
            inputs = fetch_langfuse_inputs(interview_id)
            if not inputs:
                print("⚠️ Langfuse 未配置或观测未落库：跳过媒体引用核对（不静默当通过）")
            else:
                with_media = [t for t in inputs if "@@@langfuseMedia:" in t]
                base64_leak = [t for t in inputs if "base64," in t]
                assert with_media, "没有任何观测的 input 带媒体引用——图可能没进请求"
                assert not base64_leak, f"有 {len(base64_leak)} 条观测 input 落了 base64（应为媒体引用）"
                print(f"Langfuse OK：{len(inputs)} 条观测，其中 {len(with_media)} 条含图（媒体引用形态、零 base64）")

            # ── 5. 删除场次连带清图（--keep 时保留现场供审计脚本对拍）────
            if args.keep:
                print(f"--keep：保留场次与临时目录 {_TMP_DIR}")
                print(f"  审计对拍：DB_PATH={os.environ['DB_PATH']} "
                      f"CHECKPOINT_DB_PATH={os.environ['CHECKPOINT_DB_PATH']} "
                      f"UPLOAD_DIR={os.environ['UPLOAD_DIR']} \\\n"
                      f"    uv run python scripts/eval_judge_vision.py")
            else:
                upload_dir = get_settings().upload_dir
                assert (upload_dir / interview_id).is_dir()
                assert client.delete(f"{BASE}/api/interviews/{interview_id}").status_code == 204
                assert not (upload_dir / interview_id).exists(), "删除场次后图片目录仍在"
                print("删除场次 OK：图片目录已清理")

        print("\n✅ 图片通道 smoke 全过（上传/取回 · 带图一场跑通 · 报告计数 · Langfuse 媒体引用"
              + ("）" if args.keep else " · 清理）"))
    finally:
        proc.terminate()
        proc.wait(timeout=10)
        if not args.keep:
            shutil.rmtree(_TMP_DIR, ignore_errors=True)


if __name__ == "__main__":
    main()
