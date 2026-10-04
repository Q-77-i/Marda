"""组合场真实链路 smoke（P2-M8：语音 + 视觉 + 文字在**同一场次**跑通）。

M5/M6 各自验过单条通道，但三条通道从未在同一个场次、同一份 state 上一起跑过——
本脚本就是 PRD §8.2「组合场 smoke」的载体。一场 N=3 里：

1. **语音进**：答案文本 → `POST /api/tts` 合成音频 → 转 16k PCM → **走应用层
   `WS /api/asr`**（连带验中继/query token/二进制帧，比 smoke_voice 直连上游更真）
   → 转写文本**原样作为该轮真实答案提交**，并断言它出现在报告复盘卡里（闭环到底）；
2. **语音出**：开场消息与收尾回答各自 `POST /api/tts`（面试官的每一条终稿都该可播）；
3. **视觉**：合成截图（图内埋标识串）随一轮回答上传 → 收尾反问的回答必须引用图内
   标识（机械判据，同 smoke_vision）+ 报告逐题 image_count + Langfuse 媒体引用形态；
4. **文字**：其余轮次照常 → 报告 / PDF 读回 / 决策回放，全部零回归。

节奏按 `meta.phase` 驱动、不写死轮次（追问轮数由真实 LLM 每轮决策，M6 的教训）；
SSE error 按 P1-M4.7 语义重发同一文本一次（真实用户也是这么做的），仍失败才红。

用法：cd backend && uv run python scripts/smoke_e2e.py [--image /path/to.png] [--keep]
不给 --image 时用 headless Chrome 现渲染一张合成代码截图（图内埋标识串）。
--keep 保留现场（跳过删除清理断言、不删临时目录，供 eval_judge_vision 对拍）。

前置：.env 里 DEEPSEEK_API_KEY / JWT_SECRET / VOLCANO_SPEECH_API_KEY（豆包语音控制台
签发，非方舟 key），且账号已开通「豆包流式语音识别」。
隔离（同 smoke_api/vision）：业务库/checkpointer/上传目录全部落 /tmp，不碰正式数据。
"""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from pypdf import PdfReader

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # backend/

# 必须先于 app.config 首次 get_settings 注入临时路径（lru_cache）
_TMP_DIR = Path(tempfile.mkdtemp(prefix="marda-e2e-smoke-"))
os.environ["DB_PATH"] = str(_TMP_DIR / "marda.sqlite3")
os.environ["CHECKPOINT_DB_PATH"] = str(_TMP_DIR / "checkpoints.sqlite3")
os.environ["UPLOAD_DIR"] = str(_TMP_DIR / "uploads")

import sqlite3  # noqa: E402
import wave  # noqa: E402

_REAL_DB = Path(__file__).resolve().parents[2] / "data" / "marda.sqlite3"
with sqlite3.connect(os.environ["DB_PATH"]) as _dst:
    _dst.execute("ATTACH DATABASE ? AS real", (str(_REAL_DB),))
    _dst.execute("CREATE TABLE questions AS SELECT * FROM real.questions")
    _dst.execute("CREATE TABLE question_sources AS SELECT * FROM real.question_sources")
    _dst.execute("DETACH DATABASE real")

import httpx  # noqa: E402

from app import observability  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.domain import DOMAIN_WEIGHTS  # noqa: E402
from app.tools import asr  # noqa: E402

PORT = 8767
BASE = f"http://127.0.0.1:{PORT}"
WS_ASR = f"ws://127.0.0.1:{PORT}/api/asr"
SMOKE_USER = "smoke_e2e"
SMOKE_PASSWORD = "smoke-e2e-123"

# 题量（轮次语义）：3 轮 = 2 项目深挖 + 1 技术题（project_count(3)=2），够验组合又省时
QUESTION_COUNT = int(os.environ.get("SMOKE_E2E_QUESTION_COUNT", "3"))

# 图内标识串（只有截图里有；收尾回答引用它 = 模型真的读了图，M6 同款机械判据）
MARKER = "Zx9KernelLoop"
IMAGE_TOKENS = (MARKER, "max_iterations", "scratchpad", "decide_action")

# 语音作答素材（**不许用题库原文**——红线；自己编一句中性的、与题目无关的通用作答）
VOICE_ANSWER = (
    "我做过一个基于状态机的对话练习项目，主要负责引擎部分，"
    "先把最小流程跑通，再逐步补齐检索和监控。"
)

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


# ── 语音工具（复用 smoke_voice 的口径：不引 Python 解码依赖，用本机转码器）─────
def char_overlap(expected: str, got: str) -> float:
    a = {c for c in expected if "一" <= c <= "鿿" or c.isdigit()}
    b = {c for c in got if "一" <= c <= "鿿" or c.isdigit()}
    return len(a & b) / len(a) if a else 0.0


