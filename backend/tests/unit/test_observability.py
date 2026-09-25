"""Langfuse 接入单测（P1-M4）：一次面试 = 一个 trace，且带场次/账号归属。

用 InMemorySpanExporter 顶替 OTLP 上报（SDK 支持注入 span_exporter），全程离线可重复——
云端「按场次可查」由 smoke_api 读回核对（那里才是真链路），这里固化的是**我们自己的接线**：
trace_id 由场次派生、多轮 resume 归同一 trace、generation 挂在轮次 span 下。

注意：每个用例用独立 public_key —— Langfuse 的资源管理器按 key 做进程级单例，
复用同一个 key 会让第二个用例拿到上一个用例的导出器。
"""

from __future__ import annotations

import uuid

import pytest
from langfuse import Langfuse
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from app import llm, observability
from app.config import Settings

_REAL_ENABLED = observability.enabled  # conftest 的 autouse 夹具会把它换成 False，先留原引用


@pytest.fixture
def langfuse_env(monkeypatch):
    """启用 Langfuse（假 key + 内存导出器）：返回 (导出器, 客户端, public_key)。

    conftest 的 autouse 夹具默认断开（enabled→False），这里把它接回来。
    """
    public_key = f"pk-lf-test-{uuid.uuid4().hex[:8]}"
    exporter = InMemorySpanExporter()
    client = Langfuse(public_key=public_key, secret_key="sk-lf-test", span_exporter=exporter)
    monkeypatch.setattr(observability, "enabled", lambda: True)
    monkeypatch.setattr(observability, "get_client", lambda: client)
    yield exporter, client, public_key

    client.flush()


def _attrs(span) -> dict:
    return dict(span.attributes or {})


def test_无key时零开销(monkeypatch):
    """未配 key：不构造客户端（不 import langfuse 也不联网），LLM 走原生 SDK。

    必须显式造一份「无 Langfuse key」的配置并撤掉 autouse 夹具的断连——否则测的是夹具而不是
    代码。也不能靠删环境变量：key 在 .env 文件里，dotenv 兜在 os.environ 下面。
    """
    monkeypatch.setattr(observability, "enabled", _REAL_ENABLED)
    monkeypatch.setattr(observability, "get_settings", lambda: Settings(
        _env_file=None, deepseek_api_key="test-key", siliconflow_api_key="test-key",
        jwt_secret="t" * 32, langfuse_public_key="", langfuse_secret_key="",
    ))

    def _boom():  # 一旦被调用即说明降级失效
        raise AssertionError("未配 key 时不该构造 Langfuse 客户端")

    monkeypatch.setattr(observability, "get_client", _boom)

    assert observability.enabled() is False
    with observability.turn_span("iv-1", user_id="u1"):
        pass  # 空操作，不抛错
    observability.flush()

    llm._get_client.cache_clear()
    client = llm._get_client()
    assert client.__class__.__module__.startswith("openai")  # 原生客户端，非 drop-in


def test_一次面试一个trace且多轮归并(langfuse_env):
    exporter, client, _ = langfuse_env

    for _ in range(2):  # 两轮 resume（各自一个请求）
        with observability.turn_span("iv-abc", user_id="u1"):
            pass
    with observability.turn_span("iv-other", user_id="u2"):
        pass
    client.flush()

    spans = exporter.get_finished_spans()
    assert len(spans) == 3
    expected_trace_id = client.create_trace_id(seed="iv-abc")
    same_interview = [s for s in spans if s.context.trace_id == int(expected_trace_id, 16)]
    assert len(same_interview) == 2  # 多轮同 trace
    other = next(s for s in spans if s.context.trace_id != int(expected_trace_id, 16))
    assert _attrs(other)["session.id"] == "iv-other"
    assert _attrs(other)["user.id"] == "u2"
    assert all(_attrs(s)["session.id"] == "iv-abc" for s in same_interview)
    assert all(_attrs(s)["user.id"] == "u1" for s in same_interview)
    assert {s.name for s in spans} == {"interview-turn"}


def test_LLM调用挂在轮次span下(langfuse_env):
    """drop-in 客户端生成的 generation 必须挂在轮次 span 下、落同一个 trace。

    这里用 SDK 同一条调用路径（start_as_current_observation(as_type="generation")）
    代替真实 LLM 请求：验证的是我们的上下文接线，不是 SDK 自身。
    """
    exporter, client, _ = langfuse_env

    with observability.turn_span("iv-xyz", user_id="u1"):
        with client.start_as_current_observation(as_type="generation", name="deepseek-chat") as gen:
            gen.update(usage_details={"input": 10, "output": 20})
    client.flush()

    by_name = {s.name: s for s in exporter.get_finished_spans()}  # 导出顺序不保证
    turn = by_name["interview-turn"]
    generation = by_name["deepseek-chat"]
    assert generation.context.trace_id == turn.context.trace_id  # 同一 trace
    assert generation.parent.span_id == turn.context.span_id  # 挂在轮次下
    assert _attrs(generation)["langfuse.observation.type"] == "generation"
    assert _attrs(generation)["session.id"] == "iv-xyz"
