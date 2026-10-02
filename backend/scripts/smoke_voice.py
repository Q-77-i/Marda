"""语音通道真实链路 smoke（P2-M5 FR-24，手动跑，不进 pytest）。

验的是单测打不到的三件事：
1. **TTS 真链路**：edge-tts 合成中文 → MP3 字节（大小/帧头/耗时）；
2. **ASR 真链路**：把一段真实语音喂给豆包流式识别 → 拿到累计转写与 final（TTFT/整段耗时）；
3. **闭环**：TTS 合成的那段话，原样喂回 ASR，**转写与原文对得上**——不需要麦克风就能验
   「音频 → 文本」这一整条链路（浏览器侧的采集另由 CDP 验收覆盖）。

用法：
  cd backend && uv run python scripts/smoke_voice.py [--wav /tmp/answer.wav]

ASR 需要一段 16k 单声道 WAV。不给 --wav 时脚本自己用 edge-tts 合成 + 本机转码
（macOS 用自带 afconvert；Linux 需 ffmpeg）；两者都没有就**明确跳过第 2 步**并给出命令，
不静默通过。

前置：.env 里的 VOLCANO_SPEECH_API_KEY（豆包语音控制台签发，非方舟 key），
且账号已开通「豆包流式语音识别」——未开通时上游回 403 requested resource not granted。
"""

from __future__ import annotations

import argparse
import asyncio
import shutil
import subprocess
import sys
import tempfile
import time
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # backend/

from app.config import get_settings  # noqa: E402
from app.tools import asr, tts  # noqa: E402

# 合成用的测试句：**不许用题库原文**（红线），自己编一句中性的
SENTENCE = "这是一段用于验证语音链路的测试文本，包含中文与数字二零二六。"


def char_overlap(expected: str, got: str) -> float:
    """字符重合率（识别允许标点/同音差异，按字符集合口径看「听没听对」）。"""
    a = {c for c in expected if "一" <= c <= "鿿" or c.isdigit()}
    b = {c for c in got if "一" <= c <= "鿿" or c.isdigit()}
    return len(a & b) / len(a) if a else 0.0


async def check_tts(voice: str) -> bytes:
    print("— 1. TTS 真链路（edge-tts）" + "—" * 20)
    started = time.perf_counter()
    chunks: list[bytes] = []
    async for piece in tts.synthesize(SENTENCE, voice=voice):
        if not chunks:
            print(f"    首块 {time.perf_counter() - started:.2f}s")
        chunks.append(piece)
    audio = b"".join(chunks)
    elapsed = time.perf_counter() - started
    print(f"    音色={voice} 字节={len(audio)} 片数={len(chunks)} 耗时={elapsed:.2f}s")
    assert len(audio) > 2000, "TTS 返回的音频太小"
    assert audio[:2] in (b"\xff\xfb", b"\xff\xf3", b"ID"), f"不像 MP3 帧头：{audio[:2].hex()}"
    print("    ✅ MP3 帧头与体量正常\n")
    return audio


def to_wav(mp3: bytes, out: Path) -> bool:
    """MP3 → 16k 单声道 WAV（用本机能力，不引 Python 解码依赖）。"""
    src = out.with_suffix(".mp3")
    src.write_bytes(mp3)
    if shutil.which("afconvert"):  # macOS 自带
        cmd = ["afconvert", "-f", "WAVE", "-d", "LEI16@16000", "-c", "1", str(src), str(out)]
    elif shutil.which("ffmpeg"):
        cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", str(src), "-ar", "16000", "-ac", "1", str(out)]
    else:
        return False
    subprocess.run(cmd, check=True, capture_output=True)
    return True


async def check_asr(wav_path: Path, expect: str) -> None:
    print("— 2. ASR 真链路（豆包流式识别）" + "—" * 20)
    with wave.open(str(wav_path)) as w:
        assert w.getframerate() == 16000 and w.getnchannels() == 1, "需要 16k 单声道 WAV"
        pcm = w.readframes(w.getnframes())
    seconds = w.getnframes() / w.getframerate()

    settings = get_settings()
    session = asr.AsrSession(
        url=settings.volcano_asr_url,
        api_key=settings.volcano_speech_api_key,
        resource_id=settings.volcano_asr_resource_id,
        uid="smoke",
    )
    started = time.perf_counter()
    try:
        await session.open()
    except asr.AsrError as exc:
        # 连不上上游（未开通 / key 不对 / 网络）：给可诊断文案，不甩堆栈
        print(f"!! {exc}")
        sys.exit(4)
    await asyncio.sleep(0.3)  # 让上游把配置帧吃进去（真链路里浏览器采集本身就有这段延迟）
    first_partial: float | None = None
    partials = 0
    try:
        for i in range(0, len(pcm), asr.FRAME_BYTES):
            await session.feed(pcm[i:i + asr.FRAME_BYTES])
            while not session.events.empty():
                event = session.events.get_nowait()
                if event.type == "partial":
                    partials += 1
                    if first_partial is None:
                        first_partial = time.perf_counter() - started
                        print(f"    首字 {first_partial:.2f}s：{event.text}")
                elif event.type == "error":
                    print(f"!! 上游报错：{event.message}")
                    sys.exit(4)
            await asyncio.sleep(0.1)  # 近实时喂：与浏览器每 100ms 一片同节奏
        final = await session.finish()
        elapsed = time.perf_counter() - started
        print(f"    音频 {seconds:.2f}s · partial {partials} 次 · 整段 {elapsed:.2f}s（≈{elapsed / seconds:.1f}× 实时）")
        print(f"    final：{final.text}")
        assert final.type == "final", f"没有拿到 final：{final.type}"
        assert final.text.strip(), "转写为空"
        if expect:
            overlap = char_overlap(expect, final.text)
            print(f"    与原文的字符重合率：{overlap:.0%}（原文：{expect}）")
            assert overlap >= 0.8, f"转写与原文差太多（{overlap:.0%}），链路可疑"
            print("    ✅ 转写与原文对得上\n")
        else:
            # 外部音频（--wav）不知道内容：**明说跳过比对**，不静默装作验过
            print("    ⚠️ 外部音频未给 --expect：只验证了「有转写」，未比对内容\n")
    finally:
        await session.close()


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--wav", help="现有的 16k 单声道 WAV（不给则用 TTS 现合成）")
    parser.add_argument("--expect", help="--wav 里那句话的文本（不给则只验「有转写」、不比对内容）")
    args = parser.parse_args()

    settings = get_settings()
    if not settings.volcano_speech_api_key:
        print("!! 未配置 VOLCANO_SPEECH_API_KEY（豆包语音控制台签发的 key，非方舟 key）")
        sys.exit(2)

    mp3 = await check_tts(settings.tts_voice)

    with tempfile.TemporaryDirectory() as tmp:
        wav = Path(args.wav) if args.wav else Path(tmp) / "speech.wav"
        expect = args.expect or ""
        if not args.wav:
            if not to_wav(mp3, wav):
                print("— 2. ASR 真链路：跳过（本机没有 afconvert/ffmpeg 转码器）")
                print("    装 ffmpeg 或传 --wav <16k 单声道 wav> 后重跑")
                sys.exit(3)
            expect = SENTENCE  # 自己合成的音频，原文已知
        await check_asr(wav, expect)

    print("✅ 语音通道 smoke 全过（TTS 合成 + ASR 识别 + 闭环一致性）")


if __name__ == "__main__":
    asyncio.run(main())
