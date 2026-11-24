"""J6-0：应用级 LLM_MODE=fake 的构造与流式延迟。"""

from __future__ import annotations

import json
from types import SimpleNamespace

from app import llm
from app.llm_fake import FakeLLMClient


def test_fake_mode构造_fake_客户端():
    client = llm._build_client(
        SimpleNamespace(llm_mode="fake", llm_fake_delay_s=0.25)
    )
    assert isinstance(client, FakeLLMClient)
    assert client.delay_s == 0.25


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
