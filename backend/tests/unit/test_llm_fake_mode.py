"""J6-0：应用级 LLM_MODE=fake 的构造与流式延迟。"""

from __future__ import annotations

import json
import time
from types import SimpleNamespace

from app import llm
from app.llm_fake import FakeLLMClient


def test_fake_mode构造_fake_客户端():
    client = llm._build_client(
        SimpleNamespace(
            llm_mode="fake", llm_fake_delay_s=0.25, llm_fake_call_delay_s=0.5
        )
    )
    assert isinstance(client, FakeLLMClient)
    assert client.delay_s == 0.25
    assert client.call_delay_s == 0.5


async def test_fake_客户端按身份行路由且分片有延迟():
    client = FakeLLMClient(delay_s=0.001)
    response = await client.chat.completions.create(
        model="fake",
        messages=[{"role": "system", "content": "你是「码达 Marda」的评分官"}],
        stream=True,
    )
    pieces = [chunk.choices[0].delta.content async for chunk in response if chunk.choices]
    assert json.loads("".join(pieces))["technical_depth"] == 4
    assert len(pieces) > 1, "流式必须是多片（杀 Agent 演练需要可击杀的窗口）"


async def test_fake_流式首片受调用前延迟约束():
    """J7：TTFB 旋钮必须作用在**第一片之前**，否则首 token SLO 测不出真实语义。"""
    client = FakeLLMClient(call_delay_s=0.05)
    stream = await client.chat.completions.create(
        model="fake",
        messages=[{"role": "system", "content": "你是「码达 Marda」的评分官"}],
        stream=True,
    )
    started = time.perf_counter()
    first = await anext(stream)
    elapsed = time.perf_counter() - started
    assert first.choices[0].delta.content
    assert elapsed >= 0.04, f"首片应等待 call_delay_s，实际 {elapsed:.3f}s"


async def test_fake_非流式调用受调用前延迟约束():
    """judge / report 是非流式：同样要能被 J7 的延迟旋钮表达。"""
    client = FakeLLMClient(call_delay_s=0.05)
    started = time.perf_counter()
    response = await client.chat.completions.create(
        model="fake",
        messages=[{"role": "system", "content": "你是「码达 Marda」的评分官"}],
    )
    elapsed = time.perf_counter() - started
    assert response.choices[0].message.content
    assert elapsed >= 0.04, f"非流式应等待 call_delay_s，实际 {elapsed:.3f}s"