def to_wav(mp3: bytes, out: Path) -> bool:
    """MP3 → 16k 单声道 WAV（macOS 自带 afconvert；Linux 需 ffmpeg）。"""
    src = out.with_suffix(".mp3")
    src.write_bytes(mp3)
    if shutil.which("afconvert"):
        cmd = ["afconvert", "-f", "WAVE", "-d", "LEI16@16000", "-c", "1", str(src), str(out)]
    elif shutil.which("ffmpeg"):
        cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", str(src), "-ar", "16000", "-ac", "1", str(out)]
    else:
        return False
    subprocess.run(cmd, check=True, capture_output=True)
    # afconvert 的 WAV 带 FLLR 块（Chromium 不认）→ 按规范布局重写（P2-M5 踩坑）
    with wave.open(str(out)) as w:
        frames, rate, channels = w.readframes(w.getnframes()), w.getframerate(), w.getnchannels()
    assert rate == 16000 and channels == 1, f"转码结果不是 16k 单声道：{rate}Hz/{channels}ch"
    with wave.open(str(out), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(frames)
    return True


async def transcribe_via_ws(pcm: bytes, token: str) -> tuple[str, int, float]:
    """把 PCM 喂给**应用层** WS /api/asr（query token 鉴权 + 二进制帧 + stop 文本帧）。

    返回 (final 文本, partial 次数, 整段耗时)。这条路径以前只有浏览器 CDP 走过，
    脚本里直连上游（smoke_voice）拿不到中继/鉴权/帧协议的覆盖——组合场把它补上。
    """
    from websockets.asyncio.client import connect

    started = time.perf_counter()
    partials = 0
    async with connect(f"{WS_ASR}?token={token}", max_size=None) as ws:
        for i in range(0, len(pcm), asr.FRAME_BYTES):
            await ws.send(pcm[i : i + asr.FRAME_BYTES])
            await asyncio.sleep(0.1)  # 近实时喂：与浏览器每 100ms 一片同节奏
        await ws.send(json.dumps({"type": "stop"}))
        deadline = time.monotonic() + 20
        while True:
            remaining = deadline - time.monotonic()
            assert remaining > 0, "ASR 20s 内未返回 final"
            raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
            message = json.loads(raw)
            if message["type"] == "partial":
                partials += 1
            elif message["type"] == "error":
                raise RuntimeError(f"ASR 上游报错：{message.get('message')}")
            elif message["type"] == "final":
                elapsed = time.perf_counter() - started
                return message["text"], partials, elapsed


# ── 图片工具（同 smoke_vision：headless Chrome 渲染，图内埋标识串）────────────
def _find_chrome() -> str | None:
    candidates = [
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        shutil.which("google-chrome"),
        shutil.which("chromium"),
        shutil.which("chromium-browser"),
    ]
    return next((c for c in candidates if c and Path(c).exists()), None)


def make_screenshot(out: Path) -> None:
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


# ── SSE / 会话（同 smoke_vision 的同步结构）──────────────────────────────────
def _sse_events(response) -> list[dict]:
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
    """一场面试的 HTTP 会话：SSE error 按用户重试语义重发一次（P1-M4.7）。"""

    def __init__(self, client: httpx.Client, interview_id: str) -> None:
        self.client = client
        self.interview_id = interview_id

    def send(self, content: str, images: list[str] | None = None) -> list[dict]:
        for attempt in (1, 2):
            body: dict = {"content": content}
            if images:
                body["images"] = images
            with self.client.stream(
                "POST", f"{BASE}/api/interviews/{self.interview_id}/messages", json=body
            ) as r:
                assert r.status_code == 200, f"{r.status_code} {r.text}"
                events = _sse_events(r)
            errors = [e for e in events if e.get("event") == "error"]
            if not errors:
                return events
            # 真实 LLM 抖动（偶发非法 JSON）：重发同一文本 = 重跑失败节点、不重复计分
            print(f"  ⚠ SSE error（第 {attempt} 次）：{errors[0]['data']['message'][:60]}"
                  + ("，重发同一文本" if attempt == 1 else ""))
        raise AssertionError(f"同一文本重发后仍报错：{errors}")

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


def check_tts(client: httpx.Client, text: str, *, where: str) -> bytes:
    """文本 → POST /api/tts → MP3 体量/帧头（语音出通道）。返回音频字节供复用。"""
    r = client.post(f"{BASE}/api/tts", json={"text": text})
    assert r.status_code == 200, f"{where} TTS 失败：{r.status_code} {r.text[:120]}"
    assert r.headers["content-type"] == "audio/mpeg", f"{where} 不是 audio/mpeg"
    audio = r.content
    assert len(audio) > 2000, f"{where} 音频太小（{len(audio)}B）"
    assert audio[:2] in (b"\xff\xfb", b"\xff\xf3", b"ID"), f"{where} 不像 MP3 帧头"
    print(f"  TTS OK（{where}）：{len(audio) // 1024}KB MP3")
    return audio


def langfuse_inputs(interview_id: str) -> list[str]:
    """读回云端观测的 input 文本（未配置 Langfuse 时返回空表，不静默当通过）。"""
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
    time.sleep(5)  # 上报是异步批量的（smoke_api 有重试轮询；这里睡一口再读）
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
    parser.add_argument("--keep", action="store_true",
                        help="保留现场：跳过删除清理断言、不删临时目录（供审计脚本对拍）")
    args = parser.parse_args()

    settings = get_settings()
    assert settings.volcano_speech_api_key, \
        "未配置 VOLCANO_SPEECH_API_KEY（豆包语音控制台签发的 key，非方舟 key）"

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
            r = client.post(f"{BASE}/api/auth/register",
                            json={"username": SMOKE_USER, "password": SMOKE_PASSWORD})
            assert r.status_code == 201, r.text
            token = r.json()["token"]
            client.headers["Authorization"] = f"Bearer {token}"
            print(f"账号 OK：{SMOKE_USER}")

            # ── 1. 语音进：答案音频（TTS 端点合成 → WS /api/asr 转写）──────────
            print("=" * 60)
            print("语音进（FR-24）：TTS 合成答案 → 应用层 WS 转写 → 作为真实答案")
            print("=" * 60)
            mp3 = check_tts(client, VOICE_ANSWER, where="答案素材")
            wav = _TMP_DIR / "answer.wav"
            assert to_wav(mp3, wav), "本机没有 afconvert/ffmpeg 转码器（装 ffmpeg 后重跑）"
            with wave.open(str(wav)) as w:
                pcm = w.readframes(w.getnframes())
                seconds = w.getnframes() / w.getframerate()
            transcript, partials, elapsed = asyncio.run(transcribe_via_ws(pcm, token))
            overlap = char_overlap(VOICE_ANSWER, transcript)
            print(f"  音频 {seconds:.1f}s · partial {partials} 次 · 整段 {elapsed:.1f}s"
                  f"（≈{elapsed / seconds:.1f}× 实时）· 与原文重合 {overlap:.0%}")
            print(f"  转写：{transcript}")
            assert overlap >= 0.8, f"转写与原文差太多（{overlap:.0%}），链路可疑"

            # ── 2. 建场次（SSE 开场）────────────────────────────────────────
            with client.stream(
                "POST", f"{BASE}/api/interviews",
                json={"position": "Agent/AI 工程师", "question_count": QUESTION_COUNT},
            ) as resp:
                assert resp.status_code == 200
                opening = _sse_events(resp)
            interview_id = opening[0]["data"]["interview_id"]
            session = Session(client, interview_id)
            opening_text = session.replies(opening)
            print(f"场次 {interview_id[:8]} 开场 OK")

            # ── 3. 语音出：开场消息可播报（面试官每一条终稿都该能合成 MP3）────
            check_tts(client, opening_text, where="开场")

            # ── 4. 组合节奏：文字 → 语音答一题 → 带图答一题 → 文字兜到收尾 ──────
            session.send("我是应届生，做过 RAG 问答与 Agent 编排项目。")
            print("── 语音轮：把 ASR 转写原样提交为作答 " + "─" * 26)
            voice_events = session.send(transcript)
            print(f"[面试官] {session.replies(voice_events)[:80]}…")

            png = image_path.read_bytes()
            r = client.post(f"{BASE}/api/interviews/{interview_id}/images",
                            files={"file": (image_path.name, png, "image/png")})
            assert r.status_code == 201, r.text
            image_id = r.json()["image_id"]
            assert client.get(
                f"{BASE}/api/interviews/{interview_id}/images/{image_id}"
            ).content == png, "取回的图与上传不一致"
            print(f"── 图片轮：上传 OK（image_id={image_id[:8]}…，{len(png) // 1024}KB）" + "─" * 14)
            image_events = session.send(
                "这一题结合我截图里的循环实现回答：检索与决策都挂在同一段状态里。", [image_id]
            )
            print(f"[面试官] {session.replies(image_events)[:80]}…")

            phase = Session.phase_of(image_events) or ""
            for _ in range(12):  # 追问轮数由真实 LLM 决策：按 meta 相位驱动、不写死轮次
                events = session.send("继续补充：结合项目里的取舍展开说明。")
                phase = Session.phase_of(events) or phase
                if phase == "closing":
                    break
            assert phase == "closing", f"未推进到收尾段（phase={phase}）"

            # ── 5. 收尾反问带图：回答必须引用图内标识（机械判据）──────────────
            closing = session.replies(
                session.send("你能看看我这段代码有什么问题吗？", [image_id])
            )
            hit = [t for t in IMAGE_TOKENS if t in closing]
            assert hit, f"收尾回答没有引用图内任何标识 {IMAGE_TOKENS}——模型可能没看到图：{closing}"
            print(f"✅ 收尾回答引用了图内标识：{hit}")
            check_tts(client, closing, where="收尾回答")

            done_events = session.send("晋升路径是怎么样的？")
            assert any(e.get("event") == "done" for e in done_events), "最后一次作答后未结束"

            # ── 6. 报告：逐题图片计数 + 语音转写真的进了报告 ──────────────────
            report = client.get(f"{BASE}/api/interviews/{interview_id}/report").json()["report"]
            counts = [row.get("image_count", 0) for row in report["per_question_comments"]]
            assert sum(counts) == 1 and 1 in counts, f"应恰好一题计 1 张截图：{counts}"
            voice_row = next(
                (row for row in report["per_question_comments"]
                 if transcript[:12] in (row.get("candidate_answer") or "")),
                None,
            )
            assert voice_row, "语音转写没有出现在任何一轮的复盘回答里（语音进未闭环）"
            bad_domains = [a["domain"] for a in report["study_advice"]
                           if a["domain"] not in DOMAIN_WEIGHTS]
            assert report["study_advice"] and not bad_domains, f"学习建议域不合法：{bad_domains}"
            print(f"报告 OK：总分 {report['overall']}，逐题图片计数 {counts}"
                  f"（语音轮 = 第{voice_row['number']}轮）")

            # ── 7. PDF 读回（中文无乱码）+ 决策回放（整场事件流）──────────────
            r = client.get(f"{BASE}/api/interviews/{interview_id}/report.pdf")
            assert r.status_code == 200 and r.headers["content-type"] == "application/pdf"
            pdf_text = re.sub(
                r"\s+", "",
                "\n".join(p.extract_text() or "" for p in PdfReader(io.BytesIO(r.content)).pages),
            )
            assert pdf_text and "�" not in pdf_text, "PDF 中文乱码/缺字"
            assert report["position"].replace(" ", "") in pdf_text, "PDF 缺岗位名"
            print(f"PDF OK：{len(r.content) // 1024}KB，中文读回无乱码")

            trace = client.get(f"{BASE}/api/interviews/{interview_id}/trace").json()
            types = [e["type"] for e in trace["events"]]
            asked = [e["round"] for e in trace["events"] if e["type"] == "ask"]
            assert trace["status"] == "finished"
            assert asked == list(range(1, QUESTION_COUNT + 1)), f"出题轮次异常：{asked}"
            assert types[0] == "ask" and types[-1] == "report", f"事件流首尾异常：{types}"
            print(f"回放 OK：{len(trace['events'])} 个事件，轮次 1-{QUESTION_COUNT}")

            # ── 8. Langfuse：含图调用的 input 是媒体引用（零 base64）───────────
            inputs = langfuse_inputs(interview_id)
            if not inputs:
                print("⚠ Langfuse 未配置或观测未落库：跳过媒体引用核对（不静默当通过）")
            else:
                with_media = [t for t in inputs if "@@@langfuseMedia:" in t]
                base64_leak = [t for t in inputs if "base64," in t]
                assert with_media, "没有任何观测的 input 带媒体引用——图可能没进请求"
                assert not base64_leak, f"有 {len(base64_leak)} 条观测 input 落了 base64"
                print(f"Langfuse OK：{len(inputs)} 条观测，其中 {len(with_media)} 条含图（媒体引用、零 base64）")

            # ── 9. 清理闭环（--keep 时保留现场供审计脚本对拍）─────────────────
            if args.keep:
                print(f"--keep：保留场次与临时目录 {_TMP_DIR}")
                print(f"  审计对拍：DB_PATH={os.environ['DB_PATH']} "
                      f"UPLOAD_DIR={os.environ['UPLOAD_DIR']} \\\n"
                      f"    uv run python scripts/eval_judge_vision.py")
            else:
                upload_dir = get_settings().upload_dir
                assert (upload_dir / interview_id).is_dir()
                assert client.delete(f"{BASE}/api/interviews/{interview_id}").status_code == 204
                assert not (upload_dir / interview_id).exists(), "删除场次后图片目录仍在"
                print("删除场次 OK：图片目录已清理")

        print("\n✅ 组合场 smoke 全过（语音进/语音出 · 截图理解 · 文字 · 报告/PDF/回放"
              + ("）" if args.keep else " · 清理）"))
    finally:
        proc.terminate()
        proc.wait(timeout=10)
        if not args.keep:
            shutil.rmtree(_TMP_DIR, ignore_errors=True)


if __name__ == "__main__":
    main()
